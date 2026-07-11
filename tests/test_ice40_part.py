"""Tests for the iCE40 ordering part-number parser."""

import pytest

from gbs.builtin.ice40_part import Ice40Part


def test_parse_is_case_insensitive_and_canonicalises():
    p = Ice40Part.parse("ice40up5k-sg48i")
    assert p.part == "ICE40UP5K-SG48I"
    assert p.fam == "UP"
    assert p.size == "5K"
    assert p.package_code == "SG48"
    assert p.suffix == "I"


def test_parse_rejects_non_ice40():
    assert Ice40Part.parse("LFE5U-25F-6BG256C") is None
    assert Ice40Part.parse("up5k") is None


@pytest.mark.parametrize("part, device, package", [
    ("iCE40UP5K-SG48I", "up5k", "sg48"),
    ("iCE40HX1K-TQ144", "hx1k", "tq144"),
    ("iCE40HX8K-CT256", "hx8k", "ct256"),
    ("iCE40LP1K-QN84", "lp1k", "qn84"),
    ("iCE40LP384-CM36", "lp384", "cm36"),
    ("iCE40UP3K-UWG30ITR", "up3k", "uwg30"),
    ("iCE5LP4K-SG48", "u4k", "sg48"),
    ("iCE5LP1K-SWG16TR", "u1k", "swg16"),
    ("iCE40HX4K-TQ144", "hx4k", "tq144"),
])
def test_nextpnr_translation(part, device, package):
    p = Ice40Part.parse(part)
    assert p.nextpnr_device == device
    assert p.nextpnr_package == package


def test_nextpnr_device_rejects_unmapped_family():
    # UltraLite has no nextpnr chipdb.
    p = Ice40Part.parse("iCE40UL1K-SWG16")
    assert p is not None
    with pytest.raises(ValueError):
        p.nextpnr_device
