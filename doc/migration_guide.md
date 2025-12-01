# Migration Guide: Profile → Output-Group Format

## Overview

This guide helps you migrate existing GBS projects from the profile-based format to the new output-group format.

## When to Migrate

**Migrate now** if your project uses:
- ✅ GHDL backend for simulation
- ✅ Verilog-to-VHDL conversion
- ✅ Memory initialization

**Wait for migration** if your project uses:
- ⏳ Gowin FPGA synthesis (backend not yet converted)

## Migration Steps

### Step 1: Identify Current Configuration

Find your project's profile reference:

```yaml
# Old project.gbs.yaml
name: my_project
topcell: my_entity
profile: simulation  # ← Profile reference
```

Look up the profile in parent `.gbs.yaml`:

```yaml
# Parent .gbs.yaml
profiles:
  simulation:
    backends:
      - name: ghdl
        config:
          vhdl_standard: "93c"
          ghdl_tool: "ghdl:system"
```

### Step 2: Remove Profile Reference

Delete these lines from `project.gbs.yaml`:
```yaml
topcell: my_entity  # ← Move to output group
profile: simulation  # ← Delete
```

### Step 3: Add Output Section

Add the `output:` section with explicit configuration:

```yaml
output:
  - name: simulation
    topcell: my_entity  # ← Moved from root level
    filter_vars: {}
    backend_config:
      gbs.backend.ghdl:  # ← Backend module path
        vhdl_standard: "93c"
        ghdl_tool: "ghdl:system"
    outputs:
      - type: ghdl-simulator
        path: build/my_entity
```

### Step 4: Test the Build

```bash
cd your_project
rm -rf build
gbs project build
```

Verify the output in the build directory.

## Complete Examples

### Example 1: Simple GHDL Simulation

**Before:**
```yaml
name: hello
topcell: top
profile: simulation

root:
  name: top
  deps:
    - nsl_data.text
  sources:
    - language: vhdl
      files:
        - top.vhd
```

**After:**
```yaml
name: hello

root:
  name: top
  deps:
    - nsl_data.text
  sources:
    - language: vhdl
      files:
        - top.vhd

output:
  - name: simulation
    topcell: top
    filter_vars: {}
    backend_config:
      gbs.backend.ghdl:
        vhdl_standard: "93c"
        ghdl_tool: "ghdl:jit"
    outputs:
      - type: ghdl-simulator
        path: build/top
```

### Example 2: Multi-Library Project

**Before:**
```yaml
name: axi4_fifo
topcell: top
profile: simulation

root:
  name: top
  deps:
    - nsl_amba.axi4_stream
    - nsl_simulation.assertions
  sources:
    - language: vhdl
      files:
        - top.vhd
```

**After:**
```yaml
name: axi4_fifo

root:
  name: top
  deps:
    - nsl_amba.axi4_stream
    - nsl_simulation.assertions
  sources:
    - language: vhdl
      files:
        - top.vhd

output:
  - name: simulation
    topcell: top
    filter_vars: {}
    backend_config:
      gbs.backend.ghdl:
        vhdl_standard: "93c"
        ghdl_tool: "ghdl:system"
    outputs:
      - type: ghdl-simulator
        path: build/top
```

### Example 3: Multiple Outputs

If you have separate profiles for simulation and synthesis, combine them:

**Before (two separate projects):**
```yaml
# sim/project.gbs.yaml
name: my_project_sim
topcell: testbench
profile: simulation

# syn/project.gbs.yaml
name: my_project_syn
topcell: top
profile: synthesis
```

**After (one project with two output groups):**
```yaml
name: my_project

root:
  name: work
  sources:
    - language: vhdl
      files:
        - top.vhd
        - testbench.vhd

output:
  - name: simulation
    topcell: testbench
    filter_vars:
      target-usage: simulation
    backend_config:
      gbs.backend.ghdl:
        vhdl_standard: "2008"
    outputs:
      - type: ghdl-simulator
        path: build/sim/testbench

  - name: synthesis
    topcell: top
    filter_vars:
      target-usage: synthesis
    backend_config:
      gbs.backend.gowin:  # When Gowin backend is converted
        device: "GW2A-LV18"
    outputs:
      - type: bitstream
        path: build/impl/top.fs
```

