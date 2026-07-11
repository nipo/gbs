"""Tests for the shared ECP5 ordering part-number parser."""

import pytest

from gbs.builtin.ecp5_part import Ecp5Part


def test_parse_is_case_insensitive_and_canonicalises():
    p = Ecp5Part.parse("lfe5u-25f-6bg256c")
    assert p.part == "LFE5U-25F-6BG256C"
    assert p.prefix == "LFE5U"
    assert p.device == "LFE5U-25F"
    assert p.size == "25"
    assert p.speed == "6"
    assert p.package_code == "BG256"
    assert p.grade == "C"


def test_parse_rejects_non_ecp5():
    assert Ecp5Part.parse("xc7a35t-1cpg236") is None
    assert Ecp5Part.parse("hx1k") is None


def test_family_and_speed_grade_filter_values():
    p = Ecp5Part.parse("LFE5U-45F-7BG381I")
    assert p.family == "ecp5"
    assert p.speed_grade == "-7"


@pytest.mark.parametrize("part, device, package, speed", [
    ("LFE5U-25F-6BG256C", "25k", "CABGA256", "6"),
    ("LFE5U-12F-6BG256C", "12k", "CABGA256", "6"),
    ("LFE5U-85F-8BG756C", "85k", "CABGA756", "8"),
    ("LFE5UM-45F-7MG285C", "um-45k", "CSFBGA285", "7"),
    ("LFE5UM5G-85F-8BG756I", "um5g-85k", "CABGA756", "8"),
    ("LFE5U-25F-6TG144C", "25k", "TQFP144", "6"),
])
def test_nextpnr_translation(part, device, package, speed):
    p = Ecp5Part.parse(part)
    assert p.nextpnr_device == device
    assert p.nextpnr_package == package
    assert p.nextpnr_speed == speed


def test_nextpnr_device_rejects_automotive_series():
    p = Ecp5Part.parse("LAE5U-25F-6BG256C")
    assert p is not None
    with pytest.raises(ValueError):
        p.nextpnr_device


def test_nextpnr_package_rejects_unknown_code():
    p = Ecp5Part.parse("LFE5U-25F-6XX256C")
    assert p is not None
    with pytest.raises(ValueError):
        p.nextpnr_package
