Build System
============

The GBS build system follows a hierarchical architecture where each
component has a distinct responsibility:

.. code-block:: text

   Backend (toolchain plugin)
   └── Pass (planning metadata)
       └── Dispatcher (execution engine)
           └── Task (work unit)
               └── Resource (file/data)

Backend System
--------------

Backends represent toolchains like GHDL, Gowin EDA, or Xilinx ISE.
They participate in build planning by contributing passes.

Backend Protocol
~~~~~~~~~~~~~~~~

Backends implement the ``Backend`` protocol:

.. code-block:: python

   class Backend(Protocol):
       name: str

       def contribute_passes(
           self,
           config: dict[str, Any],
           output_types: set[str]
       ) -> list[Pass]:
           """Return passes that can produce the requested output types"""
           ...

**Parameters**:

- ``config``: Backend-specific configuration from project file
- ``output_types``: Set of output types the project wants

**Returns**: List of Pass instances that can produce those outputs

Backend Implementation Example
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from gbs.backend.protocol import BaseBackend
   from gbs.planner.passes import Pass

   class GHDLBackend(BaseBackend):
       name = "gbs.builtin.ghdl"

       def contribute_passes(self, config, output_types):
           passes = []

           # Contribute simulation pass if simulator requested
           if "simulator" in output_types or "ghdl-simulator" in output_types:
               passes.append(GHDLSimulatePass(config))

           return passes

Pass System
-----------

Passes are **pure planning metadata**. They declare file type transformations
but do NOT execute anything. Execution is handled by Dispatchers.

Pass Attributes
~~~~~~~~~~~~~~~

Each Pass class defines:

.. code-block:: python

   class Pass:
       name: str              # Human-readable name
       input_types: set[str]  # Required input file types
       output_types: set[str] # Produced output file types
       priority: int = 100    # Planning priority (lower = preferred)
       can_fork: bool = False # Allow multiple paths through this pass

Pass Methods
~~~~~~~~~~~~

**filter_vars()**
    Return filter variables for source selection. These are merged with
    OutputGroup's filter_vars before resolving sources.

    .. code-block:: python

       def filter_vars(self) -> dict[str, Any]:
           return {
               "target-usage": "simulation",
               "compiler": "ghdl",
               "vhdl-version": self.config.get("vhdl_standard", "93"),
           }

**dispatchers()**
    Return Dispatcher instances for execution. Called after planning
    to create the execution engines.

    .. code-block:: python

       def dispatchers(self) -> list[Dispatcher]:
           return [GHDLDispatcher(
               output_dir=Path(self.config.get("output_dir", "build")),
               vhdl_std=self.config.get("vhdl_standard", "93c"),
           )]

Pass Example
~~~~~~~~~~~~

.. code-block:: python

   from gbs.planner.passes import Pass

   class GHDLSimulatePass(Pass):
       """GHDL simulation pass: VHDL → simulator executable"""

       name = "ghdl-simulate"
       input_types = {"vhdl"}
       output_types = {"ghdl-simulator", "simulator"}

       def filter_vars(self):
           vhdl_std = self.config.get("vhdl_standard", "93")
           # Map GHDL-style to filter-style
           version_map = {"93c": "1993", "93": "1993", "08": "2008", "2008": "2008"}
           return {
               "target-usage": "simulation",
               "compiler": "ghdl",
               "vhdl-version": version_map.get(vhdl_std, "1993"),
           }

       def dispatchers(self):
           from .dispatcher import GHDLDispatcher
           return [GHDLDispatcher(
               output_dir=Path(self.config.get("output_dir", "build")),
               vhdl_std=self.config.get("vhdl_standard", "93c"),
               ghdl_tool=self.config.get("ghdl_tool", "ghdl"),
           )]

Build Planning
--------------

The planner finds transformation paths from available sources to desired
outputs using type matching.

Planning Algorithm
~~~~~~~~~~~~~~~~~~

1. Collect available source types from repositories
2. Collect desired output types from OutputGroups
3. Query backends for passes that produce desired outputs
4. For each pass, check if inputs are available
5. If not, recursively find passes that produce missing types
6. Select shortest path (iterative deepening)
7. Combine filter_vars from all selected passes

Example Planning
~~~~~~~~~~~~~~~~

Given:
- Sources: ``{vhdl}``
- Desired outputs: ``{ghdl-simulator}``

Planner queries backends:

1. GHDLBackend contributes ``GHDLSimulatePass``
2. Pass declares: ``input_types = {vhdl}``, ``output_types = {ghdl-simulator}``
3. Available sources include ``vhdl`` → inputs satisfied
4. Pass selected for build plan

Dispatcher System
-----------------

Dispatchers process the BuildFileSet and create Tasks for execution.
They run iteratively until the fileset stabilizes.

