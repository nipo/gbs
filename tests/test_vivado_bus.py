"""Tests for the Vivado IP-XACT bus definition generator."""

import xml.etree.ElementTree as ET

import pytest

from gbs.builtin.vivado_bus import generator as G


SPIRIT = "{http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009}"
XILINX = "{http://www.xilinx.com}"


def bus(spec):
    return G.Bus(spec, "test")


def minimal(**overrides):
    spec = {
        "library": "interface",
        "name": "tick",
        "ports": {
            "tick": {"role": "m2s", "width": 1, "qualifier": "data",
                     "description": "Tick"},
        },
    }
    spec.update(overrides)
    return spec


def abstraction_root(spec):
    return ET.fromstring(bus(spec).abstraction_definition())


def port_by_name(root, name):
    for port in root.iter(f"{SPIRIT}port"):
        if port.find(f"{SPIRIT}logicalName").text == name:
            return port
    raise AssertionError(f"port {name} not found")


# --- Golden output -----------------------------------------------------------

def test_bus_definition_golden():
    assert bus(minimal()).bus_definition() == """\
<?xml version="1.0" encoding="UTF-8"?>
<spirit:busDefinition xmlns:xilinx="http://www.xilinx.com" xmlns:spirit="http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <spirit:vendor>nsl</spirit:vendor>
  <spirit:library>interface</spirit:library>
  <spirit:name>tick</spirit:name>
  <spirit:version>1.0</spirit:version>
  <spirit:directConnection>false</spirit:directConnection>
  <spirit:isAddressable>false</spirit:isAddressable>
  <spirit:maxMasters>1</spirit:maxMasters>
  <spirit:maxSlaves>1</spirit:maxSlaves>
</spirit:busDefinition>
"""


def test_abstraction_definition_golden():
    assert bus(minimal()).abstraction_definition() == """\
<?xml version="1.0" encoding="UTF-8"?>
<spirit:abstractionDefinition xmlns:xilinx="http://www.xilinx.com" xmlns:spirit="http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <spirit:vendor>nsl</spirit:vendor>
  <spirit:library>interface</spirit:library>
  <spirit:name>tick_rtl</spirit:name>
  <spirit:version>1.0</spirit:version>
  <spirit:busType spirit:vendor="nsl" spirit:library="interface" spirit:name="tick" spirit:version="1.0"/>
  <spirit:ports>
    <spirit:port>
      <spirit:logicalName>tick</spirit:logicalName>
      <spirit:description>Tick</spirit:description>
      <spirit:wire>
        <spirit:qualifier>
          <spirit:isData>true</spirit:isData>
        </spirit:qualifier>
        <spirit:onMaster>
          <spirit:presence>required</spirit:presence>
          <spirit:width>1</spirit:width>
          <spirit:direction>out</spirit:direction>
        </spirit:onMaster>
        <spirit:onSlave>
          <spirit:presence>required</spirit:presence>
          <spirit:width>1</spirit:width>
          <spirit:direction>in</spirit:direction>
        </spirit:onSlave>
      </spirit:wire>
    </spirit:port>
  </spirit:ports>
</spirit:abstractionDefinition>
"""


def test_outputs_file_names():
    files = bus(minimal()).outputs()
    assert set(files) == {"tick.xml", "tick_rtl.xml"}


# --- Roles -------------------------------------------------------------------

def test_s2m_mirrors_m2s():
    spec = minimal(ports={"ready": {"role": "s2m"}})
    port = port_by_name(abstraction_root(spec), "ready")
    master = port.find(f"{SPIRIT}wire/{SPIRIT}onMaster")
    slave = port.find(f"{SPIRIT}wire/{SPIRIT}onSlave")
    assert master.find(f"{SPIRIT}direction").text == "in"
    assert slave.find(f"{SPIRIT}direction").text == "out"


def test_role_base_override():
    spec = minimal(
        roles={"broadcast": {"base": "m2s",
                             "slave": {"presence": "optional"}}},
        ports={"second": {"role": "broadcast", "width": 32}},
    )
    port = port_by_name(abstraction_root(spec), "second")
    master = port.find(f"{SPIRIT}wire/{SPIRIT}onMaster")
    slave = port.find(f"{SPIRIT}wire/{SPIRIT}onSlave")
    assert master.find(f"{SPIRIT}presence").text == "required"
    assert slave.find(f"{SPIRIT}presence").text == "optional"
    # Direction inherited from the base role
    assert slave.find(f"{SPIRIT}direction").text == "in"


