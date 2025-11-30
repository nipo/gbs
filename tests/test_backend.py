"""Tests for backend system"""

import pytest
import asyncio
from pathlib import Path

from gbs.backend import (
    Backend,
    BaseBackend,
    BackendRegistry,
    run_backend_iteration,
)
from gbs.tasks import BuildContext, BuildFileSet, BuildResource


class TestBackend:
    """Tests for BaseBackend and Backend protocol"""

    def test_base_backend_creation(self):
        """Test creating a BaseBackend subclass"""

        class TestBackend(BaseBackend):
            def get_filter_variables(self, context):
                return {"test_var": "test_value"}

            async def process(self, context, fileset):
                pass

        backend = TestBackend("test_backend", priority=100)
        assert backend.name == "test_backend"
        assert backend.priority == 100

    def test_base_backend_default_priority(self):
        """Test default priority is 500"""

        class TestBackend(BaseBackend):
            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                pass

        backend = TestBackend("test")
        assert backend.priority == 500

    def test_backend_filter_variables(self):
        """Test backend provides filter variables"""

        class VHDLBackend(BaseBackend):
            def get_filter_variables(self, context):
                return {
                    "target_language": "vhdl",
                    "vhdl_version": "2008",
                    "has_verilog": False
                }

            async def process(self, context, fileset):
                pass

        backend = VHDLBackend("vhdl")
        ctx = BuildContext()
        vars = backend.get_filter_variables(ctx)

        assert vars["target_language"] == "vhdl"
        assert vars["vhdl_version"] == "2008"
        assert vars["has_verilog"] is False

    @pytest.mark.asyncio
    async def test_backend_process(self, tmp_path):
        """Test backend process method"""
        processed = []

        class LoggingBackend(BaseBackend):
            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                processed.append(self.name)

        backend = LoggingBackend("logger")
        ctx = BuildContext()
        fileset = BuildFileSet(ctx)

        await backend.process(ctx, fileset)

        assert "logger" in processed


class TestBackendRegistry:
    """Tests for BackendRegistry"""

    def test_registry_creation(self):
        """Test creating empty registry"""
        registry = BackendRegistry()
        assert len(registry) == 0

    def test_register_backend(self):
        """Test registering a backend"""

        class TestBackend(BaseBackend):
            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                pass

        registry = BackendRegistry()
        backend = TestBackend("test", priority=100)
        registry.register(backend)

        assert len(registry) == 1

    def test_register_duplicate_name_fails(self):
        """Test that registering duplicate names raises error"""

        class TestBackend(BaseBackend):
            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                pass

        registry = BackendRegistry()
        backend1 = TestBackend("test", priority=100)
        backend2 = TestBackend("test", priority=200)

        registry.register(backend1)

        with pytest.raises(ValueError, match="already registered"):
            registry.register(backend2)

    def test_backends_ordered_by_priority(self):
        """Test backends are ordered by priority"""

        class TestBackend(BaseBackend):
            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                pass

        registry = BackendRegistry()

        # Register in random order
        backend3 = TestBackend("backend3", priority=300)
        backend1 = TestBackend("backend1", priority=100)
        backend2 = TestBackend("backend2", priority=200)

        registry.register(backend3)
        registry.register(backend1)
        registry.register(backend2)

        ordered = registry.get_backends_ordered()

        assert len(ordered) == 3
        assert ordered[0].name == "backend1"
        assert ordered[1].name == "backend2"
        assert ordered[2].name == "backend3"

    def test_backends_ordered_by_name_when_same_priority(self):
        """Test backends with same priority are ordered by name"""

        class TestBackend(BaseBackend):
            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                pass

        registry = BackendRegistry()

        backend_c = TestBackend("c", priority=100)
        backend_a = TestBackend("a", priority=100)
        backend_b = TestBackend("b", priority=100)

        registry.register(backend_c)
        registry.register(backend_a)
        registry.register(backend_b)

        ordered = registry.get_backends_ordered()

        assert ordered[0].name == "a"
        assert ordered[1].name == "b"
        assert ordered[2].name == "c"

    def test_collect_filter_variables(self):
        """Test collecting filter variables from multiple backends"""

        class Backend1(BaseBackend):
            def get_filter_variables(self, context):
                return {"var1": "value1", "shared": "backend1"}

            async def process(self, context, fileset):
                pass

        class Backend2(BaseBackend):
            def get_filter_variables(self, context):
                return {"var2": "value2", "shared": "backend2"}

            async def process(self, context, fileset):
                pass

        registry = BackendRegistry()
        registry.register(Backend1("backend1", priority=100))
        registry.register(Backend2("backend2", priority=200))

        ctx = BuildContext()
        variables = registry.get_filter_variables(ctx)

        # Should have both vars
        assert variables["var1"] == "value1"
        assert variables["var2"] == "value2"
        # Later backend should override
        assert variables["shared"] == "backend2"

    def test_iterate_over_registry(self):
        """Test iterating over registry yields backends in order"""

        class TestBackend(BaseBackend):
            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                pass

        registry = BackendRegistry()
        registry.register(TestBackend("b", priority=200))
        registry.register(TestBackend("a", priority=100))

        names = [b.name for b in registry]
        assert names == ["a", "b"]


