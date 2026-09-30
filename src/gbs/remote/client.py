"""Client side of a remote connection

A RemoteHost runs a gbs server as a child process, usually through
ssh, speaks to it over the child's standard input and output, and logs
what it writes to standard error.
"""

from __future__ import annotations
import asyncio
import collections
import shlex
from typing import Optional

from ..config.model import GBSConfig, RemoteHostConfig
from ..logging import get_logger
from .channel import ChannelClosed, FrameChannel
from .handshake import HandshakeError, HelloReply, Identity
from .peer import Peer, RemoteError
from .toolhost import RemoteToolHost

__all__ = ["RemoteHostError", "RemoteHost"]

logger = get_logger(__name__)


class RemoteHostError(Exception):
    """A remote host cannot be reached or worked with"""
    pass


class RemoteHost:
    """Connection to a gbs instance on another host

    Created by connect(); use as an async context manager or call
    close().

    Attributes:
        name: Host name for diagnostics
        peer: Messaging endpoint, for requests to the remote
        hello: What the remote answered at connection
        tool_host: The remote tool inventory
    """

    SERVE_ARGS = ["remote", "serve", "--stdio"]
    STDERR_TAIL = 20
    CLOSE_TIMEOUT = 10.0

    def __init__(self, name: str, process: asyncio.subprocess.Process):
        self.name = name
        self.process = process
        self.peer = Peer(FrameChannel(process.stdout, process.stdin), name)
        self.hello: Optional[HelloReply] = None
        self.tool_host: Optional[RemoteToolHost] = None
        self.__stderr_tail: collections.deque[str] = collections.deque(maxlen=self.STDERR_TAIL)
        self.__stderr_task = asyncio.create_task(self.__stderr_forward())

    @classmethod
    def ssh_argv(cls, destination: str, gbs_config: Optional[GBSConfig],
                 keep: bool = False) -> tuple[str, list[str]]:
        """Command line reaching a destination

        Args:
            destination: Name of a configured remote host, or else an
                ssh destination
            gbs_config: Configuration holding remote host definitions
            keep: Have the remote keep its workspace

        Returns:
            Host name and command line
        """
        host = None if gbs_config is None else gbs_config.remote_hosts.get(destination)
        if host is None:
            host = RemoteHostConfig(destination, [destination])
        remote = shlex.join(host.command + cls.SERVE_ARGS + (["--keep"] if keep else []))
        return host.name, ["ssh", "-T", *host.ssh, "--", remote]

    @classmethod
    async def open(cls, destination: str, gbs_config: Optional[GBSConfig],
                   keep: bool = False) -> RemoteHost:
        """Connect to a destination over ssh, see ssh_argv()"""
        name, argv = cls.ssh_argv(destination, gbs_config, keep)
        return await cls.connect(name, argv)

    @classmethod
    async def connect(cls,
                      name: str,
                      argv: list[str],
                      env: Optional[dict[str, str]] = None,
                      cwd: Optional[str] = None,
                      identity: Optional[Identity] = None) -> RemoteHost:
        """Run a server command and handshake with it

        Args:
            name: Host name for diagnostics
            argv: Command line whose standard input and output are a
                served channel
            env: Environment of the command, inherited when None
            cwd: Working directory of the command
            identity: Identity to present, the local one when None

        Raises:
            RemoteHostError: If the command cannot start, the
                connection fails, or the remote is incompatible.
        """
        if identity is None:
            identity = Identity.local()
        logger.debug(f"{name}: running {shlex.join(argv)}")
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
            )
        except OSError as e:
            raise RemoteHostError(f"{name}: cannot run {argv[0]}: {e}") from e

        host = cls(name, process)
        host.peer.start()
        try:
            reply = await host.peer.request("hello", identity.to_json())
            host.hello = HelloReply.from_json(reply.result)
            identity.check(host.hello.identity, "local host", name)
        except (ChannelClosed, RemoteError) as e:
            await host.close()
            raise RemoteHostError(host.failure_describe(f"{name}: handshake failed: {e}")) from e
        except HandshakeError as e:
            await host.close()
            raise RemoteHostError(f"{name}: {e}") from e
        except BaseException:
            await host.close()
            raise
        host.tool_host = RemoteToolHost(name, host.hello.tools, host.peer)
        return host

    def failure_describe(self, message: str) -> str:
        """A failure message followed by the last remote log lines"""
        if not self.__stderr_tail:
            return message
        return message + "\nLast remote output:\n" + "\n".join(
            f"  {line}" for line in self.__stderr_tail
        )

    async def __stderr_forward(self) -> None:
        while True:
            line = await self.process.stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip("\n")
            self.__stderr_tail.append(text)
            logger.info(f"{self.name}: {text}")

    async def close(self) -> None:
        """Shut the remote down and reap it

        Asks the remote to shut down, waits for it to exit, and
        terminates it if it does not in time.
        """
        if self.peer.closed is None:
            try:
                await asyncio.wait_for(self.peer.request("shutdown"), self.CLOSE_TIMEOUT)
            except (ChannelClosed, RemoteError, TimeoutError) as e:
                logger.debug(f"{self.name}: shutdown request failed: {e}")
        await self.peer.close()
        try:
            await asyncio.wait_for(self.process.wait(), self.CLOSE_TIMEOUT)
        except TimeoutError:
            logger.warning(f"{self.name}: remote does not exit, terminating it")
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), self.CLOSE_TIMEOUT)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        await self.__stderr_task
        if self.process.returncode != 0:
            logger.warning(f"{self.name}: remote exited with status {self.process.returncode}")

    async def __aenter__(self) -> RemoteHost:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()