## Backend-Specific Migration

### GHDL Backend

**Profile config:**
```yaml
backends:
  - name: ghdl
    config:
      vhdl_standard: "93c"
      ghdl_tool: "ghdl:system"
```

**Output-group config:**
```yaml
backend_config:
  gbs.backend.ghdl:
    vhdl_standard: "93c"
    ghdl_tool: "ghdl:system"
```

**Output type:** `ghdl-simulator`

### Verilog-to-VHDL Backend

**Profile config:**
```yaml
backends:
  - name: verilog_to_vhdl
    config:
      converter: "sv2v"
```

**Output-group config:**
```yaml
backend_config:
  gbs.backend.verilog_to_vhdl:
    converter: "sv2v"
```

**Input type:** `verilog`, **Output type:** `vhdl`

## Common Pitfalls

### 1. Forgetting to Remove Topcell from Root

❌ **Wrong:**
```yaml
topcell: top  # Still at root level!

output:
  - name: simulation
    topcell: top
```

✅ **Correct:**
```yaml
# No topcell at root level

output:
  - name: simulation
    topcell: top
```

### 2. Using Wrong Backend Module Path

❌ **Wrong:**
```yaml
backend_config:
  ghdl:  # Short name doesn't work!
    vhdl_standard: "93c"
```

✅ **Correct:**
```yaml
backend_config:
  gbs.backend.ghdl:  # Full module path
    vhdl_standard: "93c"
```

### 3. Missing Output Specifications

❌ **Wrong:**
```yaml
output:
  - name: simulation
    topcell: top
    backend_config:
      gbs.backend.ghdl:
        vhdl_standard: "93c"
    # Missing outputs!
```

✅ **Correct:**
```yaml
output:
  - name: simulation
    topcell: top
    backend_config:
      gbs.backend.ghdl:
        vhdl_standard: "93c"
    outputs:
      - type: ghdl-simulator
        path: build/top
```

## Verification

After migration, verify your build works:

```bash
# Clean build directory
rm -rf build

# Run build
gbs project build

# Check output
ls -la build/

# Run simulator (if GHDL)
./build/top --stop-time=100ns
```

Expected output:
```
Resolving dependencies...
Resolved X files in Y libraries
Discovering backends...
Discovered 4 backend(s) with N passes:
  - gbs.backend.ghdl (1 passes)
  ...

Planning build for 1 output group(s)...
  Output group 'simulation':
    Topcell: top
    Passes: 1
    Outputs: 1

Executing build plans...

Build complete!
  Output group 'simulation': X files processed
```

## Rollback

If you encounter issues, you can temporarily revert by:

1. Restore original `project.gbs.yaml`
2. Ensure parent `.gbs.yaml` has profile definitions
3. Use the old build system (still supported)

## Benefits of Migration

After migration, you gain:

- ✅ Self-contained project files (no external profile dependencies)
- ✅ Explicit configuration (easier to understand)
- ✅ Multiple output groups per project
- ✅ Better build planning and optimization
- ✅ Type-driven output specification
- ✅ Clearer build logs and progress reporting

## Next Steps

1. Migrate simple projects first (single file, GHDL-only)
2. Test thoroughly before migrating complex projects
3. Update CI/CD pipelines if needed
4. Share migration experiences to improve this guide

## Need Help?

- Check [Output-Group Format Guide](output_group_format.md) for format details
- Review converted examples in [`example/`](../example/README.md)
- Run with `--debug` flag for detailed logging

## Future

Once all backends are converted to the Pass interface:
- Profile system will be removed
- All projects must use output-group format
- Migration will be mandatory
