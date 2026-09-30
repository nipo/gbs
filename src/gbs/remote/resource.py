"""Serializable resource descriptors"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from ..build.task import Resource, ResourceTypology
from .roots import RootedPath, RootTable
from .wire import WireError, WireObject

__all__ = ["ResourceMetadataCodec", "ResourceDescriptor"]


class ResourceMetadataCodec:
    """Declared kinds of the resource metadata keys

    Metadata is free-form on a Resource, but a host path in it would be
    meaningless on another host, and nothing tells a path from any other
    string. So every key a resource may carry across hosts is declared
    here with its kind, and an undeclared key is an error rather than
    passed through. Plugins attaching their own metadata register
    their keys with register().

    Kinds:
        STRING: A plain string.
        DIRECTORY_LIST: A list of directory paths. Each is translated
            through the root table, and each tree is content the
            resource depends on, transferred along with it.
    """

    STRING = "string"
    DIRECTORY_LIST = "directory-list"

    KINDS = (STRING, DIRECTORY_LIST)

    keys: dict[str, str] = {
        # Compile include directories attached to a source by its
        # repository (BuildContext.populate_pending).
        "include_dirs": DIRECTORY_LIST,
        # HDL language of a Gowin project input.
        "language": STRING,
        # File type tag of the Gowin SerDes init resource.
        "file_type": STRING,
    }

    @classmethod
    def register(cls, key: str, kind: str) -> None:
        if kind not in cls.KINDS:
            raise ValueError(f"Unknown metadata kind {kind!r}")
        existing = cls.keys.get(key)
        if existing is not None and existing != kind:
            raise ValueError(
                f"Metadata key {key!r} is already declared as {existing!r}"
            )
        cls.keys[key] = kind

    @classmethod
    def kind_of(cls, key: str) -> str:
        kind = cls.keys.get(key)
        if kind is None:
            raise WireError(
                f"Resource metadata key {key!r} is not declared; "
                f"declare its kind with ResourceMetadataCodec.register()"
            )
        return kind

    @classmethod
    def locate(cls, metadata: dict[str, Any], table: RootTable) -> dict[str, Any]:
        """Host metadata to host-independent metadata"""
        result: dict[str, Any] = {}
        for key, value in metadata.items():
            kind = cls.kind_of(key)
            if kind == cls.STRING:
                if not isinstance(value, str):
                    raise WireError(f"Resource metadata {key!r} must be a string, got {value!r}")
                result[key] = value
            elif kind == cls.DIRECTORY_LIST:
                if not isinstance(value, (list, tuple)):
                    raise WireError(f"Resource metadata {key!r} must be a list of paths")
                result[key] = [table.locate(Path(p).resolve()) for p in value]
            else:
                raise AssertionError(kind)
        return result

    @classmethod
    def place(cls, metadata: dict[str, Any], table: RootTable) -> dict[str, Any]:
        """Host-independent metadata to host metadata"""
        result: dict[str, Any] = {}
        for key, value in metadata.items():
            kind = cls.kind_of(key)
            if kind == cls.STRING:
                result[key] = value
            elif kind == cls.DIRECTORY_LIST:
                result[key] = [table.path_of(p) for p in value]
            else:
                raise AssertionError(kind)
        return result

    @classmethod
    def to_json(cls, metadata: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in metadata.items():
            kind = cls.kind_of(key)
            if kind == cls.STRING:
                result[key] = value
            elif kind == cls.DIRECTORY_LIST:
                result[key] = [p.to_json() for p in value]
            else:
                raise AssertionError(kind)
        return result

    @classmethod
    def from_json(cls, data: Any) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise WireError("resource metadata: expected an object")
        result: dict[str, Any] = {}
        for key, value in data.items():
            kind = cls.kind_of(key)
            if kind == cls.STRING:
                if not isinstance(value, str):
                    raise WireError(f"Resource metadata {key!r} must be a string")
                result[key] = value
            elif kind == cls.DIRECTORY_LIST:
                if not isinstance(value, list):
                    raise WireError(f"Resource metadata {key!r} must be a list")
                result[key] = [RootedPath.from_json(p) for p in value]
            else:
                raise AssertionError(kind)
        return result

    @classmethod
    def directories(cls, metadata: dict[str, Any]) -> Iterator[RootedPath]:
        """Directory trees host-independent metadata refers to"""
        for key, value in metadata.items():
            if cls.kind_of(key) == cls.DIRECTORY_LIST:
                yield from value


@dataclass(frozen=True)
class ResourceDescriptor:
    """Host-independent description of a Resource

    Metadata is held in host-independent form (see
    ResourceMetadataCodec): path-valued entries are RootedPaths.
    """
    location: RootedPath
    file_type: Optional[str]
    file_type_aliases: frozenset[str]
    file_type_version: Optional[str]
    library: Optional[str]
    typology: ResourceTypology
    generated_by: Optional[str]
    directory: bool
    metadata: dict[str, Any] = field(default_factory=dict, hash=False)

    @classmethod
    def from_resource(cls, resource: Resource, table: RootTable) -> ResourceDescriptor:
        if not isinstance(resource, Resource):
            raise WireError(f"Only file resources can be described, not {resource!r}")
        return cls(
            location=table.locate(resource.path),
            file_type=resource.file_type,
            file_type_aliases=frozenset(resource.file_type_aliases),
            file_type_version=resource.file_type_version,
            library=resource.library,
            typology=resource.typology,
            generated_by=resource.generated_by,
            directory=resource.directory,
            metadata=ResourceMetadataCodec.locate(resource.metadata, table),
        )

    def resource_get(self, context: Any, table: RootTable) -> Resource:
        """Get or create the described resource in a build context

        Args:
            context: BuildContext to register the resource in
            table: Root table placed on this host
        """
        return context.get_resource(
            table.path_of(self.location),
            file_type=self.file_type,
            library=self.library,
            file_type_version=self.file_type_version,
            typology=self.typology,
            generated_by=self.generated_by,
            metadata=ResourceMetadataCodec.place(self.metadata, table),
            legacy_file_type=sorted(self.file_type_aliases),
            directory=self.directory,
        )

    def trees(self) -> Iterator[tuple[RootedPath, bool]]:
        """Content the resource consists of, as (location, is_directory)"""
        yield self.location, self.directory
        for location in ResourceMetadataCodec.directories(self.metadata):
            yield location, True

    def to_json(self) -> dict[str, Any]:
        return {
            "location": self.location.to_json(),
            "file_type": self.file_type,
            "file_type_aliases": sorted(self.file_type_aliases),
            "file_type_version": self.file_type_version,
            "library": self.library,
            "typology": self.typology.value,
            "generated_by": self.generated_by,
            "directory": self.directory,
            "metadata": ResourceMetadataCodec.to_json(self.metadata),
        }

    @classmethod
    def from_json(cls, data: Any) -> ResourceDescriptor:
        reader = WireObject(data, "resource")
        location = RootedPath.from_json(reader.field("location", dict))
        file_type = reader.field("file_type", str, type(None))
        aliases = reader.string_list("file_type_aliases")
        file_type_version = reader.field("file_type_version", str, type(None))
        library = reader.field("library", str, type(None))
        typology_value = reader.field("typology", str)
        generated_by = reader.field("generated_by", str, type(None))
        directory = reader.field("directory", bool)
        metadata = ResourceMetadataCodec.from_json(reader.field("metadata", dict))
        reader.finish()
        try:
            typology = ResourceTypology(typology_value)
        except ValueError:
            raise WireError(f"resource: unknown typology {typology_value!r}") from None
        return cls(
            location=location,
            file_type=file_type,
            file_type_aliases=frozenset(aliases),
            file_type_version=file_type_version,
            library=library,
            typology=typology,
            generated_by=generated_by,
            directory=directory,
            metadata=metadata,
        )
