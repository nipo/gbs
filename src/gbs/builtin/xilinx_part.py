"""Xilinx part-number parsing shared by vivado and openxc7 backends.

Vivado writes parts as `name-speedpackage` (e.g. `xc7a35t-1cpg236`).
Both the vivado and openxc7 flows need to split them into
(name, speed, package) for filter variables that repositories
(such as nsl_hwdep) use to enumerate the right sources.

nextpnr-xilinx / openxc7 additionally uses a combined `name+package`
key (without the speed grade) as the chipdb filename.
"""

from __future__ import annotations
import re
from typing import Optional

__all__ = ["parse_part", "family_name", "chipdb_key", "filter_vars"]


_PART_RE = re.compile(
    r"^(?P<name>xc[^-]+?)(?P<speed>-\d)(?P<package>[a-z]+\d+)$",
    re.IGNORECASE,
)


def parse_part(part: str) -> Optional[re.Match]:
    """Match the `xc<name>-<speed><package>` form; None on mismatch."""
    return _PART_RE.match(part)


def family_name(part: str) -> Optional[str]:
    """Return the Xilinx family for a part name (e.g. 'artix7')."""
    p = part.lower()
    if p.startswith("xc7a"):
        return "artix7"
    if p.startswith("xc7k"):
        return "kintex7"
    if p.startswith("xc7v"):
        return "virtex7"
    if p.startswith("xc7z"):
        return "zynq7"
    if p.startswith("xc7s"):
        return "spartan7"
    return None


def chipdb_key(part: str) -> Optional[str]:
    """Return the `<name><package>` chipdb key used by openxc7.

    Speed grade is baked into part.json, not the chipdb, so it is
    stripped. Returns None when the part cannot be parsed.
    """
    m = parse_part(part)
    if not m:
        return None
    return m.group("name") + m.group("package")


def filter_vars(part: str) -> dict[str, str]:
    """Extract source-enumeration filter variables from a part.

    Same shape as the vivado backend's variables so a project already
    targeting vivado enumerates the same sources when built via
    openxc7.
    """
    result: dict[str, str] = {}
    fam = family_name(part)
    if fam:
        result["target_part_name"] = fam
    m = parse_part(part)
    if m:
        result["target_part"] = m.group("name")
        result["target_speed"] = m.group("speed")
        result["target_package"] = m.group("package")
    else:
        # Fall back to the raw string so unparseable parts still filter
        # deterministically on `target_part`.
        result["target_part"] = part
    return result
