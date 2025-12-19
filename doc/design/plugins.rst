Plugin System
=============

GBS uses a plugin architecture for extensibility. Plugins can provide:

- Repository loaders (custom source tree formats)
- Backends (new toolchains)
- Dispatchers (preprocessing, code generation)

Plugin Discovery
----------------

GBS uses PEP 420 namespace packages for plugin discovery. Plugins are
installed as Python packages under the ``gbs.plugin`` namespace.

Namespace Package Structure
~~~~~~~~~~~~~~~~~~~~~~~~~~~

A plugin package looks like::

   gbs-plugin-myloader/
   ├── pyproject.toml
   └── src/
       └── gbs/
           └── plugin/
               └── myloader/
                   ├── __init__.py
                   └── tree.py

Note: No ``__init__.py`` in ``gbs/`` or ``gbs/plugin/`` directories -
this enables namespace packages.

pyproject.toml Example
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: toml

   [project]
   name = "gbs-plugin-myloader"
   version = "0.1.0"
   dependencies = ["gbs"]

   [project.optional-dependencies]
   dev = ["pytest"]

   [build-system]
   requires = ["hatchling"]
   build-backend = "hatchling.build"

   [tool.hatch.build.targets.wheel]
   packages = ["src/gbs"]

Repository Loader Plugins
-------------------------

Repository loaders parse source trees into Repository objects. They're
referenced in configuration:

.. code-block:: yaml

   repositories:
     - path: /path/to/source
       loader: myloader

Loader Interface
~~~~~~~~~~~~~~~~

A loader must subclass ``RepositoryLoader`` and implement the ``load()`` method:

.. code-block:: python

   # gbs/plugin/myloader/tree.py

   from pathlib import Path
   from gbs.repository.loader import RepositoryLoader
   from gbs.repository.model import Repository, Library, Partition

   class MyTreeLoader(RepositoryLoader):
       """Custom repository loader for my tree format

       The loader is instantiated with a path and provides a load()
       method to load the repository from that path.
       """

       def load(self) -> Repository:
           """Load repository from self.path

           Returns:
               Repository object with libraries and partitions

           Raises:
               LoadError: If repository cannot be loaded
           """
           repo = Repository(name=self.path.name, root=self.path)

           # Scan path for libraries...
           for lib_dir in self.path.iterdir():
               if lib_dir.is_dir():
                   library = Library(name=lib_dir.name)

                   # Load partitions...
                   for part_file in lib_dir.glob("*.yaml"):
                       partition = parse_partition(part_file)
                       library.add_partition(partition)

                   repo.add_library(library)

           return repo

Plugin Registration
~~~~~~~~~~~~~~~~~~~

Plugins must register their repository loaders via ``enumerate_repository_parsers()``:

.. code-block:: python

   # gbs/plugin/myloader/__init__.py

   from gbs.plugins.plugin import Plugin
   from .tree import MyTreeLoader

   class MyLoaderPlugin(Plugin):
       name = "gbs.plugin.myloader"

       def enumerate_repository_parsers(self) -> dict[str, type]:
           """Return dict of loader name -> RepositoryLoader class"""
           return {
               "myloader": MyTreeLoader,
           }

Complete Loader Example
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # gbs/plugin/myloader/tree.py

   from pathlib import Path
   import yaml
   from gbs.repository.loader import RepositoryLoader
   from gbs.repository.model import (
       Repository, Library, Partition,
       ConditionalGroup, FilterCondition, SourceFile
   )

   class ManifestLoader(RepositoryLoader):
       """Load repository from manifest.yaml format"""

       def load(self) -> Repository:
           """Load repository from self.path"""
           repo = Repository(name=self.path.name, root=self.path)

           # Read manifest file
           manifest_path = self.path / "manifest.yaml"
           if not manifest_path.exists():
               return repo

           with open(manifest_path) as f:
               manifest = yaml.safe_load(f)

           for lib_name, lib_def in manifest.get("libraries", {}).items():
               library = Library(name=lib_name)

               for part_name, part_def in lib_def.get("partitions", {}).items():
                   partition = Partition(name=part_name)

                   # Create default condition with sources
                   condition = FilterCondition(expression="default")

                   for source_def in part_def.get("sources", []):
                       source = SourceFile(
                           path=self.path / lib_name / source_def["file"],
                           file_type=source_def.get("type", "vhdl"),
                       )
                       condition.sources.append(source)

                   for dep in part_def.get("deps", []):
                       condition.deps.append(dep)

                   group = ConditionalGroup(name="default")
                   group.conditions.append(condition)
                   partition.groups.append(group)

                   library.add_partition(partition)

               repo.add_library(library)

           return repo

Backend Plugins
---------------

Backends provide toolchain integration. They contribute passes for
planning and dispatchers for execution.

Backend Registration
~~~~~~~~~~~~~~~~~~~~

Backends are referenced by module path in configuration:

.. code-block:: yaml

   backend_config:
     gbs.plugin.mybackend:
       option1: value1

