# Output-Group Format Guide

## Overview

The output-group format is the new declarative project configuration system in GBS. It replaces the profile-based approach with explicit, self-contained output specifications that don't rely on external profile definitions.

## Architecture

### Old System (Profile-Based)
```
Project → Profile Reference → Backend Configuration → Build
```

- Projects referenced profiles defined elsewhere
- Backend configuration scattered across profile definitions
- Implicit dependencies on global configuration

### New System (Output-Group)
```
Project → Output Groups → Build Plans → Execution
```

- Self-contained project definitions
- Explicit backend configuration per output group
- Declarative pass-based build planning

## Format Specification

### Basic Structure

```yaml
name: project_name

root:
  name: library_name
  deps:
    - dependency_library
  sources:
    - language: vhdl
      files:
        - file.vhd

output:
  - name: output_group_name
    topcell: entity_name
    filter_vars: {}
    backend_config: {}
    outputs:
      - type: output_type
        path: build/path
```

### Output Group Fields

#### Required Fields

- **`name`**: Unique identifier for the output group
- **`topcell`**: Top-level entity/module to build
- **`outputs`**: List of output file specifications (type + path)

#### Optional Fields

- **`filter_vars`**: Variables for conditional source filtering (dict)
- **`backend_config`**: Backend-specific configuration (dict, keyed by backend module path)
- **`require_passes`**: Only allow these passes (list of `backend:pass` names)
- **`exclude_passes`**: Exclude these passes (list of `backend:pass` names)
- **`require_backends`**: Only allow these backends (list of backend module paths)
- **`exclude_backends`**: Exclude these backends (list of backend module paths)

### Output File Specification

Each output file has:
- **`type`**: Output type that passes can produce (e.g., `ghdl-simulator`, `bitstream`)
- **`path`**: Destination path for the output file

The planner automatically selects passes that can produce the specified output types.

### Backend Configuration

Backend config is specified per output group using the backend's module path as the key:

```yaml
backend_config:
  gbs.backend.ghdl:
    vhdl_standard: "93c"
    ghdl_tool: "ghdl:jit"
  gbs.backend.gowin:
    device: "GW2A-LV18PG256C8/I7"
    speed_grade: "C8/I7"
```

## Complete Example

```yaml
name: axi4_stream_fifo

root:
  name: top
  deps:
    - nsl_data.bytestream
    - nsl_amba.axi4_stream
    - nsl_simulation.assertions
  sources:
    - language: vhdl
      files:
        - top.vhd

output:
  - name: simulation
    topcell: top
    filter_vars:
      target-usage: simulation
    backend_config:
      gbs.backend.ghdl:
        vhdl_standard: "93c"
        ghdl_tool: "ghdl:system"
    outputs:
      - type: ghdl-simulator
        path: build/top

  - name: synthesis
    topcell: top
    filter_vars:
      target-usage: synthesis
    backend_config:
      gbs.backend.gowin:
        device: "GW2A-LV18PG256C8/I7"
    outputs:
      - type: bitstream
        path: build/impl/top.fs
```

## Migration from Profile Format

### Old Format

```yaml
name: my_project
topcell: my_entity
profile: simulation  # References ../.gbs.yaml

root:
  name: work
  sources:
    - language: vhdl
      files:
        - my_entity.vhd
```

### New Format

```yaml
name: my_project

root:
  name: work
  sources:
    - language: vhdl
      files:
        - my_entity.vhd

output:
  - name: simulation
    topcell: my_entity
    filter_vars: {}
    backend_config:
      gbs.backend.ghdl:
        vhdl_standard: "93c"
        ghdl_tool: "ghdl:system"
    outputs:
      - type: ghdl-simulator
        path: build/my_entity
```

### Key Changes

1. **Remove** `topcell` from root level
2. **Remove** `profile` reference
3. **Add** `output:` section with explicit output groups
4. **Move** backend configuration inline under `backend_config`
5. **Specify** explicit output files with type and path

## Build Flow

### Planning Phase

1. **Backend Discovery**: Scan for available backends and their passes
2. **Output Analysis**: Determine required output types from `outputs` list
3. **Pass Selection**: Find passes that can produce required outputs
4. **Constraint Application**: Apply `require_passes`/`exclude_passes` filters
5. **Plan Validation**: Verify exactly one viable plan exists

### Execution Phase

1. **Dependency Resolution**: Resolve all source file dependencies
2. **Topcell Setup**: Set topcell from output group
3. **Pass Iteration**: Execute passes until build stabilizes
4. **Output Generation**: Create specified output files

## Advanced Features

### Multiple Output Groups

Build multiple targets from one project:

```yaml
output:
  - name: simulation
    topcell: testbench
    backend_config:
      gbs.backend.ghdl:
        vhdl_standard: "2008"
    outputs:
      - type: ghdl-simulator
        path: build/sim/testbench

  - name: synthesis
    topcell: top
    backend_config:
      gbs.backend.gowin:
        device: "GW2A-LV18"
    outputs:
      - type: bitstream
        path: build/impl/top.fs
```

### Pass Constraints

Control which passes are allowed:

```yaml
output:
  - name: simulation
    topcell: top
    require_passes:
      - gbs.backend.ghdl:simulate
    exclude_backends:
      - gbs.backend.gowin
    outputs:
      - type: ghdl-simulator
        path: build/top
```

### Filter Variables

Conditional source inclusion based on usage:

```yaml
output:
  - name: simulation
    topcell: top
    filter_vars:
      target-usage: simulation
      debug: "true"
    outputs:
      - type: ghdl-simulator
        path: build/top
```

## Compatibility

### Coexistence

Both formats are supported:
- Projects with `output:` section use **new planner + executor**
- Projects with `profile:` use **legacy backend system**

This allows gradual migration.

### Future

The profile system will be removed once all backends are converted to the Pass interface. Currently:
- ✅ **Converted**: GHDL, Verilog-to-VHDL, MemInit
- ⏳ **Pending**: Gowin (requires Pass conversion)

## See Also

- [Backend System Design](plan/backend_system_design.md) - Pass architecture details
- [Build System Refactoring](plan/build_system_refactoring.md) - Migration plan
- [Example Projects](../example/README.md) - Working examples
