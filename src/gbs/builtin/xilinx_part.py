"""Xilinx part-number parsing shared by Xilinx backends."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

__all__ = ["XilinxPart"]


# Speed grades: -1, -2, -3, plus optional letter suffix (-1L, -2LI, -1LE).
_SPEED = r"-\d[a-z]{0,3}"

# Known Xilinx package prefixes across 7-series, UltraScale(+) and
# Versal. Restricting the package start to this set disambiguates a
# possible die-suffix letter ("t", "s", "i", "l") from the package's
# first letter (e.g. `xc7s25csga324-1L` splits as die `xc7s25`,
# package `csga324` -- not die `xc7s25c`, package `sga324`).
# Sorted longest first so the alternation prefers longer prefixes.
_PACKAGE_PREFIXES = (
    "wlcsp",
    "eflga", "eflgb",
    "cpga", "csga", "ftga", "ftgb",
    "sbva", "sbvb", "sbvc", "sbvd",
    "ffvb", "ffvc", "ffvd", "ffve", "ffvf", "ffvg",
    "sfva", "sfvb", "sfvc", "sfvd",
    "vsva", "vsvb", "vsvc", "vsvd", "vsve", "vsvh",
    "lfva", "lfvb", "lfvc",
    "cpg", "csg", "clg",
    "ftg", "fgg", "fbg", "ffg", "flg", "ffv",
    "sbg", "sbv",
    "tqg",
    "sfv", "vsv", "lfv",
)
_PACKAGE = rf"(?:{'|'.join(_PACKAGE_PREFIXES)})\d+"


class XilinxPart:
    """A parsed Xilinx ordering part number."""

    # Middle-dash form: die-<speed><package>
    _re_middle = re.compile(
        rf"^(?P<die>xc[a-z0-9]+)(?P<speed>{_SPEED})(?P<package>{_PACKAGE})$",
        re.IGNORECASE,
    )
    # Trailing-dash form: die<package>-<speed>.
    _re_trailing = re.compile(
        rf"^(?P<die>xc[a-z0-9]+)(?P<package>{_PACKAGE})(?P<speed>{_SPEED})$",
        re.IGNORECASE,
    )
    # Fully dashed form used by UltraScale and later devices. The package is
    # unambiguous here, so it intentionally does not use the prefix whitelist.
    _re_dashed = re.compile(
        r"^(?P<die>xc[a-z0-9]+)-(?P<package>[a-z]+\d+)"
        rf"(?P<speed>{_SPEED})(?:-(?P<temperature>[a-z]))?"
        r"(?P<suffix>(?:-[a-z0-9]+)*)$",
        re.IGNORECASE,
    )

    def __init__(self, part: str, match: re.Match):
        self.part = part
        self.die = match.group("die")
        self.speed = match.group("speed")
        self.package = match.group("package")
        self.temperature = match.groupdict().get("temperature")
        suffix = match.groupdict().get("suffix") or ""
        self.suffix = tuple(suffix.lstrip("-").split("-")) if suffix else ()

    @classmethod
    def parse(cls, part: str) -> XilinxPart | None:
        """Parse any supported Xilinx part-number form."""
        match = (
            cls._re_middle.match(part)
            or cls._re_trailing.match(part)
            or cls._re_dashed.match(part)
        )
        if not match:
            return None
        return cls(part, match)

    @classmethod
    def family_of(cls, part: str) -> str | None:
        """Return the canonical Xilinx family for a part or die name."""
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
        if p.startswith("xczu") or p.startswith("xck26") or p.startswith("xck24"):
            return "zynqusp"
        if p.startswith(("xcvm", "xcvp", "xcve", "xcvc", "xcvh", "xcvr")):
            return "versal"
        # UltraScale+ dies end their numeric core with a "p" suffix.
        match = re.match(r"^xcku(\d+)(p?)", p)
        if match:
            return "kintexusp" if match.group(2) == "p" else "kintexu"
        match = re.match(r"^xcvu(\d+)(p?)", p)
        if match:
            return "virtexusp" if match.group(2) == "p" else "virtexu"
        return None

    @classmethod
    def filter_vars_of(cls, part: str) -> dict[str, str]:
        """Return canonical filter variables, with a fallback on mismatch."""
        parsed = cls.parse(part)
        if parsed:
            return parsed.filter_vars

        logger.warning(
            "Cannot parse device <%s>; expected <die>-<speed><package>, "
            "<die><package>-<speed>, or <die>-<package>-<speed>[-<temperature>]. "
            "Speed, package, and temperature filter variables will be unset.",
            part,
        )
        result = {"part": part, "die": part}
        family = cls.family_of(part)
        if family:
            result["family"] = family
        return result

    @property
    def family(self) -> str | None:
        """Canonical family filter value for this part."""
        return self.family_of(self.die)

    @property
    def chipdb_key(self) -> str:
        """Combined die and package key used by openxc7."""
        return self.die + self.package

    @property
    def filter_vars(self) -> dict[str, str]:
        """Canonical technology-stack filter variables for this part."""
        result = {
            "part": self.part,
            "die": self.die,
            "speed": self.speed,
            "package": self.package,
        }
        if self.family:
            result["family"] = self.family
        if self.temperature:
            result["temperature"] = self.temperature
        return result