def test_custom_role_without_direction():
    spec = minimal(
        roles={"pad": {"master": {"presence": "required"},
                       "slave": {"presence": "required"}}},
        ports={"dio_t": {"role": "pad"}},
    )
    port = port_by_name(abstraction_root(spec), "dio_t")
    for side in ("onMaster", "onSlave"):
        assert port.find(f"{SPIRIT}wire/{SPIRIT}{side}/{SPIRIT}direction") is None


def test_role_single_side():
    spec = minimal(
        roles={"master_only": {"master": {"presence": "optional"}}},
        ports={"probe": {"role": "master_only"}},
    )
    port = port_by_name(abstraction_root(spec), "probe")
    assert port.find(f"{SPIRIT}wire/{SPIRIT}onMaster") is not None
    assert port.find(f"{SPIRIT}wire/{SPIRIT}onSlave") is None


# --- Port attributes ---------------------------------------------------------

def test_default_value():
    spec = minimal(ports={"oe": {"role": "m2s", "default": 0}})
    port = port_by_name(abstraction_root(spec), "oe")
    assert port.find(f"{SPIRIT}wire/{SPIRIT}defaultValue").text == "0"


def test_tristate_extension():
    spec = minimal(ports={
        "dio_o": {"role": "m2s",
                  "tristate": {"role": "out", "group": "dio"}},
    })
    port = port_by_name(abstraction_root(spec), "dio_o")
    info = port.find(f"{SPIRIT}vendorExtensions/"
                     f"{XILINX}abstractionDefinitionPortInfo")
    assert info.find(f"{XILINX}tristate_role").text == "out"
    assert info.find(f"{XILINX}group").text == "dio"


def test_description_escaped():
    spec = minimal(ports={"d": {"role": "m2s", "description": "a < b & c"}})
    text = bus(spec).abstraction_definition()
    assert "a &lt; b &amp; c" in text


# --- Errors ------------------------------------------------------------------

def test_unknown_top_level_key():
    with pytest.raises(G.BusDefError, match="unknown keys"):
        bus(minimal(bogus=1))


def test_unknown_role():
    with pytest.raises(G.BusDefError, match="unknown role"):
        bus(minimal(ports={"a": {"role": "nope"}}))


def test_builtin_role_redefinition():
    with pytest.raises(G.BusDefError, match="redefines"):
        bus(minimal(roles={"m2s": {"master": {}}}))


def test_bad_qualifier():
    with pytest.raises(G.BusDefError, match="qualifier"):
        bus(minimal(ports={"a": {"role": "m2s", "qualifier": "banana"}}))


def test_bad_width():
    with pytest.raises(G.BusDefError, match="width"):
        bus(minimal(ports={"a": {"role": "m2s", "width": 0}}))


def test_missing_role():
    with pytest.raises(G.BusDefError, match="missing keys"):
        bus(minimal(ports={"a": {"description": "no role"}}))


def test_role_without_sides():
    with pytest.raises(G.BusDefError, match="defines no side"):
        bus(minimal(roles={"empty": {}},
                    ports={"a": {"role": "empty"}}))


def test_duplicate_yaml_key(tmp_path):
    path = tmp_path / "dup.yaml"
    path.write_text(
        "library: x\nname: dup\nports:\n"
        "  a: {role: m2s}\n  a: {role: s2m}\n"
    )
    with pytest.raises(G.BusDefError, match="duplicate key"):
        G.Bus.load(path)


def test_load_round_trip(tmp_path):
    path = tmp_path / "tick.yaml"
    path.write_text(
        "library: interface\nname: tick\nports:\n"
        "  tick:\n    role: m2s\n    width: 1\n"
    )
    loaded = G.Bus.load(path)
    assert loaded.outputs() == bus({
        "library": "interface",
        "name": "tick",
        "ports": {"tick": {"role": "m2s", "width": 1}},
    }).outputs()