Creating a Backend
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # gbs/plugin/mybackend/__init__.py

   from gbs.backend.protocol import BaseBackend
   from .passes import MySynthesizePass

   class MyBackend(BaseBackend):
       name = "gbs.plugin.mybackend"

       def contribute_passes(self, config, output_types):
           passes = []

           if "my-output" in output_types:
               passes.append(MySynthesizePass(config))

           return passes

   # Export for automatic discovery
   Backend = MyBackend

Creating Passes
~~~~~~~~~~~~~~~

.. code-block:: python

   # gbs/plugin/mybackend/passes.py

   from gbs.planner.passes import Pass

   class MySynthesizePass(Pass):
       name = "my-synthesize"
       input_types = {"vhdl", "verilog"}
       output_types = {"my-output"}

       def filter_vars(self):
           return {
               "target-usage": "synthesis",
               "vendor": "mytool",
           }

       def dispatchers(self):
           from .dispatcher import MyDispatcher
           return [MyDispatcher(self.config)]

Creating Dispatchers
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # gbs/plugin/mybackend/dispatcher.py

   from gbs.backend.dispatcher import BaseDispatcher
   from .task import MySynthTask

   class MyDispatcher(BaseDispatcher):
       def __init__(self, context, config):
           super().__init__(context, name="mybackend", tool_name="mybackend")
           self.config = config

       def get_filter_variables(self, context):
           return {"vendor": "mytool"}

       async def process(self, context, fileset):
           # Get input files
           vhdl_files = fileset.filter(file_type="vhdl")
           verilog_files = fileset.filter(file_type="verilog")

           if not vhdl_files and not verilog_files:
               return

           # Create output resource
           output_path = Path(self.config.get("output_dir", "build")) / "output.bin"
           output = context.get_resource(output_path)
           output.metadata["file_type"] = "my-output"

           # Create task
           inputs = [f.resource for f in vhdl_files + verilog_files]
           task = MySynthTask(context, inputs, output)

           # Add output to fileset
           from gbs.build.context import BuildResource
           fileset.add(BuildResource(
               resource=output,
               file_type="my-output",
               is_source=False,
               generated_by=self.name,
           ))

Creating Tasks
~~~~~~~~~~~~~~

.. code-block:: python

   # gbs/plugin/mybackend/task.py

   from gbs.build.task import Task

   class MySynthTask(Task):
       def __init__(self, context, inputs, output):
           super().__init__(
               context=context,
               name=f"mysynth-{output.path.name}",
               inputs=inputs,
               outputs=[output],
               description="Synthesize design",
           )

       async def work(self):
           import subprocess

           # Build command
           cmd = ["mytool", "synth"]
           for inp in self.inputs:
               cmd.extend(["-i", str(inp.path)])
           cmd.extend(["-o", str(self.outputs[0].path)])

           # Run with semaphore
           async with self.context.semaphore:
               await self.update_progress(0.1, "Running mytool")
               proc = subprocess.run(cmd, capture_output=True, text=True)

               if proc.returncode != 0:
                   raise BuildError(f"mytool failed: {proc.stderr}")

           await self.update_progress(1.0, "Complete")

Preprocessing Dispatchers
-------------------------

Dispatchers can preprocess files before main compilation:

.. code-block:: python

   class TranspileDispatcher(BaseDispatcher):
       def __init__(self, context):
           super().__init__(context, name="transpile", tool_name="transpile")

       async def process(self, context, fileset):
           # Find files to transpile
           custom_files = fileset.filter(file_type="custom-hdl")

           for f in custom_files:
               # Generate VHDL
               vhdl_path = f.path.with_suffix(".vhd")
               vhdl_resource = context.get_resource(vhdl_path)
               vhdl_resource.metadata["file_type"] = "vhdl"
               vhdl_resource.metadata["library"] = f.library

               # Create transpile task
               task = TranspileTask(context, f.resource, vhdl_resource)

               # Replace in fileset
               fileset.replace(
                   f.path,
                   BuildResource(
                       resource=vhdl_resource,
                       file_type="vhdl",
                       library=f.library,
                       is_source=False,
                   )
               )

This dispatcher runs before main compilation (priority 200 < 500),
converting custom HDL to VHDL.

Plugin Best Practices
---------------------

1. **Use namespace packages**: Don't create ``__init__.py`` in ``gbs/``
   or ``gbs/plugin/`` directories.

2. **Declare dependencies**: Include ``gbs`` as a dependency in
   ``pyproject.toml``.

3. **Handle missing tools gracefully**: Check tool availability and
   provide clear error messages.

4. **Support filter variables**: Contribute appropriate filter_vars
   so conditional sources work correctly.

5. **Report progress**: Call ``update_progress()`` during long operations
   for user feedback.

6. **Collect messages**: Use ``add_message()`` to report warnings and
   errors with source locations.

7. **Test thoroughly**: Create test fixtures and verify round-trip
   loading of repositories.
