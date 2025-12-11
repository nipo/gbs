"""GBS Configuration System

Handles loading and merging configuration from:
- ~/.config/gbs.yaml (user config)
- .gbs.yaml (tree config, first found walking up from CWD)
- Plugin-contributed defaults
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
import yaml

from ..logging import get_logger

logger = get_logger(__name__)


@dataclass
class ToolConfig:
    """Tool definition with optional variant

    Examples:
        >>> tool = ToolConfig("ghdl", "llvm", {"executable": "/usr/bin/ghdl"})
        >>> tool.identifier
        'ghdl:llvm'

        >>> tool = ToolConfig("gcc", None, {"path": "/usr/bin/gcc"})
        >>> tool.identifier
        'gcc'
    """
    name: str
    variant: Optional[str] = None
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def identifier(self) -> str:
        """Returns 'name' or 'name:variant'"""
        if self.variant:
            return f"{self.name}:{self.variant}"
        return self.name

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
    repositories: list[dict] = field(default_factory=list)
    max_parallel: Optional[int] = None  # Maximum parallel tasks (None = use default)
    max_log_count: Optional[int] = None  # Number of log files to keep (None = use default, 0 = keep all)

    def get_tool(self, identifier: str) -> Optional[ToolConfig]:
        """Lookup tool by 'name' or 'name:variant'

        Args:
            identifier: Either 'name' or 'name:variant'

        Returns:
            Matching ToolConfig, or None if not found.
            If only name specified, returns first matching name (stable order).

        Examples:
            >>> config.get_tool("ghdl:llvm")  # Exact match
            >>> config.get_tool("ghdl")       # Any variant of ghdl
        """
        if ':' in identifier:
            name, variant = identifier.split(':', 1)
            for tool in self.tools:
                if tool.name == name and tool.variant == variant:
                    return tool
        else:
            # Match any variant for this name (first match)
            for tool in self.tools:
                if tool.name == identifier:
                    return tool
        return None

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

        logger.debug(f"Loaded config: {len(final.tools)} tools, "
                    f"{len(final.repositories)} repositories")

        return final

    @classmethod
    def _load_user_config(cls) -> 'GBSConfig':
        """Load ~/.config/gbs.yaml"""
        config_path = Path.home() / ".config" / "gbs.yaml"
        if config_path.exists():
            logger.debug(f"Loading user config from {config_path}")
            return cls._parse_config_file(config_path)
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
                return cls._parse_config_file(config_path)

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
                config=tool_data.get('config', {})
            ))

        # Parse repositories
        repositories = data.get('repositories', [])

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

        return cls(
            tools=tools,
            repositories=repositories,
            max_parallel=max_parallel,
            max_log_count=max_log_count,
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

        # Repositories: extend unconditionally
        merged_repos = base.repositories + override.repositories

        # max_parallel: override wins if set, otherwise keep base
        merged_max_parallel = override.max_parallel if override.max_parallel is not None else base.max_parallel

        # max_log_count: override wins if set, otherwise keep base
        merged_max_log_count = override.max_log_count if override.max_log_count is not None else base.max_log_count

        return cls(
            tools=merged_tools,
            repositories=merged_repos,
            max_parallel=merged_max_parallel,
            max_log_count=merged_max_log_count,
        )