Dispatcher Protocol
~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   class Dispatcher(Protocol):
       name: str
       priority: int

       def get_filter_variables(self, context: BuildContext) -> dict[str, Any]:
           """Provide filter variables for partition evaluation"""
           ...

       async def process(
           self,
           context: BuildContext,
           fileset: BuildFileSet
       ) -> None:
           """Process the fileset, creating tasks"""
           ...

**Priority Ranges**:

- 100-299: Preprocessing (transpilers, code generators)
- 300-499: Intermediate processing
- 500-699: Main compilation
- 700-899: Post-processing
- 900+: Output extraction (output-copy)

Dispatcher Iteration
~~~~~~~~~~~~~~~~~~~~

Dispatchers run in a loop until convergence:

.. code-block:: python

   async def run_dispatcher_iteration(context, fileset, registry):
       iteration = 0
       while iteration < max_iterations:
           serial_before = fileset.modification_serial

           for dispatcher in registry.get_dispatchers_ordered():
               await dispatcher.process(context, fileset)

           if fileset.modification_serial == serial_before:
               # Fileset stabilized - done
               return iteration

           iteration += 1

This allows dispatchers to:

- Add generated files for other dispatchers to process
- React to files added by earlier dispatchers
- Converge naturally when all transformations complete

BuildContext
------------

BuildContext is the shared state for a build:

.. code-block:: python

   class BuildContext:
       def __init__(self, max_parallel=4, project=None, gbs_config=None):
           self._semaphore = Semaphore(max_parallel)
           self.project = project
           self.gbs_config = gbs_config
           self.steps = set()        # All registered build steps
           self._resources = {}      # Path -> Resource (singleton)
           self._messages = []       # Tool messages (warnings, errors)

Key Methods:

**get_resource(path)**
    Get or create a Resource for a file path (singleton pattern).

**get_virtual_resource(name)**
    Get or create a VirtualResource for in-memory data.

**get_tool(identifier)**
    Look up tool configuration by ``name:variant`` identifier.

**set_output_group_context(topcell, library)**
    Set the current top cell for this build.

BuildFileSet
------------

BuildFileSet is the mutable collection of files being transformed:

.. code-block:: python

   class BuildFileSet:
       def add(self, build_resource: BuildResource) -> None:
           """Add a resource (increments modification_serial)"""

       def remove(self, path: Path) -> set[BuildResource]:
           """Remove a resource, returns dependents"""

       def replace(self, old_path, new_resource) -> set[BuildResource]:
           """Replace a resource, optionally transferring dependencies"""

       def filter(self, **criteria) -> list[BuildResource]:
           """Query resources by attributes"""

       def by_library_ordered(self) -> list[tuple[str, list[BuildResource]]]:
           """Get resources grouped by library in dependency order"""

BuildResource wraps a Resource with metadata:

.. code-block:: python

   @dataclass
   class BuildResource:
       resource: Resource       # The asyncio Future
       file_type: str           # e.g., "vhdl", "verilog"
       library: str | None      # Library name for HDL files
       is_source: bool = True   # True if source, False if generated
       depends_on: set[BuildResource]  # Dependencies

Task System
-----------

Tasks are asyncio Futures that perform work. The dependency graph is
resolved naturally through await.

BuildStep Base Class
~~~~~~~~~~~~~~~~~~~~

All build steps inherit from BuildStep, which is an asyncio.Future:

.. code-block:: python

   class BuildStep(asyncio.Future):
       def __init__(self, context, name):
           self.context = context
           self.depends_on = set()     # Steps we wait on
           self.expected_by = set()    # Steps waiting on us
           self.progress = 0.0         # 0.0 to 1.0

       def dependency_add(self, dep: BuildStep):
           """Add dependency (also adds reverse reference)"""

       async def update_progress(self, progress, message=None):
           """Update progress and notify UI"""

       async def work(self):
           """Override in subclasses to do actual work"""

Resource
~~~~~~~~

Represents a file. Awaiting it waits for the file to exist:

.. code-block:: python

   class Resource(BuildStep):
       def __init__(self, context, path: Path):
           self.path = path
           self.metadata = {}  # file_type, library, etc.

       async def work(self):
           if not self.path.exists():
               raise BuildError(f"File {self.path} missing")

VirtualResource
~~~~~~~~~~~~~~~

Represents in-memory data (no file):

.. code-block:: python

   class VirtualResource(BuildStep):
       """Used for data that doesn't correspond to a file"""
       pass

Task
~~~~

Performs work, awaiting inputs and producing outputs:

