"""Vivado XDC to nextpnr-xilinx constraint transpiler.

nextpnr-xilinx (the openxc7 place-and-route engine) reads a constraint
file with the ``--xdc`` flag, but its embedded reader accepts only a
small subset of Vivado's XDC/TCL and does not expand wildcard port
patterns. This module translates a Vivado XDC into the reduced dialect
nextpnr-xilinx accepts:

- Port queries (``get_ports``) are resolved against the design's real
  top-level ports, expanding bus wildcards like ``led[*]`` to the
  individual bits found in the synthesized netlist.
- Only pin/IO ``set_property`` names on the caller-supplied whitelist
  are emitted; timing exceptions, bitstream settings and constraints on
  nets/cells (which nextpnr cannot honour) are dropped and reported.
- ``create_clock`` is passed through with its resolved port target.

XDC is a TCL program, so it is evaluated by a real TCL interpreter
rather than pattern-matched. The interpreter is hosted by yosys (its
``tcl`` command), which is always available in the yosys -> nextpnr
flow. This module owns the two halves that do not need the interpreter:
building the port universe from the netlist JSON and generating the TCL
preamble (:class:`NetlistPorts`, :func:`build_preamble`), and turning the
interpreter's neutral output records into the final constraint file
(:func:`parse_records`, :func:`emit_nextpnr_xdc`). Running the
interpreter itself is the caller's job.
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from collections import Counter

__all__ = [
    "NetlistPorts",
    "Diagnostic",
    "TranspileError",
    "DEFAULT_PORT_PROPERTIES",
    "build_preamble",
    "parse_records",
    "emit_nextpnr_xdc",
]


# Pin/IO property names emitted verbatim into the nextpnr constraint
# file. Restricted to the port properties nextpnr-xilinx's reader is
# known to accept: PACKAGE_PIN/LOC place the IO, IOSTANDARD selects the
# buffer, and SLEW/DRIVE/PULLUP/PULLDOWN tune it. Names outside this set
# (timing exceptions, bitstream settings, cell/net attributes) are
# dropped rather than risk tripping the reader; a project can override
# the set through the backend's `port_properties` configuration.
# Compared case-insensitively.
DEFAULT_PORT_PROPERTIES = frozenset({
    "PACKAGE_PIN",
    "LOC",
    "IOSTANDARD",
    "SLEW",
    "DRIVE",
    "PULLUP",
    "PULLDOWN",
})


class TranspileError(Exception):
    """The XDC could not be evaluated as TCL.

    Raised when the interpreter reported an error while sourcing the
    constraint file (unbalanced braces, a genuine TCL syntax error, ...).
    """


@dataclass
class Diagnostic:
    """A note about something the transpiler dropped or could not map.

    ``level`` is ``"warning"`` for cases the user probably cares about
    (a pattern that matched nothing, a supported property that landed on
    an object nextpnr cannot see) and ``"info"`` for the expected,
    harmless drops (bitstream settings, timing exceptions).
    """
    level: str
    message: str


class NetlistPorts:
    """Top-level port universe extracted from a yosys JSON netlist.

    ``pins`` is the ordered list of individually-addressable port bits
    as Vivado names them: a scalar port ``led`` contributes ``led``, a
    bus ``data`` of width 4 contributes ``data[0]``..``data[3]``.
    ``busbits`` maps each bus base name to its bit names so a pattern
    written against the base (``get_ports data``) can expand to every
    bit, matching Vivado.
    """

    def __init__(self, pins: list[str], busbits: dict[str, list[str]]):
        self.pins = pins
        self.busbits = busbits

    @classmethod
    def from_json(cls, data: dict, top_hint: str | None = None) -> NetlistPorts:
        """Build the port universe from a parsed yosys JSON netlist.

        The top module is the one carrying yosys' ``top`` attribute; if
        that is absent, ``top_hint`` (the build's top cell name) is used,
        then a sole module. Bus bits are named ``<base>[<i>]`` with ``i``
        running from 0, matching the JSON's LSB-first ``bits`` order and
        the names nextpnr derives from the same netlist.
        """
        modules = data.get("modules") or {}
        top_name, top = cls.__find_top(modules, top_hint)

        pins: list[str] = []
        busbits: dict[str, list[str]] = {}
        for name, port in top.get("ports", {}).items():
            width = len(port.get("bits", []))
            if width <= 1:
                pins.append(name)
            else:
                bits = [f"{name}[{i}]" for i in range(width)]
                pins.extend(bits)
                busbits[name] = bits
        return cls(pins, busbits)

    @classmethod
    def __find_top(cls, modules: dict, top_hint: str | None) -> tuple[str, dict]:
        for name, module in modules.items():
            attr = (module.get("attributes") or {}).get("top")
            if attr is not None and cls.__attr_true(attr):
                return name, module
        if top_hint and top_hint in modules:
            return top_hint, modules[top_hint]
        if len(modules) == 1:
            (name, module), = modules.items()
            return name, module
        raise ValueError(
            "cannot determine the top module of the netlist: no module "
            "carries the 'top' attribute"
            + (f", and hint {top_hint!r} is not present" if top_hint else "")
            + f" (modules: {sorted(modules)})"
        )

    @staticmethod
    def __attr_true(value) -> bool:
        # yosys stores attributes as fixed-width binary strings, e.g.
        # "00000000000000000000000000000001" for a set flag.
        text = str(value)
        if text and set(text) <= set("01"):
            return int(text, 2) != 0
        return bool(value)

    def render_tcl_data(self) -> str:
        """Emit the TCL that seeds the preamble with this port universe."""
        pins = " ".join("{" + p + "}" for p in self.pins)
        lines = [f"set gbs_pins [list {pins}]", "array unset gbs_busbits"]
        for base, bits in self.busbits.items():
            joined = " ".join("{" + b + "}" for b in bits)
            lines.append(f"set gbs_busbits({base}) [list {joined}]")
        return "\n".join(lines)


# TCL evaluated by the interpreter host. It resolves get_ports against
# the injected port universe and prints one tab-separated record per
# constraint call; it encodes no policy about which properties survive.
# ``{data}`` is replaced with NetlistPorts.render_tcl_data(),
# ``{sources}`` with a TCL list of XDC paths, ``{out}`` with the record
# file path.
_PREAMBLE_TEMPLATE = r"""
{data}

set gbs_out [open {{{out}}} w]
proc gbs_rec {{args}} {{ global gbs_out; puts $gbs_out [join $args \t] }}

proc gbs_escape {{pat}} {{
    # Keep * and ? as glob wildcards; make every other glob-special
    # character literal so a bus pattern like led[*] matches led[0]
    # instead of being read as a [*] character class.
    return [string map {{\\ \\\\ \[ \\\[ \] \\\]}} $pat]
}}
proc gbs_resolve {{patterns}} {{
    global gbs_pins gbs_busbits
    set out {{}}
    foreach pat $patterns {{
        set esc [gbs_escape $pat]
        set hit 0
        foreach pin $gbs_pins {{
            if {{[string match $esc $pin]}} {{ lappend out $pin; set hit 1 }}
        }}
        foreach base [array names gbs_busbits] {{
            if {{[string match $esc $base]}} {{
                foreach b $gbs_busbits($base) {{ lappend out $b }}
                set hit 1
            }}
        }}
        if {{!$hit}} {{ gbs_rec NOMATCH $pat }}
    }}
    set seen {{}}; set res {{}}
    foreach o $out {{
        if {{![dict exists $seen $o]}} {{ dict set seen $o 1; lappend res $o }}
    }}
    return $res
}}
proc get_ports {{args}} {{
    set pats {{}}; set skip 0
    foreach a $args {{
        if {{$skip}} {{ set skip 0; continue }}
        switch -glob -- $a {{
            -filter - -of_objects - -match_style {{ set skip 1 }}
            -* {{}}
            default {{ foreach p $a {{ lappend pats $p }} }}
        }}
    }}
    return [gbs_resolve $pats]
}}

# Object queries that do not name top-level ports: return an empty list
# so any constraint on them resolves to no target and is dropped.
proc current_design {{}} {{ return "" }}
proc get_nets {{args}} {{ return "" }}
proc get_cells {{args}} {{ return "" }}
proc get_pins {{args}} {{ return "" }}
proc get_clocks {{args}} {{ return "" }}
proc get_iobanks {{args}} {{ return "" }}

proc set_property {{args}} {{
    set dict_mode 0; set props {{}}; set pos {{}}
    set i 0
    while {{$i < [llength $args]}} {{
        set a [lindex $args $i]
        if {{$a eq "-dict"}} {{
            incr i
            foreach {{k v}} [lindex $args $i] {{ lappend props $k $v }}
            set dict_mode 1
        }} elseif {{[string match "-*" $a]}} {{
        }} else {{
            lappend pos $a
        }}
        incr i
    }}
    if {{!$dict_mode}} {{
        lappend props [lindex $pos 0] [lindex $pos 1]
    }}
    set objs [lindex $pos end]
    foreach {{k v}} $props {{
        if {{[llength $objs] == 0}} {{
            gbs_rec EMPTYTARGET $k
        }} else {{
            foreach o $objs {{ gbs_rec SETPROP $k $v $o }}
        }}
    }}
}}
proc create_clock {{args}} {{
    set period ""; set name ""; set wave ""; set objs {{}}
    set i 0
    while {{$i < [llength $args]}} {{
        set a [lindex $args $i]
        switch -- $a {{
            -period {{ incr i; set period [lindex $args $i] }}
            -name {{ incr i; set name [lindex $args $i] }}
            -waveform {{ incr i; set wave [lindex $args $i] }}
            -add {{}}
            default {{ set objs $a }}
        }}
        incr i
    }}
    if {{[llength $objs] == 0}} {{ gbs_rec CLOCKNOTARGET $name; return }}
    foreach o $objs {{ gbs_rec CLOCK $period $name $wave $o }}
}}
proc unknown {{args}} {{ gbs_rec UNKNOWNCMD [lindex $args 0] }}

foreach gbs_src {sources} {{
    gbs_rec FILE $gbs_src
    if {{[catch {{source $gbs_src}} gbs_err]}} {{ gbs_rec SOURCEERROR $gbs_err }}
}}
close $gbs_out
"""


def build_preamble(
    ports: NetlistPorts,
    xdc_paths: list[Path],
    out_path: Path,
) -> str:
    """Generate the TCL script the interpreter sources.

    The script seeds the port universe, sources each file in
    ``xdc_paths`` in order (sharing interpreter state, as Vivado does
    within one constraint set), and writes its records to ``out_path``.
    """
    sources = "[list " + " ".join("{" + str(p) + "}" for p in xdc_paths) + "]"
    return _PREAMBLE_TEMPLATE.format(
        data=ports.render_tcl_data(),
        sources=sources,
        out=out_path,
    )


def parse_records(text: str) -> list[list[str]]:
    """Split the interpreter's record file into tab-separated fields."""
    records = []
    for line in text.splitlines():
        if not line:
            continue
        records.append(line.split("\t"))
    return records


class _XdcEmitter:
    """Turns interpreter records into the nextpnr constraint file.

    Holds the whitelist and accumulates diagnostics so repeated drops
    (the same unsupported property on 32 bus bits) collapse into one
    reported line.
    """

    def __init__(self, port_properties: frozenset[str]):
        self.properties = frozenset(p.upper() for p in port_properties)
        self.lines: list[str] = []
        self.diagnostics: list[Diagnostic] = []
        self.current_file: str | None = None
        self.dropped_props: Counter[str] = Counter()
        self.unknown_cmds: Counter[str] = Counter()
        self.empty_targets: Counter[str] = Counter()

    @staticmethod
    def __tcl_word(value: str) -> str:
        # Brace values with whitespace so they survive as a single word;
        # IO property values (pin names, IO standards) normally do not
        # need it, and the examples nextpnr ships leave them bare.
        if value == "" or any(c.isspace() for c in value):
            return "{" + value + "}"
        return value

    def feed(self, record: list[str]) -> None:
        kind = record[0]
        handler = getattr(self, f"_on_{kind.lower()}", None)
        if handler is None:
            self.diagnostics.append(Diagnostic(
                "warning", f"internal: unrecognized interpreter record {kind!r}"))
            return
        handler(record[1:])

    def _on_file(self, args: list[str]) -> None:
        # Spill the full path: this marker names the origin of every
        # line that follows it, so an absolute path keeps it navigable
        # once the constraint files are concatenated.
        self.current_file = args[0] if args else None
        self.lines.append(f"# constraints from {self.current_file}"
                          if self.current_file else "#")

    def _on_setprop(self, args: list[str]) -> None:
        prop, value, port = args[0], args[1], args[2]
        if prop.upper() not in self.properties:
            self.dropped_props[prop] += 1
            return
        self.lines.append(
            f"set_property {prop.upper()} {self.__tcl_word(value)} "
            f"[get_ports {{{port}}}]"
        )

    def _on_clock(self, args: list[str]) -> None:
        period, name, wave, port = args[0], args[1], args[2], args[3]
        parts = ["create_clock"]
        if period:
            parts += ["-period", period]
        parts += ["-name", name or port]
        if wave:
            parts += ["-waveform", "{" + wave + "}"]
        parts.append(f"[get_ports {{{port}}}]")
        self.lines.append(" ".join(parts))

    def _on_emptytarget(self, args: list[str]) -> None:
        prop = args[0]
        if prop.upper() in self.properties:
            # A property we would have emitted landed on an object query
            # nextpnr cannot resolve (a net, a cell, current_design).
            self.empty_targets[prop] += 1
        else:
            self.dropped_props[prop] += 1

    def _on_nomatch(self, args: list[str]) -> None:
        self.diagnostics.append(Diagnostic(
            "warning",
            f"get_ports pattern {args[0]!r} matched no top-level port"
            + (f" (in {Path(self.current_file).name})" if self.current_file else "")
        ))

    def _on_clocknotarget(self, args: list[str]) -> None:
        name = args[0] if args else ""
        self.diagnostics.append(Diagnostic(
            "warning",
            f"create_clock {name or '(unnamed)'} has no resolvable port "
            f"target; dropped"
        ))

    def _on_unknowncmd(self, args: list[str]) -> None:
        self.unknown_cmds[args[0]] += 1

    def _on_sourceerror(self, args: list[str]) -> None:
        raise TranspileError(args[0] if args else "unknown TCL error")

    def finish(self) -> tuple[str, list[Diagnostic]]:
        for cmd, count in sorted(self.unknown_cmds.items()):
            self.diagnostics.append(Diagnostic(
                "warning",
                f"unsupported XDC command {cmd!r} skipped"
                + (f" ({count} occurrences)" if count > 1 else "")
            ))
        for prop, count in sorted(self.empty_targets.items()):
            self.diagnostics.append(Diagnostic(
                "warning",
                f"property {prop} targeted an object nextpnr cannot see "
                f"(net/cell/design); dropped"
                + (f" ({count} times)" if count > 1 else "")
            ))
        for prop, count in sorted(self.dropped_props.items()):
            self.diagnostics.append(Diagnostic(
                "info",
                f"property {prop} not in the nextpnr port-property set; dropped"
                + (f" ({count} times)" if count > 1 else "")
            ))
        return "\n".join(self.lines) + "\n", self.diagnostics


def emit_nextpnr_xdc(
    records: list[list[str]],
    port_properties: frozenset[str] = DEFAULT_PORT_PROPERTIES,
) -> tuple[str, list[Diagnostic]]:
    """Render interpreter records into a nextpnr constraint file.

    Returns the constraint file text and the list of diagnostics
    describing everything that was dropped or could not be mapped.

    Raises:
        TranspileError: if the records include a TCL source error.
    """
    emitter = _XdcEmitter(port_properties)
    for record in records:
        emitter.feed(record)
    return emitter.finish()
