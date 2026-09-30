"""Planning through a remote host

A remote host answers planning queries by asking its own backends, so
pass construction and probes run where the tool is. Each contributed
pass comes back as a PassContribution: its PassDescriptor and the
interface the planner reads. Locally, a RemotePass stands for it in
the plan.
"""

from __future__ import annotations
import copy
from pathlib import Path
from typing import Any, Optional

from .segment import PassDescriptor
from .wire import WireError, WireFormat, WireObject

__all__ = ["PassContribution", "RemotePass"]


class PassContribution:
    """A pass a remote backend contributed, as the planner sees it

    Attributes:
        descriptor: Identity of the pass, to recreate it remotely
        input_types: Input file types
        output_types: Output file types
        types_with_library: File types requiring library classification
        can_fork: Pass can_fork attribute
        priority: Pass priority attribute
        filter_vars: Filter variables the pass contributes, None when
            the pass is not usable
        problem: Why the pass is not usable on its host, None if usable
    """

    def __init__(self, descriptor: PassDescriptor,
                 input_types: set[str], output_types: set[str],
                 types_with_library: set[str],
                 can_fork: bool, priority: int,
                 filter_vars: Optional[dict[str, Any]],
                 problem: Optional[str]):
        if (filter_vars is None) == (problem is None):
            raise WireError(
                f"Pass {descriptor.name}: filter variables are expected "
                f"exactly when the pass is usable"
            )
        self.descriptor = descriptor
        self.input_types = frozenset(input_types)
        self.output_types = frozenset(output_types)
        self.types_with_library = frozenset(types_with_library)
        self.can_fork = can_fork
        self.priority = priority
        self.filter_vars = filter_vars
        self.problem = problem

    @classmethod
    def from_pass(cls, pass_obj: Any, problem: Optional[str], backend: str,
                  config: dict[str, Any], requested_types: set[str]) -> PassContribution:
        """Describe a pass contributed by a backend of this host

        Args:
            pass_obj: Contributed pass
            problem: Verdict of the pass probe
            backend: Backend name
            config: Backend configuration, as the backend was given it
            requested_types: Output types the backend was asked for
        """
        what = f"pass {pass_obj.name}"
        return cls(
            descriptor=PassDescriptor(
                backend=backend,
                name=pass_obj.name,
                pass_class=PassDescriptor.class_name(pass_obj),
                config=WireFormat.json_check(config, f"configuration of {what}"),
                requested_types=requested_types,
            ),
            input_types=set(pass_obj.input_types),
            output_types=set(pass_obj.output_types),
            types_with_library=set(pass_obj.types_with_library),
            can_fork=bool(pass_obj.can_fork),
            priority=int(pass_obj.priority),
            filter_vars=(None if problem is not None else
                         WireFormat.json_check(pass_obj.filter_vars(), f"filter variables of {what}")),
            problem=problem,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "pass": self.descriptor.to_json(),
            "input_types": sorted(self.input_types),
            "output_types": sorted(self.output_types),
            "types_with_library": sorted(self.types_with_library),
            "can_fork": self.can_fork,
            "priority": self.priority,
            "filter_vars": self.filter_vars,
            "problem": self.problem,
        }

    @classmethod
    def from_json(cls, data: Any) -> PassContribution:
        reader = WireObject(data, "pass contribution")
        contribution = cls(
            descriptor=PassDescriptor.from_json(reader.field("pass", dict)),
            input_types=set(reader.string_list("input_types")),
            output_types=set(reader.string_list("output_types")),
            types_with_library=set(reader.string_list("types_with_library")),
            can_fork=reader.field("can_fork", bool),
            priority=reader.field("priority", int),
            filter_vars=reader.field("filter_vars", dict, type(None)),
            problem=reader.field("problem", str, type(None)),
        )
        reader.finish()
        return contribution

    @classmethod
    def list_to_json(cls, contributions: list[PassContribution]) -> dict[str, Any]:
        return {"passes": [c.to_json() for c in contributions]}

    @classmethod
    def list_from_json(cls, data: Any) -> list[PassContribution]:
        reader = WireObject(data, "passes.contribute result")
        contributions = [cls.from_json(c) for c in reader.field("passes", list)]
        reader.finish()
        return contributions


class RemotePass:
    """Planning stand-in for a pass that runs on a remote host

    Exposes the planning interface of a pass; everything else about the
    pass happens on its host. A RemoteSegmentDispatcher dispatches it,
    along with the other passes of its segment.

    Attributes:
        host: Name of the host the pass runs on
        descriptor: Identity of the pass on its host
    """

    def __init__(self, host: str, contribution: PassContribution):
        self.host = host
        self.descriptor = contribution.descriptor
        self.name = contribution.descriptor.name
        self.pass_class = contribution.descriptor.pass_class
        self.input_types = set(contribution.input_types)
        self.output_types = set(contribution.output_types)
        self.types_with_library = set(contribution.types_with_library)
        self.can_fork = contribution.can_fork
        self.priority = contribution.priority
        self.__filter_vars = contribution.filter_vars
        self.__problem = contribution.problem

    def filter_vars(self) -> dict[str, Any]:
        return copy.deepcopy(self.__filter_vars)

    def probe(self) -> Optional[str]:
        """Verdict of the probe on the pass host"""
        return self.__problem

    def output_path(self, file_type: str, path: Path) -> Path:
        """Keep the declared path

        The host produces the output under the name its own pass
        chooses; locally the output is placed at the declared path.
        """
        return path

    def dispatchers(self, context: Any) -> list:
        raise AssertionError(
            f"Pass {self.name} runs on {self.host}: its segment dispatches it"
        )

    def __repr__(self) -> str:
        return f"RemotePass({self.name} on {self.host})"
