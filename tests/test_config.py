"""Tests for GBS Configuration System

Tests configuration loading, merging, tool lookup, and profile expansion.
"""

import pytest
from pathlib import Path
from gbs.config import GBSConfig, ToolConfig, Profile
from gbs.loaders import load_project, load_project_with_repositories, LoadError


class TestToolConfig:
    """Test ToolConfig dataclass"""

    def test_tool_config_creation(self):
        """Test creating a tool config"""
        tool = ToolConfig(name="ghdl", variant="llvm", config={"executable": "/usr/bin/ghdl"})
        assert tool.name == "ghdl"
        assert tool.variant == "llvm"
        assert tool.config == {"executable": "/usr/bin/ghdl"}

    def test_tool_identifier_with_variant(self):
        """Test identifier property with variant"""
        tool = ToolConfig(name="ghdl", variant="llvm", config={})
        assert tool.identifier == "ghdl:llvm"

    def test_tool_identifier_without_variant(self):
        """Test identifier property without variant"""
        tool = ToolConfig(name="gcc", config={})
        assert tool.identifier == "gcc"

    def test_tool_config_defaults(self):
        """Test default values"""
        tool = ToolConfig(name="ghdl")
        assert tool.variant is None
        assert tool.config == {}


class TestProfile:
    """Test Profile dataclass"""

    def test_profile_creation(self):
        """Test creating a profile"""
        profile = Profile(
            name="sim",
            filter_vars={"sim": 1},
            backends=[{"backend": "gbs.backend:GHDLBackend"}],
            repositories=[{"path": "libs/nsl"}]
        )
        assert profile.name == "sim"
        assert profile.filter_vars == {"sim": 1}
        assert len(profile.backends) == 1
        assert len(profile.repositories) == 1

    def test_profile_defaults(self):
        """Test default values"""
        profile = Profile(name="test")
        assert profile.filter_vars == {}
        assert profile.backends == []
        assert profile.repositories == []


class TestGBSConfigMerge:
    """Test GBSConfig merge logic"""

    def test_merge_tools_extend(self):
        """Test that tools are extended when different"""
        base = GBSConfig(tools=[
            ToolConfig("ghdl", "system", {"executable": "ghdl"}),
            ToolConfig("gcc", "system", {"executable": "gcc"}),
        ])
        override = GBSConfig(tools=[
            ToolConfig("vivado", "2023.1", {"path": "/opt/vivado"}),
        ])

        merged = GBSConfig._merge_configs(base, override)
        assert len(merged.tools) == 3
        assert merged.tools[0].name == "ghdl"
        assert merged.tools[1].name == "gcc"
        assert merged.tools[2].name == "vivado"

    def test_merge_tools_override_exact_match(self):
        """Test that exact (name, variant) matches override"""
        base = GBSConfig(tools=[
            ToolConfig("ghdl", "system", {"executable": "ghdl"}),
            ToolConfig("ghdl", "llvm", {"executable": "ghdl-llvm"}),
        ])
        override = GBSConfig(tools=[
            ToolConfig("ghdl", "llvm", {"executable": "/usr/local/bin/ghdl"}),
        ])

        merged = GBSConfig._merge_configs(base, override)
        assert len(merged.tools) == 2
        # First tool unchanged
        assert merged.tools[0].name == "ghdl"
        assert merged.tools[0].variant == "system"
        # Second tool overridden
        assert merged.tools[1].name == "ghdl"
        assert merged.tools[1].variant == "llvm"
        assert merged.tools[1].config["executable"] == "/usr/local/bin/ghdl"

    def test_merge_tools_no_override_different_variant(self):
        """Test that different variants don't override each other"""
        base = GBSConfig(tools=[
            ToolConfig("ghdl", "system", {"executable": "ghdl"}),
        ])
        override = GBSConfig(tools=[
            ToolConfig("ghdl", "llvm", {"executable": "ghdl-llvm"}),
        ])

        merged = GBSConfig._merge_configs(base, override)
        assert len(merged.tools) == 2

    def test_merge_profiles_override_by_name(self):
        """Test that profiles override by name"""
        base = GBSConfig(profiles={
            "sim": Profile("sim", filter_vars={"sim": 1}),
            "synth": Profile("synth", filter_vars={"sim": 0}),
        })
        override = GBSConfig(profiles={
            "sim": Profile("sim", filter_vars={"sim": 1, "vendor": "xilinx"}),
        })

        merged = GBSConfig._merge_configs(base, override)
        assert len(merged.profiles) == 2
        # sim profile overridden
        assert merged.profiles["sim"].filter_vars == {"sim": 1, "vendor": "xilinx"}
        # synth profile unchanged
        assert merged.profiles["synth"].filter_vars == {"sim": 0}

    def test_merge_repositories_extend(self):
        """Test that repositories extend unconditionally"""
        base = GBSConfig(repositories=[
            {"path": "libs/repo1"},
            {"path": "libs/repo2"},
        ])
        override = GBSConfig(repositories=[
            {"path": "libs/repo3"},
        ])

        merged = GBSConfig._merge_configs(base, override)
        assert len(merged.repositories) == 3

    def test_merge_empty_configs(self):
        """Test merging with empty configs"""
        base = GBSConfig()
        override = GBSConfig()

        merged = GBSConfig._merge_configs(base, override)
        assert len(merged.tools) == 0
        assert len(merged.profiles) == 0
        assert len(merged.repositories) == 0


