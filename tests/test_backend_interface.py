"""Tests for the new Backend planning interface"""

import pytest
from pathlib import Path
from gbs.backend.protocol import Backend, BaseBackend
from gbs.backend.dispatcher import BaseDispatcher


class TestBackendInterface:
    """Test the Backend protocol and BaseBackend class"""

    def test_base_backend_creation(self):
        """Test creating a BaseBackend subclass"""

        class TestBackend(BaseBackend):
            def contribute_passes(self, config, output_types):
                return []

            def create_dispatcher(self, config):
                # Return a simple dispatcher
                class TestDispatcher(BaseDispatcher):
                    def get_filter_variables(self, context):
                        return {}

                    async def process(self, context, fileset):
                        pass

                return TestDispatcher("test_dispatcher")

        backend = TestBackend("test_backend")
        assert backend.name == "test_backend"
        assert hasattr(backend, "logger")

    def test_backend_contribute_passes(self):
        """Test contribute_passes method"""

        class MockPass:
            name = "mock_pass"
            input_types = {"vhdl"}
            output_types = {"simulator"}

        class TestBackend(BaseBackend):
            def contribute_passes(self, config, output_types):
                if "simulator" in output_types:
                    return [MockPass]
                return []

            def create_dispatcher(self, config):
                class TestDispatcher(BaseDispatcher):
                    def get_filter_variables(self, context):
                        return {}

                    async def process(self, context, fileset):
                        pass

                return TestDispatcher("test")

        backend = TestBackend("test_backend")

        # Should return passes when output type matches
        passes = backend.contribute_passes({}, {"simulator"})
        assert len(passes) == 1
        assert passes[0] == MockPass

        # Should return empty when output type doesn't match
        passes = backend.contribute_passes({}, {"bitstream"})
        assert len(passes) == 0

    def test_backend_create_dispatcher(self):
        """Test create_dispatcher method"""

        class TestBackend(BaseBackend):
            def contribute_passes(self, config, output_types):
                return []

            def create_dispatcher(self, config):
                class TestDispatcher(BaseDispatcher):
                    def __init__(self, name, test_config):
                        super().__init__(name)
                        self.test_config = test_config

                    def get_filter_variables(self, context):
                        return {}

                    async def process(self, context, fileset):
                        pass

                return TestDispatcher("test_dispatcher", config.get("test_value"))

        backend = TestBackend("test_backend")
        dispatcher = backend.create_dispatcher({"test_value": 42})

        assert dispatcher.name == "test_dispatcher"
        assert dispatcher.test_config == 42

    def test_backend_with_config(self):
        """Test backend using configuration"""

        class ConfigurableBackend(BaseBackend):
            def contribute_passes(self, config, output_types):
                # Different passes based on configuration
                if config.get("mode") == "simulation":
                    return ["SimPass"]
                elif config.get("mode") == "synthesis":
                    return ["SynthPass"]
                return []

            def create_dispatcher(self, config):
                class TestDispatcher(BaseDispatcher):
                    def get_filter_variables(self, context):
                        return {}

                    async def process(self, context, fileset):
                        pass

                return TestDispatcher("test")

        backend = ConfigurableBackend("configurable")

        # Simulation mode
        passes = backend.contribute_passes({"mode": "simulation"}, set())
        assert passes == ["SimPass"]

        # Synthesis mode
        passes = backend.contribute_passes({"mode": "synthesis"}, set())
        assert passes == ["SynthPass"]
