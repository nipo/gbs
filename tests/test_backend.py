"""Tests for backend system"""

import pytest
import asyncio
from pathlib import Path

from gbs.protocol import Dispatcher
from gbs.base import BaseDispatcher
from gbs.backend import (
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
        dispatcher = TestBackend(ctx, "test_backend", tool_name="test")
        assert dispatcher.name == "test_backend"

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
        dispatcher = TestBackend(ctx, "test", tool_name="test")
        registry.register(dispatcher)

        assert len(registry) == 1

    def test_register_duplicate_name_fails(self):
        """Test that registering duplicate names raises error"""

        class TestBackend(BaseDispatcher):
            async def process(self):
                pass

        registry = DispatcherRegistry()
        ctx = BuildContext()
        dispatcher1 = TestBackend(ctx, "test", tool_name="test")
        dispatcher2 = TestBackend(ctx, "test", tool_name="test")

        registry.register(dispatcher1)

        with pytest.raises(ValueError, match="already registered"):
            registry.register(dispatcher2)

    def test_iterate_over_registry(self):
        """Test iterating over registry yields dispatchers in registration order"""

        class TestBackend(BaseDispatcher):
            async def process(self):
                pass

        registry = DispatcherRegistry()
        ctx = BuildContext()
        registry.register(TestBackend(ctx, "b", tool_name="test"))
        ctx = BuildContext()
        registry.register(TestBackend(ctx, "a", tool_name="test"))

        names = [b.name for b in registry]
        assert names == ["b", "a"]


# Temporarily disabled - needs rewrite for new API without BuildFileSet

# TestDispatcherIteration class temporarily disabled - needs rewrite for new API
# The old tests used BuildFileSet which has been merged into BuildContext
# New tests need to be written to test the pending queue iteration logic