class TestGBSConfigToolLookup:
    """Test GBSConfig.get_tool() method"""

    def test_get_tool_exact_match(self):
        """Test getting tool with exact name:variant match"""
        config = GBSConfig(tools=[
            ToolConfig("ghdl", "system", {"executable": "ghdl"}),
            ToolConfig("ghdl", "llvm", {"executable": "ghdl-llvm"}),
        ])

        tool = config.get_tool("ghdl:llvm")
        assert tool is not None
        assert tool.name == "ghdl"
        assert tool.variant == "llvm"

    def test_get_tool_name_only_match(self):
        """Test getting tool with just name (returns first match)"""
        config = GBSConfig(tools=[
            ToolConfig("ghdl", "system", {"executable": "ghdl"}),
            ToolConfig("ghdl", "llvm", {"executable": "ghdl-llvm"}),
        ])

        tool = config.get_tool("ghdl")
        assert tool is not None
        assert tool.name == "ghdl"
        assert tool.variant == "system"  # First match

    def test_get_tool_not_found(self):
        """Test getting non-existent tool returns None"""
        config = GBSConfig(tools=[
            ToolConfig("ghdl", "system", {"executable": "ghdl"}),
        ])

        tool = config.get_tool("vivado")
        assert tool is None

    def test_get_tool_variant_not_found(self):
        """Test getting tool with non-existent variant returns None"""
        config = GBSConfig(tools=[
            ToolConfig("ghdl", "system", {"executable": "ghdl"}),
        ])

        tool = config.get_tool("ghdl:llvm")
        assert tool is None

    def test_get_tool_empty_config(self):
        """Test getting tool from empty config returns None"""
        config = GBSConfig()
        tool = config.get_tool("ghdl")
        assert tool is None


class TestBuildContextToolLookup:
    """Test BuildContext.get_tool() method"""

    def test_get_tool_with_config(self):
        """Test getting tool when config is present"""
        from gbs.tasks import BuildContext

        config = GBSConfig(tools=[
            ToolConfig("ghdl", "llvm", {"executable": "/usr/bin/ghdl"}),
        ])

        ctx = BuildContext(gbs_config=config)
        tool = ctx.get_tool("ghdl:llvm")

        assert tool is not None
        assert tool == {"executable": "/usr/bin/ghdl"}

    def test_get_tool_required_not_found(self):
        """Test that required=True raises error when tool not found"""
        from gbs.tasks import BuildContext
        from gbs.model.build import BuildError

        config = GBSConfig()
        ctx = BuildContext(gbs_config=config)

        with pytest.raises(BuildError, match="Tool 'vivado' not found"):
            ctx.get_tool("vivado", required=True)

    def test_get_tool_optional_not_found(self):
        """Test that required=False returns None when tool not found"""
        from gbs.tasks import BuildContext

        config = GBSConfig()
        ctx = BuildContext(gbs_config=config)

        tool = ctx.get_tool("vivado", required=False)
        assert tool is None

    def test_get_tool_no_config(self):
        """Test that no config raises error when required=True"""
        from gbs.tasks import BuildContext
        from gbs.model.build import BuildError

        ctx = BuildContext()

        with pytest.raises(BuildError, match="no GBS config loaded"):
            ctx.get_tool("ghdl", required=True)

    def test_get_tool_no_config_optional(self):
        """Test that no config returns None when required=False"""
        from gbs.tasks import BuildContext

        ctx = BuildContext()
        tool = ctx.get_tool("ghdl", required=False)
        assert tool is None


