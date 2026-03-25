Configuration System
====================

GBS uses a hierarchical configuration system with three levels:

1. **Global Configuration**: ``~/.config/gbs.yaml``
2. **Tree Configuration**: ``.gbs.yaml`` files in parent directories
3. **Project Configuration**: ``*.gbs.yaml`` project files

Each level can define tools, profiles, and repositories. Settings merge
from global to project, with later levels overriding earlier ones.

Configuration Files
-------------------

Global Configuration
~~~~~~~~~~~~~~~~~~~~

Located at ``~/.config/gbs.yaml``, this file contains user-wide settings
like tool paths that don't change between projects:

.. code-block:: yaml

   # Global GBS configuration
   tools:
     - name: ghdl
       variant: llvm
       config:
         executable: /opt/ghdl-llvm/bin/ghdl

     - name: gowin
       variant: V1.9.12
       config:
         path: /opt/Gowin/V1.9.12

Tree Configuration
~~~~~~~~~~~~~~~~~~

Located at ``.gbs.yaml`` in any parent directory of your project. Used
for shared settings across multiple projects in a directory tree:

.. code-block:: yaml

   # .gbs.yaml - Tree-level configuration

   # Tool definitions
   tools:
     - name: ghdl
       variant: system
       config:
         executable: ghdl

     - name: ghdl
       variant: jit
       config:
         executable: /usr/local/bin/ghdl

   # Profiles define reusable build configurations
   profiles:
     simulation:
       filter_vars: {}
       backends:
         - backend: gbs.builtin.ghdl
           config:
             output_dir: build
             vhdl_std: "1993"
             tool: ghdl:system
       repositories: []

     synthesis-gowin:
       backends:
         - backend: gbs.builtin.gowin
           config:
             tool: gowin:V1.9.12

   # Common repositories for all projects in this tree
   repositories:
     - path: /home/user/libs/nsl
       loader: gbs.plugin.nsl.tree

Project Configuration
~~~~~~~~~~~~~~~~~~~~~

Project files (``*.gbs.yaml``) define what to build. See :doc:`../project_file`
for complete documentation.

Tool Configuration
------------------

Tools are external programs (GHDL, Gowin EDA, Xilinx ISE) that GBS invokes.
Each tool has:

- **name**: Tool identifier (e.g., ``ghdl``, ``gowin``, ``ise``)
- **variant**: Distinguishes different installations (e.g., ``llvm``, ``jit``)
- **config**: Tool-specific settings (paths, executables)

Tool Reference Format
~~~~~~~~~~~~~~~~~~~~~

Reference tools using ``name:variant`` format:

.. code-block:: yaml

   backend_config:
     gbs.builtin.ghdl:
       tool: ghdl:llvm    # Uses GHDL LLVM variant

If variant is omitted, the first matching tool is used.

Tool Configuration Examples
~~~~~~~~~~~~~~~~~~~~~~~~~~~

**GHDL**:

.. code-block:: yaml

   tools:
     - name: ghdl
       variant: mcode
       config:
         executable: ghdl-mcode

     - name: ghdl
       variant: llvm
       config:
         executable: /opt/ghdl-llvm/bin/ghdl

**Gowin EDA**:

.. code-block:: yaml

   tools:
     - name: gowin
       variant: V1.9.12
       config:
         path: /opt/Gowin/V1.9.12

**Xilinx ISE**:

.. code-block:: yaml

   tools:
     - name: ise
       variant: "14.7"
       config:
         path: /opt/Xilinx/14.7

Per-Tool Environment Variables
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Any tool can have an ``env`` key in its config that defines environment
variables to inject when running the tool's subprocesses and interactive
sessions. This is applied by all backends automatically.

The primary use case is license server configuration, which varies per
tool installation:

.. code-block:: yaml

   tools:
     - name: vivado
       variant: "2022.2"
       config:
         path: /opt/Xilinx/Vivado/2022.2
         env:
           LM_LICENSE_FILE: 1700@license-server
           XILINXD_LICENSE_FILE: /path/to/license.lic

     - name: quartus-prime
       variant: "24.1"
       config:
         path: /opt/Altera/prime
         env:
           LM_LICENSE_FILE: 1700@license-server

     - name: gowin
       variant: V1.9.12
       config:
         path: /opt/Gowin/V1.9.12
         env:
           # Workaround for bundled library conflicts
           LD_PRELOAD: /usr/lib/x86_64-linux-gnu/libfreetype.so.6

Environment variables are merged with the current process environment.
Tool-specific variables take precedence over existing values.

The ``tool_env`` property on ``BaseDispatcher`` provides these variables
to backends. All 11 built-in backends pass them to their subprocess and
session constructors automatically.

Profile System
--------------

Profiles group related configuration for reuse across projects:

.. code-block:: yaml

   profiles:
     simulation:
       # Filter variables applied to source selection
       filter_vars:
         target-usage: simulation

       # Backends to use for this profile
       backends:
         - backend: gbs.builtin.ghdl
           config:
             vhdl_std: 2008
             tool: ghdl:llvm

       # Additional repositories for this profile
       repositories: []

