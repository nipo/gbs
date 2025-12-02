# Partition YAML Format Refactoring

**Date:** November 26, 2025
**Status:** ✅ Complete

## Summary

Simplified the partition YAML format to make it more intuitive and concise. The partition name is now derived from the filename, and the root of the YAML is a `FilterCondition` (similar to the design in `filter_design.md`).

## Changes Made

### 1. Standardized File Extension

**Decision:** Use `.gbs.yaml` as the standard partition file extension
- Clear that files are YAML format
- Better editor support (syntax highlighting)
- Consistent across all partition files

### 2. Partition Name from Filename

**Before:**
```yaml
name: my_partition
description: Optional description
groups: [...]
```

**After:**
- Filename: `my_partition.gbs.yaml`
- Partition name automatically derived: `my_partition`
- No `name` or `description` fields in YAML

### 3. Root is FilterCondition

**Before (verbose):**
```yaml
name: partition_name
description: Description here
groups:
  common:
    - condition: default
      sources:
        - language: vhdl
          files:
            - file.vhd
      deps:
        - other_lib.partition
```

**After (minimal):**
```yaml
# Root is a FilterCondition with implicit "condition: default"
sources:
  - language: vhdl
    files:
      - file.vhd
deps:
  - other_lib.partition
```

### 4. Conditional Groups Still Supported

Nested conditional groups work the same way:

```yaml
# Root level (always included)
sources:
  - language: vhdl
    files:
      - common.vhd
deps:
  - base_lib.utils

# Conditional groups
groups:
  vendor_selection:
    - condition: vendor = "xilinx"
      sources:
        - language: vhdl
          files:
            - xilinx.vhd
    - condition: default
      sources:
        - language: vhdl
          files:
            - generic.vhd
```

## Code Changes

### Data Model

**`src/gbs/models.py`**
- Removed `description` field from `Partition` class
- Updated docstring to reflect that partition name comes from filename
- No YAML representation for Partition object itself

### Loaders

**`src/gbs/loaders.py`**
- `load_partition()`: Completely refactored
  - Extracts partition name from `path.stem` (basename without extension)
  - Parses root YAML as FilterCondition (sources, deps, groups)
  - Wraps in a ConditionalGroup named "root"
  - Creates Partition with single root group

- `load_library()`: Updated
  - Auto-appends `.gbs.yaml` extension if not present
  - Removed fallback to `.gbs` extension

- `load_project()`: Updated
  - Inline partitions follow same format as external partition files
  - Root is FilterCondition, not named groups

### Tests

**All 60 tests updated and passing:**
- Updated test fixtures to new format
- Updated test expectations (partition name from filename)
- Updated assertions (root group structure)
- Added test for empty partition files

### Documentation

**`doc/yaml_schema_examples.md`**
- Complete rewrite of partition examples
- Added file naming conventions
- Clarified minimal vs. full syntax
- Updated all examples to new format

## Benefits

1. **Simpler syntax:** Minimal partition = just sources and deps
2. **Less duplication:** Name comes from filename (single source of truth)
3. **Cleaner files:** No metadata fields cluttering the content
4. **Consistent:** Matches the design philosophy from `filter_design.md`
5. **More intuitive:** Root level = default/common code, nested groups = conditional

## Examples

### Minimal Partition

**File:** `uart.gbs.yaml`
```yaml
sources:
  - language: vhdl
    files:
      - uart_tx.vhd
      - uart_rx.vhd
deps:
  - utils.fifo
```

### Partition with Conditionals

**File:** `memory_controller.gbs.yaml`
```yaml
# Common sources (always included)
sources:
  - language: vhdl
    files:
      - memory_controller_base.vhd

# Vendor-specific implementations
groups:
  vendor:
    - condition: vendor = "xilinx"
      sources:
        - language: vhdl
          files:
            - xilinx_memory_controller.vhd
      deps:
        - xilinx_lib.primitives

    - condition: vendor = "intel"
      sources:
        - language: vhdl
          files:
            - intel_memory_controller.vhd
      deps:
        - intel_lib.primitives
```

## Migration Guide

For existing partition files:

1. **Rename file** to match partition name: `partition_name.gbs.yaml`
2. **Remove** `name:` and `description:` fields
3. **Flatten** the root level:
   - Move sources/deps from `groups.common[0]` to root level
   - Keep conditional groups in `groups` as before

**Before:**
```yaml
name: my_partition
groups:
  common:
    - condition: default
      sources: [...]
      deps: [...]
```

**After (file: `my_partition.gbs.yaml`):**
```yaml
sources: [...]
deps: [...]
```

## Testing

- ✅ All 60 tests pass
- ✅ Test coverage for new format
- ✅ Test coverage for edge cases (empty partitions)
- ✅ Integration tests with libraries and projects

## Backward Compatibility

⚠️ **Breaking change** - Old partition format is not supported
- As specified in requirements, backward compatibility is not needed
- This is a fresh implementation with no legacy code
