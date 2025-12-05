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

Output Goal Resolution
----------------------

GBS uses a demand-driven (pull-based) approach for output resolution.
Desired outputs are added to the BuildFileSet as "goals" that dispatchers
work backwards from to create the necessary tasks.

How It Works
~~~~~~~~~~~~

1. **Output Goals**: When building, each ``output`` entry from the project
   is added to the BuildFileSet with ``is_output=True`` and no producer

2. **Backward Resolution**: Dispatchers scan for unsatisfied outputs
   (``is_output=True`` with no producing task) and work backwards:

   - Find the output type needed (e.g., ``ise-bitstream+gzip``)
   - Determine what source type is required (e.g., ``ise-bitstream``)
   - Either find the source in fileset, or create an intermediate goal
   - Create a task connecting source to output

3. **Iteration**: The dispatcher loop runs until all outputs are satisfied
   or no progress can be made

This allows natural chaining of transforms. For ``ise-bitstream+gzip+base64``:

1. First iteration: base64 dispatcher sees unsatisfied output, needs
   ``ise-bitstream+gzip``, creates intermediate goal
2. Second iteration: gzip dispatcher sees unsatisfied ``ise-bitstream+gzip``,
   needs ``ise-bitstream``, creates intermediate goal
3. Third iteration: output-copy sees unsatisfied ``ise-bitstream``,
   finds source in fileset, creates copy task
4. All goals now have producers, build can execute

Output Copy Dispatcher
~~~~~~~~~~~~~~~~~~~~~~

The output-copy dispatcher (priority 900) copies files from the build
fileset to user-specified output paths:

1. Finds unsatisfied outputs (``is_output=True``, no producer)
2. For each output, searches fileset for matching ``file_type``
3. Creates ``CopyTask`` from source to output

Configuration in project file:

.. code-block:: yaml

   output:
     - name: synthesis
       outputs:
         - type: gowin-fs
           path: release/firmware.fs

Example flow:

1. Gowin backend generates ``gbs-build/synthesis/design.fs`` (type: gowin-fs)
2. OutputCopyDispatcher finds unsatisfied output ``gowin-fs`` at ``release/firmware.fs``
3. Matches with source in fileset, creates CopyTask
4. Build executes, file is copied

Compression Dispatcher
~~~~~~~~~~~~~~~~~~~~~~

The compression dispatcher (priority 850) handles type suffixes like ``+gzip``.
It works backwards from unsatisfied outputs, stripping one transform at a time.

Type Suffix Syntax
^^^^^^^^^^^^^^^^^^

Output types can include compression suffixes:

.. code-block:: yaml

   outputs:
     - type: ise-bitstream+gzip
       path: firmware.bit.gz

The syntax is: ``<base-type>+<transform>[+<transform>...]``

Multiple transforms can be chained:

.. code-block:: yaml

   outputs:
     - type: gowin-fs+gzip+base64
       path: design.fs.gz.b64

How It Works
^^^^^^^^^^^^

The dispatcher handles **one transform at a time** (the rightmost/outermost):

1. Finds unsatisfied outputs with transform suffixes
2. Strips the last transform (e.g., ``+gzip``) to get source type
3. If source exists in fileset, creates compression task
4. If source doesn't exist, creates intermediate output goal
5. Next iteration handles the next transform level

Example with chained transforms (``ise-bitstream+gzip``):

.. code-block:: text

   Iteration 1:
     - CompressDispatcher sees unsatisfied "ise-bitstream+gzip"
     - Strips "+gzip", needs "ise-bitstream"
     - "ise-bitstream" exists in fileset (from ISE backend)
     - Creates GzipTask: ise-bitstream -> ise-bitstream+gzip

   Iteration 2:
     - OutputCopyDispatcher sees unsatisfied output at release/firmware.bit.gz
     - Type "ise-bitstream+gzip" now exists (from GzipTask)
     - Creates CopyTask to final location

Supported Compressions
^^^^^^^^^^^^^^^^^^^^^^

``+gzip``
    Standard gzip compression. Adds ``.gz`` extension.

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
