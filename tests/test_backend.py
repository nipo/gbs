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
from gbs.build import BuildContext
from gbs.build.task import ResourceTypology


class TestDispatcher:
    """Tests for BaseDispatcher and Backend protocol"""

    def test_base_dispatcher_creation(self):
        """Test creating a BaseDispatcher subclass"""

        class TestBackend(BaseDispatcher):
            async def process(self):
                pass

        ctx = BuildContext()
        dispatcher = TestBackend(ctx, "test_backend", tool_name="test", priority=100)
        assert dispatcher.name == "test_backend"
        assert dispatcher.priority == 100

    def test_base_dispatcher_default_priority(self):
        """Test default priority is 500"""

        class TestBackend(BaseDispatcher):
            async def process(self):
                pass

        ctx = BuildContext()
        dispatcher = TestBackend(ctx, "test", tool_name="test")
        assert dispatcher.priority == 500

    # Test removed - get_filter_variables no longer part of Dispatcher protocol
    # Filter variables are now only provided by Pass.filter_vars() during planning

    @pytest.mark.asyncio
    async def test_dispatcher_process(self, tmp_path):
        """Test dispatcher process method"""
        processed = []

        class LoggingBackend(BaseDispatcher):
            def get_filter_variables(self, context):
                return {}

            async def process(self):
                processed.append(self.name)

        ctx = BuildContext()
        dispatcher = LoggingBackend(ctx, "logger", tool_name="test")
        ctx = BuildContext()

        await dispatcher.process()

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
            async def process(self):
                pass

        registry = DispatcherRegistry()
        ctx = BuildContext()
        dispatcher = TestBackend(ctx, "test", tool_name="test", priority=100)
        registry.register(dispatcher)

        assert len(registry) == 1

    def test_register_duplicate_name_fails(self):
        """Test that registering duplicate names raises error"""

        class TestBackend(BaseDispatcher):
            async def process(self):
                pass

        registry = DispatcherRegistry()
        ctx = BuildContext()
        dispatcher1 = TestBackend(ctx, "test", tool_name="test", priority=100)
        dispatcher2 = TestBackend(ctx, "test", tool_name="test", priority=200)

        registry.register(dispatcher1)

        with pytest.raises(ValueError, match="already registered"):
            registry.register(dispatcher2)

    def test_dispatchers_ordered_by_priority(self):
        """Test dispatchers are ordered by priority"""

        class TestBackend(BaseDispatcher):
            async def process(self):
                pass

        registry = DispatcherRegistry()

        # Register in random order
        ctx = BuildContext()
        dispatcher3 = TestBackend(ctx, "backend3", tool_name="test", priority=300)
        ctx = BuildContext()
        dispatcher1 = TestBackend(ctx, "backend1", tool_name="test", priority=100)
        ctx = BuildContext()
        dispatcher2 = TestBackend(ctx, "backend2", tool_name="test", priority=200)

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
            async def process(self):
                pass

        registry = DispatcherRegistry()

        ctx = BuildContext()
        dispatcher_c = TestBackend(ctx, "c", tool_name="test", priority=100)
        ctx = BuildContext()
        dispatcher_a = TestBackend(ctx, "a", tool_name="test", priority=100)
        ctx = BuildContext()
        dispatcher_b = TestBackend(ctx, "b", tool_name="test", priority=100)

        registry.register(dispatcher_c)
        registry.register(dispatcher_a)
        registry.register(dispatcher_b)

        ordered = registry.get_dispatchers_ordered()

        assert ordered[0].name == "a"
        assert ordered[1].name == "b"
        assert ordered[2].name == "c"

    # Test removed - get_filter_variables no longer part of Dispatcher protocol
    # Filter variables are now only provided by Pass.filter_vars() during planning

    def test_iterate_over_registry(self):
        """Test iterating over registry yields backends in order"""

        class TestBackend(BaseDispatcher):
            async def process(self):
                pass

        registry = DispatcherRegistry()
        ctx = BuildContext()
        registry.register(TestBackend(ctx, "b", tool_name="test", priority=200))
        ctx = BuildContext()
        registry.register(TestBackend(ctx, "a", tool_name="test", priority=100))

        names = [b.name for b in registry]
        assert names == ["a", "b"]


# Temporarily disabled - needs rewrite for new API without BuildFileSet

# TestDispatcherIteration class temporarily disabled - needs rewrite for new API
# The old tests used BuildFileSet which has been merged into BuildContext
# New tests need to be written to test the pending queue iteration logic

