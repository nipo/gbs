"""Multiplexed request/response and event messaging over a frame channel

Both ends of a connection are Peers: either may issue requests to the
other, several may be in flight in each direction, and each side
answers requests with handlers registered by method name.

Messages are frame headers, one of:

    {kind: request, id, method, params}
    {kind: response, id, result}
    {kind: response, id, error: {type, message, data?}}
    {kind: event, name, data, request?}
    {kind: cancel, id}

Request ids are chosen by the requesting side; a response or a cancel
refers to an id of the side that issued the request. An event carrying
`request` is emitted by the handler serving that request and delivered
to the requester's event callback for it, always before the response.
A frame body travels along any message.
"""

from __future__ import annotations
import asyncio
import itertools
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from ..logging import get_logger
from .channel import ChannelClosed, ChannelError, Frame, FrameChannel, FrameError
from .wire import WireError, WireFormat, WireObject

__all__ = [
    "RemoteError", "MethodError", "Reply", "Event", "Call", "Peer",
]

logger = get_logger(__name__)


class RemoteError(Exception):
    """The other side answered a request with an error

    Attributes:
        type: Error type name
        message: Human-readable description
        data: Structured details, None when absent
    """

    def __init__(self, method: str, type: str, message: str, data: Any = None):
        super().__init__(f"{method}: {type}: {message}")
        self.method = method
        self.type = type
        self.message = message
        self.data = data


class MethodError(Exception):
    """Raised by a handler to answer with a given error type"""

    def __init__(self, type: str, message: str, data: Any = None):
        super().__init__(message)
        self.type = type
        self.message = message
        self.data = None if data is None else WireFormat.json_check(data, f"data of {type} error")


@dataclass(frozen=True)
class Reply:
    """Result of a request, with its frame body"""
    result: Any = None
    body: bytes = b""


@dataclass(frozen=True)
class Event:
    """An event received from the other side

    Attributes:
        name: Event name
        data: JSON value
        body: Frame body
        request: Id of the request it relates to, None if unsolicited
    """
    name: str
    data: Any
    body: bytes = b""
    request: Optional[int] = None


class Call:
    """A request being served by a handler"""

    def __init__(self, peer: Peer, id: int, method: str, params: Any, body: bytes):
        self.peer = peer
        self.id = id
        self.method = method
        self.params = params
        self.body = body
        self.close_after_reply = False

    async def event(self, name: str, data: Any = None, body: bytes = b"") -> None:
        """Send an event tied to this request"""
        await self.peer.event(name, data, body, request=self.id)

    def closing(self) -> None:
        """Close the connection once the response to this call is sent"""
        self.close_after_reply = True


Handler = Callable[[Call], Awaitable[Any]]
EventCallback = Callable[[Event], None]


