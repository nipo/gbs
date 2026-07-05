"""Tests for the toolchain provider mechanism in GBSConfig."""

from __future__ import annotations
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from gbs.base import BasePlugin, BaseToolchainProvider
from gbs.config.model import GBSConfig, ToolConfig, ToolchainSpec


class RecordingProvider(BaseToolchainProvider):
    """Test provider: emits tools based on options for observation."""

    type = "test-fake"
    calls: list[dict] = []

    def enumerate_tools(self):
        RecordingProvider.calls.append(dict(self.options))
        return [
            ToolConfig(name="yosys", variant=None,
                       config={"executable": "/fake/yosys"}),
            ToolConfig(name="nextpnr-ice40", variant=None,
                       config={"executable": "/fake/nextpnr-ice40"}),
        ]


class OverridingProvider(BaseToolchainProvider):
    """Second provider used to check later-wins on (name, variant) collision."""

    type = "test-override"

    def enumerate_tools(self):
        return [
            ToolConfig(name="yosys", variant=None,
                       config={"executable": "/override/yosys"}),
        ]


class FakePlugin(BasePlugin):
    def __init__(self):
        super().__init__(name="gbs.test.fake", description="", version="0.0")

    def enumerate_toolchain_providers(self):
        return {
            "test-fake": RecordingProvider,
            "test-override": OverridingProvider,
        }


@pytest.fixture
def registry_with_fake_plugin():
    """Install a registry that only knows about FakePlugin."""
    from gbs.plugins.loader import PluginRegistry
    reg = PluginRegistry()
    reg._register_plugin(FakePlugin())
    with patch("gbs.plugins.loader.get_plugin_registry", return_value=reg), \
         patch("gbs.config.model.get_plugin_registry", return_value=reg,
               create=True):
        yield reg


@pytest.fixture(autouse=True)
def clear_recorder():
    RecordingProvider.calls.clear()


def _write_config(dir: Path, content: str) -> Path:
    path = dir / ".gbs.yaml"
    path.write_text(textwrap.dedent(content))
    return path


