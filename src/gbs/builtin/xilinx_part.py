"""Xilinx part-number parsing shared by vivado and openxc7 backends.

Vivado writes parts as `name-speedpackage` (e.g. `xc7a35t-1cpg236`).
Both the vivado and openxc7 flows need to split them into
(name, speed, package) for filter variables that repositories use to
enumerate the right sources.

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
    """Return the Xilinx marketing family for a part name.

    Values match the vocabulary defined in doc/design/filter_vars.rst.
    """
    p = part.lower()
    if p.startswith("xc6slx"):
        return "spartan6"
    if p.startswith("xc6v"):
        return "virtex6"
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
    if p.startswith("xcau"):
        return "artixusp"
    if p.startswith("xczu"):
        return "zynqusp"
    if p.startswith("xcvm") or p.startswith("xcvp") or p.startswith("xcve"):
        return "versal"
    # UltraScale vs UltraScale+ split: '+' dies end their numeric core
    # with a 'p' suffix (e.g. xcku3p vs xcku115).
    m = re.match(r"^xcku(\d+)(p?)", p)
    if m:
        return "kintexusp" if m.group(2) == "p" else "kintexu"
    m = re.match(r"^xcvu(\d+)(p?)", p)
    if m:
        return "virtexusp" if m.group(2) == "p" else "virtexu"
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
    """Return canonical technology-stack filter variables for a part.

    Emits a subset of the canonical set: ``family``, ``die``,
    ``speed``, ``package``. The caller adds ``vendor`` and the raw
    ``part`` field.
    """
    result: dict[str, str] = {}
    fam = family_name(part)
    if fam:
        result["family"] = fam
    m = parse_part(part)
    if m:
        result["die"] = m.group("name")
        result["speed"] = m.group("speed")
        result["package"] = m.group("package")
    else:
        # Unparseable part: still expose it as die so filters that
        # only need a die-level prefix keep working.
        result["die"] = part
    return result