class TestProfileExpansion:
    """Test profile expansion in project loader"""

    def test_profile_expansion_basic(self, tmp_path):
        """Test basic profile expansion"""
        # Create project with profile reference
        project_file = tmp_path / "project.gbs.yaml"
        project_file.write_text("""
name: test_project
topcell: top
profile: sim

root:
  name: root
""")

        # Create config with profile
        config = GBSConfig(profiles={
            "sim": Profile(
                "sim",
                filter_vars={"sim": 1, "vendor": "xilinx"},
                backends=[{"backend": "gbs.backend:GHDLBackend"}],
                repositories=[{"path": "libs/nsl"}]
            )
        })

        # Load project with config
        project = load_project(project_file, gbs_config=config)

        # Verify profile was expanded
        assert project.filter_vars == {"sim": 1, "vendor": "xilinx"}
        assert "backends" in project.raw_config
        assert project.raw_config["backends"] == [{"backend": "gbs.backend:GHDLBackend"}]

    def test_profile_not_found_error(self, tmp_path):
        """Test that missing profile raises error"""
        project_file = tmp_path / "project.gbs.yaml"
        project_file.write_text("""
name: test_project
topcell: top
profile: nonexistent

root:
  name: root
""")

        config = GBSConfig(profiles={
            "sim": Profile("sim")
        })

        with pytest.raises(LoadError, match="Profile 'nonexistent' not found"):
            load_project(project_file, gbs_config=config)

    def test_profile_with_explicit_config_error(self, tmp_path):
        """Test that profile + explicit config raises error"""
        project_file = tmp_path / "project.gbs.yaml"
        project_file.write_text("""
name: test_project
topcell: top
profile: sim
filter_vars:
  custom: 1

root:
  name: root
""")

        config = GBSConfig(profiles={
            "sim": Profile("sim")
        })

        with pytest.raises(LoadError, match="cannot specify both 'profile' and"):
            load_project(project_file, gbs_config=config)

    def test_profile_with_backends_conflict(self, tmp_path):
        """Test that profile + backends raises error"""
        project_file = tmp_path / "project.gbs.yaml"
        project_file.write_text("""
name: test_project
topcell: top
profile: sim
backends:
  - backend: gbs.backend:GHDLBackend

root:
  name: root
""")

        config = GBSConfig(profiles={
            "sim": Profile("sim")
        })

        with pytest.raises(LoadError, match="cannot specify both 'profile' and"):
            load_project(project_file, gbs_config=config)

    def test_profile_without_config_error(self, tmp_path):
        """Test that profile without config raises error"""
        project_file = tmp_path / "project.gbs.yaml"
        project_file.write_text("""
name: test_project
topcell: top
profile: sim

root:
  name: root
""")

        with pytest.raises(LoadError, match="no GBSConfig provided"):
            load_project(project_file, gbs_config=None)


