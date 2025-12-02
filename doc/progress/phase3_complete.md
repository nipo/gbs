# Phase 3 Complete: Basic CLI & Repository Introspection

**Date:** November 26, 2025
**Status:** ✅ Complete

## Summary

Phase 3 of the GBS implementation is complete. We have implemented all introspection and query commands for repositories and projects. Users can now interact with GBS to inspect repositories, validate configurations, query dependencies, and view resolved build file sets - all before implementing the task system.

## Completed Commands

### Repository Commands

#### `gbs repo list <path>`
Lists all libraries, partitions, and file counts in a repository.

**Example:**
```bash
$ gbs repo list tests/fixtures/repositories/simple_repo.gbs.yaml

Repository: simple_repository
Description: A simple test repository
Root: tests/fixtures/repositories

Libraries (1):
  simple_library
    Description: A simple test library
    Partitions (1):
      simple (2 source files)
```

#### `gbs repo validate <path>`
Validates repository structure and reports errors/warnings.

**Example:**
```bash
$ gbs repo validate tests/fixtures/repositories/simple_repo.gbs.yaml

Repository: simple_repository
Libraries: 1
Partitions: 1

✓ Repository is valid
```

**Validation checks:**
- YAML syntax correctness
- Repository structure
- Library presence
- Partition content
- Errors for missing required elements
- Warnings for empty libraries/partitions

#### `gbs repo query <path> -p <library.partition> [-f var=value]`
Queries dependency tree for a specific partition with optional filter variables.

**Example:**
```bash
$ gbs repo query repo.yaml -p simple_library.simple

Query: simple_library.simple

Dependency tree (1 partitions):

→ simple_library.simple
    Sources: 2 files

Build order: simple_library.simple
```

**With filters:**
```bash
$ gbs repo query repo.yaml -p mylib.part -f vendor=xilinx -f family=7series
```

### Project Commands

#### `gbs project show <project_file>`
Shows complete project configuration.

**Example:**
```bash
$ gbs project show tests/fixtures/projects/simple.gbs.yaml

Project: simple_project
Description: A simple test project

Configuration:
  Topcell: top
  Output format: bitstream

Toolsuite:
  Name: vivado
  Backend: gbs.backends.vivado
  Configuration:
    version: 2023.1

Filter variables:
  family = 7series
  vendor = xilinx

Root library:
  Name: project_root
  Description: Project root library
  Partitions: 1
    - top
```

#### `gbs project fileset <project_file> [-r <repo>]`
Shows resolved build file set after dependency resolution.

**Example:**
```bash
$ gbs project fileset project.gbs.yaml -r repo1.gbs.yaml -r repo2.gbs.yaml

Project: simple_project
Topcell: top

Build file set (4 files):

Library: simple_library
  Partition: simple (2 files)
    - file1.vhd (vhdl)
    - file2.vhd (vhdl)

Library: project_root
  Partition: top (2 files)
    - top.vhd (vhdl)
    - constraints.xdc (other)

Build order: simple_library → project_root
```

## Features Implemented

### 1. Repository Introspection
- **List**: Complete hierarchy view (repo → libraries → partitions → files)
- **Validate**: Structural validation with errors and warnings
- **Query**: Dependency traversal with filter evaluation

### 2. Project Introspection
- **Show**: Complete project configuration display
- **Fileset**: Full dependency resolution and build order

### 3. Error Handling
- Clear error messages for:
  - Missing files
  - Invalid YAML
  - Malformed configurations
  - Missing partitions
  - Invalid filter syntax
  - Resolution errors
- Proper exit codes (0 for success, 1 for errors)
- Errors to stderr, output to stdout

### 4. Output Formatting
- Human-readable output
- Hierarchical structure with indentation
- File counts and statistics
- Unicode symbols (✓, ✗, ⚠, →)
- Sorted output for consistency

### 5. Filter Support
- Parse filter variables from command line (`-f var=value`)
- Auto-detect integer vs string values
- Pass to dependency resolver
- Show applied filters in output

## Implementation Details

### CLI Structure
```
gbs [global-options] <command> [command-options] [arguments]

Global options:
  -v, --verbose    Enable verbose logging
  -d, --debug      Enable debug logging
  --log-dir DIR    Custom log directory

Commands:
  repo list <path>                     List repository contents
  repo validate <path>                 Validate repository
  repo query <path> -p <ref> [-f ...]  Query dependencies
  project show <file>                  Show project config
  project fileset <file> [-r <repo>]   Show build file set
```

### Integration Points

**With Loaders:**
- Uses `load_repository()` and `load_project()`
- Handles `LoadError` exceptions
- Proper error reporting

**With Resolver:**
- Uses `DependencyResolver` for queries
- Uses `resolve_project()` for fileset
- Shows dependency graphs and build order

**With Logging:**
- Respects global verbosity settings
- Logs errors appropriately
- Shows log file location in verbose mode

## Testing

### Manual Testing
All commands tested with real fixtures:
- ✅ `gbs repo list` - Shows repository hierarchy
- ✅ `gbs repo validate` - Validates structure
- ✅ `gbs repo query` - Queries dependencies
- ✅ `gbs project show` - Shows configuration
- ✅ `gbs project fileset` - Shows resolved files

