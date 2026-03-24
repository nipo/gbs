===================
 Writing a backend
===================

A backend provides build methods for GBS.  A backend can hook itself
in the build through two methods:

* Through passes, that declare a method for reaching an output build
  product type,

* Through a generic dispatcher, that is hooked on any type of build.

A backend is registered by a `plugin <plugin>`_.

Passes
======

Passes are queried to backends by the build planner. Planner starts
from user-requested output file type. In turn, passes define what they
need in terms of inputs to be able to work.  Planner is responsible
for finding its way between the user-provided inputs and outputs file
types.

When contributing passes, backend code can evaluate backend
configuration and project configuration.

A backend must not return a pass that is not able to produce any of
the output types requested.

All passes kept for final build may contribute filter_vars for source
enumeration or dispatchers.

Dispatcher
==========

A dispatcher is part of the build context and must interoperate with
other dispatcher to take all inputs to produce all outputs.  Some
dispatchers only work at an intermediate level where they transform
intermediate outputs from other backends to be injected as inputs to
other backends (like dynamically create constraint files from the
synthesized netlist).

A dispatcher creates 2 main types of tasks:

* Tasks that are singleton, whatever the number of source files,
  libraries, etc.  These tasks typically take a variable number of
  inputs.

* Tasks that are per-something in the build (per source, per library,
  etc).

Dispatchers must consume (remove from build context) any source file
they handle and integrated as a task input.  In turn, dispatchers must
add to build context the artifacts they produce.  As dispatchers
`process()` is called many times in turn until build context
stabilizes, not all resources to work on may be present in context on
the first pass.

Usual pattern is:

* create singleton tasks and keep a reference to them not to re-create
  them on subsequent calls,

* remove handled inputs from build context so that they are not
  visible on subsequent calls,

* keep track internally for per-something tasks that are not linked
  exactly to a file (libraries for instance).

Dispatcher code must not create anything in the filesystem at the time
Task objects are created from process().

Tool Configuration
==================

Backends specify which tool to use via the standardized ``tool:`` key
in their backend config section:

.. code-block:: yaml

   output:
     - name: synthesis
       target:
         part: GW1NR-9C
         family: GW1NR
       backend_config:
         gbs.builtin.gowin:
           tool: gowin:V1.9.12

The ``target:`` dict is specified at the output group level. The planner
injects it into the backend config so passes can access it as
``self.config["target"]``.

Passes read the tool identifier with ``self.config.get("tool", "<default>")``:

.. code-block:: python

   def dispatchers(self, context):
       tool = self.config.get("tool", "mybackend")
       target = self.config["target"]
       return [MyDispatcher(context=context, tool_name=tool, target=target)]

Dispatchers access individual tool options via ``get_tool_option()``:

.. code-block:: python

   # Required option -- raises MissingToolError with helpful message
   path = self.get_tool_option("path")

   # Optional with fallback
   executable = self.get_tool_option("executable", "mytool")

This replaces directly accessing the tool dict through the build context.

Tasks
=====

Tasks are asyncio-based runners with dependency tracking.  They are
created by dispatchers and run on build (they may be created but never
run, it typically happens with "clean" command).  They keep a
reference on the dispatcher that created them, so they can retrieve
dispatcher-dependent context like command path or shared state.

Tasks must ensure the output directory path for their outputs is valid
and created.

Tasks may use helpers:

* for long-running interpreter contexts like TCL interpreters (or any
  other shell), there are helpers in `gbs.build.shell` and
  `gbs.build.tcl`.

* for one-shot commands, there is a `gbs.build.subprocess` module that
  contains generic boilerplate for spawning tools that produce
  formatted messages on stdout/stderr.

* for creating files and directories, pathlib may be used.

Definition Dependencies
=======================

Setup tasks (project initialization, environment creation) should declare
a dependency on build definition files so they re-run when the project
file, configuration, or repository definitions change. Use
``attach_definition_dependencies()`` on the dispatcher:

.. code-block:: python

   async def process(self):
       if self._setup_task is None:
           self._setup_task = MySetupTask(
               dispatcher=self,
               inputs=[],
               outputs=[project_resource],
           )
           self.attach_definition_dependencies(self._setup_task)

This attaches all ``DEFINITION``-typology resources (config files,
project file, repository definitions, config fingerprint) as
non-consuming inputs to the task.
