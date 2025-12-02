"""Tests for backend system"""

import pytest
import asyncio
from pathlib import Path

from gbs.backend import (
    Dispatcher,
    BaseDispatcher,
    DispatcherRegistry,
    run_dispatcher_iteration,
)
from gbs.build import BuildContext, BuildFileSet, BuildResource


class TestDispatcher:
    """Tests for BaseDispatcher and Backend protocol"""

    def test_base_dispatcher_creation(self):
        """Test creating a BaseDispatcher subclass"""

        class TestBackend(BaseDispatcher):
            def get_filter_variables(self, context):
                return {"test_var": "test_value"}

            async def process(self, context, fileset):
                pass

        dispatcher = TestBackend("test_backend", priority=100)
        assert dispatcher.name == "test_backend"
        assert dispatcher.priority == 100

    def test_base_dispatcher_default_priority(self):
        """Test default priority is 500"""

        class TestBackend(BaseDispatcher):
            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                pass

        dispatcher = TestBackend("test")
        assert dispatcher.priority == 500

    def test_dispatcher_filter_variables(self):
        """Test dispatcher provides filter variables"""

        class VHDLBackend(BaseDispatcher):
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
    async def test_dispatcher_process(self, tmp_path):
        """Test dispatcher process method"""
        processed = []

        class LoggingBackend(BaseDispatcher):
            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                processed.append(self.name)

        dispatcher = LoggingBackend("logger")
        ctx = BuildContext()
        fileset = BuildFileSet(ctx)

        await dispatcher.process(ctx, fileset)

        assert "logger" in processed


class TestDispatcherRegistry:
    """Tests for DispatcherRegistry"""

    def test_registry_creation(self):
        """Test creating empty registry"""
        registry = DispatcherRegistry()
        assert len(registry) == 0

    def test_register_dispatcher(self):
        """Test registering a dispatcher"""

        class TestBackend(BaseDispatcher):
            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                pass

        registry = DispatcherRegistry()
        dispatcher = TestBackend("test", priority=100)
        registry.register(dispatcher)

        assert len(registry) == 1

    def test_register_duplicate_name_fails(self):
        """Test that registering duplicate names raises error"""

        class TestBackend(BaseDispatcher):
            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                pass

        registry = DispatcherRegistry()
        dispatcher1 = TestBackend("test", priority=100)
        dispatcher2 = TestBackend("test", priority=200)

        registry.register(dispatcher1)

        with pytest.raises(ValueError, match="already registered"):
            registry.register(dispatcher2)

    def test_dispatchers_ordered_by_priority(self):
        """Test dispatchers are ordered by priority"""

        class TestBackend(BaseDispatcher):
            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                pass

        registry = DispatcherRegistry()

        # Register in random order
        dispatcher3 = TestBackend("backend3", priority=300)
        dispatcher1 = TestBackend("backend1", priority=100)
        dispatcher2 = TestBackend("backend2", priority=200)

        registry.register(dispatcher3)
        registry.register(dispatcher1)
        registry.register(dispatcher2)

        ordered = registry.get_dispatchers_ordered()

        assert len(ordered) == 3
        assert ordered[0].name == "backend1"
        assert ordered[1].name == "backend2"
        assert ordered[2].name == "backend3"

    def test_dispatchers_ordered_by_name_when_same_priority(self):
        """Test dispatchers with same priority are ordered by name"""

        class TestBackend(BaseDispatcher):
            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                pass

        registry = DispatcherRegistry()

        dispatcher_c = TestBackend("c", priority=100)
        dispatcher_a = TestBackend("a", priority=100)
        dispatcher_b = TestBackend("b", priority=100)

        registry.register(dispatcher_c)
        registry.register(dispatcher_a)
        registry.register(dispatcher_b)

        ordered = registry.get_dispatchers_ordered()

        assert ordered[0].name == "a"
        assert ordered[1].name == "b"
        assert ordered[2].name == "c"

    def test_collect_filter_variables(self):
        """Test collecting filter variables from multiple backends"""

        class Backend1(BaseDispatcher):
            def get_filter_variables(self, context):
                return {"var1": "value1", "shared": "backend1"}

            async def process(self, context, fileset):
                pass

        class Backend2(BaseDispatcher):
            def get_filter_variables(self, context):
                return {"var2": "value2", "shared": "backend2"}

            async def process(self, context, fileset):
                pass

        registry = DispatcherRegistry()
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

        class TestBackend(BaseDispatcher):
            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                pass

        registry = DispatcherRegistry()
        registry.register(TestBackend("b", priority=200))
        registry.register(TestBackend("a", priority=100))

        names = [b.name for b in registry]
        assert names == ["a", "b"]


