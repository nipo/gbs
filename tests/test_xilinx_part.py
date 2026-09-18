"""Tests for the shared Xilinx ordering part-number parser."""

import pytest

from gbs.builtin.xilinx_part import XilinxPart


@pytest.mark.parametrize("part, die, speed, package, temperature", [
    ("xc7a35t-1cpg236", "xc7a35t", "-1", "cpg236", None),
    ("xc7a35tcsg324-1", "xc7a35t", "-1", "csg324", None),
    ("xc7a35ticsg324-1L", "xc7a35ti", "-1L", "csg324", None),
    ("xc7z020clg400-1", "xc7z020", "-1", "clg400", None),
    ("xc7s25csga324-1L", "xc7s25", "-1L", "csga324", None),
    ("xczu9eg-ffvb1156-2-e", "xczu9eg", "-2", "ffvb1156", "e"),
    ("xcku040-ffva1156-2-e", "xcku040", "-2", "ffva1156", "e"),
    ("xcvu9p-flga2104-2L-e", "xcvu9p", "-2L", "flga2104", "e"),
    ("xczu3eg-sbva484-1-i", "xczu3eg", "-1", "sbva484", "i"),
    ("xczu7ev-ffvc1156-2-i-es2", "xczu7ev", "-2", "ffvc1156", "i"),
    ("xcvm1802-vsva2197-2MP-e-S", "xcvm1802", "-2MP", "vsva2197", "e"),
    ("xck26-sfvc784-2LV-c", "xck26", "-2LV", "sfvc784", "c"),
])
def test_parse(part, die, speed, package, temperature):
    parsed = XilinxPart.parse(part)

    assert parsed is not None
    assert parsed.part == part
    assert parsed.die == die
    assert parsed.speed == speed
    assert parsed.package == package
    assert parsed.temperature == temperature


def test_parse_preserves_case_and_suffix():
    parsed = XilinxPart.parse("XCVM1802-VSVA2197-2MP-E-S")

    assert parsed is not None
    assert parsed.die == "XCVM1802"
    assert parsed.speed == "-2MP"
    assert parsed.package == "VSVA2197"
    assert parsed.temperature == "E"
    assert parsed.suffix == ("S",)

    engineering_sample = XilinxPart.parse("xczu7ev-ffvc1156-2-i-es2")
    assert engineering_sample is not None
    assert engineering_sample.suffix == ("es2",)


@pytest.mark.parametrize("part, family", [
    ("xc6slx9", "spartan6"),
    ("xc6vlx75t", "virtex6"),
    ("xc7s25", "spartan7"),
    ("xc7a35t", "artix7"),
    ("xc7k325t", "kintex7"),
    ("xc7vx485t", "virtex7"),
    ("xc7z020", "zynq7"),
    ("xcku040", "kintexu"),
    ("xcvu095", "virtexu"),
    ("xcau25p", "artixusp"),
    ("xcku3p", "kintexusp"),
    ("xcvu9p", "virtexusp"),
    ("xczu9eg", "zynqusp"),
    ("xck26", "zynqusp"),
    ("xck24", "zynqusp"),
    ("xcvm1802", "versal"),
    ("xcvp1202", "versal"),
    ("xcve2802", "versal"),
    ("xcvc1902", "versal"),
    ("xcvh1582", "versal"),
    ("xcvr1602", "versal"),
])
def test_family_of(part, family):
    assert XilinxPart.family_of(part) == family


@pytest.mark.parametrize("part", ["lfe5u-25f-6bg256c", "xc7a35t"])
def test_parse_rejects_invalid_part(part):
    assert XilinxPart.parse(part) is None


def test_derived_values():
    parsed = XilinxPart.parse("xczu9eg-ffvb1156-2-e")

    assert parsed is not None
    assert parsed.family == "zynqusp"
    assert parsed.chipdb_key == "xczu9egffvb1156"
    assert parsed.filter_vars == {
        "part": "xczu9eg-ffvb1156-2-e",
        "family": "zynqusp",
        "die": "xczu9eg",
        "speed": "-2",
        "package": "ffvb1156",
        "temperature": "e",
    }


def test_filter_vars_unparseable_fallback(caplog):
    assert XilinxPart.filter_vars_of("xc7a35t") == {
        "part": "xc7a35t",
        "family": "artix7",
        "die": "xc7a35t",
    }
    assert "Cannot parse device <xc7a35t>" in caplog.text
