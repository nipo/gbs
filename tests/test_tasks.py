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
    BuildResource,
    BuildFileSet,
)


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
            ctx,
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

        task1 = ExecutorTask(ctx, "task1", [res1], [res2], task1_exec)

        # Task 2: add 10
        async def task2_exec(context, inputs):
            val = int(inputs[0].path.read_text())
            file3.write_text(str(val + 10))
            return [file3]

        task2 = ExecutorTask(ctx, "task2", [res2], [res3], task2_exec)

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
                ctx,
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

        task = ExecutorTask(ctx, "producer", [], [vres], executor)

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

        task = ExecutorTask(ctx, "parse", [input_res], [vres], executor)

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

        task1 = ExecutorTask(ctx, "task1", [], [res1], failing_executor)

        # Task 2: depends on task1
        async def task2_executor(context, inputs):
            return [tmp_path / "file2.txt"]

        task2 = ExecutorTask(ctx, "task2", [res1], [res2], task2_executor)

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

        taskA = ExecutorTask(ctx, "taskA", [res1], [res2], taskA_exec)

        # Task B: res1 -> res3
        async def taskB_exec(context, inputs):
            val = int(inputs[0].path.read_text())
            (tmp_path / "file3.txt").write_text(str(val * 3))
            return [tmp_path / "file3.txt"]

        taskB = ExecutorTask(ctx, "taskB", [res1], [res3], taskB_exec)

        # Task C: res2, res3 -> res4
        async def taskC_exec(context, inputs):
            val2 = int(inputs[0].path.read_text())
            val3 = int(inputs[1].path.read_text())
            (tmp_path / "file4.txt").write_text(str(val2 + val3))
            return [tmp_path / "file4.txt"]

        taskC = ExecutorTask(ctx, "taskC", [res2, res3], [res4], taskC_exec)

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

        task = ExecutorTask(ctx, "task", [input_res], [output_res], executor)

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

        task = ExecutorTask(ctx, "task", [input_res], [output_res], executor)

        # Launch all steps
        async with ctx.build():
            # Await output - should execute
            await output_res

            assert executed  # Task should have been executed
            assert output_file.read_text() == "updated"


class TestBuildResource:
    """Tests for BuildResource"""

    def test_build_resource_creation(self, tmp_path):
        """Test creating a BuildResource"""
        ctx = BuildContext()
        file_path = tmp_path / "test.vhd"
        resource = ctx.get_resource(file_path)

        br = BuildResource(
            resource=resource,
            file_type="vhdl",
            library="work",
            file_type_version="2008",
            is_source=True
        )

        assert br.resource is resource
        assert br.file_type == "vhdl"
        assert br.library == "work"
        assert br.file_type_version == "2008"
        assert br.is_source is True
        assert len(br.depends_on) == 0
        assert br.generated_by is None
        assert br.path == file_path.resolve()

    def test_build_resource_with_dependencies(self, tmp_path):
        """Test BuildResource with dependencies"""
        ctx = BuildContext()

        dep1 = BuildResource(
            resource=ctx.get_resource(tmp_path / "dep1.vhd"),
            file_type="vhdl",
            library="lib1"
        )
        dep2 = BuildResource(
            resource=ctx.get_resource(tmp_path / "dep2.vhd"),
            file_type="vhdl",
            library="lib2"
        )

        main = BuildResource(
            resource=ctx.get_resource(tmp_path / "main.vhd"),
            file_type="vhdl",
            library="work"
        )
        main.depends_on.add(dep1)
        main.depends_on.add(dep2)

        assert len(main.depends_on) == 2
        assert dep1 in main.depends_on
        assert dep2 in main.depends_on

    def test_build_resource_equality(self, tmp_path):
        """Test BuildResource equality based on path"""
        ctx = BuildContext()
        path = tmp_path / "test.vhd"

        br1 = BuildResource(
            resource=ctx.get_resource(path),
            file_type="vhdl",
            library="work"
        )
        br2 = BuildResource(
            resource=ctx.get_resource(path),
            file_type="vhdl",
            library="other"
        )

        # Same path = equal
        assert br1 == br2
        assert hash(br1) == hash(br2)

    def test_build_resource_metadata(self, tmp_path):
        """Test BuildResource metadata field"""
        ctx = BuildContext()

        br = BuildResource(
            resource=ctx.get_resource(tmp_path / "test.vhd"),
            file_type="vhdl",
            library="work"
        )
        br.metadata["custom_key"] = "custom_value"
        br.metadata["flags"] = ["-O2", "-Wall"]

        assert br.metadata["custom_key"] == "custom_value"
        assert br.metadata["flags"] == ["-O2", "-Wall"]


