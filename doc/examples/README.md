# GBS Configuration Examples

This directory contains example configuration files demonstrating the GBS configuration system.

## Configuration File Types

### 1. User Config (`~/.config/gbs.yaml`)

User-wide configuration that applies to all your GBS projects.

**Location**: `~/.config/gbs.yaml`

**Contains**:
- Tool definitions (paths to GHDL, GCC, Vivado, etc.)
- Reusable profiles (simulation, synthesis, etc.)
- Global repositories (shared libraries)

**Examples**:
- [`user-config-basic.yaml`](./user-config-basic.yaml) - Simple user config
- [`user-config-advanced.yaml`](./user-config-advanced.yaml) - Full-featured config with multiple tool variants

### 2. Tree Config (`.gbs.yaml`)

Project tree configuration that applies to all projects in a directory tree.

**Location**: `.gbs.yaml` (at tree root, like `.git`)

**Discovery**: GBS walks up from current directory until it finds `.gbs.yaml` (first found wins)

**Contains**:
- Project-specific tool overrides
- Project-specific profiles
- Tree-level repositories

**Example**:
- [`tree-config.yaml`](./tree-config.yaml) - Tree-level configuration

### 3. Project Config (`project.gbs.yaml`)

Individual project configuration.

**Location**: `project.gbs.yaml` (in project directory)

**Two approaches**:

#### A. Profile-based (Recommended)
Reference a profile from user/tree config:

```yaml
name: my_project
topcell: top
output_format: filelist
profile: simulation  # References profile from config files
# ...
```

**Example**: [`project-with-profile.yaml`](./project-with-profile.yaml)

#### B. Explicit configuration
Specify everything directly:

```yaml
name: my_project
topcell: top
output_format: filelist
filter_vars:
  sim: 1
backends:
  - backend: gbs.backend:GHDLBackend
    config: {...}
# ...
```

**Example**: [`project-explicit.yaml`](./project-explicit.yaml)

## Configuration Merge Order

Configurations are merged in this order (later wins):

1. **Plugin defaults** (built-in defaults like `ghdl: ghdl`)
2. **User config** (`~/.config/gbs.yaml`)
3. **Tree config** (`.gbs.yaml`)
4. **Project** (`project.gbs.yaml`)

### Merge Rules

Different sections have different merge semantics:

| Section | Merge Behavior |
|---------|---------------|
| **tools** | Extend list, but exact `(name, variant)` matches override |
| **profiles** | Override by name |
| **repositories** | Extend unconditionally (all sources merged) |

### Examples

#### Tools
```yaml
# User config defines:
tools:
  - name: ghdl
    variant: system
    config: {executable: ghdl}

# Tree config overrides:
tools:
  - name: ghdl
    variant: system  # Same name+variant -> OVERRIDES
    config: {executable: ./tools/ghdl}
```

Result: `ghdl:system` uses `./tools/ghdl`

#### Repositories
```yaml
# User config:
repositories:
  - path: ~/libs/common

# Tree config:
repositories:
  - path: libs/project

# Project:
repositories:
  - path: vendor/ip
```

Result: All three repositories are available (merged)

## Tool Identifiers

Tools are identified by `name` or `name:variant`:

```yaml
tools:
  - name: ghdl
    variant: system
    # identifier: "ghdl:system"

  - name: ghdl
    variant: llvm
    # identifier: "ghdl:llvm"
```

### Using Tool Identifiers in Backends

```yaml
backends:
  - backend: gbs.backend:GHDLBackend
    config:
      ghdl_tool: ghdl:llvm  # Use specific variant
```

If no variant specified, first matching name is used:
```yaml
config:
  ghdl_tool: ghdl  # Uses first ghdl variant (stable order)
```

## Profiles

Profiles are named configuration sets for common scenarios.

### Defining a Profile

```yaml
# In ~/.config/gbs.yaml or .gbs.yaml
profiles:
  simulation:
    filter_vars:
      sim: 1
      vendor: generic
    backends:
      - backend: gbs.backend:GHDLBackend
        config:
          output_dir: build/sim
          vhdl_std: "08"
    repositories:
      - path: ~/libs/simulation
```

### Using a Profile

```yaml
# In project.gbs.yaml
name: my_project
profile: simulation  # References the profile above
# ...
```

### Profile Restrictions

You **cannot** mix profiles with explicit configuration:

```yaml
# ❌ ERROR: Cannot specify both
profile: simulation
filter_vars:
  custom: 1
```

But you **can** add repositories alongside a profile:

```yaml
# ✅ OK: Repositories are always merged
profile: simulation
repositories:
  - path: vendor/ip  # Merged with profile repos
```

