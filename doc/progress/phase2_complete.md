# Phase 2 Complete: Repository Management & Dependency Resolution

**Date:** November 26, 2025
**Status:** ✅ Complete

## Summary

Phase 2 of the GBS implementation is complete. We have implemented a comprehensive dependency resolution system that evaluates filter conditions and builds a topologically-sorted dependency graph.

## Completed Components

### 2.1 Repository Indexing ✅

Already completed in Phase 1:
- Repository discovery with glob matching
- Library loader/indexer
- Partition discovery system
- YAML-based configuration loading

### 2.2 Filtering & Dependency Resolution ✅

**Core Resolver Module (`src/gbs/resolver.py`):**

#### PartitionRef
- Parses `library.partition` references
- Hashable for use in sets/dicts
- Validates format

#### DependencyResolver
- **Library Indexing**: Builds index of all available libraries from repositories
- **Filter Evaluation**: Evaluates FilterConditions with project's filter_vars context
- **Conditional Group Evaluation**: First-match-wins semantic for conditional groups
- **Recursive Evaluation**: Handles nested conditional groups
- **Partition Resolution**: Resolves individual partitions with all their sources and deps

#### Dependency Graph Building
- **Breadth-first traversal** starting from project root partitions
- **Cycle detection** during traversal
- **Deduplication**: Each partition resolved only once
- Returns complete dependency graph

#### Topological Sorting
- **Kahn's algorithm** for topological sort
- **Dependencies-first ordering**: Ensures correct build order
- **Cycle detection**: Fails if graph contains cycles

#### BuildFileSet Generation
- **Ordered libraries**: In dependency order
- **Ordered partitions**: Within each library
- **Filtered sources**: Only sources matching filter conditions

## Features Implemented

### 1. Filter-Aware Dependency Traversal

Dependencies are evaluated based on filter context:
```python
# Only includes xilinx deps when vendor="xilinx"
groups:
  vendor:
    - condition: vendor = "xilinx"
      deps: ["xilinx_lib.primitives"]
    - condition: vendor = "intel"
      deps: ["intel_lib.primitives"]
```

### 2. Dependency Graph (DAG)

Builds complete dependency graph:
- Nodes: Partition references
- Edges: Dependencies
- Validates: No cycles
- Resolves: All transitive dependencies

### 3. Cycle Detection

Two levels of cycle detection:
1. **During traversal**: Detects cycles while building graph
2. **During topological sort**: Detects any remaining cycles

Errors clearly identify which partitions are involved.

### 4. Topological Sorting

Produces correct build order:
- Dependencies compiled before dependents
- Handles diamond dependencies correctly
- Preserves partial order when multiple valid orders exist

### 5. BuildFileSet Generation

Creates final build file set:
- Libraries in dependency order
- Partitions within libraries
- Only sources that match filters

## Test Coverage

**73 tests total** (13 new resolver tests):

### Resolver Tests:
- ✅ PartitionRef parsing and validation
- ✅ Simple linear dependencies (A→B→C)
- ✅ Diamond dependencies (A→B,C; B,C→D)
- ✅ Cyclic dependency detection
- ✅ Missing partition error handling
- ✅ Conditional dependency resolution with filters
- ✅ Empty project error handling
- ✅ Library name conflict handling
- ✅ Integration with full dependency resolution

### Test Scenarios:

**Linear Dependencies:**
```
project.top → lib1.part1 → lib2.part2
Result: [lib2, lib1, project]
```

**Diamond Dependencies:**
```
project.root → libA.partA → libB.partB → libD.partD
                         ↘ libC.partC ↗
Result: [libD, libB, libC, libA, project]
```

**Conditional Dependencies:**
```
vendor="xilinx" → includes xilinx_lib.xilinx
vendor="intel"  → includes intel_lib.intel
```

## Architecture

### Data Flow

```
Project + Repositories
        ↓
DependencyResolver
        ↓
1. Index all libraries
2. Start from root partitions
3. Resolve each partition:
   - Evaluate filters with context
   - Extract matching sources
   - Extract matching deps
   - Recursively resolve deps
4. Build dependency graph (DAG)
5. Detect cycles
6. Topologically sort
7. Generate BuildFileSet
        ↓
BuildFileSet (ordered libraries, partitions, files)
```

