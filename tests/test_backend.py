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
            async def process(self, context):
                pass

        dispatcher = TestBackend("test_backend", priority=100)
        assert dispatcher.name == "test_backend"
        assert dispatcher.priority == 100

    def test_base_dispatcher_default_priority(self):
        """Test default priority is 500"""

        class TestBackend(BaseDispatcher):
            async def process(self, context):
                pass

        dispatcher = TestBackend("test")
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

            async def process(self, context):
                processed.append(self.name)

        dispatcher = LoggingBackend("logger")
        ctx = BuildContext()

        await dispatcher.process(ctx)

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
            async def process(self, context):
                pass

        registry = DispatcherRegistry()
        dispatcher = TestBackend("test", priority=100)
        registry.register(dispatcher)

        assert len(registry) == 1

    def test_register_duplicate_name_fails(self):
        """Test that registering duplicate names raises error"""

        class TestBackend(BaseDispatcher):
            async def process(self, context):
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
            async def process(self, context):
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
            async def process(self, context):
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

    # Test removed - get_filter_variables no longer part of Dispatcher protocol
    # Filter variables are now only provided by Pass.filter_vars() during planning

    def test_iterate_over_registry(self):
        """Test iterating over registry yields backends in order"""

        class TestBackend(BaseDispatcher):
            async def process(self, context):
                pass

        registry = DispatcherRegistry()
        registry.register(TestBackend("b", priority=200))
        registry.register(TestBackend("a", priority=100))

        names = [b.name for b in registry]
        assert names == ["a", "b"]


# Temporarily disabled - needs rewrite for new API without BuildFileSet

# TestDispatcherIteration class temporarily disabled - needs rewrite for new API
# The old tests used BuildFileSet which has been merged into BuildContext
# New tests need to be written to test the pending queue iteration logic