class TestRepositoryMerging:
    """Test repository merging from multiple sources"""

    def test_config_repositories_loaded(self, tmp_path):
        """Test that config-level repositories are loaded"""
        # Create dummy repository
        repo_file = tmp_path / "libs" / "repo1" / "repository.gbs.yaml"
        repo_file.parent.mkdir(parents=True)
        repo_file.write_text("""
name: repo1
libraries: []
""")

        # Create project without repositories
        project_file = tmp_path / "project.gbs.yaml"
        project_file.write_text("""
name: test_project
topcell: top

root:
  name: root
""")

        # Create config with repositories
        config = GBSConfig(repositories=[
            {"path": str(repo_file)}
        ])

        # Load project with config
        project, repos = load_project_with_repositories(project_file, gbs_config=config)

        # Verify config repository was loaded
        assert len(repos) == 1
        assert repos[0].name == "repo1"

    def test_profile_repositories_loaded(self, tmp_path):
        """Test that profile repositories are loaded"""
        # Create dummy repository
        repo_file = tmp_path / "libs" / "repo1" / "repository.gbs.yaml"
        repo_file.parent.mkdir(parents=True)
        repo_file.write_text("""
name: repo1
libraries: []
""")

        # Create project with profile
        project_file = tmp_path / "project.gbs.yaml"
        project_file.write_text("""
name: test_project
topcell: top
profile: sim

root:
  name: root
""")

        # Create config with profile containing repositories
        config = GBSConfig(profiles={
            "sim": Profile(
                "sim",
                repositories=[{"path": str(repo_file)}]
            )
        })

        # Load project with config
        project, repos = load_project_with_repositories(project_file, gbs_config=config)

        # Verify profile repository was loaded
        assert len(repos) == 1
        assert repos[0].name == "repo1"

    def test_all_repositories_merged(self, tmp_path):
        """Test that config, profile, and project repositories are all merged"""
        # Create dummy repositories
        repo1 = tmp_path / "libs" / "repo1" / "repository.gbs.yaml"
        repo1.parent.mkdir(parents=True)
        repo1.write_text("name: repo1\nlibraries: []")

        repo2 = tmp_path / "libs" / "repo2" / "repository.gbs.yaml"
        repo2.parent.mkdir(parents=True)
        repo2.write_text("name: repo2\nlibraries: []")

        repo3 = tmp_path / "libs" / "repo3" / "repository.gbs.yaml"
        repo3.parent.mkdir(parents=True)
        repo3.write_text("name: repo3\nlibraries: []")

        # Create project with profile and repositories
        project_file = tmp_path / "project.gbs.yaml"
        project_file.write_text(f"""
name: test_project
topcell: top
profile: sim

root:
  name: root

repositories:
  - path: {repo3}
""")

        # Create config with repositories and profile
        config = GBSConfig(
            repositories=[{"path": str(repo1)}],
            profiles={
                "sim": Profile(
                    "sim",
                    repositories=[{"path": str(repo2)}]
                )
            }
        )

        # Load project with config
        project, repos = load_project_with_repositories(project_file, gbs_config=config)

        # Verify all repositories were merged (config + profile + project)
        assert len(repos) == 3
        repo_names = [r.name for r in repos]
        assert "repo1" in repo_names
        assert "repo2" in repo_names
        assert "repo3" in repo_names


class TestPluginSystem:
    """Test plugin discovery and default tool contribution"""

    def test_plugin_registry_creation(self):
        """Test creating a plugin registry"""
        from gbs.plugins import PluginRegistry

        registry = PluginRegistry()
        assert len(registry._backend_plugins) == 0
        assert len(registry._loader_plugins) == 0
        assert len(registry._default_tools) == 0
        assert registry._discovered is False

    def test_register_backend(self):
        """Test registering a backend plugin"""
        from gbs.plugins import PluginRegistry
        from gbs.backend import GHDLBackend

        registry = PluginRegistry()
        registry.register_backend("test", GHDLBackend)

        assert "test" in registry._backend_plugins
        assert registry._backend_plugins["test"] == GHDLBackend

    def test_contribute_tool_defaults(self):
        """Test contributing default tools"""
        from gbs.plugins import PluginRegistry

        registry = PluginRegistry()
        tools = [
            ToolConfig("ghdl", "system", {"executable": "ghdl"}),
            ToolConfig("gcc", "system", {"executable": "gcc"}),
        ]

        registry.contribute_tool_defaults(tools)
        assert len(registry._default_tools) == 2

    def test_get_default_tools(self):
        """Test getting default tools triggers discovery"""
        from gbs.plugins import get_plugin_registry

        registry = get_plugin_registry()
        default_tools = registry.get_default_tools()

        # Should have discovered plugins
        assert registry._discovered is True
        # Should have at least the built-in tools (ghdl, gcc)
        assert len(default_tools) >= 2

        # Verify expected tools are present
        tool_names = [t.name for t in default_tools]
        assert "ghdl" in tool_names
        assert "gcc" in tool_names