class TestBackendIteration:
    """Tests for backend iteration loop"""

    @pytest.mark.asyncio
    async def test_single_iteration_convergence(self, tmp_path):
        """Test that iteration converges when backends don't modify"""

        class NoOpBackend(BaseBackend):
            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                # Don't modify fileset
                pass

        ctx = BuildContext()
        fileset = BuildFileSet(ctx)
        registry = BackendRegistry()
        registry.register(NoOpBackend("noop"))

        iterations = await run_backend_iteration(ctx, fileset, registry)

        assert iterations == 1

    @pytest.mark.asyncio
    async def test_multi_iteration_convergence(self, tmp_path):
        """Test convergence after multiple iterations"""

        class CountingBackend(BaseBackend):
            def __init__(self, name, max_count=3):
                super().__init__(name)
                self.count = 0
                self.max_count = max_count

            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                # Modify fileset for first few iterations
                if self.count < self.max_count:
                    # Add a dummy resource to trigger modification
                    path = tmp_path / f"file_{self.count}.txt"
                    br = BuildResource(
                        resource=context.get_resource(path),
                        file_type="text",
                    )
                    fileset.add(br)
                    self.count += 1

        ctx = BuildContext()
        fileset = BuildFileSet(ctx)
        registry = BackendRegistry()
        registry.register(CountingBackend("counter", max_count=3))

        iterations = await run_backend_iteration(ctx, fileset, registry)

        # Should take 4 iterations:
        # Iteration 1: add file_0 (count=1)
        # Iteration 2: add file_1 (count=2)
        # Iteration 3: add file_2 (count=3)
        # Iteration 4: no changes (count=3, but >= max_count) -> converge
        assert iterations == 4
        assert len(fileset) == 3

    @pytest.mark.asyncio
    async def test_max_iterations_exceeded(self, tmp_path):
        """Test that max iterations raises error"""

        class InfiniteBackend(BaseBackend):
            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                # Always modify
                import random
                path = tmp_path / f"file_{random.randint(0, 1000000)}.txt"
                br = BuildResource(
                    resource=context.get_resource(path),
                    file_type="text",
                )
                fileset.add(br)

        ctx = BuildContext()
        fileset = BuildFileSet(ctx)
        registry = BackendRegistry()
        registry.register(InfiniteBackend("infinite"))

        with pytest.raises(RuntimeError, match="did not converge"):
            await run_backend_iteration(ctx, fileset, registry, max_iterations=10)

    @pytest.mark.asyncio
    async def test_multiple_backends_execution_order(self, tmp_path):
        """Test that backends execute in priority order"""
        execution_order = []

        class OrderedBackend(BaseBackend):
            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                execution_order.append(self.name)

        ctx = BuildContext()
        fileset = BuildFileSet(ctx)
        registry = BackendRegistry()

        # Register in reverse order
        registry.register(OrderedBackend("backend3", priority=300))
        registry.register(OrderedBackend("backend2", priority=200))
        registry.register(OrderedBackend("backend1", priority=100))

        await run_backend_iteration(ctx, fileset, registry)

        # Should execute in priority order
        assert execution_order == ["backend1", "backend2", "backend3"]

    @pytest.mark.asyncio
    async def test_backend_can_add_resources(self, tmp_path):
        """Test backend can add resources to fileset"""

        class ResourceAdder(BaseBackend):
            def __init__(self, name):
                super().__init__(name)
                self.added = False

            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                if not self.added:
                    for i in range(3):
                        path = tmp_path / f"generated_{i}.vhd"
                        br = BuildResource(
                            resource=context.get_resource(path),
                            file_type="vhdl",
                            library="work",
                            is_source=False,
                            generated_by=self.name
                        )
                        fileset.add(br)
                    self.added = True

        ctx = BuildContext()
        fileset = BuildFileSet(ctx)
        registry = BackendRegistry()
        registry.register(ResourceAdder("generator"))

        await run_backend_iteration(ctx, fileset, registry)

        assert len(fileset) == 3
        generated = fileset.filter(generated_by="generator")
        assert len(generated) == 3

    @pytest.mark.asyncio
    async def test_backend_can_remove_resources(self, tmp_path):
        """Test backend can remove resources from fileset"""

        class ResourceRemover(BaseBackend):
            def __init__(self, name):
                super().__init__(name)
                self.removed = False

            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                if not self.removed:
                    # Remove all verilog files
                    verilog_files = fileset.filter(file_type="verilog")
                    for vf in verilog_files:
                        fileset.remove(vf.path)
                    self.removed = True

        ctx = BuildContext()
        fileset = BuildFileSet(ctx)

        # Add some files
        for i in range(2):
            br = BuildResource(
                resource=ctx.get_resource(tmp_path / f"file{i}.v"),
                file_type="verilog",
                library="work"
            )
            fileset.add(br)

        for i in range(2):
            br = BuildResource(
                resource=ctx.get_resource(tmp_path / f"file{i}.vhd"),
                file_type="vhdl",
                library="work"
            )
            fileset.add(br)

        assert len(fileset) == 4

        registry = BackendRegistry()
        registry.register(ResourceRemover("remover"))

        await run_backend_iteration(ctx, fileset, registry)

        # Verilog files should be removed
        assert len(fileset) == 2
        assert len(fileset.filter(file_type="vhdl")) == 2
        assert len(fileset.filter(file_type="verilog")) == 0

    @pytest.mark.asyncio
    async def test_backend_can_replace_resources(self, tmp_path):
        """Test backend can replace resources"""

        class ResourceReplacer(BaseBackend):
            def __init__(self, name):
                super().__init__(name)
                self.replaced = False

            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                if not self.replaced:
                    # Replace all verilog with vhdl
                    verilog_files = fileset.filter(file_type="verilog")
                    for vf in verilog_files:
                        new_path = vf.path.with_suffix(".vhd")
                        new_br = BuildResource(
                            resource=context.get_resource(new_path),
                            file_type="vhdl",
                            library=vf.library,
                            is_source=False,
                            generated_by=self.name
                        )
                        fileset.replace(vf.path, new_br, transfer_dependencies=True)
                    self.replaced = True

        ctx = BuildContext()
        fileset = BuildFileSet(ctx)

        # Add verilog files
        for i in range(2):
            br = BuildResource(
                resource=ctx.get_resource(tmp_path / f"file{i}.v"),
                file_type="verilog",
                library="work"
            )
            fileset.add(br)

        assert len(fileset) == 2
        assert len(fileset.filter(file_type="verilog")) == 2

        registry = BackendRegistry()
        registry.register(ResourceReplacer("replacer"))

        await run_backend_iteration(ctx, fileset, registry)

        # Should have vhdl files instead
        assert len(fileset) == 2
        assert len(fileset.filter(file_type="verilog")) == 0
        assert len(fileset.filter(file_type="vhdl")) == 2
        assert len(fileset.filter(generated_by="replacer")) == 2


