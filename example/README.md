# GBS Example Projects

This directory contains example projects demonstrating GBS features and the configuration system.

## Shared Configuration

All example projects share a common configuration defined in `.gbs.yaml`:

- **Profile**: `simulation` - Standard simulation setup with GHDL
- **Tools**: GHDL and GCC from PATH
- **Repository**: NSL library (shared VHDL library)

### Tree Configuration (`.gbs.yaml`)

The `.gbs.yaml` file in this directory provides:

```yaml
profiles:
  simulation:
    backends:
      - backend: gbs.backend:GHDLBackend
        config:
          output_dir: build
          vhdl_std: 93c
```

All example projects reference this profile with `profile: simulation`, eliminating configuration duplication.

## Example Projects

### 1. Hello (`hello/`)

**Simple example** demonstrating basic GBS usage.

**Features**:
- Single VHDL file
- NSL library dependencies (text, assertions)
- Profile-based configuration

**Build**:
```bash
cd hello
gbs build
```

### 2. AXI4 Stream FIFO (`amba/axi4_stream_fifo/`)

**AMBA example** demonstrating AXI4-Stream interface.

**Features**:
- Complex dependency tree (63 files, 8 libraries)
- NSL AMBA, data, and simulation libraries
- Profile-based configuration

**Build**:
```bash
cd amba/axi4_stream_fifo
gbs build
```

### 3. Ethernet (`inet/ethernet/`)

**Network example** demonstrating Ethernet protocol implementation.

**Features**:
- Large dependency tree (150 files, 11 libraries)
- NSL inet, MII, and BNOC libraries
- Multi-file root library
- Profile-based configuration

**Build**:
```bash
cd inet/ethernet
gbs build
```

## Configuration Benefits

By using a shared `.gbs.yaml` configuration:

1. **DRY Principle**: Configuration defined once, used everywhere
2. **Consistency**: All examples use identical build settings
3. **Maintainability**: Update one file to change all examples
4. **Simplicity**: Project files contain only project-specific information

### Before (Duplicate Configuration)

Each project repeated:
```yaml
filter_vars:
  some-config: some-value
backends:
  - backend: gbs.backend:GHDLBackend
    config:
      output_dir: build
      vhdl_std: 93c
repositories:
  - path: /Users/nipo/projects/nsl_clean
    loader: gbs.plugin.nsl.tree
```

### After (Profile-Based)

Each project simply references the shared profile:
```yaml
profile: simulation
```

The profile and repository are automatically loaded from the tree-level `.gbs.yaml`.

## Repository Path

The `.gbs.yaml` file references the NSL library at:
```yaml
repositories:
  - path: /Users/nipo/projects/nsl_clean
    loader: gbs.plugin.nsl.tree
```

**Note**: You may need to adjust this path to match your local NSL installation.

## Build Results

| Example | Files | Libraries | Build Output |
|---------|-------|-----------|--------------|
| hello | 7 | 3 | 15 files processed |
| axi4_stream_fifo | 63 | 8 | 127 files processed |
| ethernet | 150 | 11 | 301 files processed |

## See Also

- [Configuration Examples](../doc/examples/) - More configuration patterns
- Main GBS documentation - User guide and tutorials
