"""Content manifest and blob store

A manifest lists the files and directories a set of resources consists
of, each file identified by its content hash, so the receiving side can
rebuild the tree from a content-addressed blob store and only blobs it
does not hold yet need transferring.

Symbolic links are followed: a link is listed, and rebuilt, as the file
or directory it points to, under the link's own location. A dangling
link and a directory link looping back to one of its ancestors are
errors. Tools read through links transparently, and the receiving side
may not hold the link target anywhere.
"""

from __future__ import annotations
import hashlib
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from .resource import ResourceDescriptor
from .roots import RootedPath, RootTable
from .wire import WireError, WireFormat, WireObject

__all__ = ["ManifestEntry", "ContentManifest", "BlobStore"]


@dataclass(frozen=True)
class ManifestEntry:
    """One file or directory of a manifest

    Attributes:
        location: Where the entry goes
        kind: FILE or DIRECTORY
        sha256: Hex content hash, None for a directory
        size: Content size in bytes, None for a directory
        executable: Whether the file is executable, False for a directory
    """
    location: RootedPath
    kind: str
    sha256: Optional[str] = None
    size: Optional[int] = None
    executable: bool = False

    FILE = "file"
    DIRECTORY = "directory"

    def __post_init__(self):
        if self.kind == self.FILE:
            BlobStore.digest_check(self.sha256)
            if not isinstance(self.size, int) or self.size < 0:
                raise WireError(f"Manifest file {self.location} has invalid size {self.size!r}")
        elif self.kind == self.DIRECTORY:
            if self.sha256 is not None or self.size is not None or self.executable:
                raise WireError(f"Manifest directory {self.location} carries file attributes")
        else:
            raise WireError(f"Manifest entry {self.location} has unknown kind {self.kind!r}")

    def to_json(self) -> dict[str, Any]:
        data = {"location": self.location.to_json(), "kind": self.kind}
        if self.kind == self.FILE:
            data["sha256"] = self.sha256
            data["size"] = self.size
            data["executable"] = self.executable
        return data

    @classmethod
    def from_json(cls, data: Any) -> ManifestEntry:
        reader = WireObject(data, "manifest entry")
        location = RootedPath.from_json(reader.field("location", dict))
        kind = reader.field("kind", str)
        if kind == cls.FILE:
            entry = cls(
                location, kind,
                sha256=reader.field("sha256", str),
                size=reader.field("size", int),
                executable=reader.field("executable", bool),
            )
        else:
            entry = cls(location, kind)
        reader.finish()
        return entry


