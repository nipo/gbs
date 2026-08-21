"""Partition validation report and analyzer diagnostics sidecar.

Validation is analysis only: sources are handed to an analyzer (GHDL
today) and whatever it says about them is collected into a YAML report
together with the dependency tree, the compile order and the files no
validator in the plan is able to read.

Analyzers cache their work per content signature, so a second run
normally skips the analysis and its messages with it. To keep the
report identical on a warm cache, every analysis run writes its
diagnostics to a JSON sidecar next to the cached artifact; the report
reads the sidecars rather than the messages of the current run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# File type of the JSON sidecar an analysis task writes beside its
# cached output, and of the report itself. Both are backend neutral:
# any analyzer able to produce diagnostics participates in the same
# aggregation.
DIAGNOSTICS_FILE_TYPE = "validation-diagnostics"
VALIDATION_REPORT_FILE_TYPE = "validation-report"

SKIPPED_FILE_REASON = "no validator for this file type"

ERROR_SEVERITIES = ("error", "fatal")


@dataclass
class Diagnostic:
    """One analyzer message about one source location."""

    severity: str
    message: str
    file: str | None = None
    line: int | None = None
    column: int | None = None
    extended_message: str | None = None

    @classmethod
    def from_tool_message(cls, msg) -> "Diagnostic":
        return cls(
            severity=str(msg.severity),
            message=msg.message,
            file=str(msg.file_path) if msg.file_path is not None else None,
            line=msg.line,
            column=msg.column,
            extended_message=msg.extended_message,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Diagnostic":
        return cls(
            severity=data["severity"],
            message=data["message"],
            file=data.get("file"),
            line=data.get("line"),
            column=data.get("column"),
            extended_message=data.get("extended_message"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "extended_message": self.extended_message,
        }

    @property
    def is_error(self) -> bool:
        return self.severity in ERROR_SEVERITIES


class DiagnosticsSidecar:
    """JSON file holding the diagnostics of one analysis run.

    An empty list is meaningful content: it says the library was
    analyzed and the analyzer had nothing to report, as opposed to a
    missing file, which says the library was never analyzed.
    """

    @staticmethod
    def keep(msg) -> bool:
        """Whether a tool message is a diagnostic worth persisting.

        Tool chatter that names no source location and stays below
        warning level is progress output, not a finding.
        """
        from .ui.messages import MessageSeverity

        return msg.file_path is not None or msg.severity >= MessageSeverity.WARNING

    @classmethod
    def write(cls, path: Path, messages: list) -> None:
        """Write the diagnostics of `messages` (ToolMessage) to `path`."""
        records = [
            Diagnostic.from_tool_message(m).to_dict()
            for m in messages
            if cls.keep(m)
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records, indent=2) + "\n")

    @staticmethod
    def read(path: Path) -> list[Diagnostic]:
        """Read a sidecar. A missing sidecar yields no diagnostics."""
        try:
            raw = path.read_text()
        except FileNotFoundError:
            return []
        return [Diagnostic.from_dict(d) for d in json.loads(raw)]


class ValidationReport:
    """Assembles the YAML report of one partition validation run.

    Everything but the diagnostics is known before any tool runs, so a
    report can also be produced for a run whose analysis failed.
    """

    def __init__(self, context, error: str | None = None):
        """Describe the run `context` drove.

        The plan and the resolved source file set are attached to the
        context by the realization driving it, and the diagnostics
        sidecars are the pending resources the analysis tasks declared —
        present whether those tasks ran or found their cache warm.

        Args:
            context: The BuildContext of the validation run.
            error: Why the run failed, when it did. A failed run still
                has a dependency tree and a compile order to report.
        """
        plan = context.plan
        if plan is None:
            raise ValueError("Build context carries no plan; cannot build a report")

        self.root_partition = plan.output_group.partition
        self.filter_vars = plan.filter_vars
        self.backends = sorted({pm.backend_name for pm in plan.passes})
        self.passes = sorted({pm.name for pm in plan.passes})
        self.source_fileset = context.source_fileset
        self.consumed_types = set()
        for pass_meta in plan.passes:
            self.consumed_types |= set(pass_meta.input_types)
        self.error = error

        self.diagnostics: list[Diagnostic] = []
        for resource in context.filter_pending(file_type=DIAGNOSTICS_FILE_TYPE):
            self.diagnostics.extend(DiagnosticsSidecar.read(resource.path))

    @property
    def status(self) -> str:
        if self.error is not None:
            return "error"
        if any(d.is_error for d in self.diagnostics):
            return "error"
        return "ok"

    def skipped_files(self) -> list[dict[str, Any]]:
        """Resolved sources whose type no pass in the plan consumes."""
        skipped = []
        for partition in self.source_fileset.partitions:
            for source in self.source_fileset.sources.get(partition, []):
                if source.file_type in self.consumed_types:
                    continue
                skipped.append({
                    "path": str(source.path),
                    "type": source.file_type,
                    "partition": partition,
                    "reason": SKIPPED_FILE_REASON,
                })
        return skipped

    def dependency_tree(self) -> list[dict[str, Any]]:
        tree = []
        for partition in self.source_fileset.partitions:
            deps = self.source_fileset.partition_deps.get(partition, set())
            tree.append({
                "partition": partition,
                "deps": sorted(deps),
                "sources": [
                    {"path": str(s.path), "type": s.file_type}
                    for s in self.source_fileset.sources.get(partition, [])
                ],
            })
        return tree

    def diagnostics_by_file(self) -> list[dict[str, Any]]:
        by_file: dict[str, list[Diagnostic]] = {}
        for record in self.diagnostics:
            by_file.setdefault(record.file or "", []).append(record)

        result = []
        for file_name in sorted(by_file):
            messages = sorted(
                by_file[file_name],
                key=lambda d: (d.line or 0, d.column or 0, d.message),
            )
            result.append({
                "file": file_name or None,
                "messages": [
                    {
                        key: value
                        for key, value in d.to_dict().items()
                        if key != "file" and value is not None
                    }
                    for d in messages
                ],
            })
        return result

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "partition": self.root_partition,
            "status": self.status,
            "filter_vars": dict(sorted(self.filter_vars.items())),
            "backends": self.backends,
            "passes": self.passes,
            "compile_order": list(self.source_fileset.partitions),
            "dependency_tree": self.dependency_tree(),
            "skipped_files": self.skipped_files(),
            "diagnostics": self.diagnostics_by_file(),
            "summary": {
                "partitions": len(self.source_fileset.partitions),
                "errors": sum(1 for d in self.diagnostics if d.is_error),
                "warnings": sum(1 for d in self.diagnostics if d.severity == "warning"),
            },
        }
        if self.error is not None:
            data["error"] = self.error
        return data

    def render(self) -> str:
        import yaml

        return yaml.safe_dump(self.to_dict(), sort_keys=False, default_flow_style=False)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render())


__all__ = [
    "DIAGNOSTICS_FILE_TYPE",
    "VALIDATION_REPORT_FILE_TYPE",
    "SKIPPED_FILE_REASON",
    "Diagnostic",
    "DiagnosticsSidecar",
    "ValidationReport",
]
