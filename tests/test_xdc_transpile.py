"""Tests for the Vivado XDC to nextpnr-xilinx constraint transpiler."""

import shutil
import subprocess
from pathlib import Path

import pytest

from gbs.builtin.xdc_transpile import transpiler as T


# --- Port universe extraction ------------------------------------------------

def _netlist(modules):
    return {"modules": modules}


def test_top_detected_by_attribute():
    data = _netlist({
        "cell_lib": {"attributes": {}, "ports": {"A": {"direction": "input", "bits": [1]}}},
        "top": {
            "attributes": {"top": "00000000000000000000000000000001"},
            "ports": {"led": {"direction": "output", "bits": [2]}},
        },
    })
    ports = T.NetlistPorts.from_json(data)
    assert ports.pins == ["led"]
    assert ports.busbits == {}


def test_bus_expands_to_bits():
    data = _netlist({
        "top": {
            "attributes": {"top": "1"},
            "ports": {
                "clk": {"direction": "input", "bits": [3]},
                "data": {"direction": "output", "bits": [4, 5, 6, 7]},
            },
        },
    })
    ports = T.NetlistPorts.from_json(data)
    assert ports.pins == ["clk", "data[0]", "data[1]", "data[2]", "data[3]"]
    assert ports.busbits == {"data": ["data[0]", "data[1]", "data[2]", "data[3]"]}


def test_top_hint_used_when_no_attribute():
    data = _netlist({
        "other": {"attributes": {}, "ports": {"x": {"direction": "input", "bits": [1]}}},
        "wanted": {"attributes": {}, "ports": {"y": {"direction": "output", "bits": [2]}}},
    })
    ports = T.NetlistPorts.from_json(data, top_hint="wanted")
    assert ports.pins == ["y"]


def test_single_module_fallback():
    data = _netlist({
        "only": {"attributes": {}, "ports": {"z": {"direction": "input", "bits": [1]}}},
    })
    ports = T.NetlistPorts.from_json(data)
    assert ports.pins == ["z"]


def test_ambiguous_top_raises():
    data = _netlist({
        "a": {"attributes": {}, "ports": {}},
        "b": {"attributes": {}, "ports": {}},
    })
    with pytest.raises(ValueError):
        T.NetlistPorts.from_json(data)


def test_zero_attribute_is_not_top():
    data = _netlist({
        "a": {"attributes": {"top": "00000000000000000000000000000000"}, "ports": {}},
        "b": {"attributes": {"top": "1"}, "ports": {"p": {"direction": "input", "bits": [1]}}},
    })
    ports = T.NetlistPorts.from_json(data)
    assert ports.pins == ["p"]


# --- Record parsing ----------------------------------------------------------

def test_parse_records_splits_on_tabs():
    text = "FILE\tled.xdc\nSETPROP\tPACKAGE_PIN\tH5\tled\n\nNOMATCH\tfoo\n"
    assert T.parse_records(text) == [
        ["FILE", "led.xdc"],
        ["SETPROP", "PACKAGE_PIN", "H5", "led"],
        ["NOMATCH", "foo"],
    ]


# --- Emission policy ---------------------------------------------------------

def test_emit_keeps_whitelisted_drops_others():
    records = [
        ["FILE", "led.xdc"],
        ["SETPROP", "IOSTANDARD", "LVCMOS33", "led"],
        ["SETPROP", "PACKAGE_PIN", "H5", "led"],
        ["SETPROP", "MARK_DEBUG", "true", "led"],
    ]
    text, diags = T.emit_nextpnr_xdc(records)
    assert "set_property IOSTANDARD LVCMOS33 [get_ports {led}]" in text
    assert "set_property PACKAGE_PIN H5 [get_ports {led}]" in text
    assert "MARK_DEBUG" not in text
    assert any(d.level == "info" and "MARK_DEBUG" in d.message for d in diags)


def test_emit_keeps_default_io_properties():
    records = [
        ["SETPROP", "SLEW", "FAST", "led"],
        ["SETPROP", "DRIVE", "12", "led"],
        ["SETPROP", "PULLUP", "true", "led"],
        ["SETPROP", "PULLDOWN", "true", "led"],
    ]
    text, diags = T.emit_nextpnr_xdc(records)
    assert "set_property SLEW FAST [get_ports {led}]" in text
    assert "set_property DRIVE 12 [get_ports {led}]" in text
    assert "set_property PULLUP true [get_ports {led}]" in text
    assert "set_property PULLDOWN true [get_ports {led}]" in text
    assert diags == []


def test_emit_property_name_uppercased():
    text, _ = T.emit_nextpnr_xdc([["SETPROP", "package_pin", "H5", "led"]])
    assert "set_property PACKAGE_PIN H5 [get_ports {led}]" in text


def test_extra_property_opt_in():
    records = [["SETPROP", "IN_TERM", "UNTUNED_SPLIT_50", "clk"]]
    text, _ = T.emit_nextpnr_xdc(
        records, port_properties=T.DEFAULT_PORT_PROPERTIES | {"IN_TERM"})
    assert "set_property IN_TERM UNTUNED_SPLIT_50 [get_ports {clk}]" in text


def test_create_clock_passthrough():
    records = [["CLOCK", "20.0", "sysclk", "0 5", "clk"]]
    text, _ = T.emit_nextpnr_xdc(records)
    assert text.strip() == (
        "create_clock -period 20.0 -name sysclk -waveform {0 5} "
        "[get_ports {clk}]"
    )


def test_create_clock_defaults_name_to_port():
    text, _ = T.emit_nextpnr_xdc([["CLOCK", "10", "", "", "clk"]])
    assert "create_clock -period 10 -name clk [get_ports {clk}]" in text


