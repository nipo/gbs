"""Xilinx part-number parsing shared by vivado and openxc7 backends.

Vivado accepts both `die-speedpackage` (e.g. `xc7a35t-1cpg236`) and
`diepackage-speed` (e.g. `xc7a35tcsg324-1`); both need to split into
(die, speed, package) for filter variables that repositories use to
enumerate the right sources.

nextpnr-xilinx / openxc7 additionally uses a combined `die+package`
key (without the speed grade) as the chipdb filename.
"""

from __future__ import annotations
import re
from typing import Optional

__all__ = ["parse_part", "family_name", "chipdb_key", "filter_vars"]


# Speed grades: -1, -2, -3, plus optional letter suffix (-1L, -2LI, -1LE).
_SPEED = r"-\d[a-z]{0,3}"

# Known Xilinx package prefixes across 7-series, UltraScale(+) and
# Versal. Restricting the package start to this set disambiguates a
# possible die-suffix letter ("t", "s", "i", "l") from the package's
# first letter (e.g. `xc7s25csga324-1L` splits as die `xc7s25`,
# package `csga324` — not die `xc7s25c`, package `sga324`).
# Sorted longest first so the alternation prefers 4-letter prefixes
# over their 3-letter substrings.
_PACKAGE_PREFIXES = (
    # 5 letters
    "wlcsp",
    "eflga", "eflgb",
    # 4 letters
    "cpga", "csga", "ftga", "ftgb",
    "sbva", "sbvb", "sbvc", "sbvd",
    "ffvb", "ffvc", "ffvd", "ffve", "ffvf", "ffvg",
    "sfva", "sfvb", "sfvc", "sfvd",
    "vsva", "vsvb", "vsvc", "vsvd", "vsve", "vsvh",
    "lfva", "lfvb", "lfvc",
    # 3 letters
    "cpg", "csg", "clg",
    "ftg", "fgg", "fbg", "ffg", "flg", "ffv",
    "sbg", "sbv",
    "tqg",
    "sfv", "vsv", "lfv",
)
_PACKAGE = rf"(?:{'|'.join(_PACKAGE_PREFIXES)})\d+"

# Middle-dash form: die-<speed><package>
_PART_RE_MIDDLE = re.compile(
    rf"^(?P<name>xc[a-z0-9]+)(?P<speed>{_SPEED})(?P<package>{_PACKAGE})$",
    re.IGNORECASE,
)
# Trailing-dash form: die<package>-<speed>. The `[a-z0-9]+` on die is
# greedy so the regex engine backtracks the die/package split from the
# longest die down; that way `xc7a35tcsg324-1` splits at `xc7a35t` +
# `csg324` (not the shorter `xc7a35` + `tcsg324`).
_PART_RE_TRAILING = re.compile(
    rf"^(?P<name>xc[a-z0-9]+)(?P<package>{_PACKAGE})(?P<speed>{_SPEED})$",
    re.IGNORECASE,
)


def parse_part(part: str) -> Optional[re.Match]:
    """Match either Vivado part-number form; None on mismatch."""
    return _PART_RE_MIDDLE.match(part) or _PART_RE_TRAILING.match(part)


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
