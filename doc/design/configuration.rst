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
- **variant**: User-declared label distinguishing different installations
  (e.g., ``llvm``, ``jit``, ``prime``). Part of the selection identity.
- **version**: Optional scalar orthogonal to variant. Not part of the
  selection identity, but a separate filter at lookup time. Typically
  set by a toolchain provider from install metadata (e.g. the
  ``release-tag`` of an ``oss-cad-suite`` build).
- **config**: Tool-specific settings (paths, executables).

Tool Reference Format
~~~~~~~~~~~~~~~~~~~~~

Reference tools using ``name[:variant][@version]``:

.. code-block:: yaml

   backend_config:
     gbs.builtin.ghdl:
       tool: ghdl:llvm             # variant filter
     gbs.builtin.yosys:
       tool: yosys@2026-03-24      # version filter (any variant)
     gbs.builtin.nextpnr:
       tool: nextpnr-ecp5:apio-2026@2026-03-24  # both

Any component may be omitted; each specified component filters by
exact equality, and unspecified components mean "any". If more than
one tool matches, the first one in the merged config wins.

Selection identity is ``(name, variant)``: two entries with the same
``(name, variant)`` but different versions do not coexist — the later
one overrides. To keep two versions of the same tool selectable
side-by-side, give them different variants (a toolchain entry can do
this in one line, see below).

An orthogonal ``tool_version`` key on a backend config pins the
version without touching ``tool``. Backends combine both at plan
time as ``name[:variant]@version``. The CLI ``--tool-version`` flag
sets this key.

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

Toolchains
----------

A toolchain is a shared install prefix that yields many tools at once
(e.g. an ``oss-cad-suite`` tree, an apio package directory, a vendor
suite install). Rather than writing a ``tools:`` entry per binary, a
``toolchains:`` entry names a provider ``type`` and its options; the
provider is dispatched by the plugin registry and returns
``ToolConfig`` entries at config-load time. Explicit ``tools:``
entries still overlay on top, so a single override does not disable
the rest of the toolchain.

.. code-block:: yaml

   toolchains:
     - type: apio                     # provider dispatched by 'type'
       # root: ~/.apio/packages       # provider-specific option
       variant: apio-2026             # tags every emitted tool

   tools:
     - name: yosys
       variant: apio-2026
       config:
         executable: /custom/yosys    # overrides the apio-provided one

Merge rules:

- ``toolchains:`` extends unconditionally across config layers (user,
  tree, project).
- Later toolchain entries override earlier ones on ``(name, variant)``
  collisions during expansion.
- Explicit ``tools:`` entries win over anything expansion produced.

Every expanded ``ToolConfig`` carries a ``via`` provenance tag equal
to the source toolchain's ``type[:variant]`` identifier, which
``gbs config tool`` renders in its output so users can see why a tool
appeared in their config. A provider that pre-sets ``via`` keeps its
own, more specific label.

Two dimensions to distinguish coexisting installs:

- **Variant** is user-declared. When two toolchain entries would
  emit tools with the same ``(name, variant)`` the second overrides
  the first. To keep them selectable side by side, give each entry a
  distinct ``variant:``.
- **Version** is a metadata scalar, typically filled by the provider
  from install-time metadata (``BUILD-INFO.json`` for oss-cad-suite,
  for example). Version does not participate in identity but can
  filter a selection via ``name@version`` or the ``--tool-version``
  CLI flag.

Built-in providers:

``apio``
   Scans ``~/.apio/packages`` (or the ``root:`` option) for known
   package directories: ``oss-cad-suite`` (yosys, nextpnr-ice40/ecp5,
   ecppack, icepack, gowin_pack, ghdl, verilator, ...) and
   ``openxc7`` (nextpnr-xilinx, fasm2frames, xc7frames2bit, bit2fasm,
   bitread, xc7patch, bbasm). Emits one ``ToolConfig`` per binary
   that actually exists in the tree; missing binaries are silently
   skipped so partial installs still yield partial toolchains.

   Version detection reads ``installed_packages.json`` at the apio
   root first (apio writes this manifest with a uniform ``version``
   field for every package), then falls back to a package's
   ``BUILD-INFO.json`` (``release-tag`` field, oss-cad-suite tarball
   convention) and finally a plain ``VERSION`` file (openxc7 layout).

   An optional ``variant:`` key on the toolchain entry is stamped on
   every emitted tool, overriding any per-tool default variant. This
   is the escape hatch for keeping two apio installs distinguishable
   side-by-side: give each entry a different ``variant:``.

   Beyond the ``executable`` path, some tools also carry package-
   relative data directories the backends need:

   - ``nextpnr-xilinx`` gets ``chipdb_root`` pointing at
     ``<openxc7>/chipdb``; the nextpnr Xilinx target reads this to
     resolve ``--chipdb <name><package>.bin``.
   - ``fasm2frames`` and ``xc7frames2bit`` both get
     ``prjxray_db_root`` pointing at
     ``<openxc7>/share/nextpnr/external/prjxray-db``; the openxc7
     backend joins it with the target part's family and speed grade
     to reach ``part.yaml``.

External plugins can register additional provider types by returning
them from ``Plugin.enumerate_toolchain_providers()``.

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
   Maximum number of log files to keep in ``gbs-build/logs/``. Old logs are
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

All built-in backends read ``self.resolve_tool_identifier("<default_name>")``
in their ``dispatchers()`` method. The helper folds the ``tool_version``
key (used by ``--tool-version`` and by explicit config) into the returned
identifier.

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