class TestExampleBackends:
    """Integration tests with example backends"""

    @pytest.mark.asyncio
    async def test_verilog_to_vhdl_backend(self, tmp_path):
        """Test VerilogToVHDL backend transpiles files"""
        from gbs.backend import VerilogToVHDLBackend

        ctx = BuildContext()
        fileset = BuildFileSet(ctx)

        # Add verilog files
        for i in range(2):
            br = BuildResource(
                resource=ctx.get_resource(tmp_path / f"module{i}.v"),
                file_type="verilog",
                library="work"
            )
            fileset.add(br)

        assert len(fileset) == 2
        assert len(fileset.filter(file_type="verilog")) == 2

        # Run backend
        registry = BackendRegistry()
        registry.register(VerilogToVHDLBackend())

        await run_backend_iteration(ctx, fileset, registry)

        # Should have VHDL files instead
        assert len(fileset) == 2
        assert len(fileset.filter(file_type="verilog")) == 0
        assert len(fileset.filter(file_type="vhdl")) == 2
        assert len(fileset.filter(generated_by="verilog_to_vhdl")) == 2

    @pytest.mark.asyncio
    async def test_ghdl_backend_creates_tasks(self, tmp_path):
        """Test GHDL backend creates compilation tasks"""
        from gbs.backend import GHDLBackend

        ctx = BuildContext()
        ctx.project = type('obj', (object,), {
            'topcell': 'entity0',
            'root_library_name': 'work'
        })()
        fileset = BuildFileSet(ctx)

        # Add VHDL files
        for i in range(2):
            br = BuildResource(
                resource=ctx.get_resource(tmp_path / f"entity{i}.vhd"),
                file_type="vhdl",
                library="work"
            )
            fileset.add(br)

        assert len(fileset) == 2

        # Run backend
        registry = BackendRegistry()
        registry.register(GHDLBackend(output_dir=tmp_path / "build"))

        await run_backend_iteration(ctx, fileset, registry)

        # Should have original VHDL files + simulator executable
        # New GHDL backend doesn't add intermediate .o files to fileset
        assert len(fileset) == 3  # 2 VHDL + 1 simulator
        assert len(fileset.filter(file_type="vhdl")) == 2
        assert len(fileset.filter(file_type="ghdl-simulator")) == 1
        assert len(fileset.filter(generated_by="ghdl")) == 1

    @pytest.mark.asyncio
    async def test_mem_init_backend(self, tmp_path):
        """Test MemInit backend generates files"""
        from gbs.backend import MemInitBackend

        ctx = BuildContext()
        fileset = BuildFileSet(ctx)

        # Add memory spec files
        for i in range(2):
            br = BuildResource(
                resource=ctx.get_resource(tmp_path / f"mem{i}.mem"),
                file_type="mem_spec",
                library="work"
            )
            fileset.add(br)

        assert len(fileset) == 2

        # Run backend
        registry = BackendRegistry()
        registry.register(MemInitBackend())

        await run_backend_iteration(ctx, fileset, registry)

        # Should have spec + generated VHDL files
        assert len(fileset) == 4
        assert len(fileset.filter(file_type="mem_spec")) == 2
        assert len(fileset.filter(file_type="vhdl")) == 2
        assert len(fileset.filter(generated_by="mem_init")) == 2

    @pytest.mark.asyncio
    async def test_full_pipeline_verilog_to_ghdl(self, tmp_path):
        """Test full pipeline: Verilog -> VHDL -> GHDL compilation"""
        from gbs.backend import VerilogToVHDLBackend, GHDLBackend

        ctx = BuildContext()
        ctx.project = type('obj', (object,), {
            'topcell': 'module0',
            'root_library_name': 'work'
        })()
        fileset = BuildFileSet(ctx)

        # Add Verilog source files
        for i in range(2):
            br = BuildResource(
                resource=ctx.get_resource(tmp_path / f"module{i}.v"),
                file_type="verilog",
                library="work"
            )
            fileset.add(br)

        assert len(fileset) == 2

        # Register backends in order
        registry = BackendRegistry()
        registry.register(VerilogToVHDLBackend())  # priority 200
        registry.register(GHDLBackend(output_dir=tmp_path / "build"))  # priority 500

        # Run iteration
        iterations = await run_backend_iteration(ctx, fileset, registry)

        # Should converge after 2 iterations:
        # 1. VerilogToVHDL transpiles, GHDL compiles transpiled files
        # 2. No more changes
        assert iterations == 2

        # Final fileset should have:
        # - 2 VHDL files (transpiled from Verilog)
        # - 1 simulator executable (compiled by GHDL)
        # - 0 Verilog files (replaced by VHDL)
        assert len(fileset) == 3  # 2 VHDL + 1 simulator
        assert len(fileset.filter(file_type="verilog")) == 0
        assert len(fileset.filter(file_type="vhdl")) == 2
        assert len(fileset.filter(file_type="ghdl-simulator")) == 1
        assert len(fileset.filter(generated_by="verilog_to_vhdl")) == 2
        assert len(fileset.filter(generated_by="ghdl")) == 1

    @pytest.mark.asyncio
    async def test_full_pipeline_with_mem_init(self, tmp_path):
        """Test full pipeline with all three backends"""
        from gbs.backend import MemInitBackend, VerilogToVHDLBackend, GHDLBackend

        ctx = BuildContext()
        ctx.project = type('obj', (object,), {
            'topcell': 'entity',
            'root_library_name': 'work'
        })()
        fileset = BuildFileSet(ctx)

        # Add mixed source files
        # 1 Verilog file
        fileset.add(BuildResource(
            resource=ctx.get_resource(tmp_path / "module.v"),
            file_type="verilog",
            library="work"
        ))

        # 1 VHDL file
        fileset.add(BuildResource(
            resource=ctx.get_resource(tmp_path / "entity.vhd"),
            file_type="vhdl",
            library="work"
        ))

        # 1 memory spec
        fileset.add(BuildResource(
            resource=ctx.get_resource(tmp_path / "rom.mem"),
            file_type="mem_spec",
            library="work"
        ))

        assert len(fileset) == 3

        # Register all backends
        registry = BackendRegistry()
        registry.register(MemInitBackend())  # priority 150
        registry.register(VerilogToVHDLBackend())  # priority 200
        registry.register(GHDLBackend(output_dir=tmp_path / "build"))  # priority 500

        # Get filter variables
        variables = registry.get_filter_variables(ctx)
        assert variables["has_mem_init"] is True
        assert variables["target_language"] == "vhdl"
        assert variables["has_verilog_transpiler"] is True
        assert variables["compiler"] == "ghdl"

        # Run iteration
        iterations = await run_backend_iteration(ctx, fileset, registry)

        # Should converge
        assert iterations <= 3

        # Final fileset analysis:
        # - 1 mem_spec (rom.mem - original)
        # - 3 VHDL source files (entity.vhd original + module.vhd from verilog + rom_init.vhd from mem_init)
        # - 1 simulator executable (from GHDL)
        # - 0 Verilog files (module.v was replaced)
        # Total: 5 files

        assert len(fileset.filter(file_type="mem_spec")) == 1
        vhdl_files = fileset.filter(file_type="vhdl")
        assert len(vhdl_files) == 3  # entity.vhd, module.vhd, rom_init.vhd
        assert len(fileset.filter(file_type="ghdl-simulator")) == 1
        assert len(fileset.filter(file_type="verilog")) == 0

        # Check generated_by
        assert len(fileset.filter(generated_by="mem_init")) >= 1
        assert len(fileset.filter(generated_by="verilog_to_vhdl")) >= 1
        assert len(fileset.filter(generated_by="ghdl")) == 1

    @pytest.mark.asyncio
    async def test_backend_priority_order(self, tmp_path):
        """Test that backends execute in priority order"""
        from gbs.backend import MemInitBackend, VerilogToVHDLBackend, GHDLBackend

        ctx = BuildContext()
        registry = BackendRegistry()

        # Register in random order
        registry.register(GHDLBackend())  # 500
        registry.register(MemInitBackend())  # 150
        registry.register(VerilogToVHDLBackend())  # 200

        # Get ordered list
        ordered = registry.get_backends_ordered()

        assert len(ordered) == 3
        assert ordered[0].name == "mem_init"
        assert ordered[1].name == "verilog_to_vhdl"
        assert ordered[2].name == "ghdl"

    @pytest.mark.asyncio
    async def test_backend_idempotency(self, tmp_path):
        """Test that backends are idempotent"""
        from gbs.backend import VerilogToVHDLBackend

        ctx = BuildContext()
        fileset = BuildFileSet(ctx)

        # Add verilog file
        fileset.add(BuildResource(
            resource=ctx.get_resource(tmp_path / "module.v"),
            file_type="verilog",
            library="work"
        ))

        registry = BackendRegistry()
        registry.register(VerilogToVHDLBackend())

        # Run first time
        iterations1 = await run_backend_iteration(ctx, fileset, registry)
        assert iterations1 == 2  # Process + converge

        # Run again - should converge immediately (idempotent)
        iterations2 = await run_backend_iteration(ctx, fileset, registry)
        assert iterations2 == 1  # Already converged