def _load(tmp_path: Path, monkeypatch) -> GBSConfig:
    """Load config with tmp_path as CWD and a HOME that has no user config."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "no-home"))
    return GBSConfig.load()


def test_toolchain_expands_to_tools(tmp_path, monkeypatch, registry_with_fake_plugin):
    _write_config(tmp_path, """\
        toolchains:
          - type: test-fake
            root: /some/where
    """)
    config = _load(tmp_path, monkeypatch)

    assert len(config.toolchains) == 1
    assert config.toolchains[0].type == "test-fake"
    assert config.toolchains[0].options == {"root": "/some/where"}

    names = {t.name for t in config.tools}
    assert names == {"yosys", "nextpnr-ice40"}

    yosys = next(t for t in config.tools if t.name == "yosys")
    assert yosys.config["executable"] == "/fake/yosys"


def test_options_passed_to_provider(tmp_path, monkeypatch, registry_with_fake_plugin):
    _write_config(tmp_path, """\
        toolchains:
          - type: test-fake
            root: /a
            packages: [foo]
    """)
    _load(tmp_path, monkeypatch)
    assert RecordingProvider.calls == [{"root": "/a", "packages": ["foo"]}]


def test_explicit_tool_overrides_toolchain(tmp_path, monkeypatch, registry_with_fake_plugin):
    _write_config(tmp_path, """\
        toolchains:
          - type: test-fake
        tools:
          - name: yosys
            config:
              executable: /custom/yosys
    """)
    config = _load(tmp_path, monkeypatch)
    yosys = next(t for t in config.tools if t.name == "yosys")
    assert yosys.config["executable"] == "/custom/yosys"


def test_later_toolchain_overrides_earlier(tmp_path, monkeypatch, registry_with_fake_plugin):
    _write_config(tmp_path, """\
        toolchains:
          - type: test-fake
          - type: test-override
    """)
    config = _load(tmp_path, monkeypatch)
    yosys = next(t for t in config.tools if t.name == "yosys")
    assert yosys.config["executable"] == "/override/yosys"


def test_unknown_toolchain_type_is_skipped(tmp_path, monkeypatch, registry_with_fake_plugin, caplog):
    _write_config(tmp_path, """\
        toolchains:
          - type: does-not-exist
    """)
    config = _load(tmp_path, monkeypatch)
    assert config.tools == []
    assert any("does-not-exist" in rec.message for rec in caplog.records)


def test_invalid_toolchain_entry_missing_type(tmp_path, monkeypatch, registry_with_fake_plugin, caplog):
    _write_config(tmp_path, """\
        toolchains:
          - root: /oops
    """)
    config = _load(tmp_path, monkeypatch)
    assert config.toolchains == []
    assert config.tools == []


# --- Version dimension tests ---


def _tool(name, variant=None, version=None, exe="/x"):
    return ToolConfig(name=name, variant=variant, version=version,
                      config={"executable": exe})


def test_identifier_parsing():
    from gbs.config.model import GBSConfig
    assert GBSConfig._parse_identifier("yosys") == ("yosys", None, None)
    assert GBSConfig._parse_identifier("yosys:llvm") == ("yosys", "llvm", None)
    assert GBSConfig._parse_identifier("yosys@1.0") == ("yosys", None, "1.0")
    assert GBSConfig._parse_identifier("yosys:llvm@1.0") == ("yosys", "llvm", "1.0")


def test_identifier_property_roundtrip():
    assert _tool("yosys").identifier == "yosys"
    assert _tool("yosys", variant="llvm").identifier == "yosys:llvm"
    assert _tool("yosys", version="1.0").identifier == "yosys@1.0"
    assert _tool("yosys", variant="llvm", version="1.0").identifier == "yosys:llvm@1.0"


def test_get_tool_version_filter():
    config = GBSConfig(tools=[
        _tool("yosys", None, "1.0", "/y1"),
        _tool("yosys", None, "2.0", "/y2"),
    ])
    # No filter: first match
    assert config.get_tool("yosys").config["executable"] == "/y1"
    # Version filter picks the exact match
    assert config.get_tool("yosys@2.0").config["executable"] == "/y2"
    # Miss returns None
    assert config.get_tool("yosys@9.9") is None


def test_get_tool_variant_and_version_filter():
    config = GBSConfig(tools=[
        _tool("yosys", "apio", "1.0", "/apio1"),
        _tool("yosys", "apio", "2.0", "/apio2"),
        _tool("yosys", "system", None, "/sys"),
    ])
    assert config.get_tool("yosys:apio").config["executable"] == "/apio1"
    assert config.get_tool("yosys:apio@2.0").config["executable"] == "/apio2"
    assert config.get_tool("yosys:system").config["executable"] == "/sys"


def test_yaml_parses_version_field(tmp_path, monkeypatch, registry_with_fake_plugin):
    _write_config(tmp_path, """\
        tools:
          - name: yosys
            version: "1.2.3"
            config:
              executable: /x
    """)
    config = _load(tmp_path, monkeypatch)
    yosys = next(t for t in config.tools if t.name == "yosys")
    assert yosys.version == "1.2.3"


def test_resolve_tool_identifier_helper():
    from gbs.base.pass_ import resolve_tool_identifier
    # No tool_version: just tool
    assert resolve_tool_identifier({"tool": "yosys:llvm"}, "yosys") == "yosys:llvm"
    # tool_version alone: appended to default
    assert resolve_tool_identifier({"tool_version": "1.0"}, "yosys") == "yosys@1.0"
    # tool_version wins over pre-baked version in tool identifier
    assert resolve_tool_identifier(
        {"tool": "yosys@0.9", "tool_version": "1.0"}, "yosys"
    ) == "yosys@1.0"
    # variant preserved when injecting version
    assert resolve_tool_identifier(
        {"tool": "yosys:llvm", "tool_version": "1.0"}, "yosys"
    ) == "yosys:llvm@1.0"


def test_apio_provider_detects_build_info_version(tmp_path):
    """Feed the provider a synthetic apio tree and check version detection."""
    import json
    from gbs.builtin.apio.provider import ApioToolchainProvider

    pkg = tmp_path / "oss-cad-suite"
    (pkg / "bin").mkdir(parents=True)
    (pkg / "bin" / "yosys").write_text("#!/bin/sh\n")
    (pkg / "bin" / "yosys").chmod(0o755)
    (pkg / "BUILD-INFO.json").write_text(json.dumps({"release-tag": "2025-01-15"}))

    provider = ApioToolchainProvider({"root": str(tmp_path)}, origin=None)
    tools = provider.enumerate_tools()
    yosys = next(t for t in tools if t.name == "yosys")
    assert yosys.version == "2025-01-15"


def test_toolchain_spec_identifier():
    from gbs.config.model import ToolchainSpec
    assert ToolchainSpec(type="apio").identifier == "apio"
    assert ToolchainSpec(type="apio", variant="2026").identifier == "apio:2026"


def test_toolchain_variant_parsed_from_yaml(tmp_path, monkeypatch, registry_with_fake_plugin):
    _write_config(tmp_path, """\
        toolchains:
          - type: test-fake
            variant: my-tag
    """)
    config = _load(tmp_path, monkeypatch)
    assert len(config.toolchains) == 1
    assert config.toolchains[0].variant == "my-tag"
    assert config.toolchains[0].identifier == "test-fake:my-tag"


def test_via_stamped_on_expanded_tools(tmp_path, monkeypatch, registry_with_fake_plugin):
    _write_config(tmp_path, """\
        toolchains:
          - type: test-fake
            variant: alpha
    """)
    config = _load(tmp_path, monkeypatch)
    for tool in config.tools:
        assert tool.via == "test-fake:alpha"


def test_via_not_overwritten_when_provider_sets_it(tmp_path, monkeypatch, registry_with_fake_plugin):
    """A provider that pre-stamps a more specific via must be honored."""
    class NestedProvider(BaseToolchainProvider):
        type = "test-nested"

        def enumerate_tools(self):
            return [ToolConfig(name="yosys", via="test-nested:inner",
                               config={"executable": "/x"})]

    class NestedPlugin(BasePlugin):
        def __init__(self):
            super().__init__(name="gbs.test.nested", description="", version="0.0")

        def enumerate_toolchain_providers(self):
            return {"test-nested": NestedProvider}

    from gbs.plugins.loader import PluginRegistry
    from unittest.mock import patch
    reg = PluginRegistry()
    reg._register_plugin(NestedPlugin())
    _write_config(tmp_path, """\
        toolchains:
          - type: test-nested
    """)
    with patch("gbs.plugins.loader.get_plugin_registry", return_value=reg):
        config = _load(tmp_path, monkeypatch)
    yosys = next(t for t in config.tools if t.name == "yosys")
    assert yosys.via == "test-nested:inner"


def test_apio_provider_declared_variant_stamps_all(tmp_path):
    from gbs.builtin.apio.provider import ApioToolchainProvider
    pkg = tmp_path / "oss-cad-suite"
    (pkg / "bin").mkdir(parents=True)
    for name in ("yosys", "nextpnr-ice40", "ghdl"):
        exe = pkg / "bin" / name
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)

    provider = ApioToolchainProvider(
        {"root": str(tmp_path), "variant": "apio-2025"}, origin=None
    )
    tools = provider.enumerate_tools()
    variants = {(t.name, t.variant) for t in tools}
    # ghdl's per-package default_variant of "llvm" is overridden by the declared variant.
    assert ("ghdl", "apio-2025") in variants
    assert all(t.variant == "apio-2025" for t in tools)
