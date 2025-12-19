# NSL CDC Constraint Generation Demo

This project demonstrates the NSL CDC (Clock Domain Crossing) constraint generation backend for GBS.

## What It Does

The NSL CDC backend (`gbs.plugin.nsl.cdc.NslCdcBackend`):

1. **Runs after Gowin synthesis**
2. **Parses the generated netlist** for NSL timing-insensitive patterns:
   - `tig_reg_clr`: Registers with timing-ignored CLEAR pins
   - `tig_reg_pre`: Registers with timing-ignored PRE pins
   - `tig_reg_q`: Registers with timing-ignored outputs (O/Q pins)
   - `tig_static_reg`: Static registers (constant after initialization)
   - `cross_region_reg`: Clock domain crossing registers
   - `async_net`: Asynchronous nets
3. **Generates SDC constraints** (`set_false_path` commands)
4. **Adds the SDC file to the fileset** as `gowin-sdc`
5. **Gowin backend picks it up dynamically** and includes it in PnR

## Multi-Backend Workflow

```
Iteration 1:
- Gowin: Creates all tasks (init, synthesis, constraint aggregation, PnR)
- Gowin: Runs synthesis → generates netlist.vg
- Gowin: Adds netlist to fileset

Iteration 2:
- NSL CDC: Detects netlist in fileset
- NSL CDC: Parses netlist for TIG patterns
- NSL CDC: Generates nsl_cdc_constraints.sdc
- NSL CDC: Adds SDC to fileset as "gowin-sdc"
- Gowin: Detects new SDC file via _update_constraint_inputs()
- Gowin: Dynamically adds it to timing constraint aggregation task
- Build system: Re-runs aggregation task with new input
- Gowin: Runs PnR with NSL CDC constraints

Iteration 3:
- No new files, converged!
```

## Test Design

The demo VHDL file (`nsl_cdc_test.vhd`) uses NSL-style signal naming to test the constraint generation:

- `async_net_data`, `async_net_q`: Asynchronous nets
- `cross_region_reg_d`, `cross_region_reg_q`: CDC boundary registers
- `tig_reg_q_internal`, `tig_reg_q_o`: TIG outputs
- `tig_static_reg_q`, `tig_static_reg_d`: Static registers
- `tig_reg_clr_q`, `tig_reg_pre_q`: TIG clear/preset registers

## Running the Demo

```bash
cd /Users/nipo/projects/gbs/example/nsl_cdc_demo
gbs build
```

Expected output:
- 3 iterations (Gowin setup → NSL CDC → converged)
- Generated SDC file at `build/nsl_cdc_constraints.sdc`
- Final bitstream at `build/impl/pnr/nsl_cdc_test.fs`

## Checking Generated Constraints

```bash
cat build/nsl_cdc_constraints.sdc
```

Should show `set_false_path` constraints for each TIG pattern found in the netlist.

## Integration with Real NSL Code

For actual NSL projects:

1. Replace the VHDL test file with real NSL source code
2. Configure NSL compilation in the project
3. The NSL compiler will generate VHDL/Verilog with proper TIG naming
4. The CDC backend will automatically detect and constrain them

## Auto-Registration

The NSL CDC backend is **automatically registered** when:

1. The NSL plugin is installed (`gbs-plugin-nsl` package)
2. Any Gowin backend is present in the build

**No manual configuration needed!** The backend auto-registers via the GBS plugin system.

How it works:
- NSL plugin registers an auto-backend provider
- Provider checks if Gowin backend is loaded
- If yes, automatically instantiates and includes NslCdcBackend
- Backend only runs when a gowin-netlist file is present

It's **completely safe** for all projects:
- Only activates when Gowin backend is present
- Only generates constraints when netlist contains NSL patterns
- Harmless for non-NSL projects (no patterns = empty SDC file)
- Won't interfere with Xilinx, Intel, or simulation builds

## Files Created

**Backend Plugin**:
- `/Users/nipo/projects/nsl_clean/build/gbs/gbs/plugin/nsl/cdc.py`: CDC backend implementation
- `/Users/nipo/projects/nsl_clean/build/gbs/gbs/plugin/nsl/__init__.py`: Auto-registration via `register()` function

**Profile Configuration**:
- `/Users/nipo/projects/gbs/example/.gbs.yaml`: Gowin profile (NslCdcBackend auto-registers, no manual config needed)

**Plugin Entry Points** (`/Users/nipo/projects/nsl_clean/build/gbs/pyproject.toml`):
- `gbs.backends` entry point for auto-discovery
- `gbs.loaders` entry point for NSL tree loader

**Demo Project**:
- `nsl_cdc_test.vhd`: Test VHDL with NSL naming patterns
- `project.gbs.yaml`: GBS project configuration
- `pins.cst`: Pin constraints
- `README.md`: This file

## Reference

Original shell script: `/Users/nipo/projects/nsl_clean/build/support/gowin_sdc_auto.sh`

The Python backend replicates the same constraint generation logic but integrates
with the GBS multi-backend workflow and dynamic task system.
