"""Apio package-tree toolchain provider."""

from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ...base import BaseToolchainProvider
from ...config.model import ToolConfig
from ...logging import get_logger
from ...utils import expand_path

logger = get_logger(__name__)


DEFAULT_APIO_ROOT = "~/.apio/packages"


@dataclass(frozen=True)
class ApioToolSpec:
    """One tool inside an apio package.

    Attributes:
        name: GBS tool name (matches what backends look up).
        default_variant: Variant to stamp when the toolchain entry
            does not declare one. `None` means "leave unset".
        relative_path: Executable location under the package root.
        extra_paths: Optional mapping of extra config keys to paths
            relative to the package root. Each is resolved to an
            absolute path and added to the tool's config. Used to
            surface data directories (chipdb, prjxray-db) that
            backends need alongside the executable.
    """
    name: str
    default_variant: Optional[str]
    relative_path: str
    extra_paths: Optional[dict[str, str]] = None


# openxc7 places prjxray-db here; used by both fasm2frames and
# xc7frames2bit. Defined once so the spec table stays readable.
_OPENXC7_PRJXRAY_DB = "share/nextpnr/external/prjxray-db"


PACKAGES: dict[str, list[ApioToolSpec]] = {
    "oss-cad-suite": [
        ApioToolSpec("yosys", None, "bin/yosys"),
        ApioToolSpec("nextpnr-ice40", None, "bin/nextpnr-ice40"),
        ApioToolSpec("nextpnr-ecp5", None, "bin/nextpnr-ecp5"),
        ApioToolSpec("nextpnr-machxo2", None, "bin/nextpnr-machxo2"),
        ApioToolSpec("nextpnr-nexus", None, "bin/nextpnr-nexus"),
        ApioToolSpec("nextpnr-himbaechel", None, "bin/nextpnr-himbaechel"),
        ApioToolSpec("nextpnr-generic", None, "bin/nextpnr-generic"),
        ApioToolSpec("icepack", None, "bin/icepack"),
        ApioToolSpec("ecppack", None, "bin/ecppack"),
        # `gowin_pack` is the open bitstream packer (yosys/nextpnr-himbaechel
        # flow), a completely different tool from the vendor's Gowin IDE.
        # The IDE claims the bare `gowin` name (used by the builtin backend
        # via `resolve_tool_identifier("gowin")`).
        ApioToolSpec("gowin_pack", None, "bin/gowin_pack"),
        ApioToolSpec("ghdl", "llvm", "bin/ghdl"),
        ApioToolSpec("nvc", None, "bin/nvc"),
        ApioToolSpec("verilator", None, "bin/verilator"),
    ],
    # openXC7 xilinx flow. Missing binaries are silently skipped so
    # the entry stays valid across releases.
    "openxc7": [
        ApioToolSpec("nextpnr-xilinx", None, "bin/nextpnr-xilinx",
                     extra_paths={"chipdb_root": "chipdb"}),
        ApioToolSpec("xc7frames2bit", None, "bin/xc7frames2bit",
                     extra_paths={"prjxray_db_root": _OPENXC7_PRJXRAY_DB}),
        ApioToolSpec("fasm2frames", None, "bin/fasm2frames",
                     extra_paths={"prjxray_db_root": _OPENXC7_PRJXRAY_DB}),
        ApioToolSpec("fasm", None, "bin/fasm"),
        ApioToolSpec("bit2fasm", None, "bin/bit2fasm"),
        ApioToolSpec("bitread", None, "bin/bitread"),
        ApioToolSpec("xc7patch", None, "bin/xc7patch"),
        ApioToolSpec("bbasm", None, "bin/bbasm"),
    ],
}


