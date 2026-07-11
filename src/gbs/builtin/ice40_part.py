"""iCE40 part-number parsing for the nextpnr-ice40 backend.

Lattice iCE40 ordering part numbers combine device and package in a
single string, e.g. ``iCE40UP5K-SG48I`` or ``iCE40HX1K-TQ144``.
nextpnr-ice40 instead takes a device-type flag (``--up5k``) and a
lowercase package name (``--package sg48``). This module parses the
ordering number and exposes both, so a project sets one ``target.part``.
"""

from __future__ import annotations
import re

__all__ = ["Ice40Part"]


class Ice40Part:
    """A parsed iCE40 ordering part number.

    Attributes:
        part: Full part number, canonical uppercase (e.g. "ICE40UP5K-SG48I")
        gen: Generation digits between ICE and the family ("40" or "5")
        fam: Family letters (e.g. "UP", "HX", "LP")
        size: Size token (e.g. "5K", "1K", "384")
        package_code: Package code as embedded in the part (e.g. "SG48")
        suffix: Trailing grade/packing letters, if any (e.g. "I", "ITR")
    """

    _re = re.compile(
        r'^ICE(?P<gen>40|5)(?P<fam>LP|HX|UP|UL)(?P<size>[0-9]+K?)'
        r'-(?P<package>[A-Z]+[0-9]+)(?P<suffix>[A-Z]*)$')

    # nextpnr-ice40 device-type flags known to the shipped chipdb.
    _NEXTPNR_DEVICES = frozenset({
        "lp384", "lp1k", "lp4k", "lp8k",
        "hx1k", "hx4k", "hx8k",
        "up3k", "up5k",
        "u1k", "u2k", "u4k",
    })

    def __init__(self, part: str, match: re.Match):
        self.part = part
        self.gen = match.group("gen")
        self.fam = match.group("fam")
        self.size = match.group("size")
        self.package_code = match.group("package")
        self.suffix = match.group("suffix")

    @classmethod
    def parse(cls, part: str) -> Ice40Part | None:
        """Parse an iCE40 ordering part number, return None if it is not one.

        Matching is case-insensitive; the canonical uppercase form is
        stored on the returned instance.

        Args:
            part: Part number string from target configuration

        Returns:
            Ice40Part instance, or None when the string is not an iCE40
            ordering part number
        """
        canonical = part.upper()
        match = cls._re.match(canonical)
        if not match:
            return None
        return cls(canonical, match)

    @property
    def family(self) -> str:
        """Canonical family filter value for this part."""
        return "ice40"

    @property
    def nextpnr_device(self) -> str:
        """nextpnr-ice40 device-type flag name (without leading dashes).

        The iCE40 Ultra (``iCE5LP``) family maps to the ``u<size>``
        flags; the rest are ``<family><size>`` lowercased.

        Raises:
            ValueError: The part maps to no nextpnr device type (e.g.
                UltraLite parts, or sizes without a chipdb).
        """
        if self.gen == "5" and self.fam == "LP":
            flag = f"u{self.size.lower()}"
        else:
            flag = f"{self.fam.lower()}{self.size.lower()}"
        if flag not in self._NEXTPNR_DEVICES:
            raise ValueError(
                f"iCE40 part {self.part!r} maps to device type {flag!r}, "
                f"which nextpnr-ice40 does not provide."
            )
        return flag

    @property
    def nextpnr_package(self) -> str:
        """nextpnr-ice40 ``--package`` name (lowercase)."""
        return self.package_code.lower()

    def __str__(self) -> str:
        return self.part