.. code-block:: python

   class Task(BuildStep):
       def __init__(self, context, name, inputs, outputs, description=""):
           self.inputs = inputs
           self.outputs = outputs

           # Wire up dependencies
           for output in outputs:
               output.dependency_add(self)  # outputs depend on this task
           for input in inputs:
               self.dependency_add(input)   # this task depends on inputs

       def is_rebuild_needed(self) -> bool:
           """Check timestamps to skip up-to-date tasks"""

       async def work(self):
           """Override to execute tool"""
           ...

       def inputs_of_type(self, type: str) -> list[Resource]:
           """Filter inputs by file_type metadata"""

       def outputs_of_type(self, type: str) -> list[Resource]:
           """Filter outputs by file_type metadata"""

Execution Flow
--------------

1. **Populate BuildFileSet**: Create BuildResources from resolved sources

   .. code-block:: python

      fileset = BuildFileSet(context)
      context.populate_fileset(resolved_sources, fileset)

2. **Run Dispatchers**: Create tasks for the fileset

   .. code-block:: python

      registry = DispatcherRegistry()
      for pass_obj in plan.passes:
          for dispatcher in pass_obj.dispatchers():
              registry.register(dispatcher)

      await run_dispatcher_iteration(context, fileset, registry)

3. **Execute Tasks**: AsyncIO runs the task graph

   .. code-block:: python

      async with context.build():
          resources = [br.resource for br in fileset]
          await asyncio.gather(*resources)

The task graph resolves naturally:

- Awaiting a Resource waits for its producing Task
- Tasks await their input Resources
- AsyncIO schedules based on what's ready
- Semaphore limits parallelism

Progress Tracking
-----------------

Tasks report progress for UI feedback:

.. code-block:: python

   async def work(self):
       await self.update_progress(0.1, "Analyzing")
       # ... do work ...
       await self.update_progress(0.5, "Compiling")
       # ... more work ...
       await self.update_progress(0.9, "Linking")

The BuildContext notifies watchers when progress updates, allowing
progress bars and status displays.

Build Output Directory
----------------------

GBS uses a stable, predictable output directory structure for all build
artifacts:

.. code-block:: text

   gbs-build/
   └── <output_group_name>/
       ├── <intermediate files>
       └── <backend-specific outputs>

Each output group gets its own subdirectory under ``gbs-build/``. For example,
a project with output groups named ``simulation`` and ``synthesis`` would
produce:

.. code-block:: text

   gbs-build/
   ├── simulation/
   │   └── <GHDL work library, simulator executable, etc.>
   └── synthesis/
       └── <netlist, bitstream files, etc.>

This structure ensures:

- **Predictability**: Output locations are always ``gbs-build/<output_group>/``
- **Isolation**: Multiple output groups don't interfere with each other
- **Easy cleanup**: ``rm -rf gbs-build/`` removes all build artifacts

The ``outputs`` section in the project file specifies where to copy final
outputs from the build directory to user-specified locations using the
output-copy pass.

Output Copy Pass
----------------

The output-copy pass is a built-in pass that runs late (priority 900) to
copy generated files from the build directory to user-specified paths.

How It Works
~~~~~~~~~~~~

1. **Planning**: The ``OutputCopyBackend`` contributes an ``OutputCopyPass``
   for any requested output types

2. **Execution**: The ``OutputCopyDispatcher`` runs after other dispatchers:

   - Reads the ``output_group.outputs`` list from project configuration
   - For each output entry, finds matching files in the BuildFileSet by type
   - Creates ``CopyTask`` instances to copy files to destination paths

Configuration
~~~~~~~~~~~~~

Output copying is configured in the project file's ``outputs`` section:

.. code-block:: yaml

   output:
     - name: synthesis
       topcell: top
       outputs:
         - type: gowin-fs
           path: bitstream/design.fs
         - type: gowin-bin
           path: bitstream/design.bin

Each entry specifies:

- ``type``: The file type to look for in the build fileset
- ``path``: Where to copy the matching file

The dispatcher searches the BuildFileSet for files matching each type and
copies them to the specified paths. If multiple files match a type, only
the first is copied (with a warning).

Example
~~~~~~~

Given this configuration:

.. code-block:: yaml

   output:
     - name: synthesis
       outputs:
         - type: gowin-fs
           path: release/firmware.fs

The build process:

1. Gowin backend generates ``gbs-build/synthesis/design.fs`` (type: gowin-fs)
2. OutputCopyDispatcher finds the gowin-fs file in the fileset
3. CopyTask copies it to ``release/firmware.fs``

This separates the stable internal build structure from user-facing output
locations.

Tool Messages
-------------

Tasks can report warnings and errors:

.. code-block:: python

   await self.add_message(
       severity=MessageSeverity.WARNING,
       message="Unused signal 'clk_div'",
       file_path=Path("src/top.vhd"),
       line=42,
   )

Messages are collected in BuildContext and displayed after the build.
