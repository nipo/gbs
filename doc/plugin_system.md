# GBS Plugin System

GBS supports a plugin system for custom repository loaders, enabling integration with different repository formats beyond the standard YAML-based format.

## Overview

The plugin system allows you to:
- Load repositories from non-YAML formats (e.g., Makefiles, CMake, custom formats)
- Integrate existing codebases without conversion
- Extend GBS functionality without modifying core code

## Plugin Architecture

Plugins use **PEP 420 namespace packages** to extend the `gbs.plugin` namespace. This allows plugins to be:
- Installed as separate Python packages
- Discovered automatically by GBS
- Loaded dynamically on demand

### Directory Structure

```
gbs/                        # Main GBS package
  plugin/                   # Namespace package (no __init__.py)

gbs-plugin-nsl/            # Example plugin package
  gbs/                      # Must mirror namespace
    plugin/                 # Namespace (no __init__.py)
      nsl/                  # Plugin module
        __init__.py
        tree.py             # Must export load() function
        makefile.py         # Helper modules
```

## Creating a Plugin

### 1. Package Structure

Create a new package with the structure:

```
my-gbs-plugin/
├── pyproject.toml
├── README.md
└── gbs/
    └── plugin/
        └── myplugin/
            ├── __init__.py
            └── loader.py
```

**Important**: Do NOT add `__init__.py` files in `gbs/` or `gbs/plugin/` directories.

### 2. pyproject.toml

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "gbs-plugin-myplugin"
version = "0.1.0"
description = "GBS plugin for MyFormat repositories"
requires-python = ">=3.13"
dependencies = [
    "gbs",
]

[tool.hatch.build.targets.wheel]
packages = ["gbs"]
```

### 3. Loader Implementation

Your plugin module must provide a `load` function with this signature:

```python
from pathlib import Path
from gbs.models import Repository
from gbs.loaders import LoadError

def load(path: Path) -> Repository:
    """Load repository from custom format

    Args:
        path: Path to repository root

    Returns:
        Repository object

    Raises:
        LoadError: If repository cannot be loaded
    """
```

### 4. Installation

```bash
cd my-gbs-plugin
pip install -e .
```

## Using Plugins

### In Project Files

Reference the plugin in your project's `repositories` section:

```yaml
name: my_project

repositories:
  - path: /path/to/repo
    loader: gbs.plugin.myplugin.loader
```

### Automatic Loading

GBS will:
1. Detect the `loader` field in the repository specification
2. Dynamically import the specified module
3. Call its `load(path)` function
4. Use the returned `Repository` object

## Example: NSL Plugin

The NSL plugin demonstrates a complete implementation for loading Makefile-based repositories.

**Location**: `nsl/build/gbs/`

**Usage**:

```yaml
repositories:
  - path: /path/to/nsl_clean
    loader: gbs.plugin.nsl.tree
```

**Features**:
- Parses Makefile-based repository structure
- Handles variable expansion
- Supports conditional compilation
- Discovers libraries and packages automatically

## Namespace Package Setup

For the plugin system to work, the main GBS package must extend its path to discover plugins:

In `gbs/__init__.py`:

```python
# Extend __path__ to enable namespace packages for plugins
__path__ = __import__('pkgutil').extend_path(__path__, __name__)
```

This enables PEP 420-style namespace packages where multiple packages can contribute to the same namespace.

## Testing Plugins

### Programmatic Test

```python
from pathlib import Path
from gbs.loaders import get_repository_loader

# Load plugin
loader = get_repository_loader('gbs.plugin.nsl.tree')

# Test loading
repo = loader(Path('/path/to/nsl_clean'))

print(f"Loaded {len(repo.libraries)} libraries")
```

### CLI Test

```bash
# Test with a project file
gbs project fileset my_project.gbs.yaml
```

## Troubleshooting

### Plugin Not Found

**Error**: `ModuleNotFoundError: No module named 'gbs.plugin.myplugin'`

**Solutions**:
1. Check plugin is installed: `pip list | grep gbs-plugin`
2. Verify namespace package structure (no `__init__.py` in `gbs/` or `gbs/plugin/`)
3. Check `pyproject.toml` has `packages = ["gbs"]`
4. Ensure main GBS package has namespace support in `__init__.py`

### Load Function Missing

**Error**: `Repository loader must provide a 'load' function`

**Solutions**:
1. Ensure your module exports a `load()` function
2. Function must accept `Path` and return `Repository`

## See Also

- [GBS Loaders Module](../src/gbs/loaders.py)
- [PEP 420 - Implicit Namespace Packages](https://peps.python.org/pep-0420/)
