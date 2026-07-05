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
               "vhdl-version": self.config.get("vhdl_standard", "1993"),
           }

**dispatchers()**
    Return Dispatcher instances for execution. Called after planning
    to create the execution engines.

    .. code-block:: python

       def dispatchers(self) -> list[Dispatcher]:
           return [GHDLDispatcher(
               output_dir=Path(self.config.get("output_dir", "build")),
               vhdl_std=self.config.get("vhdl_standard", "1993"),
           )]

**probe()**
    Report whether the pass is a viable candidate for the current
    build. Return ``None`` to stay in the candidate pool, or a
    non-empty string to drop out — the string is stored as the
    rejection reason for the plan-failure diagnostic (see below).

    Called right after the pass is instantiated, before it enters
    the planner's candidate pool. Only two kinds of check belong
    here:

    - The declared target part is outside the family the backend
      supports (e.g. Vivado refusing xc6* parts).
    - The primary tool the pass would invoke is not resolvable in
      ``gbs_config`` or its executable does not exist on disk
      (respecting CLI ``--tool`` / ``--tool-version`` overrides).

    Broken-at-runtime tools — missing shared libraries, missing
    licences, crashes — are **not** a probe concern. They must stay
    as build-time failures so the user gets the real error.

    .. code-block:: python

       def probe(self) -> str | None:
           target = self.config.get("target") or {}
           part = (target.get("part") or "").lower()
           if not part.startswith("xc"):
               return f"target part {part!r} is not a Xilinx device"
           if part.startswith("xc6"):
               return (
                   f"target part {part!r} is pre-7-series; "
                   f"Vivado only handles 7-series and later"
               )
           return self.probe_tool("vivado")

    ``BasePass.probe_tool(default_name)`` is a shared helper that
    resolves the identifier (honouring CLI overrides), looks it up
    in ``gbs_config``, and asserts the executable or install-path
    exists on disk. Backends whose primary tool needs a stronger
    check (e.g. Gowin needs the ``path`` install root, not just an
    ``executable``) override ``probe()`` and layer their own check
    on top.

Pass Example
~~~~~~~~~~~~

.. code-block:: python

   from gbs.base import BasePass

   class GHDLSimulatePass(BasePass):
       """GHDL simulation pass: VHDL → simulator executable"""

       name = "ghdl-simulate"
       input_types = {"ghdl-cf", "ghdl-vhpidirect-c"}
       output_types = {"simulator", "ghdl-simulator"}

       def probe(self) -> str | None:
           return self.probe_tool("ghdl")

       def filter_vars(self):
           flavor = _GhdlFlavorProbe.flavor(self.config, self.gbs_config)
           engine = f"ghdl_{flavor}"
           return {
               "purpose": "simulation",
               "vhdl_frontend": engine,
               "simulation_engine": engine,
               "vhdl_std": self.config.get("vhdl_standard", "1993"),
           }

       def dispatchers(self, context):
           from .dispatcher import GHDLSimulateDispatcher
           return [GHDLSimulateDispatcher(
               context=context,
               vhdl_std=self.config.get("vhdl_standard", "1993"),
               tool_name=self.resolve_tool_identifier("ghdl"),
           )]

Build Planning
--------------

The planner finds transformation paths from available sources to desired
outputs using type matching.

Planning Algorithm
~~~~~~~~~~~~~~~~~~

1. Collect available source types from repositories
2. Collect desired output types from OutputGroups
3. Expand desired output types with sibling aliases from the
   terminal-type registry (see below)
4. Query backends for passes that produce desired outputs
5. For each pass, run :py:meth:`Pass.probe` — passes returning a
   rejection reason drop out and are logged for the diagnostic
6. For every surviving pass, check if inputs are available
7. If not, recursively find passes that produce missing types
8. Apply the OutputGroup's ``require_backends`` / ``exclude_backends``
   / ``require_passes`` / ``exclude_passes`` filters
