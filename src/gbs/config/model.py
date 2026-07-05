"""GBS Configuration System

Handles loading and merging configuration from:
- ~/.config/gbs.yaml (user config)
- .gbs.yaml (tree config, first found walking up from CWD)
- Plugin-contributed defaults

Also supports `toolchains:` entries: each entry names a provider `type`
(from a plugin) and options; after merging config layers, providers are
consulted to expand their entries into ToolConfig objects. Explicit
`tools:` entries then overlay the expanded set.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
import yaml

from ..logging import get_logger
from ..utils import expand_path

logger = get_logger(__name__)

# Default file URL template for OSC 8 hyperlinks
DEFAULT_FILE_URL_TEMPLATE = "file://{path}#L{line}:{column}"


@dataclass
class ToolConfig:
    """Tool definition with optional variant and version

    `variant` is a user-declared label (e.g. "llvm" vs "mcode" for ghdl,
    "prime" vs "standard" for quartus). `version` is orthogonal — a
    scalar taken from the installed tool or its package metadata.

    Selection identity is (name, variant): two entries with the same
    (name, variant) but different versions do not coexist; the later
    one overrides. To keep two versions of the same tool selectable,
    give them different variants (e.g. one per toolchain entry).

    Examples:
        >>> tool = ToolConfig("ghdl", "llvm", None, {"executable": "/usr/bin/ghdl"})
        >>> tool.identifier
        'ghdl:llvm'

        >>> tool = ToolConfig("yosys", None, "2026-03-24", {"executable": "/apio/bin/yosys"})
        >>> tool.identifier
        'yosys@2026-03-24'
    """
    name: str
    variant: Optional[str] = None
    version: Optional[str] = None
    config: dict[str, Any] = field(default_factory=dict)
    origin: Optional[Path] = None  # Config file this tool was loaded from

    @property
    def identifier(self) -> str:
        """Returns 'name[:variant][@version]' from the fields that are set."""
        result = self.name
        if self.variant:
            result += f":{self.variant}"
        if self.version:
            result += f"@{self.version}"
        return result

@dataclass
class ToolchainSpec:
    """A `toolchains:` config entry, prior to expansion.

    Attributes:
        type: Provider type name (dispatched via the plugin registry).
        options: Remaining keys from the entry (root, packages, ...).
        origin: Config file this entry was declared in.
    """
    type: str
    options: dict[str, Any] = field(default_factory=dict)
    origin: Optional[Path] = None


@dataclass
class GBSConfig:
    """Global GBS configuration from user and tree config files

    Configuration is loaded and merged from multiple sources:
    1. Plugin-contributed defaults (lowest priority)
    2. ~/.config/gbs.yaml (user config)
    3. .gbs.yaml (tree config, first found walking up from CWD)

    Merge rules:
    - Tools: Extend list, but (name, variant) tuples override
    - Repositories: Extend list unconditionally
    - max_parallel: Override (higher priority wins)
    - max_log_count: Override (higher priority wins)
    """
    tools: list[ToolConfig] = field(default_factory=list)
    toolchains: list[ToolchainSpec] = field(default_factory=list)
    repositories: list[dict] = field(default_factory=list)
    max_parallel: Optional[int] = None  # Maximum parallel tasks (None = use default)
    max_log_count: Optional[int] = None  # Number of log files to keep (None = use default, 0 = keep all)
    file_url_template: str = DEFAULT_FILE_URL_TEMPLATE  # Template for OSC 8 file URLs
    loaded_files: list[Path] = field(default_factory=list)  # Config files that were loaded

    def get_tool(self, identifier: str) -> Optional[ToolConfig]:
        """Lookup tool by 'name[:variant][@version]'.

        Any of the three fields may be omitted to widen the match. First
        match in insertion order wins (stable ordering across load runs).

        Args:
            identifier: 'name', 'name:variant', 'name@version', or
                        'name:variant@version'.

        Returns:
            Matching ToolConfig, or None if not found.

        Examples:
            >>> config.get_tool("ghdl:llvm")            # variant filter
            >>> config.get_tool("ghdl")                 # any variant
            >>> config.get_tool("yosys@2026-03-24")     # version filter
            >>> config.get_tool("yosys:apio@2026-03-24")
        """
        name, variant, version = self._parse_identifier(identifier)
        for tool in self.tools:
            if tool.name != name:
                continue
            if variant is not None and tool.variant != variant:
                continue
            if version is not None and tool.version != version:
                continue
            return tool
        return None

    @staticmethod
    def _parse_identifier(identifier: str) -> tuple[str, Optional[str], Optional[str]]:
        """Split 'name[:variant][@version]' into (name, variant, version).

        Only the first ':' and the first '@' are treated as delimiters,
        so tool paths/versions with either character are preserved.
        """
        rest = identifier
        version: Optional[str] = None
        if '@' in rest:
            rest, version = rest.split('@', 1)
        variant: Optional[str] = None
        if ':' in rest:
            name, variant = rest.split(':', 1)
        else:
            name = rest
        return name, variant, version

    @classmethod
    def load(cls, plugin_defaults: Optional[list[ToolConfig]] = None) -> 'GBSConfig':
        """Load and merge configuration files

        Args:
            plugin_defaults: Tool defaults contributed by plugins

        Returns:
            Merged configuration
        """
        # Start with plugin defaults
        if plugin_defaults is None:
            plugin_defaults = []

        base_config = cls(tools=plugin_defaults)

        # Load user config
        user_config = cls._load_user_config()
        merged = cls._merge_configs(base_config, user_config)

        # Load tree config
        tree_config = cls._load_tree_config()
        final = cls._merge_configs(merged, tree_config)

        # Expand toolchains: each ToolchainSpec is dispatched to its
        # provider, whose ToolConfig entries are overlaid by any explicit
        # `tools:` entries from the config files above.
        final._expand_toolchains()

        logger.debug(f"Loaded config: {len(final.tools)} tools, "
                    f"{len(final.toolchains)} toolchains, "
                    f"{len(final.repositories)} repositories")

        return final

    def _expand_toolchains(self) -> None:
        """Resolve `toolchains:` entries and merge their tools into self.tools.

        Provider lookup uses the plugin registry singleton. Each
        toolchain expansion is a fresh ToolConfig list; later toolchains
        override earlier ones on (name, variant) collisions. Explicit
        tools already in `self.tools` win over all expansions.
        """
        if not self.toolchains:
            return

        # Late import to avoid a circular dependency during plugin discovery.
        from ..plugins.loader import get_plugin_registry
        registry = get_plugin_registry()

        expanded: list[ToolConfig] = []
        for spec in self.toolchains:
            provider_class = registry.get_toolchain_provider_class(spec.type)
            if provider_class is None:
                logger.warning(
                    f"Unknown toolchain type {spec.type!r} in "
                    f"{spec.origin or '<unknown>'}; skipping"
                )
                continue
            try:
                provider = provider_class(spec.options, origin=spec.origin)
                tools = provider.enumerate_tools()
            except Exception as e:
                logger.warning(
                    f"Toolchain provider {spec.type!r} from "
                    f"{spec.origin or '<unknown>'} failed: {e}"
                )
                continue

            for tool in tools:
                idx = self._find_tool_index(expanded, tool.name, tool.variant)
                if idx is not None:
                    expanded[idx] = tool
                else:
                    expanded.append(tool)

        # Overlay explicit tools on top of expanded set.
        for explicit in self.tools:
            idx = self._find_tool_index(expanded, explicit.name, explicit.variant)
            if idx is not None:
                expanded[idx] = explicit
            else:
                expanded.append(explicit)

        self.tools = expanded

    @staticmethod
    def _find_tool_index(tools: list[ToolConfig], name: str, variant: Optional[str]) -> Optional[int]:
        """Locate a tool by (name, variant); returns None if absent."""
        for idx, existing in enumerate(tools):
            if existing.name == name and existing.variant == variant:
                return idx
        return None

    @classmethod
    def _load_user_config(cls) -> 'GBSConfig':
        """Load ~/.config/gbs.yaml"""
        config_path = Path.home() / ".config" / "gbs.yaml"
        if config_path.exists():
            logger.debug(f"Loading user config from {config_path}")
            config = cls._parse_config_file(config_path)
            config.loaded_files = [config_path.resolve()]
            return config
        return cls()  # Empty config

    @classmethod
    def _load_tree_config(cls) -> 'GBSConfig':
        """Find and load .gbs.yaml walking up from CWD

        Searches for .gbs.yaml starting from current directory and
        walking up to filesystem root. First found wins.
        """
        current = Path.cwd()
        while True:
            config_path = current / ".gbs.yaml"
            if config_path.exists():
                logger.debug(f"Loading tree config from {config_path}")
                config = cls._parse_config_file(config_path)
                config.loaded_files = [config_path.resolve()]
                return config

            parent = current.parent
            if parent == current:  # Reached filesystem root
                break
            current = parent

        return cls()  # Not found

    @classmethod
    def _parse_config_file(cls, path: Path) -> 'GBSConfig':
        """Parse a GBS config file"""
        with open(path, 'r') as f:
            data = yaml.safe_load(f) or {}

        # Parse tools
        tools = []
        for tool_data in data.get('tools', []):
            if 'name' not in tool_data:
                logger.warning(f"Tool definition missing 'name' in {path}, skipping")
                continue

            tools.append(ToolConfig(
                name=tool_data['name'],
                variant=tool_data.get('variant'),
                version=tool_data.get('version'),
                config=tool_data.get('config', {}),
                origin=path.resolve(),
            ))

        # Parse toolchains
        toolchains = []
        for tc_data in data.get('toolchains', []):
            if not isinstance(tc_data, dict) or 'type' not in tc_data:
                logger.warning(
                    f"Invalid toolchain spec in {path}, must be a mapping with "
                    f"a 'type' key: {tc_data}"
                )
                continue
            options = {k: v for k, v in tc_data.items() if k != 'type'}
            toolchains.append(ToolchainSpec(
                type=tc_data['type'],
                options=options,
                origin=path.resolve(),
            ))

        # Parse repositories - resolve relative paths now using config file's directory
        config_dir = path.parent
        repositories = []
        for repo_spec in data.get('repositories', []):
            if not isinstance(repo_spec, dict) or 'path' not in repo_spec:
                logger.warning(f"Invalid repository spec in {path}, skipping: {repo_spec}")
                continue
            # Make a copy to avoid mutating the original
            repo_spec = dict(repo_spec)
            repo_path = expand_path(repo_spec['path'])
            if not repo_path.is_absolute():
                repo_path = (config_dir / repo_path).resolve()
            repo_spec['path'] = str(repo_path)
            repo_spec['_origin'] = str(path.resolve())
            repositories.append(repo_spec)

        # Parse max_parallel
        max_parallel = data.get('max_parallel')
        if max_parallel is not None:
            try:
                max_parallel = int(max_parallel)
                if max_parallel < 1:
                    logger.warning(f"max_parallel must be >= 1 in {path}, ignoring")
                    max_parallel = None
            except (ValueError, TypeError):
                logger.warning(f"Invalid max_parallel value in {path}, ignoring")
                max_parallel = None

        # Parse max_log_count
        max_log_count = data.get('max_log_count')
        if max_log_count is not None:
            try:
                max_log_count = int(max_log_count)
                if max_log_count < 0:
                    logger.warning(f"max_log_count must be >= 0 in {path}, ignoring")
                    max_log_count = None
            except (ValueError, TypeError):
                logger.warning(f"Invalid max_log_count value in {path}, ignoring")
                max_log_count = None

        # Parse file_url_template (if provided, must be a string)
        file_url_template_raw = data.get('file_url_template')
        if file_url_template_raw is not None:
            if not isinstance(file_url_template_raw, str):
                logger.warning(f"file_url_template must be a string in {path}, using default")
                file_url_template = DEFAULT_FILE_URL_TEMPLATE
            else:
                file_url_template = file_url_template_raw
        else:
            # Not specified in config, use default
            file_url_template = DEFAULT_FILE_URL_TEMPLATE

        return cls(
            tools=tools,
            toolchains=toolchains,
            repositories=repositories,
            max_parallel=max_parallel,
            max_log_count=max_log_count,
            file_url_template=file_url_template,
        )

    @classmethod
    def _merge_configs(cls, base: 'GBSConfig', override: 'GBSConfig') -> 'GBSConfig':
        """Custom merge logic

        - Tools: Extend list, but (name, variant) tuples override
        - Repositories: Extend list unconditionally
        - max_parallel: Override wins (if set)

        Args:
            base: Base configuration (lower priority)
            override: Override configuration (higher priority)

        Returns:
            Merged configuration
        """
        # Start with base tools
        merged_tools = list(base.tools)

        # Add/override with override tools
        for new_tool in override.tools:
            # Find if exists
            found_idx = None
            for idx, existing in enumerate(merged_tools):
                if (existing.name == new_tool.name and
                    existing.variant == new_tool.variant):
                    found_idx = idx
                    break

            if found_idx is not None:
                merged_tools[found_idx] = new_tool  # Override
            else:
                merged_tools.append(new_tool)  # Add

        # Toolchains: extend unconditionally. Later entries override
        # earlier ones only via the (name, variant) rule at expansion time.
        merged_toolchains = base.toolchains + override.toolchains

        # Repositories: extend unconditionally
        merged_repos = base.repositories + override.repositories

        # max_parallel: override wins if set, otherwise keep base
        merged_max_parallel = override.max_parallel if override.max_parallel is not None else base.max_parallel

        # max_log_count: override wins if set, otherwise keep base
        merged_max_log_count = override.max_log_count if override.max_log_count is not None else base.max_log_count

        # file_url_template: override wins if not default, otherwise keep base
        if override.file_url_template != DEFAULT_FILE_URL_TEMPLATE:
            merged_file_url_template = override.file_url_template
        else:
            merged_file_url_template = base.file_url_template

        # loaded_files: concatenate all
        merged_loaded_files = base.loaded_files + override.loaded_files

        return cls(
            tools=merged_tools,
            toolchains=merged_toolchains,
            repositories=merged_repos,
            max_parallel=merged_max_parallel,
            max_log_count=merged_max_log_count,
            file_url_template=merged_file_url_template,
            loaded_files=merged_loaded_files,
        )
