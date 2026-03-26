"""Utility functions for GBS"""

import os
import sys
import shutil
from pathlib import Path


def expand_path(path_str: str) -> Path:
    """Expand ~ and environment variables in a path string

    Performs the following expansions on path strings from configuration:
    1. Tilde expansion (~) to user home directory
    2. Environment variable expansion ($VAR or ${VAR})

    Args:
        path_str: Path string that may contain ~ and/or environment variables

    Returns:
        Expanded Path object

    Examples:
        >>> expand_path("~/projects/mylib")
        Path('/home/user/projects/mylib')

        >>> expand_path("$HOME/projects/mylib")
        Path('/home/user/projects/mylib')

        >>> expand_path("${FPGA_LIBS}/common")
        Path('/opt/fpga/libs/common')

        >>> expand_path("~/tools/$TOOL_VERSION/bin")
        Path('/home/user/tools/1.2.3/bin')
    """
    # First expand environment variables
    expanded = os.path.expandvars(path_str)

    # Then expand tilde
    path = Path(expanded).expanduser()

    return path


def clean_paths(paths: set[Path], dry_run: bool = False, echo_func=None) -> set[Path]:
    """Clean a set of paths (files or directories)

    Args:
        paths: Set of paths to clean
        dry_run: If True, show what would be deleted without actually deleting
        echo_func: Optional function to call for output (e.g., click.echo)
                  If None, no output is produced

    Returns:
        Set of paths that were actually cleaned (existed and were removed)
    """
    cleaned = set()

    cwd = Path(".").resolve()

    for path in sorted(paths):
        if not path.exists():
            continue

        if dry_run:
            echo_func(f"Would remove: {path}")
            continue

        assert path.resolve().is_relative_to(cwd)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        cleaned.add(path)

        parent = path.parent
        while not any(parent.iterdir()) and parent.resolve().is_relative_to(cwd):
            parent.rmdir()
            cleaned.add(parent)
            parent = parent.parent

    return cleaned

def resolve_tool_exe(path: Path) -> Path:
    """Resolve a tool executable path for the current platform.

    On Windows, tries .exe/.bat/.cmd extensions first since the
    extensionless file (if it exists) is typically a Unix shell script.
    On Unix, returns the path as-is.

    Args:
        path: Path to the tool executable (without extension)

    Returns:
        Path to the resolved executable

    Raises:
        FileNotFoundError: If no matching executable is found
    """
    if sys.platform == "win32":
        for ext in ('.exe', '.bat', '.cmd'):
            candidate = path.with_suffix(ext)
            if candidate.exists():
                return candidate

    if path.exists():
        return path

    raise FileNotFoundError(f"Tool executable not found: {path}")


__all__ = ['expand_path', 'clean_paths', 'resolve_tool_exe']