### Test Fixtures Used
- `tests/fixtures/repositories/simple_repo.gbs.yaml`
- `tests/fixtures/libraries/simple_lib.gbs.yaml`
- `tests/fixtures/partitions/simple.gbs.yaml`
- `tests/fixtures/projects/simple.gbs.yaml`

### All Unit Tests Pass
```bash
$ pytest tests/ -v
============================== 73 passed in 0.07s ==============================
```

## Use Cases

### 1. Repository Exploration
```bash
# See what's in a repository
gbs repo list my_repo.gbs.yaml

# Validate before using
gbs repo validate my_repo.gbs.yaml
```

### 2. Dependency Analysis
```bash
# Check what a partition depends on
gbs repo query repo.yaml -p mylib.mypart

# Check with specific vendor/target
gbs repo query repo.yaml -p mylib.mypart -f vendor=xilinx -f target=fpga
```

### 3. Project Verification
```bash
# Check project configuration
gbs project show my_project.gbs.yaml

# See what will be built
gbs project fileset my_project.gbs.yaml -r repo1.gbs.yaml
```

### 4. Build Planning
```bash
# Verify build order and file counts
gbs project fileset project.gbs.yaml -r repo.gbs.yaml

# Check different configurations
gbs project fileset project_xilinx.gbs.yaml -r vendor_libs.gbs.yaml
gbs project fileset project_intel.gbs.yaml -r vendor_libs.gbs.yaml
```

## API Usage (Programmatic)

While these are CLI commands, the underlying functionality is available programmatically:

```python
from gbs.loaders import load_repository, load_project
from gbs.resolver import resolve_project

# Load and inspect
repo = load_repository(Path("repo.gbs.yaml"))
print(f"Libraries: {list(repo.libraries.keys())}")

# Resolve project
project = load_project(Path("project.gbs.yaml"))
build_set = resolve_project(project, [repo])
print(f"Build order: {build_set.libraries}")
```

## User Experience Improvements

1. **Clear Output**: Well-formatted, hierarchical display
2. **Progress Feedback**: Shows what's being processed
3. **Error Messages**: Specific, actionable error messages
4. **Exit Codes**: Proper exit codes for scripting
5. **Help Text**: Comprehensive help for each command
6. **Examples**: Usage examples in help text

## Files Modified

### `src/gbs/cli.py`
- Added full implementations for all commands
- ~200 lines of command logic
- Error handling and output formatting
- Integration with loaders and resolver

## Dependencies

No new dependencies added. Uses:
- `asyncclick` (already in Phase 1)
- `gbs.loaders` (Phase 1)
- `gbs.resolver` (Phase 2)
- `gbs.logging` (Phase 1)

## Command Output Examples

### Repository List (Detailed)
```
Repository: my_repository
Description: Main hardware IP repository
Root: /path/to/repo

Libraries (3):
  utils
    Description: Utility components
    Partitions (5):
      fifo (3 source files)
      counter (2 source files)
      ...

  interfaces
    Description: Standard interfaces
    Partitions (10):
      axi4 (15 source files)
      uart (8 source files)
      ...

  dsp
    Partitions (3):
      fir_filter (12 source files)
      ...
```

### Dependency Query
```
Query: dsp.fir_filter
Filters: {'vendor': 'xilinx', 'family': '7series'}

Dependency tree (4 partitions):

→ dsp.fir_filter
    Sources: 12 files
    Depends on: utils.fifo, utils.counter

  utils.fifo
    Sources: 3 files

  utils.counter
    Sources: 2 files

  xilinx_lib.primitives
    Sources: 5 files

Build order: utils.fifo → utils.counter → xilinx_lib.primitives → dsp.fir_filter
```

## What's Working

1. ✅ **Full repository introspection** - list, validate, query
2. ✅ **Full project introspection** - show, fileset
3. ✅ **Filter-aware queries** - conditional deps work correctly
4. ✅ **Multi-repository support** - project fileset with multiple repos
5. ✅ **Error handling** - clear messages, proper exit codes
6. ✅ **Logging integration** - respects verbosity settings
7. ✅ **User-friendly output** - formatted, sorted, readable

## Next Steps

We've now completed Phases 1-3! The remaining phases are:

- **Phase 4**: Task Management & Build Infrastructure
- **Phase 5**: Backend System
- **Phase 6**: Build Commands & Advanced CLI
- **Phase 7**: Testing & Documentation
- **Phase 8**: Polish & Advanced Features

## Notes

- All commands work without the task system (as designed)
- Commands are fast (no heavy I/O, just loading YAML)
- Output is deterministic (sorted for consistency)
- Commands compose well with shell scripting
- Foundation ready for build system implementation

## Testing Notes

Commands tested with:
- Valid repositories and projects
- Empty repositories
- Missing files (proper error handling)
- Invalid YAML (proper error messages)
- Filter variables (both string and integer)
- Multiple repositories

All edge cases handled gracefully with clear error messages.
