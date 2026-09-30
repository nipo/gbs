"""Strict JSON wire helpers shared by remote descriptors

Descriptors travel between two gbs instances that may differ in
version. Reading them is strict: a missing field, an unknown field or
a value of the wrong type is an error rather than a silent default, so
a mismatch surfaces where it happens instead of as a wrong build.
"""

from __future__ import annotations
import json
from typing import Any

__all__ = ["WireError", "WireFormat", "WireObject"]


class WireError(ValueError):
    """A descriptor cannot be encoded or decoded"""
    pass


class WireFormat:
    """Version of the descriptor documents

    Bumped on any change of the document layout. Top-level documents
    carry it and refuse any other value.
    """

    VERSION = 1

    @classmethod
    def version_check(cls, reader: WireObject) -> None:
        version = reader.field("version", int)
        if version != cls.VERSION:
            raise WireError(
                f"{reader.what}: format version {version} is not supported "
                f"(expected {cls.VERSION})"
            )

    @staticmethod
    def json_check(value: Any, what: str) -> Any:
        """Check a free-form value is plain JSON and return a copy of it

        Free-form values (backend configuration, filter variables) are
        carried as they are, so anything JSON would turn into another
        type (tuples, non-string keys, paths) is refused instead of
        arriving changed on the other side.
        """
        try:
            text = json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as e:
            raise WireError(f"{what} is not plain JSON: {e}") from e
        copy = json.loads(text)
        if copy != value or not WireFormat.__same_types(copy, value):
            raise WireError(f"{what} does not survive a JSON round trip: {value!r}")
        return copy

    @staticmethod
    def __same_types(a: Any, b: Any) -> bool:
        if type(a) is not type(b):
            return False
        if isinstance(a, dict):
            return all(WireFormat.__same_types(a[k], b[k]) for k in a)
        if isinstance(a, list):
            return all(WireFormat.__same_types(x, y) for x, y in zip(a, b))
        return True


class WireObject:
    """Strict reader for one JSON object

    Every field is read through field(); finish() then refuses any
    field that was not read.
    """

    def __init__(self, data: Any, what: str):
        if not isinstance(data, dict):
            raise WireError(f"{what}: expected an object, got {type(data).__name__}")
        self.data = data
        self.what = what
        self.__read: set[str] = set()

    def field(self, name: str, *types: type) -> Any:
        """Value of a required field, checked against types

        bool is not accepted where int is expected. Pass type(None) to
        allow null.
        """
        if name not in self.data:
            raise WireError(f"{self.what}: missing field {name!r}")
        value = self.data[name]
        if isinstance(value, bool) and bool not in types:
            ok = False
        else:
            ok = isinstance(value, types)
        if not ok:
            expected = ", ".join(t.__name__ for t in types)
            raise WireError(
                f"{self.what}: field {name!r} must be {expected}, "
                f"got {type(value).__name__}"
            )
        self.__read.add(name)
        return value

    def string_list(self, name: str) -> list[str]:
        value = self.field(name, list)
        for item in value:
            if not isinstance(item, str):
                raise WireError(f"{self.what}: field {name!r} must only hold strings")
        return list(value)

    def finish(self) -> None:
        unknown = set(self.data) - self.__read
        if unknown:
            raise WireError(f"{self.what}: unknown field(s) {', '.join(sorted(unknown))}")