class Peer:
    """One end of a multiplexed connection

    Handlers are coroutines taking a Call and returning a JSON value or
    a Reply. Each incoming request runs in its own task. Event
    callbacks are plain functions called in arrival order from the
    reading loop; they must not block.

    When the connection ends, for whatever reason, every pending
    request fails with ChannelClosed and running handlers are
    cancelled. close() must still be called to release the channel.
    """

    def __init__(self, channel: FrameChannel, name: str):
        self.channel = channel
        self.name = name
        self.methods: dict[str, Handler] = {}
        self.subscribers: dict[str, list[EventCallback]] = {}
        self.__ids = itertools.count(1)
        self.__pending: dict[int, Peer.Pending] = {}
        self.__abandoned: set[int] = set()
        self.__incoming: dict[int, asyncio.Task] = {}
        self.__background: set[asyncio.Task] = set()
        self.__reader: Optional[asyncio.Task] = None
        self.__closed: Optional[ChannelClosed] = None
        self.__closed_event = asyncio.Event()

    @dataclass
    class Pending:
        method: str
        future: asyncio.Future
        on_event: Optional[EventCallback]

    def method_register(self, name: str, handler: Handler) -> None:
        if name in self.methods:
            raise ValueError(f"Method {name!r} is already registered")
        self.methods[name] = handler

    def subscribe(self, name: str, callback: EventCallback) -> None:
        """Receive unsolicited events of a given name"""
        self.subscribers.setdefault(name, []).append(callback)

    def start(self) -> None:
        if self.__reader is not None:
            raise RuntimeError(f"{self.name}: peer already started")
        self.__reader = asyncio.create_task(self.__read_loop())

    @property
    def closed(self) -> Optional[ChannelClosed]:
        """Why the connection ended, None while it is up"""
        return self.__closed

    async def wait_closed(self) -> ChannelClosed:
        await self.__closed_event.wait()
        return self.__closed

    async def __aenter__(self) -> Peer:
        self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def request(self,
                      method: str,
                      params: Any = None,
                      body: bytes = b"",
                      on_event: Optional[EventCallback] = None) -> Reply:
        """Issue a request and wait for its response

        Args:
            method: Method name on the other side
            params: JSON value
            body: Frame body sent along
            on_event: Called with each event the handler emits for
                this request

        Raises:
            RemoteError: If the other side answers with an error.
            ChannelClosed: If the connection ends first.
            FrameError: If the request cannot be encoded.

        Cancelling the caller cancels the request on the other side.
        """
        if self.__closed is not None:
            raise ChannelClosed(str(self.__closed))
        id = next(self.__ids)
        future = asyncio.get_running_loop().create_future()
        self.__pending[id] = self.Pending(method, future, on_event)
        try:
            await self.channel.write(Frame(
                {"kind": "request", "id": id, "method": method, "params": params},
                body,
            ))
            return await future
        except asyncio.CancelledError:
            if self.__pending.pop(id, None) is not None and self.__closed is None:
                self.__abandoned.add(id)
                self.__spawn(self.__cancel_send(id))
            raise
        except ChannelError:
            self.__pending.pop(id, None)
            raise

    async def event(self, name: str, data: Any = None, body: bytes = b"",
                    request: Optional[int] = None) -> None:
        """Send an event, tied to an incoming request when given"""
        header = {"kind": "event", "name": name, "data": data}
        if request is not None:
            header["request"] = request
        await self.channel.write(Frame(header, body))

    async def close(self) -> None:
        """End the connection

        Pending requests fail, running handlers are cancelled, and
        frames already queued are flushed before the channel closes.
        """
        self.__teardown(ChannelClosed(f"{self.name}: connection closed"))
        current = asyncio.current_task()
        tasks = [t for t in (self.__reader, *self.__background) if t is not None and t is not current]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.channel.close()

    def __teardown(self, reason: ChannelClosed) -> None:
        if self.__closed is not None:
            return
        self.__closed = reason
        for pending in self.__pending.values():
            if not pending.future.done():
                pending.future.set_exception(ChannelClosed(str(reason)))
        self.__pending.clear()
        self.__abandoned.clear()
        current = asyncio.current_task()
        for task in self.__incoming.values():
            if task is not current:
                task.cancel()
        self.__closed_event.set()

    def __spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.__background.add(task)
        task.add_done_callback(self.__background.discard)
        return task

    async def __cancel_send(self, id: int) -> None:
        try:
            await self.channel.write(Frame({"kind": "cancel", "id": id}))
        except ChannelError:
            pass

    async def __read_loop(self) -> None:
        reason = None
        try:
            while True:
                frame = await self.channel.read()
                if frame is None:
                    reason = ChannelClosed(f"{self.name}: connection closed by the other side")
                    break
                self.__dispatch(frame)
        except (FrameError, WireError) as e:
            logger.error(f"{self.name}: protocol error: {e}")
            reason = ChannelClosed(f"{self.name}: protocol error: {e}")
        except ChannelClosed as e:
            reason = ChannelClosed(f"{self.name}: {e}")
        except Exception as e:
            logger.exception(f"{self.name}: message handling failed")
            reason = ChannelClosed(f"{self.name}: message handling failed: {e}")
        finally:
            if reason is None:
                reason = ChannelClosed(f"{self.name}: connection closed")
            self.__teardown(reason)

    def __dispatch(self, frame: Frame) -> None:
        reader = WireObject(frame.header, f"{self.name} message")
        kind = reader.field("kind", str)
        if kind == "request":
            self.__request_receive(reader, frame.body)
        elif kind == "response":
            self.__response_receive(reader, frame.body)
        elif kind == "event":
            self.__event_receive(reader, frame.body)
        elif kind == "cancel":
            id = reader.field("id", int)
            reader.finish()
            task = self.__incoming.get(id)
            if task is not None:
                task.cancel()
        else:
            raise WireError(f"{reader.what}: unknown kind {kind!r}")

    def __request_receive(self, reader: WireObject, body: bytes) -> None:
        id = reader.field("id", int)
        method = reader.field("method", str)
        params = reader.value("params")
        reader.finish()
        if id in self.__incoming:
            raise WireError(f"{reader.what}: request id {id} is already in use")
        call = Call(self, id, method, params, body)
        self.__incoming[id] = self.__spawn(self.__serve(call))

    def __response_receive(self, reader: WireObject, body: bytes) -> None:
        id = reader.field("id", int)
        if "error" in reader.data:
            error = WireObject(reader.field("error", dict), f"{reader.what} error")
            type = error.field("type", str)
            message = error.field("message", str)
            data = error.value("data") if "data" in error.data else None
            error.finish()
            outcome = None
        else:
            outcome = Reply(reader.value("result"), body)
        reader.finish()

        pending = self.__pending.pop(id, None)
        if pending is None:
            if id in self.__abandoned:
                self.__abandoned.discard(id)
                return
            raise WireError(f"{reader.what}: response to unknown request {id}")
        if pending.future.done():
            return
        if outcome is None:
            pending.future.set_exception(RemoteError(pending.method, type, message, data))
        else:
            pending.future.set_result(outcome)

    def __event_receive(self, reader: WireObject, body: bytes) -> None:
        name = reader.field("name", str)
        data = reader.value("data")
        request = reader.field("request", int) if "request" in reader.data else None
        reader.finish()
        event = Event(name, data, body, request)

        if request is None:
            callbacks = self.subscribers.get(name, [])
            if not callbacks:
                logger.debug(f"{self.name}: no subscriber for event {name!r}")
        else:
            pending = self.__pending.get(request)
            if pending is None:
                if request in self.__abandoned:
                    return
                raise WireError(f"{reader.what}: event {name!r} for unknown request {request}")
            callbacks = [pending.on_event] if pending.on_event is not None else []

        for callback in callbacks:
            try:
                callback(event)
            except Exception:
                logger.exception(f"{self.name}: callback for event {name!r} failed")

    async def __serve(self, call: Call) -> None:
        try:
            handler = self.methods.get(call.method)
            if handler is None:
                raise MethodError("UnknownMethod", f"No method {call.method!r}")
            outcome = await handler(call)
            if not isinstance(outcome, Reply):
                outcome = Reply(outcome)
            frame = Frame({"kind": "response", "id": call.id, "result": outcome.result}, outcome.body)
            self.channel.encode(frame)
        except asyncio.CancelledError:
            if self.__closed is not None:
                raise
            frame = self.error_frame(call.id, "Cancelled", f"{call.method} was cancelled")
        except MethodError as e:
            frame = self.error_frame(call.id, e.type, e.message, e.data)
        except Exception as e:
            logger.debug(f"{self.name}: {call.method} failed", exc_info=True)
            frame = self.error_frame(call.id, type(e).__name__, str(e))
        finally:
            self.__incoming.pop(call.id, None)

        try:
            await self.channel.write(frame)
        except FrameError as e:
            await self.channel.write(self.error_frame(call.id, "FrameError", str(e)))
        except ChannelClosed:
            return
        if call.close_after_reply:
            await self.close()

    @staticmethod
    def error_frame(id: int, type: str, message: str, data: Any = None) -> Frame:
        error = {"type": type, "message": message}
        if data is not None:
            error["data"] = data
        return Frame({"kind": "response", "id": id, "error": error})