class TestBuildFileSet:
    """Tests for BuildFileSet"""

    def test_fileset_creation(self):
        """Test creating an empty fileset"""
        ctx = BuildContext()
        fileset = BuildFileSet(ctx)

        assert len(fileset) == 0
        assert fileset.modification_serial == 0
        assert list(fileset) == []

    def test_fileset_add_single(self, tmp_path):
        """Test adding a single resource"""
        ctx = BuildContext()
        fileset = BuildFileSet(ctx)

        br = BuildResource(
            resource=ctx.get_resource(tmp_path / "test.vhd"),
            file_type="vhdl",
            library="work"
        )

        fileset.add(br)

        assert len(fileset) == 1
        assert fileset.modification_serial == 1
        assert br.path in fileset
        assert fileset.get(br.path) is br

    def test_fileset_add_multiple(self, tmp_path):
        """Test adding multiple resources"""
        ctx = BuildContext()
        fileset = BuildFileSet(ctx)

        resources = []
        for i in range(5):
            br = BuildResource(
                resource=ctx.get_resource(tmp_path / f"file{i}.vhd"),
                file_type="vhdl",
                library="work"
            )
            fileset.add(br)
            resources.append(br)

        assert len(fileset) == 5
        assert fileset.modification_serial == 5

        # Check iteration order is stable (sorted by path)
        fileset_list = list(fileset)
        assert fileset_list == sorted(fileset_list, key=lambda r: r.path)

    def test_fileset_remove(self, tmp_path):
        """Test removing a resource"""
        ctx = BuildContext()
        fileset = BuildFileSet(ctx)

        br = BuildResource(
            resource=ctx.get_resource(tmp_path / "test.vhd"),
            file_type="vhdl",
            library="work"
        )
        fileset.add(br)

        assert len(fileset) == 1
        serial_after_add = fileset.modification_serial

        dependents = fileset.remove(br.path)

        assert len(fileset) == 0
        assert fileset.modification_serial == serial_after_add + 1
        assert br.path not in fileset
        assert len(dependents) == 0

    def test_fileset_dependency_tracking(self, tmp_path):
        """Test dependency tracking"""
        ctx = BuildContext()
        fileset = BuildFileSet(ctx)

        # Create dep1 <- main dependency
        dep1 = BuildResource(
            resource=ctx.get_resource(tmp_path / "dep1.vhd"),
            file_type="vhdl",
            library="work"
        )
        fileset.add(dep1)

        main = BuildResource(
            resource=ctx.get_resource(tmp_path / "main.vhd"),
            file_type="vhdl",
            library="work"
        )
        main.depends_on.add(dep1)
        fileset.add(main)

        # Check reverse dependencies
        dependents = fileset.get_dependents(dep1.path)
        assert len(dependents) == 1
        assert main in dependents

        # Remove dep1, should return main as dependent
        removed_dependents = fileset.remove(dep1.path)
        assert main in removed_dependents

    def test_fileset_replace(self, tmp_path):
        """Test replacing a resource"""
        ctx = BuildContext()
        fileset = BuildFileSet(ctx)

        # Create original resource
        old = BuildResource(
            resource=ctx.get_resource(tmp_path / "old.v"),
            file_type="verilog",
            library="work"
        )
        fileset.add(old)

        # Create dependent
        dependent = BuildResource(
            resource=ctx.get_resource(tmp_path / "main.vhd"),
            file_type="vhdl",
            library="work"
        )
        dependent.depends_on.add(old)
        fileset.add(dependent)

        # Replace old with new
        new = BuildResource(
            resource=ctx.get_resource(tmp_path / "new.vhd"),
            file_type="vhdl",
            library="work",
            is_source=False,
            generated_by="verilog_to_vhdl"
        )
        updated = fileset.replace(old.path, new, transfer_dependencies=True)

        # Check replacement
        assert old.path not in fileset
        assert new.path in fileset
        assert len(updated) == 1
        assert dependent in updated

        # Check dependency was transferred
        assert old not in dependent.depends_on
        assert new in dependent.depends_on

    def test_fileset_filter(self, tmp_path):
        """Test filtering resources"""
        ctx = BuildContext()
        fileset = BuildFileSet(ctx)

        # Add VHDL files in work library
        for i in range(3):
            br = BuildResource(
                resource=ctx.get_resource(tmp_path / f"work{i}.vhd"),
                file_type="vhdl",
                library="work"
            )
            fileset.add(br)

        # Add Verilog files in other library
        for i in range(2):
            br = BuildResource(
                resource=ctx.get_resource(tmp_path / f"other{i}.v"),
                file_type="verilog",
                library="other"
            )
            fileset.add(br)

        # Filter by library
        work_files = fileset.filter(library="work")
        assert len(work_files) == 3
        assert all(r.library == "work" for r in work_files)

        # Filter by file_type
        verilog_files = fileset.filter(file_type="verilog")
        assert len(verilog_files) == 2
        assert all(r.file_type == "verilog" for r in verilog_files)

        # Filter by multiple criteria
        work_vhdl = fileset.filter(file_type="vhdl", library="work")
        assert len(work_vhdl) == 3

        # Filter by list of types
        hdl_files = fileset.filter(file_type=["vhdl", "verilog"])
        assert len(hdl_files) == 5

    def test_fileset_library_dependency_graph(self, tmp_path):
        """Test building library dependency graph"""
        ctx = BuildContext()
        fileset = BuildFileSet(ctx)

        # Create lib1 file
        lib1_file = BuildResource(
            resource=ctx.get_resource(tmp_path / "lib1.vhd"),
            file_type="vhdl",
            library="lib1"
        )
        fileset.add(lib1_file)

        # Create lib2 file that depends on lib1
        lib2_file = BuildResource(
            resource=ctx.get_resource(tmp_path / "lib2.vhd"),
            file_type="vhdl",
            library="lib2"
        )
        lib2_file.depends_on.add(lib1_file)
        fileset.add(lib2_file)

        # Create lib3 file that depends on lib2
        lib3_file = BuildResource(
            resource=ctx.get_resource(tmp_path / "lib3.vhd"),
            file_type="vhdl",
            library="lib3"
        )
        lib3_file.depends_on.add(lib2_file)
        fileset.add(lib3_file)

        # Build dependency graph
        graph = fileset.library_dependency_graph()

        assert "lib1" in graph
        assert "lib2" in graph
        assert "lib3" in graph
        assert "lib1" in graph["lib2"]
        assert "lib2" in graph["lib3"]
        assert len(graph["lib1"]) == 0  # lib1 has no dependencies

    def test_fileset_libraries_in_dependency_order(self, tmp_path):
        """Test topological sort of libraries"""
        ctx = BuildContext()
        fileset = BuildFileSet(ctx)

        # Create lib1 <- lib2 <- lib3 dependency chain
        lib1 = BuildResource(
            resource=ctx.get_resource(tmp_path / "lib1.vhd"),
            file_type="vhdl",
            library="lib1"
        )
        fileset.add(lib1)

        lib2 = BuildResource(
            resource=ctx.get_resource(tmp_path / "lib2.vhd"),
            file_type="vhdl",
            library="lib2"
        )
        lib2.depends_on.add(lib1)
        fileset.add(lib2)

        lib3 = BuildResource(
            resource=ctx.get_resource(tmp_path / "lib3.vhd"),
            file_type="vhdl",
            library="lib3"
        )
        lib3.depends_on.add(lib2)
        fileset.add(lib3)

        # Get ordered libraries
        ordered = fileset.libraries_in_dependency_order()

        # Should be [lib1, lib2, lib3]
        assert ordered.index("lib1") < ordered.index("lib2")
        assert ordered.index("lib2") < ordered.index("lib3")

    def test_fileset_by_library_ordered(self, tmp_path):
        """Test getting resources grouped by library in dependency order"""
        ctx = BuildContext()
        fileset = BuildFileSet(ctx)

        # Create resources in multiple libraries
        lib1_files = []
        for i in range(2):
            br = BuildResource(
                resource=ctx.get_resource(tmp_path / f"lib1_{i}.vhd"),
                file_type="vhdl",
                library="lib1"
            )
            fileset.add(br)
            lib1_files.append(br)

        lib2_files = []
        for i in range(2):
            br = BuildResource(
                resource=ctx.get_resource(tmp_path / f"lib2_{i}.vhd"),
                file_type="vhdl",
                library="lib2"
            )
            # lib2 depends on lib1
            br.depends_on.add(lib1_files[0])
            fileset.add(br)
            lib2_files.append(br)

        # Get resources by library
        by_lib = fileset.by_library_ordered()

        # Should have two tuples: (lib1, [...]), (lib2, [...])
        assert len(by_lib) == 2
        lib1_tuple = by_lib[0]
        lib2_tuple = by_lib[1]

        assert lib1_tuple[0] == "lib1"
        assert lib2_tuple[0] == "lib2"
        assert len(lib1_tuple[1]) == 2
        assert len(lib2_tuple[1]) == 2

    def test_fileset_circular_dependency_detection(self, tmp_path):
        """Test that circular library dependencies are detected"""
        ctx = BuildContext()
        fileset = BuildFileSet(ctx)

        # Create circular dependency: lib1 <- lib2 <- lib1
        lib1 = BuildResource(
            resource=ctx.get_resource(tmp_path / "lib1.vhd"),
            file_type="vhdl",
            library="lib1"
        )

        lib2 = BuildResource(
            resource=ctx.get_resource(tmp_path / "lib2.vhd"),
            file_type="vhdl",
            library="lib2"
        )

        # Create circular dependency
        lib1.depends_on.add(lib2)
        lib2.depends_on.add(lib1)

        fileset.add(lib1)
        fileset.add(lib2)

        # Should raise ValueError when trying to sort
        with pytest.raises(ValueError, match="Circular dependency"):
            fileset.libraries_in_dependency_order()

    def test_fileset_modification_serial(self, tmp_path):
        """Test modification serial increments correctly"""
        ctx = BuildContext()
        fileset = BuildFileSet(ctx)

        assert fileset.modification_serial == 0

        br1 = BuildResource(
            resource=ctx.get_resource(tmp_path / "file1.vhd"),
            file_type="vhdl",
            library="work"
        )
        fileset.add(br1)
        assert fileset.modification_serial == 1

        br2 = BuildResource(
            resource=ctx.get_resource(tmp_path / "file2.vhd"),
            file_type="vhdl",
            library="work"
        )
        fileset.add(br2)
        assert fileset.modification_serial == 2

        fileset.remove(br1.path)
        assert fileset.modification_serial == 3

        br3 = BuildResource(
            resource=ctx.get_resource(tmp_path / "file3.vhd"),
            file_type="vhdl",
            library="work"
        )
        fileset.replace(br2.path, br3)
        # replace = remove + add = 2 increments
        assert fileset.modification_serial == 5

