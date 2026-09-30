"""Path root table

Hosts share no filesystem layout, so a path never crosses the wire as
it is. It is expressed relative to one of a few roots — the project
directory, each repository, the build output tree, the shared cache —
each named by a symbolic id. Each side then places the roots where it
wants: the local host where they are, a remote host under
`<base>/roots/<id>/`.

A root lying inside another root is placed inside it too, at the same
relative location, so relative references between files keep working
on the other side whichever root the files are expressed against.
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Optional

from .wire import WireError, WireObject

__all__ = ["RootedPath", "Root", "RootTable"]


@dataclass(frozen=True)
class RootedPath:
    """A location relative to a root of a RootTable

    Attributes:
        root: Root id
        path: Normalized relative POSIX path, "." for the root itself
    """
    root: str
    path: PurePosixPath

    def __post_init__(self):
        if not isinstance(self.path, PurePosixPath):
            raise WireError(f"Rooted path {self.path!r} is not a PurePosixPath")
        if self.path.is_absolute():
            raise WireError(f"Rooted path {self.path} is absolute")
        if any(part in ("..", ".") for part in self.path.parts):
            raise WireError(f"Rooted path {self.path} is not normalized")

    def to_json(self) -> dict[str, Any]:
        return {"root": self.root, "path": self.path.as_posix()}

    @classmethod
    def from_json(cls, data: Any) -> RootedPath:
        reader = WireObject(data, "rooted path")
        root = reader.field("root", str)
        path = reader.field("path", str)
        reader.finish()
        if "\\" in path:
            raise WireError(f"Rooted path {path!r} is not a POSIX path")
        return cls(root, PurePosixPath(path))

    def __str__(self) -> str:
        return f"<{self.root}>/{self.path.as_posix()}"


@dataclass(frozen=True)
class Root:
    """One root of a RootTable

    Attributes:
        id: Symbolic id, usable as a directory name
        label: Human-readable description, for diagnostics
        parent: Location of the root relative to the innermost other
            root containing it, None for an outermost root
        path: Where the root is on this host, None until placed
    """
    id: str
    label: str
    parent: Optional[RootedPath]
    path: Optional[Path]

    ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

    def __post_init__(self):
        if not self.ID_PATTERN.fullmatch(self.id):
            raise WireError(f"Invalid root id {self.id!r}")


class RootTable:
    """Set of roots every transferred path is expressed against

    A local table is built from host paths (from_paths(),
    for_build(), from_realization()). A table read from the wire has
    no host paths until placed() gives it some.
    """

    REMOTE_ROOTS_DIR = "roots"

    def __init__(self, roots: Iterable[Root]):
        self.roots: dict[str, Root] = {}
        for root in roots:
            if root.id in self.roots:
                raise WireError(f"Duplicate root id {root.id!r}")
            self.roots[root.id] = root
        for root in self.roots.values():
            if root.parent is not None and root.parent.root not in self.roots:
                raise WireError(
                    f"Root {root.id!r} is placed in unknown root {root.parent.root!r}"
                )
        for root in self.roots.values():
            self.__ancestry_check(root)

    def __ancestry_check(self, root: Root) -> None:
        seen = {root.id}
        while root.parent is not None:
            root = self.roots[root.parent.root]
            if root.id in seen:
                raise WireError(f"Root {root.id!r} is placed inside itself")
            seen.add(root.id)

    @classmethod
    def from_paths(cls, roots: Iterable[tuple[str, str, Path]]) -> RootTable:
        """Build a local table from (id, label, host path) triples

        Paths must be absolute and are resolved. A path already given
        by an earlier triple is dropped. Nesting between roots is
        computed from the paths.
        """
        entries: list[tuple[str, str, Path]] = []
        paths: set[Path] = set()
        for root_id, label, path in roots:
            if not path.is_absolute():
                raise WireError(f"Root {root_id!r} path {path} is not absolute")
            path = path.resolve()
            if path in paths:
                continue
            paths.add(path)
            entries.append((root_id, label, path))

        result = []
        for root_id, label, path in entries:
            parent = None
            best = None
            for other_id, _, other_path in entries:
                if other_path == path or not path.is_relative_to(other_path):
                    continue
                if best is None or len(other_path.parts) > len(best[1].parts):
                    best = (other_id, other_path)
            if best is not None:
                parent = RootedPath(best[0], cls.relative(path, best[1]))
            result.append(Root(root_id, label, parent, path))
        return cls(result)

    @classmethod
    def for_build(
        cls,
        project_dir: Path,
        repositories: Iterable[Any],
        base_output_path: Path,
        shared_cache_root: Path,
        covered: Iterable[Path] = (),
    ) -> RootTable:
        """Build the local table of a build

        Roots are the project directory, each repository root, the
        base output path and the shared cache root. Repositories may
        contribute files outside their root (sibling support
        directories, libraries declared with a relative path going up),
        so every path of covered that falls outside these roots gets
        an extra root: the path itself if it is a directory, its parent
        otherwise.
        """
        roots: list[tuple[str, str, Path]] = [
            ("project", "project directory", project_dir.resolve()),
        ]
        for index, repo in enumerate(repositories):
            roots.append((f"repo-{index}", f"repository {repo.name}", Path(repo.root).resolve()))
        roots.append(("output", "build output", base_output_path.resolve()))
        roots.append(("cache", "shared cache", shared_cache_root.resolve()))

        table = cls.from_paths(roots)
        extra: set[Path] = set()
        for path in covered:
            path = path.resolve()
            if table.root_of(path) is not None:
                continue
            extra.add(path if path.is_dir() else path.parent)

        # Keep only outermost extra directories, so one tree gets one root
        outermost = [
            p for p in sorted(extra)
            if not any(p != q and p.is_relative_to(q) for q in extra)
        ]
        for index, path in enumerate(outermost):
            roots.append((f"extra-{index}", f"directory {path}", path))

        return cls.from_paths(roots)

    @classmethod
    def from_realization(cls, realization: Any) -> RootTable:
        """Build the local table of a PlanRealization

        Covers every resolved source and its include directories,
        repository definition files, the project file and output goal
        paths.

        GBS configuration files are deliberately not covered: they
        describe the local host's tools, and the remote side uses its
        own. Their DEFINITION resources must not be transferred.
        """
        project = realization.project
        plan = realization.plan
        if project.path is None:
            raise WireError("Project has no file; cannot place its directory")
        project_file = Path(project.path).resolve()

        covered: list[Path] = [project_file]
        for source in realization.source_fileset.get_all_files():
            covered.append(Path(source.path))
            covered.extend(Path(d) for d in source.include_dirs)
        for repo in plan.repositories:
            covered.extend(Path(p) for p in repo.definition_files)
        for output in plan.output_group.outputs:
            covered.append(Path(output.path))
            covered.append(Path(plan.output_path(output)))

        return cls.for_build(
            project_dir=project_file.parent,
            repositories=plan.repositories,
            base_output_path=realization.build_ctx.base_output_path,
            shared_cache_root=realization.build_ctx.shared_cache_root,
            covered=covered,
        )

    @staticmethod
    def relative(path: Path, root: Path) -> PurePosixPath:
        rel = path.relative_to(root)
        return PurePosixPath(*rel.parts) if rel.parts else PurePosixPath(".")

    def root_of(self, path: Path) -> Optional[Root]:
        """Innermost placed root containing an absolute path, if any"""
        best = None
        for root in self.roots.values():
            if root.path is None:
                raise WireError(f"Root {root.id!r} is not placed on this host")
            if path.is_relative_to(root.path):
                if best is None or len(root.path.parts) > len(best.path.parts):
                    best = root
        return best

    def locate(self, path: Path) -> RootedPath:
        """Express an absolute host path against the innermost root containing it

        The path is taken as is, not resolved: callers pass resolved
        resource paths, and a path walked below one keeps its location
        even when it is a symbolic link.

        Raises:
            WireError: If the path is relative or outside every root.
        """
        if not path.is_absolute():
            raise WireError(f"Cannot locate relative path {path}")
        root = self.root_of(path)
        if root is None:
            raise WireError(f"Path {path} is outside every transferable root")
        return RootedPath(root.id, self.relative(path, root.path))

    def path_of(self, location: RootedPath) -> Path:
        """Host path of a location on this host"""
        root = self.roots.get(location.root)
        if root is None:
            raise WireError(f"Unknown root {location.root!r} in {location}")
        if root.path is None:
            raise WireError(f"Root {root.id!r} is not placed on this host")
        if location.path == PurePosixPath("."):
            return root.path
        return root.path.joinpath(*location.path.parts)

    def placed(self, base: Path) -> RootTable:
        """Same table, with roots placed below base

        An outermost root goes to `<base>/roots/<id>`, any other root
        inside the root containing it.
        """
        if not base.is_absolute():
            raise WireError(f"Placement base {base} is not absolute")
        base = base.resolve()
        paths: dict[str, Path] = {}

        def place(root: Root) -> Path:
            if root.id not in paths:
                if root.parent is None:
                    paths[root.id] = base / self.REMOTE_ROOTS_DIR / root.id
                else:
                    parent = place(self.roots[root.parent.root])
                    paths[root.id] = parent.joinpath(*root.parent.path.parts)
            return paths[root.id]

        return RootTable(
            Root(root.id, root.label, root.parent, place(root))
            for root in self.roots.values()
        )

    def to_json(self) -> list[dict[str, Any]]:
        return [
            {
                "id": root.id,
                "label": root.label,
                "parent": None if root.parent is None else root.parent.to_json(),
            }
            for root in self.roots.values()
        ]

    @classmethod
    def from_json(cls, data: Any) -> RootTable:
        if not isinstance(data, list):
            raise WireError("root table: expected a list")
        roots = []
        for item in data:
            reader = WireObject(item, "root")
            root_id = reader.field("id", str)
            label = reader.field("label", str)
            parent = reader.field("parent", dict, type(None))
            reader.finish()
            roots.append(Root(
                root_id, label,
                None if parent is None else RootedPath.from_json(parent),
                None,
            ))
        return cls(roots)