class ApioToolchainProvider(BaseToolchainProvider):
    """Scans an apio package tree and yields ToolConfig entries.

    Options:
        root: Path to the apio packages directory. Defaults to
            ~/.apio/packages. Environment variables and `~` expand.
        packages: Optional list restricting which packages to scan.
            When omitted, every known package is considered.
        variant: Optional user-declared variant name stamped on every
            emitted tool. Use this to keep two apio installs
            selectable side-by-side (e.g. one entry with
            variant: apio-2024, another with variant: apio-2025).
            When set, the per-tool default_variant is ignored.
    """

    type = "apio"

    def enumerate_tools(self) -> list[ToolConfig]:
        root_option = self.options.get("root", DEFAULT_APIO_ROOT)
        root = expand_path(str(root_option))
        if not root.is_dir():
            logger.debug(
                f"Apio toolchain root {root} does not exist; provider is a no-op"
            )
            return []

        wanted = self.options.get("packages")
        if wanted is not None and not isinstance(wanted, list):
            logger.warning(
                f"Apio toolchain 'packages' must be a list, ignoring: {wanted!r}"
            )
            wanted = None
        wanted_set = set(wanted) if wanted is not None else None

        declared_variant = self.options.get("variant")
        if declared_variant is not None and not isinstance(declared_variant, str):
            logger.warning(
                f"Apio toolchain 'variant' must be a string, ignoring: {declared_variant!r}"
            )
            declared_variant = None

        manifest = self._load_installed_manifest(root)

        tools: list[ToolConfig] = []
        for package_name, specs in PACKAGES.items():
            if wanted_set is not None and package_name not in wanted_set:
                continue
            package_root = root / package_name
            if not package_root.is_dir():
                logger.debug(f"Apio package {package_name!r} not installed at {package_root}")
                continue

            version = self._detect_package_version(package_root, manifest.get(package_name))

            for spec in specs:
                executable = package_root / spec.relative_path
                if not executable.exists():
                    logger.debug(
                        f"Apio package {package_name!r}: {spec.relative_path} not present, "
                        f"skipping tool {spec.name!r}"
                    )
                    continue
                variant = declared_variant if declared_variant is not None else spec.default_variant
                tool_config: dict[str, str] = {"executable": str(executable)}
                if spec.extra_paths:
                    for key, rel in spec.extra_paths.items():
                        resolved = package_root / rel
                        if not resolved.exists():
                            logger.debug(
                                f"Apio package {package_name!r}: extra path {rel} for "
                                f"{spec.name!r} not present, omitting {key!r}"
                            )
                            continue
                        tool_config[key] = str(resolved)
                tools.append(ToolConfig(
                    name=spec.name,
                    variant=variant,
                    version=version,
                    config=tool_config,
                    origin=self.origin,
                ))

        return tools

    @staticmethod
    def _load_installed_manifest(root: Path) -> dict[str, dict]:
        """Read `<root>/installed_packages.json`.

        Apio writes this file when it installs a package; the top-level
        keys are canonical package names (also directory names) and
        each value has at least a `version` field. Returns an empty
        dict when the file is missing or malformed - callers fall back
        to per-package metadata.
        """
        path = root / "installed_packages.json"
        if not path.is_file():
            return {}
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.debug(f"Failed to parse {path}: {e}")
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, dict)}

    @staticmethod
    def _detect_package_version(package_root: Path, manifest_entry: Optional[dict]) -> Optional[str]:
        """Extract a version tag for one package.

        Preference order:
        1. `version` field in `installed_packages.json` (uniform across
           packages, written by apio itself).
        2. `release-tag` field in `BUILD-INFO.json` (FPGAwars oss-cad-suite
           tarballs).
        3. Plain `VERSION` file (openxc7 layout).

        Returns None when nothing yields a usable string; the provider
        still emits tools untagged in that case.
        """
        if manifest_entry:
            version = manifest_entry.get("version")
            if isinstance(version, str) and version:
                return version

        info_path = package_root / "BUILD-INFO.json"
        if info_path.is_file():
            try:
                with info_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.debug(f"Failed to parse {info_path}: {e}")
            else:
                tag = data.get("release-tag")
                if isinstance(tag, str) and tag:
                    return tag

        version_path = package_root / "VERSION"
        if version_path.is_file():
            try:
                content = version_path.read_text(encoding="utf-8").strip()
            except OSError as e:
                logger.debug(f"Failed to read {version_path}: {e}")
            else:
                if content:
                    return content

        return None