### Key Algorithms

**Filter Evaluation:**
- Evaluate condition expression with context
- Return (sources, deps, groups) if match
- First-match wins in conditional groups
- Recursive for nested groups

**Graph Building:**
- BFS traversal from root partitions
- Track in_progress set for cycle detection
- Resolve each partition once
- Build graph: ref → ResolvedPartition

**Topological Sort (Kahn's Algorithm):**
1. Calculate in-degrees for all nodes
2. Start with nodes having zero in-degree (leaves)
3. Process nodes in order
4. Remove edges, add newly freed nodes
5. If all nodes processed: success
6. If nodes remain: cycle detected

## API

### Main Functions

```python
from gbs.resolver import resolve_project, DependencyResolver

# Simple API
build_set = resolve_project(project, repositories)

# Advanced API
resolver = DependencyResolver(project, repositories)
build_set = resolver.resolve()

# Access resolved graph
graph = resolver.build_dependency_graph(start_refs)
sorted_refs = resolver.topological_sort(graph)
```

### Errors

- `ResolutionError`: Base error for resolution failures
- `CyclicDependencyError`: Cycle detected in dependencies

## Examples

### Minimal Project Resolution

```python
from gbs.models import *
from gbs.resolver import resolve_project

# Create simple project
partition = Partition(
    name="top",
    groups=[ConditionalGroup(
        name="root",
        conditions=[FilterCondition(
            expression="default",
            sources=[SourceFile(Path("top.vhd"), Language.VHDL)]
        )]
    )]
)

library = Library(name="project")
library.add_partition(partition)

project = Project(
    name="my_project",
    root_library=library,
    toolsuite=ToolsuiteConfig("vivado", "gbs.backends.vivado"),
    topcell="top",
    output_format="bitstream"
)

# Resolve
build_set = resolve_project(project, [])

# Access results
for lib_name in build_set.libraries:
    for part_name in build_set.partitions[lib_name]:
        files = build_set.files[(lib_name, part_name)]
        print(f"{lib_name}.{part_name}: {len(files)} files")
```

### Conditional Resolution

```python
# Project with vendor-specific deps
project_xilinx = Project(
    name="test",
    root_library=root_lib,
    toolsuite=ToolsuiteConfig("vivado", "gbs.backends.vivado"),
    topcell="top",
    output_format="bitstream",
    filter_vars={"vendor": "xilinx"}  # Filter context
)

# Only xilinx-specific dependencies will be resolved
build_set = resolve_project(project_xilinx, repositories)
```

## Performance Characteristics

- **Time Complexity**: O(V + E) where V=partitions, E=dependencies
- **Space Complexity**: O(V) for graph storage
- **Cycle Detection**: O(V + E) worst case
- **Topological Sort**: O(V + E) using Kahn's algorithm

## What's Working

1. ✅ **Filter evaluation** integrated with dependency resolution
2. ✅ **Transitive dependencies** fully resolved
3. ✅ **Cycle detection** with clear error messages
4. ✅ **Topological sorting** for correct build order
5. ✅ **Diamond dependencies** handled correctly
6. ✅ **Conditional dependencies** based on filter vars
7. ✅ **Library conflict resolution** (first-wins strategy)
8. ✅ **Comprehensive error handling**

## Next Steps

Ready to move on to **Phase 3: Basic CLI & Repository Introspection**:

1. Wire up resolver to CLI commands
2. Implement `gbs repo list`
3. Implement `gbs repo query`
4. Implement `gbs repo validate`
5. Implement `gbs project show`
6. Implement `gbs project fileset`

## Files Added/Modified

### New Files:
- `src/gbs/resolver.py` (~400 lines)
- `tests/test_resolver.py` (~560 lines, 13 tests)

### Test Stats:
- **Total tests**: 73 (was 60)
- **New tests**: 13
- **All passing**: ✅

## Dependencies

No new external dependencies added. Uses:
- Standard library: `collections.deque`, `dataclasses`
- Existing GBS modules: `models`, `filters`, `logging`

## Notes

- Resolver is completely independent of I/O (doesn't load files)
- Works with in-memory model objects
- Deterministic ordering (important for reproducible builds)
- Clear separation between resolution logic and file loading
- Well-tested with realistic dependency scenarios
