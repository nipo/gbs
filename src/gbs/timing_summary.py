"""Backend-neutral FPGA timing summary extraction and rendering."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from html import escape
from pathlib import Path
from typing import ClassVar

import yaml

from .build.task import Resource, ResourceTypology, Task


TIMING_SUMMARY_FILE_TYPE = "timing-summary"


@dataclass
class ClockTiming:
    """Timing result for one clock domain."""

    name: str
    status: str
    target_mhz: float | None = None
    fmax_mhz: float | None = None
    fmax_source: str | None = None
    margin_mhz: float | None = None
    margin_percent: float | None = None
    setup_slack_ns: float | None = None
    corner: str | None = None

    def __post_init__(self) -> None:
        if self.target_mhz is None or self.fmax_mhz is None:
            return
        self.margin_mhz = round(self.fmax_mhz - self.target_mhz, 6)
        if self.target_mhz:
            self.margin_percent = round(self.margin_mhz / self.target_mhz * 100, 6)

    def to_dict(self) -> dict:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass
class TimingSummary:
    """Normalized result of one backend timing analysis."""

    backend: str
    status: str
    timing_met: bool | None
    clocks: list[ClockTiming] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    schema_version: int = 1

    def to_dict(self) -> dict:
        data = {
            "schema_version": self.schema_version,
            "backend": self.backend,
            "status": self.status,
            "constraints": {"timing_met": self.timing_met},
            "clocks": [clock.to_dict() for clock in self.clocks],
        }
        if self.notes:
            data["notes"] = self.notes
        return data

    def render(self) -> str:
        return yaml.safe_dump(self.to_dict(), sort_keys=False, default_flow_style=False)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render())


class TimingParser:
    """Base class for parsers of backend-native timing reports."""

    backend: ClassVar[str]

    @classmethod
    def parse_files(cls, paths: list[Path]) -> TimingSummary:
        text = "\n".join(path.read_text(errors="replace") for path in paths)
        return cls.parse(text)

    @classmethod
    def parse(cls, text: str) -> TimingSummary:
        raise NotImplementedError

    @classmethod
    def summary(
        cls,
        clocks: list[ClockTiming],
        timing_met: bool | None,
        *,
        unconstrained: bool = False,
        notes: list[str] | None = None,
    ) -> TimingSummary:
        if unconstrained:
            status = "unconstrained"
            timing_met = None
        elif timing_met is True:
            status = "pass"
        elif timing_met is False:
            status = "fail"
        else:
            status = "unknown"
        return TimingSummary(
            backend=cls.backend,
            status=status,
            timing_met=timing_met,
            clocks=clocks,
            notes=notes or [],
        )

    @staticmethod
    def clock_status(target_mhz: float | None, fmax_mhz: float | None) -> str:
        if target_mhz is None:
            return "unknown"
        if fmax_mhz is None:
            return "unknown"
        return "pass" if fmax_mhz >= target_mhz else "fail"


class NextpnrTimingParser(TimingParser):
    backend = "nextpnr"
    frequency = re.compile(
        r"Max frequency for clock ['\"](?P<name>.+?)['\"]:\s*"
        r"(?P<fmax>[0-9.]+) MHz \((?P<status>PASS|FAIL) at "
        r"(?P<target>[0-9.]+) MHz\)",
        re.IGNORECASE,
    )

    @classmethod
    def parse(cls, text: str) -> TimingSummary:
        by_name: dict[str, ClockTiming] = {}
        for match in cls.frequency.finditer(text):
            target = float(match.group("target"))
            fmax = float(match.group("fmax"))
            by_name[match.group("name")] = ClockTiming(
                name=match.group("name"),
                status=match.group("status").lower(),
                target_mhz=target,
                fmax_mhz=fmax,
                fmax_source="reported",
                setup_slack_ns=round(1000 / target - 1000 / fmax, 6),
            )
        clocks = list(by_name.values())
        timing_met = all(clock.status == "pass" for clock in clocks) if clocks else None
        unconstrained = not clocks and bool(re.search(r"unconstrained", text, re.IGNORECASE))
        return cls.summary(clocks, timing_met, unconstrained=unconstrained)


class VivadoTimingParser(TimingParser):
    backend = "vivado"
    clock_row = re.compile(
        r"^(?P<name>\S+)\s+\{[^}]+\}\s+(?P<period>[0-9.]+)\s+"
        r"(?P<frequency>[0-9.]+)\s*$",
        re.MULTILINE,
    )
    intra_row = re.compile(
        r"^(?P<name>\S+)\s+(?P<wns>-?[0-9.]+)\s+-?[0-9.]+\s+\d+\s+\d+",
        re.MULTILINE,
    )

    @classmethod
    def parse(cls, text: str) -> TimingSummary:
        unconstrained = "There are no user specified timing constraints." in text
        if "All user specified timing constraints are met." in text:
            timing_met = True
        elif re.search(
            r"(?:timing constraints are not met|timing constraints are violated)",
            text,
            re.IGNORECASE,
        ):
            timing_met = False
        else:
            timing_met = None

        targets = {
            match.group("name"): (float(match.group("period")), float(match.group("frequency")))
            for match in cls.clock_row.finditer(text)
        }
        intra_text = text.split("| Intra Clock Table", 1)[-1] if "| Intra Clock Table" in text else ""
        slacks = {
            match.group("name"): float(match.group("wns"))
            for match in cls.intra_row.finditer(intra_text)
        }
        clocks = []
        for name, (period, target) in targets.items():
            slack = slacks.get(name)
            fmax = None
            if slack is not None and period - slack > 0:
                fmax = round(1000 / (period - slack), 6)
            clocks.append(ClockTiming(
                name=name,
                status=(
                    "pass" if slack is not None and slack >= 0 else
                    "fail" if slack is not None else "unknown"
                ),
                target_mhz=target,
                fmax_mhz=fmax,
                fmax_source="computed_from_setup_slack" if fmax is not None else None,
                setup_slack_ns=slack,
            ))
        return cls.summary(clocks, timing_met, unconstrained=unconstrained)


class DiamondTimingParser(TimingParser):
    backend = "diamond"
    frequency_row = re.compile(
        r'^FREQUENCY\s+(?:NET|PORT)\s+"(?P<name>[^"]+)"[^|]*\|\s*'
        r"(?P<target>[0-9.]+)\s*MHz\|\s*(?P<fmax>[0-9.]+)\s*MHz\|",
        re.MULTILINE | re.IGNORECASE,
    )

    @classmethod
    def parse(cls, text: str) -> TimingSummary:
        clocks = []
        for match in cls.frequency_row.finditer(text):
            target = float(match.group("target"))
            fmax = float(match.group("fmax"))
            clocks.append(ClockTiming(
                name=match.group("name"),
                status=cls.clock_status(target, fmax),
                target_mhz=target,
                fmax_mhz=fmax,
                fmax_source="reported",
                setup_slack_ns=round(1000 / target - 1000 / fmax, 6),
            ))
        if "All preferences were met." in text:
            timing_met = True
        else:
            error_match = re.search(r"Timing errors:\s*(\d+)", text)
            timing_met = int(error_match.group(1)) == 0 if error_match else None
        unconstrained = bool(re.search(r"No timing preferences|No paths to report", text, re.IGNORECASE))
        return cls.summary(clocks, timing_met, unconstrained=unconstrained and not clocks)


class QuartusTimingParser(TimingParser):
    backend = "quartus"

    @classmethod
    def parse(cls, text: str) -> TimingSummary:
        unconstrained = bool(re.search(
            r"Timing requirements not specified|Design is not fully constrained|No clocks to report",
            text,
            re.IGNORECASE,
        ))
        fail = bool(re.search(r"Timing requirements not met|Timing Analyzer was unsuccessful", text, re.IGNORECASE))
        pass_ = bool(re.search(r"Timing requirements were met|0 paths? with violations", text, re.IGNORECASE))

        clocks: dict[str, ClockTiming] = {}
        targets: dict[str, float] = {}
        section = ""
        headers: list[str] = []
        for raw_line in text.splitlines():
            fields = [field.strip() for field in raw_line.strip().strip(";").split(";")]
            if len(fields) == 1:
                if "Fmax Summary" in fields[0]:
                    section = fields[0]
                    headers = []
                elif fields[0] == "Clocks":
                    section = "Clocks"
                    headers = []
                elif fields[0] and set(fields[0]) != {"-"}:
                    section = ""
                continue
            if not section:
                continue
            lower = [field.lower() for field in fields]
            if any("clock" in field for field in lower) and (
                any("fmax" in field for field in lower)
                or any("frequency" in field for field in lower)
            ):
                headers = lower
                continue
            if not headers or len(fields) != len(headers):
                continue
            row = dict(zip(headers, fields))
            clock_key = next((key for key in headers if "clock" in key), None)
            if section == "Clocks":
                frequency_key = next((key for key in headers if "frequency" in key), None)
                if clock_key is None or frequency_key is None:
                    continue
                target_match = re.search(r"([0-9.]+)\s*MHz", row[frequency_key], re.IGNORECASE)
                if target_match and row[clock_key]:
                    targets[row[clock_key]] = float(target_match.group(1))
                continue
            fmax_key = next((key for key in headers if "restricted fmax" in key), None)
            fmax_key = fmax_key or next((key for key in headers if "fmax" in key), None)
            if clock_key is None or fmax_key is None:
                continue
            fmax_match = re.search(r"([0-9.]+)\s*MHz", row[fmax_key], re.IGNORECASE)
            if not fmax_match or not row[clock_key]:
                continue
            name = row[clock_key]
            fmax = float(fmax_match.group(1))
            corner = section.split(" Fmax Summary", 1)[0]
            previous = clocks.get(name)
            if previous is None or previous.fmax_mhz is None or fmax < previous.fmax_mhz:
                clocks[name] = ClockTiming(
                    name=name,
                    status="unknown",
                    fmax_mhz=fmax,
                    fmax_source="reported",
                    corner=corner,
                )
        for name, clock in clocks.items():
            target = targets.get(name)
            if target is None:
                continue
            clock.target_mhz = target
            clock.status = cls.clock_status(target, clock.fmax_mhz)
            if clock.fmax_mhz:
                clock.setup_slack_ns = round(1000 / target - 1000 / clock.fmax_mhz, 6)
            clock.__post_init__()
        timing_met = False if fail else True if pass_ else None
        return cls.summary(list(clocks.values()), timing_met, unconstrained=unconstrained)


class IseTimingParser(TimingParser):
    backend = "ise"
    block = re.compile(
        r"Timing constraint:\s*(?P<constraint>.*?)(?=\n\s*Timing constraint:|\Z)",
        re.DOTALL | re.IGNORECASE,
    )

    @classmethod
    def parse(cls, text: str) -> TimingSummary:
        clocks = []
        for match in cls.block.finditer(text):
            block = match.group("constraint")
            name_match = re.match(r"\s*(?P<name>\S+)", block)
            target_match = re.search(r"(?:PERIOD[^=]*=\s*)?([0-9.]+)\s*(ns|MHz)", block, re.IGNORECASE)
            fmax_match = re.search(r"Maximum frequency is\s*([0-9.]+)\s*MHz", block, re.IGNORECASE)
            slack_match = re.search(r"Slack:\s*(-?[0-9.]+)\s*ns", block, re.IGNORECASE)
            if name_match is None or target_match is None:
                continue
            value = float(target_match.group(1))
            target = 1000 / value if target_match.group(2).lower() == "ns" else value
            fmax = float(fmax_match.group(1)) if fmax_match else None
            slack = float(slack_match.group(1)) if slack_match else None
            status = cls.clock_status(target, fmax)
            if status == "unknown" and slack is not None:
                status = "pass" if slack >= 0 else "fail"
            clocks.append(ClockTiming(
                name=name_match.group("name"),
                status=status,
                target_mhz=round(target, 6),
                fmax_mhz=fmax,
                fmax_source="reported" if fmax is not None else None,
                setup_slack_ns=slack,
            ))
        if re.search(r"All constraints were met|0 timing errors", text, re.IGNORECASE):
            timing_met = True
        elif re.search(r"Timing errors:\s*[1-9]|constraints? (?:were )?not met", text, re.IGNORECASE):
            timing_met = False
        else:
            timing_met = None
        unconstrained = bool(re.search(r"No timing constraints|No paths analyzed", text, re.IGNORECASE))
        return cls.summary(clocks, timing_met, unconstrained=unconstrained)


class GowinTimingParser(TimingParser):
    backend = "gowin"
    clock_row = re.compile(
        r"^\s*\d+\s+(?P<name>\S+)\s+(?:Base|Generated)\s+"
        r"(?P<period>[0-9.]+)\s+(?P<target>[0-9.]+)MHz\b",
        re.MULTILINE,
    )
    fmax_row = re.compile(
        r"^\s*\d+\s+(?P<name>\S+)\s+(?P<target>[0-9.]+)\(MHz\)\s+"
        r"(?P<fmax>[0-9.]+)\(MHz\)",
        re.MULTILINE,
    )

    @classmethod
    def parse(cls, text: str) -> TimingSummary:
        clock_section = cls.section(text, "2.2 Clock Summary", "2.3 Max Frequency Summary")
        fmax_section = cls.section(text, "2.3 Max Frequency Summary", "2.4 Total Negative Slack Summary")

        clocks = {
            match.group("name"): ClockTiming(
                name=match.group("name"),
                status="unknown",
                target_mhz=float(match.group("target")),
            )
            for match in cls.clock_row.finditer(clock_section)
        }
        for match in cls.fmax_row.finditer(fmax_section):
            name = match.group("name")
            target = float(match.group("target"))
            fmax = float(match.group("fmax"))
            clock = clocks.setdefault(name, ClockTiming(
                name=name,
                status="unknown",
                target_mhz=target,
            ))
            clock.target_mhz = target
            clock.fmax_mhz = fmax
            clock.fmax_source = "reported"
            clock.status = cls.clock_status(target, fmax)
            clock.setup_slack_ns = round(1000 / target - 1000 / fmax, 6)
            clock.__post_init__()

        violations = [
            int(value)
            for value in re.findall(
                r"<Numbers of \w+ Violated Endpoints>:\s*(\d+)",
                text,
                re.IGNORECASE,
            )
        ]
        timing_met = (not any(violations)) if violations else None
        unconstrained = not clocks and "2.2 Clock Summary" in text
        notes = []
        if not clocks and not unconstrained:
            notes.append("Gowin clock summary was not recognized in the timing report")
        return cls.summary(
            list(clocks.values()),
            timing_met,
            unconstrained=unconstrained,
            notes=notes,
        )

    @staticmethod
    def section(text: str, heading: str, next_heading: str) -> str:
        start = text.rfind(heading)
        if start < 0:
            return ""
        end = text.find(next_heading, start + len(heading))
        return text[start:end if end >= 0 else None]


class TimingSummaryTask(Task):
    """Parse backend reports into one or more identical YAML summaries."""

    def __init__(self, dispatcher, parser: type[TimingParser], inputs, outputs):
        super().__init__(
            dispatcher=dispatcher,
            name=f"{parser.backend}_timing_summary",
            inputs=inputs,
            outputs=outputs,
            description=f"Extract {parser.backend} timing summary",
        )
        self.parser = parser

    async def work(self) -> None:
        paths = [resource.path for resource in self.inputs if isinstance(resource, Resource)]
        summary = self.parser.parse_files(paths)
        for output in self.outputs:
            summary.write(output.path)
        self.info(f"Extracted {self.parser.backend} timing summary")

    @classmethod
    def create(cls, dispatcher, parser, reports, *, needed: bool) -> Resource | None:
        outputs = list(dispatcher.context.filter_pending(file_type=TIMING_SUMMARY_FILE_TYPE))
        if not outputs and not needed:
            return None
        if not outputs:
            outputs = [dispatcher.context.get_resource(
                dispatcher.context.output_path / "timing-summary.yaml",
                file_type=TIMING_SUMMARY_FILE_TYPE,
                typology=ResourceTypology.INTERMEDIATE,
                generated_by=dispatcher.name,
            )]
        cls(dispatcher=dispatcher, parser=parser, inputs=reports, outputs=outputs)
        return outputs[0]


class TimingSummaryHtml:
    """Render a timing-summary YAML file for an aggregate report tab."""

    @staticmethod
    def fragment(path: Path) -> str:
        data = yaml.safe_load(path.read_text())
        status = str(data["status"])
        rows = []
        for clock in data.get("clocks", []):
            def cell(key: str) -> str:
                value = clock.get(key)
                return "" if value is None else escape(str(value))

            rows.append(
                "<tr>"
                f"<td>{cell('name')}</td><td>{cell('status')}</td>"
                f"<td>{cell('target_mhz')}</td><td>{cell('fmax_mhz')}</td>"
                f"<td>{cell('margin_mhz')}</td><td>{cell('margin_percent')}</td>"
                f"<td>{cell('setup_slack_ns')}</td><td>{cell('corner')}</td>"
                "</tr>"
            )
        table = (
            "<table><tr><th>Clock</th><th>Status</th><th>Target (MHz)</th>"
            "<th>Fmax (MHz)</th><th>Margin (MHz)</th><th>Margin (%)</th>"
            "<th>Setup slack (ns)</th><th>Corner</th></tr>"
            + "".join(rows)
            + "</table>"
            if rows else "<p>No constrained clock domains were reported.</p>"
        )
        notes = "".join(f"<li>{escape(str(note))}</li>" for note in data.get("notes", []))
        return (
            f'<section class="timing-summary"><h1>Timing Summary</h1>'
            f'<p><strong>Status:</strong> {escape(status)}</p>{table}'
            f'{"<ul>" + notes + "</ul>" if notes else ""}</section>'
        )

    @classmethod
    def document(cls, path: Path) -> str:
        return (
            "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
            "<title>Timing Summary</title><style>"
            "body{font-family:system-ui,sans-serif;padding:16px}"
            "table{border-collapse:collapse}th,td{border:1px solid #ccc;padding:5px 9px}"
            "th{background:#eee}</style></head><body>"
            f"{cls.fragment(path)}</body></html>"
        )


__all__ = [
    "TIMING_SUMMARY_FILE_TYPE",
    "ClockTiming",
    "TimingSummary",
    "TimingSummaryTask",
    "TimingSummaryHtml",
    "NextpnrTimingParser",
    "VivadoTimingParser",
    "DiamondTimingParser",
    "QuartusTimingParser",
    "IseTimingParser",
    "GowinTimingParser",
]
