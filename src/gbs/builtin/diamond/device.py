"""Lattice Diamond part number handling

Diamond selects its target through a full ordering part number
(the ``device=`` attribute of a ``.ldf`` project file), e.g.
``LFE5U-25F-6BG256C``. This module parses such part numbers to
derive family information used for planning and source filtering.
"""

from __future__ import annotations
import re

__all__ = ["DiamondPart"]


class DiamondPart:
    """A parsed Diamond part number

    Attributes:
        part: Full part number as given (e.g. "LFE5U-25F-6BG256C")
        prefix: Device series prefix (e.g. "LFE5U")
        device: Device name as shown by prj_dev (e.g. "LFE5U-25F")
        speed: Speed grade digit (e.g. "6")
        package_code: Package code embedded in the part number (e.g. "BG256")
        grade: Operating grade letter ("C" commercial, "I" industrial)
    """

    # ECP5 series prefixes, longest first so the regex alternation
    # matches greedily (LFE5UM5G before LFE5UM before LFE5U)
    ECP5_PREFIXES = ("LFE5UM5G", "LFE5UM", "LFE5U", "LAE5UM", "LAE5U")

    _ecp5_re = re.compile(
        r'^(?P<device>(?P<prefix>' + '|'.join(ECP5_PREFIXES) + r')-(?P<size>[0-9]+)F?)'
        r'-(?P<speed>[0-9])(?P<package>[A-Z]+[0-9]+)(?P<grade>[CI])$')

    def __init__(self, part: str, match: re.Match):
        self.part = part
        self.prefix = match.group("prefix")
        self.device = match.group("device")
        self.speed = match.group("speed")
        self.package_code = match.group("package")
        self.grade = match.group("grade")

    @classmethod
    def ecp5_parse(cls, part: str) -> DiamondPart | None:
        """Parse an ECP5 part number, return None if it is not one

        Matching is case-insensitive; the canonical uppercase form is
        stored on the returned instance regardless of how the user
        wrote the part in the project file.

        Args:
            part: Part number string from target configuration

        Returns:
            DiamondPart instance, or None when the string is not an
            ECP5 Diamond part number
        """
        canonical = part.upper()
        match = cls._ecp5_re.match(canonical)
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

    def __str__(self) -> str:
        return self.part
