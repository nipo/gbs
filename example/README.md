# GBS Example Projects

This directory contains example projects demonstrating GBS features and the configuration system.

## ⚠️ Format Migration in Progress

Examples are being migrated from profile-based to output-group format:
- **✅ Converted**: hello, amba/axi4_stream_fifo, inet/ethernet
- **⏳ Pending**: tang_60k/blink, nsl_cdc_demo (require Gowin backend conversion)

## Shared Configuration

All example projects share a common configuration defined in `.gbs.yaml`:

- **Tools**: GHDL (system and JIT variants) and GCC from PATH
- **Repository**: NSL library (shared VHDL library)

## New Output-Group Format (Converted Examples)

The new format replaces profile references with explicit output groups:

```yaml
name: project_name

root:
  name: root
  sources:
    - language: vhdl
      files:
        - file.vhd

output:
  - name: simulation
    topcell: top
    filter_vars: {}
    backend_config:
      gbs.backend.ghdl:
        vhdl_standard: "1993"
        ghdl_tool: "ghdl:system"
    outputs:
      - type: ghdl-simulator
        path: build/top
```

**Key changes**:
- No `profile:` reference
- No root-level `topcell:`
- Explicit `output:` section with OutputGroup(s)
- Backend config specified per output group
- Output files explicitly declared

### Old Profile Format (Pending Examples)

Legacy examples still using profiles:

```yaml
name: project_name
topcell: top
profile: simulation  # References ../.gbs.yaml

root:
  name: root
  sources: [...]
```

These will be converted once their backends are migrated to Pass implementation.

## Example Projects

### ✅ Converted Examples (New Format)

#### 1. Hello (`hello/`)

**Simple example** demonstrating basic GBS usage.

**Features**:
- Single VHDL file
- NSL library dependencies (text, assertions)
- ✅ **Output-group format** with GHDL JIT backend

**Build**:
```bash
cd hello
gbs build  # Uses new planner + executor
```

#### 2. AXI4 Stream FIFO (`amba/axi4_stream_fifo/`)

**AMBA example** demonstrating AXI4-Stream interface.

**Features**:
- Complex dependency tree (63 files, 8 libraries)
- NSL AMBA, data, and simulation libraries
- ✅ **Output-group format** with GHDL system backend

**Build**:
```bash
cd amba/axi4_stream_fifo
gbs build  # Uses new planner + executor
```

#### 3. Ethernet (`inet/ethernet/`)

**Network example** demonstrating Ethernet protocol implementation.

**Features**:
- Large dependency tree (150 files, 11 libraries)
- NSL inet, MII, and BNOC libraries
- Multi-file root library
- ✅ **Output-group format** with GHDL system backend

**Build**:
```bash
cd inet/ethernet
gbs build  # Uses new planner + executor
```

### ⏳ Pending Examples (Old Format - Gowin Backend)

#### 4. Tang Primer 60K Blink (`tang_60k/blink/`)

**FPGA synthesis example** for Tang Primer 60K board.

**Features**:
- Gowin FPGA backend (synthesis + bitstream)
- VHDL and constraint files
- ⏳ **Profile format** (pending Gowin backend conversion)

**Build**:
```bash
cd tang_60k/blink
gbs build  # Uses old profile system
```

#### 5. NSL CDC Demo (`nsl_cdc_demo/`)

**Clock domain crossing demo** with constraint generation.

**Features**:
- Gowin FPGA backend
- NSL CDC constraint generation
- ⏳ **Profile format** (pending Gowin backend conversion)

**Build**:
```bash
cd nsl_cdc_demo
gbs build  # Uses old profile system
```

## New Architecture Benefits

The output-group format provides:

1. **Explicitness**: All build configuration visible in project file
2. **Flexibility**: Multiple output groups per project (simulation + synthesis)
3. **Pass-based**: Uses new declarative Pass architecture
4. **Type-driven**: Outputs specified by type (ghdl-simulator, bitstream, etc.)
5. **Backend-config**: Per-output-group backend configuration

## Repository Path

The `.gbs.yaml` file references the NSL library at:
```yaml
repositories:
  - path: /Users/nipo/projects/nsl_clean
    loader: gbs.plugin.nsl.tree
```

**Note**: You may need to adjust this path to match your local NSL installation.

## Build Results

| Example | Format | Files | Libraries | Build Output |
|---------|--------|-------|-----------|--------------|
| hello | ✅ New | 7 | 3 | 15 files processed |
| axi4_stream_fifo | ✅ New | 63 | 8 | 127 files processed |
| ethernet | ✅ New | 150 | 11 | 301 files processed |
| tang_60k/blink | ⏳ Old | - | - | Pending conversion |
| nsl_cdc_demo | ⏳ Old | - | - | Pending conversion |

## See Also

- [Configuration Examples](../doc/examples/) - More configuration patterns
- Main GBS documentation - User guide and tutorials