class TestDispatcherIteration:
    """Tests for dispatcher iteration loop"""

    @pytest.mark.asyncio
    async def test_single_iteration_convergence(self, tmp_path):
        """Test that iteration converges when backends don't modify"""

        class NoOpBackend(BaseDispatcher):
            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                # Don't modify fileset
                pass

        ctx = BuildContext()
        fileset = BuildFileSet(ctx)
        registry = DispatcherRegistry()
        registry.register(NoOpBackend("noop"))

        iterations = await run_dispatcher_iteration(ctx, fileset, registry)

        assert iterations == 1

    @pytest.mark.asyncio
    async def test_multi_iteration_convergence(self, tmp_path):
        """Test convergence after multiple iterations"""

        class CountingBackend(BaseDispatcher):
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
        registry = DispatcherRegistry()
        registry.register(CountingBackend("counter", max_count=3))

        iterations = await run_dispatcher_iteration(ctx, fileset, registry)

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

        class InfiniteBackend(BaseDispatcher):
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
        registry = DispatcherRegistry()
        registry.register(InfiniteBackend("infinite"))

        with pytest.raises(RuntimeError, match="did not converge"):
            await run_dispatcher_iteration(ctx, fileset, registry, max_iterations=10)

    @pytest.mark.asyncio
    async def test_multiple_dispatchers_execution_order(self, tmp_path):
        """Test that dispatchers execute in priority order"""
        execution_order = []

        class OrderedBackend(BaseDispatcher):
            def get_filter_variables(self, context):
                return {}

            async def process(self, context, fileset):
                execution_order.append(self.name)

        ctx = BuildContext()
        fileset = BuildFileSet(ctx)
        registry = DispatcherRegistry()

        # Register in reverse order
        registry.register(OrderedBackend("backend3", priority=300))
        registry.register(OrderedBackend("backend2", priority=200))
        registry.register(OrderedBackend("backend1", priority=100))

        await run_dispatcher_iteration(ctx, fileset, registry)

        # Should execute in priority order
        assert execution_order == ["backend1", "backend2", "backend3"]

    @pytest.mark.asyncio
    async def test_dispatcher_can_add_resources(self, tmp_path):
        """Test dispatcher can add resources to fileset"""

        class ResourceAdder(BaseDispatcher):
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
        registry = DispatcherRegistry()
        registry.register(ResourceAdder("generator"))

        await run_dispatcher_iteration(ctx, fileset, registry)

        assert len(fileset) == 3
        generated = fileset.filter(generated_by="generator")
        assert len(generated) == 3

    @pytest.mark.asyncio
    async def test_dispatcher_can_remove_resources(self, tmp_path):
        """Test dispatcher can remove resources from fileset"""

        class ResourceRemover(BaseDispatcher):
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

        registry = DispatcherRegistry()
        registry.register(ResourceRemover("remover"))

        await run_dispatcher_iteration(ctx, fileset, registry)

        # Verilog files should be removed
        assert len(fileset) == 2
        assert len(fileset.filter(file_type="vhdl")) == 2
        assert len(fileset.filter(file_type="verilog")) == 0

    @pytest.mark.asyncio
    async def test_dispatcher_can_replace_resources(self, tmp_path):
        """Test dispatcher can replace resources"""

        class ResourceReplacer(BaseDispatcher):
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

        registry = DispatcherRegistry()
        registry.register(ResourceReplacer("replacer"))

        await run_dispatcher_iteration(ctx, fileset, registry)

        # Should have vhdl files instead
        assert len(fileset) == 2
        assert len(fileset.filter(file_type="verilog")) == 0
        assert len(fileset.filter(file_type="vhdl")) == 2
        assert len(fileset.filter(generated_by="replacer")) == 2


# TestExampleBackends class removed - referenced non-existent example classes