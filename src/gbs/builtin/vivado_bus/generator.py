"""IP-XACT 1685-2009 bus definition generator.

Turns a YAML bus description into the two files Vivado consumes for a
custom bus interface: a busDefinition (``<name>.xml``) and an
abstractionDefinition (``<name>_rtl.xml``).  Only the subset of IP-XACT
that Vivado's IP integrator understands is emitted.

Each port references a role.  A role gives, for the master and slave
sides, the port presence and direction.  Roles ``m2s`` and ``s2m``
(driven by master resp. slave, required on both sides) are built in;
others can be declared in the YAML file, optionally derived from
another role.

YAML layout::

    library: interface           # required, IP-XACT library
    name: framed                 # required, IP-XACT name
    vendor: nsl                  # default "nsl"
    version: "1.0"               # default "1.0"
    description: NSL Framed bus  # optional
    display_name: Framed bus     # optional, Xilinx vendor extension
    direct_connection: false     # defaults shown
    addressable: false
    max_masters: 1
    max_slaves: 1

    roles:                       # optional
      broadcast:
        base: m2s
        slave:
          presence: optional

    ports:                       # required, order preserved
      req_valid:
        role: m2s                # required
        description: Request path valid
        width: 8                 # optional
        qualifier: data          # optional: clock, data, reset, address
        default: 0               # optional default value
        tristate:                # optional Xilinx tristate mapping
          role: out              # tristate, in or out
          group: dio
"""

from __future__ import annotations

from xml.sax.saxutils import escape

import yaml


class BusDefError(Exception):
    pass


class StrictLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys."""

    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in mapping:
                raise BusDefError(f"duplicate key {key!r}{key_node.start_mark}")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


class Spec:
    """Validation helpers for YAML mapping nodes."""

    @staticmethod
    def check_keys(spec, allowed, required, context):
        if not isinstance(spec, dict):
            raise BusDefError(f"{context}: expected a mapping")
        unknown = set(spec) - set(allowed)
        if unknown:
            raise BusDefError(f"{context}: unknown keys {sorted(unknown)}")
        missing = set(required) - set(spec)
        if missing:
            raise BusDefError(f"{context}: missing keys {sorted(missing)}")

    @staticmethod
    def enum(spec, key, values, context, default=None):
        value = spec.get(key, default)
        if value is not None and value not in values:
            raise BusDefError(
                f"{context}: {key} must be one of {values}, got {value!r}")
        return value


class Xml:
    """Line-oriented XML emitter matching Vivado bus editor output style."""

    def __init__(self):
        self.lines = ['<?xml version="1.0" encoding="UTF-8"?>']
        self.stack = []

    @staticmethod
    def attrs_text(attrs):
        return "".join(
            f' {name}="{escape(str(value), {chr(34): "&quot;"})}"'
            for name, value in attrs)

    def indent(self):
        return "  " * len(self.stack)

    def open(self, tag, attrs=()):
        self.lines.append(f"{self.indent()}<{tag}{self.attrs_text(attrs)}>")
        self.stack.append(tag)

    def close(self):
        tag = self.stack.pop()
        self.lines.append(f"{self.indent()}</{tag}>")

    def leaf(self, tag, value):
        self.lines.append(
            f"{self.indent()}<{tag}>{escape(str(value))}</{tag}>")

    def empty(self, tag, attrs=()):
        self.lines.append(f"{self.indent()}<{tag}{self.attrs_text(attrs)}/>")

    def text(self):
        if self.stack:
            raise BusDefError(f"unbalanced XML, still open: {self.stack}")
        return "\n".join(self.lines) + "\n"


class Side:
    PRESENCES = ("required", "optional", "illegal")
    DIRECTIONS = ("in", "out")

    def __init__(self, presence, direction):
        self.presence = presence
        self.direction = direction

    @classmethod
    def parse(cls, spec, context):
        Spec.check_keys(spec, ("presence", "direction"), (), context)
        presence = Spec.enum(spec, "presence", cls.PRESENCES, context,
                             default="required")
        direction = Spec.enum(spec, "direction", cls.DIRECTIONS, context)
        return cls(presence, direction)

    def overridden(self, spec, context):
        Spec.check_keys(spec, ("presence", "direction"), (), context)
        presence = Spec.enum(spec, "presence", self.PRESENCES, context,
                             default=self.presence)
        direction = self.direction
        if "direction" in spec:
            direction = Spec.enum(spec, "direction", self.DIRECTIONS, context)
        return Side(presence, direction)


class Role:
    SIDES = ("master", "slave")

    def __init__(self, name, master, slave):
        self.name = name
        self.master = master
        self.slave = slave

    @classmethod
    def builtins(cls):
        return {
            "m2s": cls("m2s", Side("required", "out"), Side("required", "in")),
            "s2m": cls("s2m", Side("required", "in"), Side("required", "out")),
        }

    @classmethod
    def parse(cls, name, spec, roles):
        context = f"role {name}"
        Spec.check_keys(spec, ("base",) + cls.SIDES, (), context)
        base = None
        if "base" in spec:
            if spec["base"] not in roles:
                raise BusDefError(
                    f"{context}: unknown base role {spec['base']!r}")
            base = roles[spec["base"]]
        sides = {}
        for side in cls.SIDES:
            base_side = getattr(base, side) if base else None
            if side in spec:
                side_context = f"{context}, {side} side"
                if base_side:
                    sides[side] = base_side.overridden(spec[side], side_context)
                else:
                    sides[side] = Side.parse(spec[side], side_context)
            else:
                sides[side] = base_side
        if sides["master"] is None and sides["slave"] is None:
            raise BusDefError(f"{context}: defines no side")
        return cls(name, sides["master"], sides["slave"])


class Port:
    QUALIFIERS = {"clock": "isClock", "data": "isData",
                  "reset": "isReset", "address": "isAddress"}
    TRISTATE_ROLES = ("tristate", "in", "out")

    def __init__(self, name, role, description, width, qualifier, default,
                 tristate):
        self.name = name
        self.role = role
        self.description = description
        self.width = width
        self.qualifier = qualifier
        self.default = default
        self.tristate = tristate

    @classmethod
    def parse(cls, name, spec, roles):
        context = f"port {name}"
        Spec.check_keys(spec, ("role", "description", "width", "qualifier",
                               "default", "tristate"), ("role",), context)
        if spec["role"] not in roles:
            raise BusDefError(f"{context}: unknown role {spec['role']!r}")
        role = roles[spec["role"]]
        width = spec.get("width")
        if width is not None and (not isinstance(width, int) or width < 1):
            raise BusDefError(f"{context}: width must be a positive integer")
        qualifier = Spec.enum(spec, "qualifier", tuple(cls.QUALIFIERS),
                              context)
        default = spec.get("default")
        if default is not None and not isinstance(default, int):
            raise BusDefError(f"{context}: default must be an integer")
        tristate = None
        if "tristate" in spec:
            tri_context = f"{context}, tristate"
            Spec.check_keys(spec["tristate"], ("role", "group"),
                            ("role", "group"), tri_context)
            Spec.enum(spec["tristate"], "role", cls.TRISTATE_ROLES,
                      tri_context)
            tristate = spec["tristate"]
        return cls(name, role, spec.get("description"), width, qualifier,
                   default, tristate)

    def emit_wire_side(self, x, tag, side):
        if side is None:
            return
        x.open(tag)
        x.leaf("spirit:presence", side.presence)
        if self.width is not None:
            x.leaf("spirit:width", self.width)
        if side.direction is not None:
            x.leaf("spirit:direction", side.direction)
        x.close()

    def emit(self, x):
        x.open("spirit:port")
        x.leaf("spirit:logicalName", self.name)
        if self.description is not None:
            x.leaf("spirit:description", self.description)
        x.open("spirit:wire")
        if self.qualifier is not None:
            x.open("spirit:qualifier")
            x.leaf(f"spirit:{self.QUALIFIERS[self.qualifier]}", "true")
            x.close()
        self.emit_wire_side(x, "spirit:onMaster", self.role.master)
        self.emit_wire_side(x, "spirit:onSlave", self.role.slave)
        if self.default is not None:
            x.leaf("spirit:defaultValue", self.default)
        x.close()
        if self.tristate is not None:
            x.open("spirit:vendorExtensions")
            x.open("xilinx:abstractionDefinitionPortInfo")
            x.leaf("xilinx:tristate_role", self.tristate["role"])
            x.leaf("xilinx:group", self.tristate["group"])
            x.close()
            x.close()
        x.close()


class Bus:
    ROOT_ATTRS = (
        ("xmlns:xilinx", "http://www.xilinx.com"),
        ("xmlns:spirit",
         "http://www.spiritconsortium.org/XMLSchema/SPIRIT/1685-2009"),
        ("xmlns:xsi", "http://www.w3.org/2001/XMLSchema-instance"),
    )
    KEYS = ("vendor", "library", "name", "version", "description",
            "display_name", "direct_connection", "addressable",
            "max_masters", "max_slaves", "roles", "ports")

    def __init__(self, spec, context):
        Spec.check_keys(spec, self.KEYS, ("library", "name", "ports"),
                        context)
        self.vendor = spec.get("vendor", "nsl")
        self.library = spec["library"]
        self.name = spec["name"]
        self.version = str(spec.get("version", "1.0"))
        self.description = spec.get("description")
        self.display_name = spec.get("display_name")
        self.direct_connection = spec.get("direct_connection", False)
        self.addressable = spec.get("addressable", False)
        self.max_masters = spec.get("max_masters", 1)
        self.max_slaves = spec.get("max_slaves", 1)
        for key in ("direct_connection", "addressable"):
            if not isinstance(getattr(self, key), bool):
                raise BusDefError(f"{context}: {key} must be a boolean")
        for key in ("max_masters", "max_slaves"):
            if not isinstance(getattr(self, key), int):
                raise BusDefError(f"{context}: {key} must be an integer")
        roles = Role.builtins()
        for role_name, role_spec in spec.get("roles", {}).items():
            if role_name in roles:
                raise BusDefError(
                    f"{context}: role {role_name} redefines an existing role")
            roles[role_name] = Role.parse(role_name, role_spec, roles)
        if not isinstance(spec["ports"], dict) or not spec["ports"]:
            raise BusDefError(f"{context}: ports must be a non-empty mapping")
        self.ports = [Port.parse(name, port_spec, roles)
                      for name, port_spec in spec["ports"].items()]

    @classmethod
    def load(cls, path):
        with open(path) as fd:
            spec = yaml.load(fd, StrictLoader)
        return cls(spec, str(path))

    @staticmethod
    def bool_text(value):
        return "true" if value else "false"

    def emit_vlnv(self, x, name):
        x.leaf("spirit:vendor", self.vendor)
        x.leaf("spirit:library", self.library)
        x.leaf("spirit:name", name)
        x.leaf("spirit:version", self.version)

    def emit_display_name(self, x, info_tag):
        if self.display_name is None:
            return
        x.open("spirit:vendorExtensions")
        x.open(f"xilinx:{info_tag}")
        x.leaf("xilinx:displayName", self.display_name)
        x.close()
        x.close()

    def bus_definition(self):
        x = Xml()
        x.open("spirit:busDefinition", self.ROOT_ATTRS)
        self.emit_vlnv(x, self.name)
        x.leaf("spirit:directConnection", self.bool_text(self.direct_connection))
        x.leaf("spirit:isAddressable", self.bool_text(self.addressable))
        x.leaf("spirit:maxMasters", self.max_masters)
        x.leaf("spirit:maxSlaves", self.max_slaves)
        if self.description is not None:
            x.leaf("spirit:description", self.description)
        self.emit_display_name(x, "busDefinitionInfo")
        x.close()
        return x.text()

    def abstraction_definition(self):
        x = Xml()
        x.open("spirit:abstractionDefinition", self.ROOT_ATTRS)
        self.emit_vlnv(x, self.name + "_rtl")
        x.empty("spirit:busType", (
            ("spirit:vendor", self.vendor),
            ("spirit:library", self.library),
            ("spirit:name", self.name),
            ("spirit:version", self.version),
        ))
        x.open("spirit:ports")
        for port in self.ports:
            port.emit(x)
        x.close()
        self.emit_display_name(x, "abstractionDefinitionInfo")
        x.close()
        return x.text()

    def outputs(self):
        return {
            f"{self.name}.xml": self.bus_definition(),
            f"{self.name}_rtl.xml": self.abstraction_definition(),
        }