## Quick Start

### 1. Create User Config

Create `~/.config/gbs.yaml`:

```yaml
tools:
  - name: ghdl
    variant: system
    config:
      executable: ghdl

profiles:
  sim:
    filter_vars:
      sim: 1
    backends:
      - backend: gbs.backend:GHDLBackend
        config:
          output_dir: build
```

### 2. Use Profile in Project

Create `project.gbs.yaml`:

```yaml
name: my_project
topcell: top
output_format: filelist
profile: sim

root_library:
  name: mylib
  partitions:
    - name: rtl
      sources:
        - language: vhdl
          files: [src/top.vhd]
```

### 3. Build

```bash
gbs build
```

The config system automatically:
1. Discovers and loads `~/.config/gbs.yaml`
2. Expands the `sim` profile
3. Configures backends with proper tool paths
4. Merges all repositories

## Common Patterns

### Pattern 1: Multiple GHDL Installations

```yaml
tools:
  - name: ghdl
    variant: mcode
    config: {executable: /usr/bin/ghdl}

  - name: ghdl
    variant: llvm
    config: {executable: /opt/ghdl-llvm/bin/ghdl}

  - name: ghdl
    variant: gcc
    config: {executable: /opt/ghdl-gcc/bin/ghdl}

profiles:
  sim-fast:
    backends:
      - backend: gbs.backend:GHDLBackend
        config:
          ghdl_tool: ghdl:mcode  # Fast compile

  sim-perf:
    backends:
      - backend: gbs.backend:GHDLBackend
        config:
          ghdl_tool: ghdl:llvm  # Optimized execution
```

### Pattern 2: Vendor-Specific Profiles

```yaml
profiles:
  xilinx-sim:
    filter_vars: {sim: 1, vendor: xilinx}
    backends: [...]
    repositories:
      - path: ~/libs/xilinx

  intel-sim:
    filter_vars: {sim: 1, vendor: intel}
    backends: [...]
    repositories:
      - path: ~/libs/intel
```

### Pattern 3: Shared Team Config

Put `.gbs.yaml` at repository root with team-standard tools and profiles:

```yaml
# .gbs.yaml (in team repo root)
tools:
  - name: ghdl
    variant: team
    config:
      executable: ./tools/ghdl-3.0.0/bin/ghdl

profiles:
  team-sim:
    filter_vars: {sim: 1, ci: 0}
    backends: [...]
```

All team members get consistent configuration.

## Troubleshooting

### Config not found?

GBS looks for:
1. `~/.config/gbs.yaml` (user config)
2. `.gbs.yaml` (walking up from CWD)

Enable debug logging to see what's loaded:
```bash
gbs --debug build
```

### Tool not found?

Check tool is defined in config:
```bash
gbs --debug build  # Shows loaded tools
```

### Profile not found?

```
Error: Profile 'simulation' not found in configuration.
Available profiles: sim, synth
```

Check profile name matches exactly (case-sensitive).

### Merge conflicts?

Remember: profiles conflict with explicit `backends:`/`filter_vars:`, but repositories are always merged.

## Backend-Provided Filter Variables

Some backends automatically provide filter variables without needing explicit configuration.

### GHDL Backend

The GHDL backend automatically provides these filter variables:

| Variable | Value | Description |
|----------|-------|-------------|
| `target-usage` | `simulation` | GHDL is a simulation tool, so this is always set |
| `compiler` | `ghdl` | Identifies GHDL as the compiler |
| `ghdl-backend` | `mcode`/`gcc`/`llvm`/`jit` | GHDL backend type (detected at runtime) |
| `vhdl-version` | `1987`/`1993`/`2000`/`2002`/`2008`/`2019` | VHDL standard version (normalized from config) |

**Usage Examples**:

```yaml
# Backend-specific optimizations
sources:
  - language: vhdl
    filter: {ghdl-backend: llvm}  # Only for LLVM backend
    files: [optimized_impl.vhd]

  - language: vhdl
    filter: {ghdl-backend: mcode}  # Only for mcode backend
    files: [fast_compile_impl.vhd]

# Version-specific code
sources:
  - language: vhdl
    filter: {vhdl-version: "2008"}  # Only when using VHDL-2008
    files: [modern_features.vhd]

  - language: vhdl
    filter: {vhdl-version: "1993"}  # Only when using VHDL-93
    files: [legacy_compat.vhd]
```

**Notes**:
- You don't need to set `target-usage: simulation` in your profile when using GHDL - it's automatic
- `vhdl-version` is normalized from the `vhdl_std` config (e.g., "93c" → "1993", "08" → "2008")

## See Also

- Main GBS documentation - User guide and tutorials