9. Prune non-minimal plans (a plan whose pass set strictly contains
   another's is dropped)
10. Fail if 0 plans remain (nothing satisfies the outputs) or if >1
    plans remain (ambiguous — user must add ``require_backends``)
11. Combine filter_vars from all selected passes

Example Planning
~~~~~~~~~~~~~~~~

Given:
- Sources: ``{vhdl}``
- Desired outputs: ``{simulator}``

Planner queries backends:

1. GHDLBackend contributes ``GHDLAnalyzePass`` (vhdl → ghdl-cf) and
   ``GHDLSimulatePass`` (ghdl-cf → simulator).
2. ``GHDLSimulatePass.probe()`` returns ``None`` — ghdl is
   configured; keeps the pass in the pool.
3. Inputs (``ghdl-cf``) are produced by ``GHDLAnalyzePass``; both
   pass are chained.
4. Available sources include ``vhdl`` — chain satisfied.
5. Chain selected for the build plan.

Plan-Failure Diagnostic
~~~~~~~~~~~~~~~~~~~~~~~

When the planner cannot settle on exactly one candidate — because
zero survive, or several do — the ``PlanningError`` message lists
every chain the search considered and every pass a probe dropped,
so the user can tell whether the plan failed because no chain
exists, a needed backend was probed out, or two backends both
applied.

Example on a Xilinx 7-series project where Vivado is not installed
but openxc7 is::

  Cannot find passes from ['vhdl', 'xilinx-xdc'] to ['bitstream',
  'pnr-report', 'synthesis-report'] for output group 'vivado'.

  Passes dropped by probe():
    - gbs.builtin.vivado/vivado-synthesize: tool 'vivado' not configured

The list of considered chains and the rejection log make the
distinction between "no chain at all" and "chain found but its
tools are unavailable" visible up front.

Terminal Output Types
~~~~~~~~~~~~~~~~~~~~~

The types a user writes in ``outputs:`` — ``bitstream``,
``synthesis-report``, ``pnr-report``, ``simulator`` — are canonical
shared names. Every backend that produces one of them advertises
the canonical name alongside the historic vendor-prefixed sibling
(``vivado-bitstream``, ``xilinx-bitstream``, ``ise-bitstream``,
``ecp5-bitstream``, ``gowin-fs``, …). This lets a project move
between backends without renaming its output goals.

The alias registry lives in :py:mod:`gbs.build.type_aliases`. It is
consulted in three places:

- At planning time, the planner expands the requested output types
  with sibling aliases before calling ``Backend.contribute_passes``,
  so backends whose historic filter only triggers on a vendor-prefixed
  name still contribute when the user asks for the canonical name.
- :py:meth:`BuildContext.get_resource` auto-populates
  ``file_type_aliases`` on any resource whose ``file_type`` is a
  registered terminal type. A dispatcher that creates the file as
  ``file_type="bitstream"`` therefore matches queries for
  ``vivado-bitstream`` (or any other sibling) transparently.
- :py:meth:`BuildContext.filter_pending` and
  :py:meth:`Task.outputs_of_type` / ``inputs_of_type`` check the
  alias set alongside the primary ``file_type``.

When the user's project still writes a legacy sibling (e.g.
``type: xilinx-bitstream``) the ``OutputCopyDispatcher`` matches it
via alias fallback and emits a warning nudging the user to rename
to the canonical form. Existing projects keep working; new projects
should use the canonical vocabulary.

Format-specific intermediates that are not user-facing goals
(``xilinx-netlist-json``, ``nextpnr-fasm``, ``ecp5-config``,
``ghdl-cf``, …) stay tool-specific — the registry only covers
terminal outputs.

Dispatcher System
-----------------

Dispatchers process the BuildFileSet and create Tasks for execution.
They run iteratively until the fileset stabilizes.

Dispatcher Protocol
~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   class Dispatcher(Protocol):
       name: str

       async def process(
           self,
           context: BuildContext,
           fileset: BuildFileSet
       ) -> None:
           """Process the fileset, creating tasks"""
           ...

Dispatcher Iteration
~~~~~~~~~~~~~~~~~~~~

Dispatchers run in a loop until convergence:

.. code-block:: python

   async def run_dispatcher_iteration(context, fileset, registry):
       iteration = 0
       while iteration < max_iterations:
           serial_before = fileset.modification_serial

           for dispatcher in registry:
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
       def __init__(self, dispatcher, name, inputs, outputs, description=""):
           self.dispatcher = dispatcher  # Reference to creating dispatcher
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

Tasks hold a reference to their creating Dispatcher, which provides:

- Access to ``dispatcher.context`` (BuildContext)
- Access to dispatcher-specific configuration
- Tool information via ``dispatcher.tool_name``

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

Resource Typology
-----------------

Each resource in the build graph has a typology that classifies its role:

.. code-block:: python

   class ResourceTypology(Enum):
       SOURCE = "source"           # User-provided source file
       INTERMEDIATE = "intermediate"  # Generated during build (default)
       OUTPUT = "output"           # Final deliverable
       DEFINITION = "definition"   # Build-influencing metadata file

The ``DEFINITION`` typology marks files that influence the build plan but are
not source files or build products. These include:

- GBS configuration files (``~/.config/gbs.yaml``, ``.gbs.yaml``)
- The project file (``*.gbs.yaml``)
- Repository definition files (partition YAML files read during loading)
- The config fingerprint file (``gbs-config-fingerprint.json``)

Definition resources are registered at build setup time by
``_register_definition_files()``. Dispatchers attach them to setup tasks
via ``attach_definition_dependencies()``, so those tasks re-run when any
build definition changes.

Config Fingerprint
~~~~~~~~~~~~~~~~~~

The config fingerprint (``gbs-build/<output_group>/gbs-config-fingerprint.json``)
is a JSON snapshot of the effective merged configuration for an output group.
It is itself a ``DEFINITION`` resource. When the effective configuration
changes between builds, the fingerprint file content changes, which
invalidates tasks that depend on it and triggers a rebuild.

attach_definition_dependencies()
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``BaseDispatcher`` provides ``attach_definition_dependencies(task)`` as a
convenience method. It iterates over all pending resources with
``typology=ResourceTypology.DEFINITION`` and adds each one as a
non-consuming input to the given task. This is typically called on setup
or initialization tasks that should re-run when the build configuration
changes:

.. code-block:: python

   # In a dispatcher's process() method:
   self._setup_task = MySetupTask(
       dispatcher=self,
       inputs=[],
       outputs=[project_resource],
   )
   self.attach_definition_dependencies(self._setup_task)

Shared Resource Registry
------------------------

When a project has multiple output groups, each gets its own
``BuildContext``. To support cross-output-group dependencies (e.g., one
output group produces a file that another consumes), all ``BuildContext``
instances share a single ``ResourceRegistry``.

The registry ensures that a ``Resource`` at a given resolved path is the
same Python object regardless of which ``BuildContext`` creates or
references it. This means if output group A produces a file and output
group B needs it, both hold the same ``Resource`` future. Group B's task
naturally blocks until group A's producing task completes.

All output groups execute concurrently via ``asyncio.gather()``, and
cross-group dependencies resolve through the shared futures.

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
