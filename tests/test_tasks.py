"""Tests for asyncio-native task system"""

import pytest
import asyncio
from pathlib import Path
import tempfile

from gbs.build import (
    BuildContext,
    Resource,
    VirtualResource,
    Task,
    ExecutorTask,
    BuildError,
    PrerequisiteFailed,
)


# Mock dispatcher for testing
class MockDispatcher:
    """Simple mock dispatcher for testing Task objects"""
    def __init__(self, context):
        self.context = context
        self.name = "mock"


class TestBuildContext:
    """Tests for BuildContext"""

    def test_create_context(self):
        """Test creating build context"""
        ctx = BuildContext(max_parallel=4)
        assert ctx._max_parallel == 4
        assert len(ctx._resources) == 0
        assert len(ctx._virtual_resources) == 0

    def test_resource_singleton(self):
        """Test that get_resource returns same instance for same path"""
        ctx = BuildContext()
        path = Path("/test/file.txt")

        res1 = ctx.get_resource(path)
        res2 = ctx.get_resource(path)

        assert res1 is res2

    def test_virtual_resource_singleton(self):
        """Test that get_virtual_resource returns same instance for same name"""
        ctx = BuildContext()

        vres1 = ctx.get_virtual_resource("data")
        vres2 = ctx.get_virtual_resource("data")

        assert vres1 is vres2


class TestResource:
    """Tests for Resource"""

    def test_resource_creation(self):
        """Test creating a resource"""
        ctx = BuildContext()
        res = ctx.get_resource(Path("/test/file.txt"))

        assert res.path == Path("/test/file.txt").resolve()
        assert res.context is ctx
        assert not res.done()

    @pytest.mark.asyncio
    async def test_input_resource_exists(self, tmp_path):
        """Test input resource that exists"""
        ctx = BuildContext()
        input_file = tmp_path / "input.txt"
        input_file.write_text("test")

        res = ctx.get_resource(input_file)

        # Launch all steps
        async with ctx.build():
            # Should resolve when file exists
            await res
            assert res.done()

    @pytest.mark.asyncio
    async def test_input_resource_missing(self, tmp_path):
        """Test input resource that doesn't exist"""
        ctx = BuildContext()
        input_file = tmp_path / "missing.txt"

        res = ctx.get_resource(input_file)

        # Launch all steps
        async with ctx.build():
            # Should fail when file doesn't exist
            with pytest.raises(BuildError):
                await res


class TestVirtualResource:
    """Tests for VirtualResource"""

    def test_virtual_resource_creation(self):
        """Test creating a virtual resource"""
        ctx = BuildContext()
        vres = ctx.get_virtual_resource("data")

        assert vres.name == "data"
        assert vres.context is ctx
        assert not vres.done()


