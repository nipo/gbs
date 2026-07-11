"""ECP5 part-number parsing shared by the Diamond and nextpnr backends.

Lattice tools identify an ECP5 target by its full ordering part number
(the ``device=`` attribute of a Diamond ``.ldf`` project), e.g.
``LFE5U-25F-6BG256C``. Diamond consumes that string verbatim; nextpnr
instead takes a device-type flag (``--25k``), a package name
(``--package CABGA256``) and a speed grade (``--speed 6``).

This module parses the part number once and exposes both views so a
single ``target.part`` in a project file drives either flow.
"""

from __future__ import annotations
import re

__all__ = ["Ecp5Part"]


class Ecp5Part:
    """A parsed ECP5 ordering part number.

    Attributes:
        part: Full part number, canonical uppercase (e.g. "LFE5U-25F-6BG256C")
        prefix: Device series prefix (e.g. "LFE5U")
        device: Device name as shown by prj_dev (e.g. "LFE5U-25F")
        size: LUT-count size digits (e.g. "25")
        speed: Speed grade digit (e.g. "6")
        package_code: Package code as embedded in the part number (e.g. "BG256")
        grade: Operating grade letter ("C" commercial, "I" industrial)
    """

    # ECP5 series prefixes, longest first so the regex alternation
    # matches greedily (LFE5UM5G before LFE5UM before LFE5U)
    PREFIXES = ("LFE5UM5G", "LFE5UM", "LFE5U", "LAE5UM", "LAE5U")

    _re = re.compile(
        r'^(?P<device>(?P<prefix>' + '|'.join(PREFIXES) + r')-(?P<size>[0-9]+)F?)'
        r'-(?P<speed>[0-9])(?P<package>[A-Z]+[0-9]+)(?P<grade>[CI])$')

    # Series prefix -> nextpnr-ecp5 device-flag prefix. The flag is that
    # prefix followed by "<size>k" (e.g. "um-45k"). LAE (automotive)
    # parts have no Trellis/nextpnr device and are absent here.
    _NEXTPNR_FLAG_PREFIX = {
        "LFE5U": "",
        "LFE5UM": "um-",
        "LFE5UM5G": "um5g-",
    }

    # Part-number package code letters -> nextpnr/Trellis package family.
    # The ball/pin count digits carry over unchanged (BG256 -> CABGA256).
    _NEXTPNR_PACKAGE_PREFIX = {
        "BG": "CABGA",
        "MG": "CSFBGA",
        "TG": "TQFP",
    }

    def __init__(self, part: str, match: re.Match):
        self.part = part
        self.prefix = match.group("prefix")
        self.device = match.group("device")
        self.size = match.group("size")
        self.speed = match.group("speed")
        self.package_code = match.group("package")
        self.grade = match.group("grade")

    @classmethod
    def parse(cls, part: str) -> Ecp5Part | None:
        """Parse an ECP5 part number, return None if it is not one.

        Matching is case-insensitive; the canonical uppercase form is
        stored on the returned instance regardless of how the user
        wrote the part in the project file.

        Args:
            part: Part number string from target configuration

        Returns:
            Ecp5Part instance, or None when the string is not an ECP5
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
        return "ecp5"

    @property
    def speed_grade(self) -> str:
        """Canonical speed filter value, dash-prefixed."""
        return f"-{self.speed}"

    @property
    def nextpnr_device(self) -> str:
        """nextpnr-ecp5 device-type flag name (without leading dashes).

        Raises:
            ValueError: The series has no nextpnr/Trellis device
                (e.g. LAE automotive parts).
        """
        flag_prefix = self._NEXTPNR_FLAG_PREFIX.get(self.prefix)
        if flag_prefix is None:
            raise ValueError(
                f"ECP5 series {self.prefix!r} has no nextpnr device type; "
                f"only {'/'.join(self._NEXTPNR_FLAG_PREFIX)} are supported "
                f"by prjtrellis."
            )
        return f"{flag_prefix}{self.size}k"

    @property
    def nextpnr_package(self) -> str:
        """nextpnr-ecp5 ``--package`` name (uppercase Trellis form).

        Raises:
            ValueError: The package code letters are not recognised.
        """
        m = re.match(r'^([A-Z]+)([0-9]+)$', self.package_code)
        letters, digits = m.group(1), m.group(2)
        name_prefix = self._NEXTPNR_PACKAGE_PREFIX.get(letters)
        if name_prefix is None:
            raise ValueError(
                f"ECP5 package code {self.package_code!r} is not recognised; "
                f"known families: {'/'.join(self._NEXTPNR_PACKAGE_PREFIX)}."
            )
        return f"{name_prefix}{digits}"

    @property
    def nextpnr_speed(self) -> str:
        """nextpnr-ecp5 ``--speed`` grade digit."""
        return self.speed

    def __str__(self) -> str:
        return self.part
