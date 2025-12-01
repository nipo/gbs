# Build System Refactoring - Progress Report

## Summary

Phases 1-4 of the build system refactoring have been completed and committed.
The new pass-based architecture is implemented but not yet integrated into
the build execution flow.

## Completed Phases

### Phase 1: Core Infrastructure ✓ (commit b2e8602)
- ✅ Pass and Backend base classes (`gbs/model/passes.py`)
- ✅ OutputFile and OutputGroup classes
- ✅ Project.output_groups field added
- ✅ Comprehensive unit tests (8 new tests)
- ✅ All 192 tests passing

### Phase 2: Backend Registry ✓ (commit 3a63911)
- ✅ BackendRegistry class (`gbs/backend/registry.py`)
- ✅ Module-based backend discovery
- ✅ Entry point support for third-party backends
- ✅ Query methods (find by input/output types)
- ✅ Global singleton with get_backend_registry()
- ✅ Comprehensive unit tests (17 new tests)
- ✅ All 209 tests passing

### Phase 3: Build Planner ✓ (commit fcd76cd)
- ✅ BuildPlan data structure
- ✅ BuildPlanner class with BFS path finding
- ✅ Constraint filtering (require/exclude)
- ✅ Loop prevention
- ✅ Comprehensive error messages
- ✅ Unit tests (10 new tests)
- ✅ All 219 tests passing

### Phase 4: Pass Migration (Partial) ✓ (commit 2df6022)
- ✅ get_backend() stubs added to all backends:
  - gbs.backend.ghdl
  - gbs.backend.gowin
  - gbs.backend.verilog_to_vhdl
  - gbs.backend.mem_init
- ✅ All backends discoverable by registry
- ⚠️  Stub implementations only (full conversion TODO)
- ✅ All 219 tests passing

## Remaining Work

### Phase 5: Execution Engine (TODO)
**Goal**: Execute BuildPlans with new iteration model

Tasks:
- [ ] Update BuildContext for OutputGroups
- [ ] Implement BuildPlan execution
- [ ] Handle backend_config propagation to Pass instances
- [ ] Maintain iteration until stabilization
- [ ] Resource reuse within same BuildPlan
- [ ] Integration tests with actual backends

### Phase 6: Cleanup (TODO)
**Goal**: Remove old systems

Tasks:
- [ ] Remove Profile system from config.py
- [ ] Remove old Backend base class (model/backend.py)
- [ ] Remove old backend iteration code
- [ ] Update all examples to use output_groups
- [ ] Update documentation
- [ ] Final integration tests

### Additional TODO (Phase 4 completion)
**Full backend conversion to Pass-based architecture**:
- [ ] Convert GHDL backend to proper GhdlSimulatePass
  - Implement proper execute() method
  - Handle analyze + elaborate as one pass
  - Use filter_vars contribution
- [ ] Convert Gowin backend to proper GowinSynthesisPass
  - Keep TCL interpreter state management
  - Implement proper execute() method
- [ ] Convert Verilog-to-VHDL backend
- [ ] Convert MemInit backend

## Architecture Status

### What Works Now
1. **Data Models**: Pass, Backend, OutputGroup, OutputFile all defined
2. **Registry**: Can discover and list all backends and passes
3. **Planner**: Can create BuildPlans by finding paths from sources to outputs
4. **Discovery**: All built-in backends are discovered at startup

### What's Missing
1. **Execution**: No code yet to execute BuildPlans
2. **Integration**: Old backend system still in use for actual builds
3. **Full Conversion**: Backends have stubs but not real Pass implementations
4. **YAML Support**: No loader for output-group based project files yet

## Testing Status

- **Total Tests**: 219
- **All Passing**: ✓
- **Coverage**: New code fully tested with mocks
- **Integration Tests**: Pending (need Phase 5)

## Migration Path

The implementation follows a coexistence strategy:
1. New code added alongside old code (Phases 1-4) ✓
2. Old code continues to work unchanged ✓
3. Full switchover requires Phases 5-6 (TODO)
4. Breaking changes acceptable (unreleased software)

## Next Steps

To complete the refactoring:

1. **Implement Phase 5**: Create execution engine that:
   - Takes BuildPlan
   - Instantiates Pass objects with backend_config
   - Executes passes in order
   - Manages resource transformations
   - Iterates until stable

2. **Complete Phase 4**: Convert backend stubs to real implementations:
   - GHDL: Real simulation pass
   - Gowin: Real synthesis/bitstream passes
   - Others: Real transformation passes

3. **Implement Phase 6**: Remove old systems and update docs

4. **Add YAML support**: Update project loaders to support output groups

## File Changes Summary

### New Files (7)
- `src/gbs/model/passes.py` - Pass and Backend base classes
- `src/gbs/backend/registry.py` - BackendRegistry
- `src/gbs/planner.py` - BuildPlanner and BuildPlan
- `tests/test_pass.py` - Pass/Backend tests
- `tests/test_registry.py` - Registry tests
- `tests/test_planner.py` - Planner tests
- `doc/plan/build_system_refactoring.md` - Complete design doc

### Modified Files (7)
- `src/gbs/model/repository.py` - Added OutputFile, OutputGroup
- `src/gbs/models.py` - Updated exports
- `src/gbs/backend/ghdl.py` - Added get_backend() stub
- `src/gbs/backend/gowin.py` - Added get_backend() stub
- `src/gbs/backend/mem_init.py` - Added get_backend() stub
- `src/gbs/backend/verilog_to_vhdl.py` - Added get_backend() stub
- `tests/test_models.py` - Added OutputFile/OutputGroup tests

## Commits

1. `b2e8602` - Phase 1: Add core infrastructure for pass-based build planning
2. `3a63911` - Phase 2: Add Backend Registry for pass discovery
3. `fcd76cd` - Phase 3: Add Build Planner with path finding
4. `2df6022` - Phase 4: Add get_backend() stubs to all backends

## References

- Full design: `doc/plan/build_system_refactoring.md`
- This progress report: `doc/plan/refactoring_progress.md`