Profiles can be referenced in project files (feature planned).

Repository References
---------------------

Repositories can be specified at any configuration level:

.. code-block:: yaml

   repositories:
     # Standard GBS repository (uses default YAML loader)
     - path: /path/to/lib

     # Repository with custom loader plugin
     - path: /path/to/nsl
       loader: gbs.plugin.nsl.tree

Repositories are loaded in order. If multiple repositories define the
same library, the first one wins.

Global Settings
---------------

These settings can be specified at any configuration level (global, tree,
or project). Later levels override earlier ones.

``max_parallel``
   Maximum number of parallel build tasks. If not set, GBS uses a
   system-dependent default.

   .. code-block:: yaml

      max_parallel: 4

``max_log_count``
   Maximum number of log files to keep in ``.gbs/logs/``. Old logs are
   automatically removed at startup, keeping only the most recent ones.
   Set to ``0`` to disable cleanup and keep all logs. Default is ``10``.

   .. code-block:: yaml

      max_log_count: 20   # Keep 20 most recent logs
      # or
      max_log_count: 0    # Keep all logs (disable cleanup)

Loaded Files Tracking
---------------------

``GBSConfig`` maintains a ``loaded_files`` list that records the resolved
paths of all configuration files that contributed to the merged config.
As configs are loaded and merged, their file paths are concatenated:

.. code-block:: python

   config = GBSConfig.load_user_config()
   # config.loaded_files == [Path("~/.config/gbs.yaml")]

   tree_config = GBSConfig.load_tree_config(project_dir)
   merged = GBSConfig.merge(config, tree_config)
   # merged.loaded_files == [Path("~/.config/gbs.yaml"), Path("/project/.gbs.yaml")]

This list is used at build time to register configuration files as
``DEFINITION`` resources for incremental rebuild tracking.

Tool Origin Tracking
--------------------

Each ``ToolConfig`` has an ``origin`` field (``Optional[Path]``) that
records which configuration file the tool definition was loaded from.
This is used by ``gbs config dump`` and ``gbs config tool`` to show
provenance annotations, making it clear where each tool definition
comes from when debugging configuration issues.

Standardized ``tool:`` Config Key
----------------------------------

Backend configuration uses the standardized ``tool:`` key to specify
which tool entry to use. This replaces the per-backend keys that
existed previously (``ghdl_tool:``, ``gowin_tool:``, ``ise_tool:``, etc.).

.. code-block:: yaml

   output:
     - name: synthesis
       backend_config:
         gbs.builtin.quartus:
           tool: quartus:prime-25.2

All built-in backends read ``self.config.get("tool", "<default_name>")``
in their ``dispatchers()`` method.

``get_tool_option()`` on BaseDispatcher
---------------------------------------

Dispatchers access tool configuration through ``get_tool_option()``
rather than directly looking up the tool dict. This method provides
clear error messages when a tool is not configured:

.. code-block:: python

   # Required option -- raises MissingToolError with config hint if missing
   path = self.get_tool_option("path")

   # Optional with fallback
   executable = self.get_tool_option("executable", "ghdl")

When the tool identified by ``self.tool_name`` is not found in GBS config
and no default is provided, ``get_tool_option()`` raises
``MissingToolError`` with a message showing exactly what YAML to add.

Target Configuration at Output Group Level
-------------------------------------------

Target device information (``target:``) is specified at the output group
level in the project file. The planner injects it into the backend config
dict as ``config["target"]``, making it available to passes via
``self.config["target"]``:

.. code-block:: yaml

   output:
     - name: synthesis
       target:
         part: 5CEBA4F23C7
         family: cyclonev
       backend_config:
         gbs.builtin.quartus: {}

Configuration Merging
---------------------

GBS merges configuration as follows:

1. Start with global configuration (if exists)
2. Walk up from project directory, loading each ``.gbs.yaml``
3. Merge project configuration on top

**Merge Rules**:

- Scalar values: Later overrides earlier
- Lists (tools, repositories): Concatenated (later appended)
- Dictionaries: Recursively merged

This allows:

- Global tool paths that work everywhere
- Team-shared profiles in repository root
- Project-specific output configurations

Loading Order Example
~~~~~~~~~~~~~~~~~~~~~

Given this directory structure::

   /home/user/
   └── projects/
       ├── .gbs.yaml          # Team config
       └── fpga/
           ├── .gbs.yaml      # FPGA-specific tools
           └── blink/
               └── project.gbs.yaml

Loading ``project.gbs.yaml`` processes:

1. ``~/.config/gbs.yaml`` (global)
2. ``/home/user/projects/.gbs.yaml`` (team)
3. ``/home/user/projects/fpga/.gbs.yaml`` (FPGA)
4. ``/home/user/projects/fpga/blink/project.gbs.yaml`` (project)
