# GBS YAML Schema Examples

This document provides examples of GBS YAML configuration files.

## Partition Definition

**File naming:** Partition name is derived from the filename.
- `my_partition.gbs.yaml` → partition name = `my_partition`
- The `.gbs.yaml` extension is standard

**File structure:** The root of a partition file is a `FilterCondition` with an implicit `condition: default`.

### Simple Partition

File: `some_partition.gbs.yaml`

```yaml
# Root-level sources and deps (implicit condition: default)
deps:
  - dep_library.other_partition
sources:
  - language: vhdl
    files:
      - some_file_a.vhd
      - some_file_b.vhd
```

### Partition with Conditional Groups

File: `vendor_specific.gbs.yaml`

```yaml
# Root-level always-included items (implicit condition: default)
deps:
  - base_lib.utils
sources:
  - language: vhdl
    files:
      - common.vhd

# Conditional groups for vendor selection
groups:
  vendor_selection:
    - condition: vendor = "xilinx"
      deps:
        - xilinx_lib.primitives
      sources:
        - language: vhdl
          files:
            - xilinx_impl.vhd
      # Nested groups for hierarchical conditions
      groups:
        family_selection:
          - condition: family = "7series"
            sources:
              - language: vhdl
                files:
                  - xilinx_7series.vhd
          - condition: family matches "ultrascale.*"
            sources:
              - language: vhdl
                files:
                  - xilinx_ultrascale.vhd
          - condition: default
            sources:
              - language: vhdl
                files:
                  - xilinx_generic.vhd

    - condition: vendor = "intel"
      deps:
        - intel_lib.primitives
      sources:
        - language: vhdl
          files:
            - intel_impl.vhd

    - condition: default
      sources:
        - language: vhdl
          files:
            - generic_impl.vhd

  # Independent conditional group
  simulation_mode:
    - condition: sim = 1
      sources:
        - language: vhdl
          files:
            - sim_specific.vhd
    - condition: default
      sources:
        - language: vhdl
          files:
            - synth_specific.vhd
```

## Library Definition

File: `library.gbs.yaml`

```yaml
# Library metadata
name: my_library
description: Optional library description

# Partitions to include
partitions:
  # Method 1: Explicit list (without extension)
  # partition1 will load partition1.gbs.yaml
  - partition1
  - partition2
  - subdir/partition3

  # Method 2: Glob patterns (for auto-discovery)
  - pattern: "*/partition*.gbs.yaml"
  - pattern: "**/partition.gbs.yaml"
```

## Repository Definition

File: `repository.gbs.yaml` (or `gbs.yaml` at repo root)

```yaml
# Repository metadata
name: my_repository
description: Optional repository description

# Libraries to index
libraries:
  # Method 1: Explicit paths
  - path: lib1
  - path: lib2

  # Method 2: Glob patterns (for auto-discovery)
  - pattern: "*/library.gbs.yaml"
  - pattern: "libs/**/library.gbs.yaml"
```

## Project Definition

File: `project.gbs.yaml` (or any user-specified name)

```yaml
# Project metadata
name: my_project
description: Optional project description

# Target configuration
toolsuite:
  name: vivado
  backend: gbs.backends.vivado
  config:
    version: "2023.1"
    part: xc7a100tcsg324-1

# Build configuration
topcell: top_module
output_format: bitstream

# Filter variables for dependency resolution
filter_vars:
  vendor: xilinx
  family: 7series
  sim: 0

# Root library (project-specific code)
root_library:
  name: project_root
  description: Project-specific code

  partitions:
    # Define partitions inline
    - name: top
      groups:
        common:
          - condition: default
            deps:
              - my_library.partition1
              - other_lib.utils
            sources:
              - language: vhdl
                files:
                  - top.vhd
              - language: vhdl
                variant: "2008"
                files:
                  - modern_code.vhd
              - language: other
                files:
                  - constraints.xdc

# External repositories to load
repositories:
  - path: /path/to/repo1
  - path: ../relative/repo2
```

## Source File Specification

Sources are specified with language and optional variant:

```yaml
sources:
  - language: vhdl
    files:
      - file1.vhd
      - file2.vhd

  - language: vhdl
    variant: "2008"
    files:
      - modern_file.vhd

  - language: verilog
    files:
      - module.v

  - language: systemverilog
    files:
      - module.sv

  - language: other
    files:
      - constraints.xdc
      - timing.sdc
```

## Minimal Examples

### Minimal Partition (minimal.gbs.yaml)
```yaml
# Just sources, no deps or conditional groups
sources:
  - language: vhdl
    files:
      - code.vhd
```

### Minimal Library
```yaml
name: simple_library
partitions:
  - minimal  # Will load minimal.gbs.yaml
```

### Minimal Project
```yaml
name: simple_project
toolsuite:
  name: generic
  backend: gbs.backends.generic
topcell: top
output_format: filelist
root_library:
  name: root
  partitions:
    - name: top
      sources:
        - language: vhdl
          files:
            - top.vhd
```
