"""CLI parsing for -t/--tool, --tool-version and --precedence.

The root ``gbs`` command declares the options once, then stashes the
parsed pairs on the loaded :class:`~gbs.config.model.GBSConfig` object
(as ``tool_overrides``, ``tool_version_overrides`` and, after the
configuration's own entries, ``backend_precedence``). Every command
that reaches the planner picks them up transparently, so there's no
per-subcommand threading.
"""

from __future__ import annotations
from typing import Iterable

import asyncclick as click

TOOL_SYNTAX = "BACKEND=TOOL[:VARIANT][@VERSION]"
TOOL_VERSION_SYNTAX = "BACKEND=VERSION"
PRECEDENCE_SYNTAX = "BACKEND=PRECEDENCE"


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


def parse_backend_precedence(entries: Iterable[str]) -> list[tuple[str, int]]:
    """Split ``BACKEND=PRECEDENCE`` strings, the precedence an integer"""
    parsed = []
    for backend, value in parse_backend_kv(entries, "--precedence"):
        try:
            parsed.append((backend, int(value)))
        except ValueError:
            raise click.ClickException(
                f"Invalid --precedence {backend}={value}: "
                f"expected {PRECEDENCE_SYNTAX}, PRECEDENCE an integer."
            )
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
