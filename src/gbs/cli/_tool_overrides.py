"""CLI parsing for -t/--tool and --tool-version.

The root ``gbs`` command declares both options once, then stashes the
parsed pairs on the loaded :class:`~gbs.config.model.GBSConfig` object
(as ``tool_overrides`` and ``tool_version_overrides``). Every command
that reaches the planner picks them up transparently through
``GBSConfig.apply_backend_overrides``, so there's no per-subcommand
threading.
"""

from __future__ import annotations
from typing import Iterable

import asyncclick as click

TOOL_SYNTAX = "BACKEND=TOOL[:VARIANT][@VERSION]"
TOOL_VERSION_SYNTAX = "BACKEND=VERSION"


def parse_backend_kv(
    overrides: Iterable[str],
    flag_name: str,
) -> list[tuple[str, str]]:
    """Split ``BACKEND=VALUE`` strings into ``(backend, value)`` pairs.

    Raises :class:`click.ClickException` for malformed entries so the
    message reaches the user with a helpful preamble.
    """
    parsed: list[tuple[str, str]] = []
    for entry in overrides:
        if "=" not in entry:
            raise click.ClickException(
                f"Invalid {flag_name} format: {entry!r}. "
                f"Expected 'backend=value'."
            )
        backend, value = entry.split("=", 1)
        parsed.append((backend, value))
    return parsed


def check_backend_kv(
    parsed: Iterable[tuple[str, str]],
    flag_name: str,
    syntax: str,
    backend_names: Iterable[str],
) -> None:
    """Reject pairs whose backend substring matches no backend name.

    ``syntax`` is the flag's expected argument form, restated in the
    error message.
    """
    backend_names = sorted(backend_names)
    for backend, value in parsed:
        if any(backend in name for name in backend_names):
            continue
        raise click.ClickException(
            f"{flag_name} {backend}={value}: {backend!r} matches no backend. "
            f"Expected {syntax}, where BACKEND is a substring of one of: "
            f"{', '.join(backend_names)}."
        )