def test_nomatch_warns():
    _, diags = T.emit_nextpnr_xdc([["NOMATCH", "bogus[*]"]])
    assert any(d.level == "warning" and "bogus[*]" in d.message for d in diags)


def test_empty_target_on_whitelisted_property_warns():
    _, diags = T.emit_nextpnr_xdc([["EMPTYTARGET", "PACKAGE_PIN"]])
    assert any(d.level == "warning" for d in diags)


def test_empty_target_on_other_property_is_info():
    _, diags = T.emit_nextpnr_xdc([["EMPTYTARGET", "CONFIG_VOLTAGE"]])
    assert diags and all(d.level == "info" for d in diags)


def test_unknown_command_aggregated():
    records = [["UNKNOWNCMD", "set_units"], ["UNKNOWNCMD", "set_units"]]
    _, diags = T.emit_nextpnr_xdc(records)
    warns = [d for d in diags if d.level == "warning" and "set_units" in d.message]
    assert len(warns) == 1
    assert "2 occurrences" in warns[0].message


def test_dropped_property_count_aggregated():
    records = [["SETPROP", "MARK_DEBUG", "true", f"d[{i}]"] for i in range(4)]
    _, diags = T.emit_nextpnr_xdc(records)
    infos = [d for d in diags if "MARK_DEBUG" in d.message]
    assert len(infos) == 1
    assert "4 times" in infos[0].message


def test_source_error_raises():
    with pytest.raises(T.TranspileError, match="missing close-brace"):
        T.emit_nextpnr_xdc([["SOURCEERROR", "missing close-brace"]])


def test_value_with_space_is_braced():
    text, _ = T.emit_nextpnr_xdc(
        [["SETPROP", "LOC", "A B", "led"]],
        port_properties=frozenset({"LOC"}))
    assert "set_property LOC {A B} [get_ports {led}]" in text


# --- Preamble generation -----------------------------------------------------

def test_render_tcl_data():
    ports = T.NetlistPorts(["led", "data[0]", "data[1]"], {"data": ["data[0]", "data[1]"]})
    tcl = ports.render_tcl_data()
    assert "set gbs_pins [list {led} {data[0]} {data[1]}]" in tcl
    assert "set gbs_busbits(data) [list {data[0]} {data[1]}]" in tcl


# --- Integration: real interpreter ------------------------------------------

def _find_yosys():
    from os import environ
    for candidate in (environ.get("GBS_TEST_YOSYS"), "/opt/oss-cad-suite/bin/yosys"):
        if candidate and Path(candidate).is_file():
            return candidate
    return shutil.which("yosys")


yosys_bin = _find_yosys()
requires_yosys = pytest.mark.skipif(
    yosys_bin is None, reason="yosys with TCL support not available")


@requires_yosys
def test_end_to_end_resolution(tmp_path):
    """Globs, -dict, foreach and unsupported targets through real yosys TCL."""
    ports = T.NetlistPorts(
        ["led", "data[0]", "data[1]", "data[2]", "data[3]", "clk"],
        {"data": ["data[0]", "data[1]", "data[2]", "data[3]"]},
    )
    xdc = tmp_path / "c.xdc"
    xdc.write_text(
        "set_property PACKAGE_PIN A1 [get_ports {data[*]}]\n"
        "set_property IOSTANDARD LVCMOS33 [get_ports {led}]\n"
        "set_property -dict {IOSTANDARD LVCMOS33 PACKAGE_PIN B2} [get_ports clk]\n"
        "foreach p [get_ports {data[*]}] { set_property LOC X $p }\n"
        "set_property CONFIG_VOLTAGE 1.8 [current_design]\n"
        "set_property PACKAGE_PIN Z9 [get_ports {missing[*]}]\n"
        "create_clock -period 10 -name sysclk [get_ports clk]\n"
        "unsupported_cmd foo\n"
    )
    records_path = tmp_path / "rec.txt"
    preamble = tmp_path / "pre.tcl"
    preamble.write_text(T.build_preamble(ports, [xdc], records_path))

    result = subprocess.run(
        [yosys_bin, "-q", "-p", f"tcl {preamble}"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    records = T.parse_records(records_path.read_text())
    text, diags = T.emit_nextpnr_xdc(
        records, port_properties=T.DEFAULT_PORT_PROPERTIES)

    # Bus wildcard expanded to all four bits.
    for i in range(4):
        assert f"set_property PACKAGE_PIN A1 [get_ports {{data[{i}]}}]" in text
    # -dict split into two properties on clk.
    assert "set_property IOSTANDARD LVCMOS33 [get_ports {clk}]" in text
    assert "set_property PACKAGE_PIN B2 [get_ports {clk}]" in text
    # foreach over get_ports resolved to individual bits.
    assert text.count("set_property LOC X") == 4
    # create_clock passed through.
    assert "create_clock -period 10 -name sysclk [get_ports {clk}]" in text
    # current_design property dropped, unsupported command and no-match warned.
    assert "CONFIG_VOLTAGE" not in text
    assert any("missing[*]" in d.message for d in diags)
    assert any("unsupported_cmd" in d.message for d in diags)


@requires_yosys
def test_end_to_end_malformed_raises(tmp_path):
    ports = T.NetlistPorts(["led"], {})
    xdc = tmp_path / "bad.xdc"
    xdc.write_text("set_property FOO bar [get_ports {led]\n")
    records_path = tmp_path / "rec.txt"
    preamble = tmp_path / "pre.tcl"
    preamble.write_text(T.build_preamble(ports, [xdc], records_path))

    result = subprocess.run(
        [yosys_bin, "-q", "-p", f"tcl {preamble}"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    records = T.parse_records(records_path.read_text())
    with pytest.raises(T.TranspileError):
        T.emit_nextpnr_xdc(records)