class TestTask:
    """Tests for Task"""

    @pytest.mark.asyncio
    async def test_simple_file_task(self, tmp_path):
        """Test simple task that creates a file"""
        ctx = BuildContext()

        # Create input file
        input_file = tmp_path / "input.txt"
        input_file.write_text("hello")

        output_file = tmp_path / "output.txt"

        # Create resources
        input_res = ctx.get_resource(input_file)
        output_res = ctx.get_resource(output_file)

        # Create task
        async def executor(context, inputs):
            input_path = inputs[0].path
            content = input_path.read_text()
            output_file.write_text(content.upper())
            return [output_file]

        task = ExecutorTask(
            MockDispatcher(ctx),
            "uppercase",
            inputs=[input_res],
            outputs=[output_res],
            executor=executor,
            description="Convert to uppercase"
        )

        # Launch all steps
        async with ctx.build():
            # Await output
            await output_res

        assert output_file.exists()
        assert output_file.read_text() == "HELLO"

    @pytest.mark.asyncio
    async def test_task_chain(self, tmp_path):
        """Test chain of tasks: task1 -> task2 -> task3"""
        ctx = BuildContext()

        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file3 = tmp_path / "file3.txt"

        # Create initial input
        file1.write_text("1")

        res1 = ctx.get_resource(file1)
        res2 = ctx.get_resource(file2)
        res3 = ctx.get_resource(file3)

        # Task 1: multiply by 2
        async def task1_exec(context, inputs):
            val = int(inputs[0].path.read_text())
            file2.write_text(str(val * 2))
            return [file2]

        task1 = ExecutorTask(MockDispatcher(ctx), "task1", [res1], [res2], task1_exec)

        # Task 2: add 10
        async def task2_exec(context, inputs):
            val = int(inputs[0].path.read_text())
            file3.write_text(str(val + 10))
            return [file3]

        task2 = ExecutorTask(MockDispatcher(ctx), "task2", [res2], [res3], task2_exec)

        # Launch all steps
        async with ctx.build():
            # Await final output
            await res3

            assert file3.exists()
            assert file3.read_text() == "12"  # (1 * 2) + 10

    @pytest.mark.asyncio
    async def test_parallel_execution(self, tmp_path):
        """Test parallel execution of independent tasks"""
        ctx = BuildContext(max_parallel=2)

        # Create 3 independent tasks
        tasks_executed = []

        async def make_executor(task_name):
            async def executor(context, inputs):
                tasks_executed.append(task_name)
                await asyncio.sleep(0.1)
                output_file = tmp_path / f"{task_name}.txt"
                output_file.write_text(task_name)
                return [output_file]
            return executor

        outputs = []
        for i in range(3):
            task_name = f"task{i}"
            output_res = ctx.get_resource(tmp_path / f"{task_name}.txt")

            task = ExecutorTask(
                MockDispatcher(ctx),
                task_name,
                inputs=[],
                outputs=[output_res],
                executor=await make_executor(task_name)
            )

            outputs.append(output_res)

        # Launch all steps
        async with ctx.build():
            # Await all outputs - should run in parallel
            await asyncio.gather(*outputs)

            assert len(tasks_executed) == 3
            assert all((tmp_path / f"task{i}.txt").exists() for i in range(3))

    @pytest.mark.asyncio
    async def test_virtual_resource(self):
        """Test task with virtual resource output"""
        ctx = BuildContext()

        vres = ctx.get_virtual_resource("data")

        async def executor(context, inputs):
            return [{"key": "value", "number": 42}]

        task = ExecutorTask(MockDispatcher(ctx), "producer", [], [vres], executor)

        # Launch all steps
        async with ctx.build():
            # Await virtual resource - note: virtual resources don't have result values in current design
            await vres
            assert vres.done()

    @pytest.mark.asyncio
    async def test_mixed_resources(self, tmp_path):
        """Test task with file input and virtual output"""
        ctx = BuildContext()

        input_file = tmp_path / "input.json"
        input_file.write_text('{"data": [1, 2, 3]}')

        input_res = ctx.get_resource(input_file)
        vres = ctx.get_virtual_resource("parsed_data")

        async def executor(context, inputs):
            import json
            data = json.loads(inputs[0].path.read_text())
            return [data["data"]]

        task = ExecutorTask(MockDispatcher(ctx), "parse", [input_res], [vres], executor)

        # Launch all steps
        async with ctx.build():
            await vres
            assert vres.done()

    @pytest.mark.asyncio
    async def test_task_failure_propagation(self, tmp_path):
        """Test that task failure propagates to dependents"""
        ctx = BuildContext()

        res1 = ctx.get_resource(tmp_path / "file1.txt")
        res2 = ctx.get_resource(tmp_path / "file2.txt")

        # Task 1: fails
        async def failing_executor(context, inputs):
            raise RuntimeError("Task failed!")

        task1 = ExecutorTask(MockDispatcher(ctx), "task1", [], [res1], failing_executor)

        # Task 2: depends on task1
        async def task2_executor(context, inputs):
            return [tmp_path / "file2.txt"]

        task2 = ExecutorTask(MockDispatcher(ctx), "task2", [res1], [res2], task2_executor)

        # Launch all steps
        async with ctx.build():
            # Awaiting res2 should fail because task1 fails
            with pytest.raises((PrerequisiteFailed,)):
                await res2

    @pytest.mark.asyncio
    async def test_diamond_dependency(self, tmp_path):
        """Test diamond dependency: res1 -> res2,res3 -> res4"""
        ctx = BuildContext()

        file1 = tmp_path / "file1.txt"
        file1.write_text("1")

        res1 = ctx.get_resource(file1)
        res2 = ctx.get_resource(tmp_path / "file2.txt")
        res3 = ctx.get_resource(tmp_path / "file3.txt")
        res4 = ctx.get_resource(tmp_path / "file4.txt")

        # Task A: res1 -> res2
        async def taskA_exec(context, inputs):
            val = int(inputs[0].path.read_text())
            (tmp_path / "file2.txt").write_text(str(val * 2))
            return [tmp_path / "file2.txt"]

        taskA = ExecutorTask(MockDispatcher(ctx), "taskA", [res1], [res2], taskA_exec)

        # Task B: res1 -> res3
        async def taskB_exec(context, inputs):
            val = int(inputs[0].path.read_text())
            (tmp_path / "file3.txt").write_text(str(val * 3))
            return [tmp_path / "file3.txt"]

        taskB = ExecutorTask(MockDispatcher(ctx), "taskB", [res1], [res3], taskB_exec)

        # Task C: res2, res3 -> res4
        async def taskC_exec(context, inputs):
            val2 = int(inputs[0].path.read_text())
            val3 = int(inputs[1].path.read_text())
            (tmp_path / "file4.txt").write_text(str(val2 + val3))
            return [tmp_path / "file4.txt"]

        taskC = ExecutorTask(MockDispatcher(ctx), "taskC", [res2, res3], [res4], taskC_exec)

        # Launch all steps
        async with ctx.build():
            # Await final output
            await res4

            assert (tmp_path / "file4.txt").exists()
            assert (tmp_path / "file4.txt").read_text() == "5"  # (1*2) + (1*3)

    @pytest.mark.asyncio
    async def test_up_to_date_file_task(self, tmp_path):
        """Test that up-to-date file task is skipped"""
        ctx = BuildContext()

        input_file = tmp_path / "input.txt"
        output_file = tmp_path / "output.txt"

        # Create input
        input_file.write_text("test")

        # Create output (newer than input)
        await asyncio.sleep(0.01)
        output_file.write_text("result")

        input_res = ctx.get_resource(input_file)
        output_res = ctx.get_resource(output_file)

        executed = False

        async def executor(context, inputs):
            nonlocal executed
            executed = True
            return [output_file]

        task = ExecutorTask(MockDispatcher(ctx), "task", [input_res], [output_res], executor)

        # Launch all steps
        async with ctx.build():
            # Await output - should skip execution
            await output_res

            assert not executed  # Task should have been skipped

    @pytest.mark.asyncio
    async def test_outdated_file_task(self, tmp_path):
        """Test that outdated file task is executed"""
        ctx = BuildContext()

        input_file = tmp_path / "input.txt"
        output_file = tmp_path / "output.txt"

        # Create output first
        output_file.write_text("old")

        # Create input (newer than output)
        await asyncio.sleep(0.01)
        input_file.write_text("new")

        input_res = ctx.get_resource(input_file)
        output_res = ctx.get_resource(output_file)

        executed = False

        async def executor(context, inputs):
            nonlocal executed
            executed = True
            output_file.write_text("updated")
            return [output_file]

        task = ExecutorTask(MockDispatcher(ctx), "task", [input_res], [output_res], executor)

        # Launch all steps
        async with ctx.build():
            # Await output - should execute
            await output_res

            assert executed  # Task should have been executed
            assert output_file.read_text() == "updated"


# Temporarily disabled - BuildResource removed in refactoring

# TestBuildResource and TestBuildFileSet classes temporarily disabled
# These tested BuildResource and BuildFileSet which have been merged into Resource/BuildContext
# New tests need to be written to test the integrated functionality