class ContentManifest:
    """Deduplicated list of the files and directories of some resources

    Entries are keyed by location: a file listed by itself and again
    below a directory resource, or two overlapping directory resources,
    yield one entry per location. Every directory of a directory
    resource is listed, so empty ones are rebuilt too.
    """

    def __init__(self, entries: Iterable[ManifestEntry]):
        self.entries: dict[RootedPath, ManifestEntry] = {}
        for entry in entries:
            self.__add(entry)

    def __add(self, entry: ManifestEntry) -> None:
        existing = self.entries.get(entry.location)
        if existing is None:
            self.entries[entry.location] = entry
        elif existing != entry:
            raise WireError(
                f"Manifest lists {entry.location} twice with different content"
            )

    @property
    def files(self) -> list[ManifestEntry]:
        return [e for e in self.entries.values() if e.kind == ManifestEntry.FILE]

    @property
    def directories(self) -> list[ManifestEntry]:
        return [e for e in self.entries.values() if e.kind == ManifestEntry.DIRECTORY]

    def digests(self) -> set[str]:
        """Content hashes the manifest needs"""
        return {e.sha256 for e in self.files}

    @classmethod
    def compute(cls, table: RootTable, descriptors: Iterable[ResourceDescriptor]) -> ContentManifest:
        """List the content of resources from this host

        Args:
            table: Root table placed on this host
            descriptors: Resources whose content to list, including the
                directory trees their metadata refers to

        Raises:
            WireError: If a resource is missing, has the wrong kind, or
                holds a dangling or looping link.
        """
        builder = cls.Builder(table)
        for descriptor in descriptors:
            for location, is_directory in descriptor.trees():
                builder.tree_add(location, is_directory)
        return cls(builder.entries.values())

    class Builder:
        """Walks host trees into manifest entries, hashing each file once"""

        def __init__(self, table: RootTable):
            self.table = table
            self.entries: dict[RootedPath, ManifestEntry] = {}

        def tree_add(self, location: RootedPath, is_directory: bool) -> None:
            path = self.table.path_of(location)
            if is_directory:
                if not path.is_dir():
                    raise WireError(f"Directory {path} is missing")
                self.directory_walk(path, ancestors=())
            else:
                if not path.is_file():
                    raise WireError(f"File {path} is missing or not a regular file")
                self.file_add(path)

        def entry_add(self, entry: ManifestEntry) -> None:
            existing = self.entries.get(entry.location)
            if existing is not None and existing.kind != entry.kind:
                raise WireError(f"{entry.location} is both a file and a directory")
            self.entries[entry.location] = entry

        def file_add(self, path: Path) -> None:
            location = self.table.locate(path)
            if location in self.entries:
                if self.entries[location].kind != ManifestEntry.FILE:
                    raise WireError(f"{location} is both a file and a directory")
                return
            st = path.stat()
            self.entry_add(ManifestEntry(
                location, ManifestEntry.FILE,
                sha256=BlobStore.file_digest(path),
                size=st.st_size,
                executable=bool(st.st_mode & stat.S_IXUSR),
            ))

        def directory_walk(self, path: Path, ancestors: tuple[tuple[int, int], ...]) -> None:
            st = path.stat()
            identity = (st.st_dev, st.st_ino)
            if identity in ancestors:
                raise WireError(f"Directory link loop at {path}")
            ancestors = ancestors + (identity,)
            self.entry_add(ManifestEntry(self.table.locate(path), ManifestEntry.DIRECTORY))
            for child in sorted(path.iterdir()):
                if child.is_symlink() and not child.exists():
                    raise WireError(f"Dangling link {child}")
                if child.is_dir():
                    self.directory_walk(child, ancestors)
                elif child.is_file():
                    self.file_add(child)
                else:
                    raise WireError(f"{child} is neither a regular file nor a directory")

    def materialize(self, table: RootTable, store: BlobStore, link: bool = True) -> None:
        """Rebuild the listed trees on this host from a blob store

        Files are hard-linked from the store when link is set, the
        filesystem allows it, and the blob's executable bit matches;
        copied otherwise. Nothing listed may already exist, except
        directories.

        Args:
            table: Root table placed on this host
            store: Store holding every blob the manifest needs
            link: Whether hard links may be used. A hard-linked file
                shares the store's copy: a tool writing to it in place
                would alter the store.
        """
        missing = sorted(d for d in self.digests() if not store.has(d))
        if missing:
            raise WireError(f"Blob store lacks {len(missing)} blob(s), first {missing[0]}")

        for entry in sorted(self.directories, key=lambda e: len(e.location.path.parts)):
            table.path_of(entry.location).mkdir(parents=True, exist_ok=True)

        for entry in self.files:
            dest = table.path_of(entry.location)
            if dest.exists() or dest.is_symlink():
                raise WireError(f"Cannot materialize {entry.location}: {dest} exists")
            blob = store.path(entry.sha256)
            blob_stat = blob.stat()
            if blob_stat.st_size != entry.size:
                raise WireError(
                    f"Blob {entry.sha256} is {blob_stat.st_size} bytes, "
                    f"{entry.location} expects {entry.size}"
                )
            dest.parent.mkdir(parents=True, exist_ok=True)
            blob_executable = bool(blob_stat.st_mode & stat.S_IXUSR)
            if link and blob_executable == entry.executable and store.link(entry.sha256, dest):
                continue
            shutil.copyfile(blob, dest)
            dest.chmod(0o755 if entry.executable else 0o644)

    def to_json(self) -> dict[str, Any]:
        return {
            "version": WireFormat.VERSION,
            "entries": [
                e.to_json()
                for e in sorted(self.entries.values(),
                                key=lambda e: (e.location.root, e.location.path.parts))
            ],
        }

    @classmethod
    def from_json(cls, data: Any) -> ContentManifest:
        reader = WireObject(data, "manifest")
        WireFormat.version_check(reader)
        entries = reader.field("entries", list)
        reader.finish()
        manifest = cls([])
        for item in entries:
            entry = ManifestEntry.from_json(item)
            if entry.location in manifest.entries:
                raise WireError(f"manifest: {entry.location} is listed twice")
            manifest.entries[entry.location] = entry
        return manifest


class BlobStore:
    """Content-addressed file store

    Blob of hash h is `<root>/<h[:2]>/<h>`. Blobs are stored read-only
    and never executable; materialization sets the executable bit on
    its own copies.
    """

    CHUNK = 1 << 20

    def __init__(self, root: Path):
        self.root = root

    @staticmethod
    def digest_check(digest: Any) -> str:
        if (not isinstance(digest, str) or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)):
            raise WireError(f"Invalid sha256 digest {digest!r}")
        return digest

    @classmethod
    def file_digest(cls, path: Path) -> str:
        with open(path, "rb") as f:
            return hashlib.file_digest(f, "sha256").hexdigest()

    def path(self, digest: str) -> Path:
        self.digest_check(digest)
        return self.root / digest[:2] / digest

    def has(self, digest: str) -> bool:
        return self.path(digest).is_file()

    def file_add(self, source: Path) -> str:
        """Store a copy of a file, return its hash"""
        digest = self.file_digest(source)
        if not self.has(digest):
            with open(source, "rb") as f:
                self.__write(digest, lambda out: shutil.copyfileobj(f, out, self.CHUNK))
        return digest

    def bytes_add(self, data: bytes, digest: Optional[str] = None) -> str:
        """Store content, return its hash

        When digest is given, the content must match it.
        """
        actual = hashlib.sha256(data).hexdigest()
        if digest is not None and digest != actual:
            raise WireError(f"Blob content hashes to {actual}, not {digest}")
        if not self.has(actual):
            self.__write(actual, lambda out: out.write(data))
        return actual

    def __write(self, digest: str, writer) -> None:
        dest = self.path(digest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as out:
                writer(out)
            os.chmod(tmp, 0o444)
            os.replace(tmp, dest)
        except BaseException:
            os.unlink(tmp)
            raise

    def link(self, digest: str, dest: Path) -> bool:
        """Hard-link a blob to dest, False when the filesystem refuses"""
        try:
            os.link(self.path(digest), dest)
        except OSError:
            return False
        return True
