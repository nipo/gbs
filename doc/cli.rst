Command Line Interface
======================

GBS provides a CLI for building projects and inspecting repositories.

General Options
---------------

.. code-block:: text

   gbs [OPTIONS] COMMAND [ARGS]...

   Options:
     --version                          Show version and exit
     -C, --directory DIRECTORY          Change to directory before executing command
     -v, --verbose                      Enable verbose output (INFO level)
     -d, --debug                        Enable debug output (DEBUG level)
     -P, --no-progress                  Disable progress bars
     --log-dir PATH                     Custom directory for log files
     -t, --tool BACKEND=TOOL[:VARIANT]  Override tool identifier for a backend
     --tool-version BACKEND=VERSION     Pin a tool version for a backend
     --help                             Show help and exit

``-C, --directory DIRECTORY``
    Change to the specified directory before executing the command. All relative
    paths (project files, logs, etc.) will be resolved relative to this directory.
    Logs are written to ``<directory>/gbs-build/logs/``.

``-P, --no-progress``
    Disable progress bars in terminal output.

``-t, --tool BACKEND=TOOL[:VARIANT]``
    Override tool identifier for a backend. ``BACKEND`` is a substring
    match against backend names (e.g. ``vivado`` matches
    ``gbs.builtin.vivado``). ``TOOL`` follows the tool identifier
    syntax ``name[:variant][@version]`` (see ``gbs config tool``
    below). May be given multiple times.

    Applies to every command that loads a project: ``project build``,
    ``project show``, ``project outputs``, ``project clean``,
    ``suite build`` (propagated to every project in the suite),
    ``suite list``, ``suite outputs``, ``suite clean``. Must appear
    **before** the subcommand: ``gbs -t vivado=vivado:sim project build``.

``--tool-version BACKEND=VERSION``
    Pin a tool version for a backend without changing the tool
    identifier. Combined with ``--tool`` (or the backend's default
    tool) at plan time as ``name[:variant]@version``. May be given
    multiple times. Same command coverage as ``--tool``.

Log files are written to ``gbs-build/logs/`` by default. Use ``--verbose`` or
``--debug`` for console output; otherwise only errors are shown.

Project Commands
----------------

gbs project build
~~~~~~~~~~~~~~~~~

Build a project.

.. code-block:: bash

   gbs project [-f FILE] build [OPTIONS] [OUTPUT_GROUP...]

**Positional arguments:**

``OUTPUT_GROUP...``
    Restrict the build to these output groups by name. With no
    argument the command builds every group declared in the project.
    Unknown names fail up front with the list of known groups.

    Useful when a project declares parallel groups for different
    toolchains — say a ``vivado`` and an ``openxc7`` group for the
    same Xilinx target — and only one of them is installed on the
    current machine.

**Options:**

``-f, --file PATH``
    Project file path. If not specified, auto-discovers ``*.gbs.yaml``
    in current directory.

``-j, --jobs N``
    Maximum number of parallel tasks. Overrides ``max_parallel`` from config files.
    Must be >= 1.

``-t, --tool BACKEND=TOOL[:VARIANT]``
    Override tool identifier for a backend. BACKEND is a substring
    match against backend names. TOOL follows the tool identifier
    syntax ``name[:variant][@version]`` (see ``gbs config tool``
    below). Can be specified multiple times.

``--tool-version BACKEND=VERSION``
    Pin a tool version for a backend without changing the tool
    identifier. Combined with ``--tool`` (or the backend's default
    tool) at plan time as ``name[:variant]@version``. Can be
    specified multiple times.

**Examples:**

.. code-block:: bash

   # Build with auto-discovered project file
   gbs project build

   # Build only the "openxc7" output group
   gbs project build openxc7

   # Build several groups explicitly
   gbs project build simulation bitstream

   # Build specific project file
   gbs project -f my_project.gbs.yaml build

   # Build with debug output
   gbs -d project build

   # Build with 8 parallel jobs
   gbs project build -j 8

   # Override tool variant for quartus backends
   gbs project build -t quartus=quartus:prime

   # Pin the yosys version supplied by an apio toolchain
   gbs project build --tool-version yosys=2026-03-24

   # Build project in another directory
   gbs -C /path/to/project project build

gbs project show
~~~~~~~~~~~~~~~~

Display project configuration and build plan.

.. code-block:: bash

   gbs project [-f FILE] show [OPTIONS]

Shows:

- Project name and configuration
- Output groups with their targets
- Build plan (selected passes)
- Resolved file set
- Resources (source files, intermediate files, outputs)
- Tasks and their dependencies

**Options:**

``--diagram PATH``
    Generate a graphviz diagram of the build graph in SVG format.
    The diagram shows:

    - **Tasks** (cds shape): Build tasks with color based on task name hash
    - **Resources** (note shape): Files with colors by typology:

      - Blue: SOURCE files
      - Yellow: INTERMEDIATE files
      - Green: OUTPUT files

    - **Virtual Resources** (octagon shape): In-memory build artifacts
    - **Grouped Resources** (folder shape): Groups of >5 source files of same type
      with tooltip showing all filenames
    - **Solid black edges**: Explicit data flow (task inputs/outputs)
    - **Dashed light gray edges**: Implicit dependencies
    - **Medium gray edges**: Task-to-task dependencies

    Individual resource nodes have clickable hyperlinks (using configured URL template).

**Examples:**

.. code-block:: bash

   # Show project information
   gbs project show

   # Generate build graph diagram
   gbs project show --diagram build_graph.svg

   # Output:
   # Project: blink
   # Output groups: 1
   #   synthesis:
   #     topcell: boundary
   #     outputs: ise-bitstream -> blink.bit
   #     passes: ise-synthesize

gbs project outputs
~~~~~~~~~~~~~~~~~~~

List output files, types, and required backends for each output group in
machine-readable format. The report is the only thing written to stdout;
diagnostics go to stderr.

.. code-block:: bash

   gbs project [-f FILE] outputs [OPTIONS]

**Options:**

``--format yaml|json``
    Output format. Default: ``yaml``.

**Output schema**

A list of records, one per output group, in project-file order. The same
schema as :ref:`gbs suite outputs <suite-outputs>`, so documents from
both commands can be read by one consumer and concatenated.

.. code-block:: yaml

   - project: blink            # project name; the suite entry name under `suite outputs`
     group: pnr                # output group name
     topcell: top
     part: iCE40UP5K-SG48I     # output group target part, absent when unset
     partition: mylib.top      # root partition, absent when the project has only one
     backends:                 # backends contributing passes, absent when planning failed
       - gbs.builtin.yosys
     outputs:
       - type: bitstream
         path: blink.bin       # as declared, resolved against the project directory

Keys carrying no value are omitted rather than emitted empty. Naming the
backends means planning the output group, which needs its toolchain
installed; when planning fails the record carries an ``error`` key with
the headline of the diagnostic instead of ``backends``, and the full
diagnostic goes to the log. That is not an error exit — the declared
outputs are reported either way.

**Example:**

.. code-block:: bash

   gbs project outputs
   gbs project outputs --format json

gbs project clean
~~~~~~~~~~~~~~~~~

Remove build artifacts.

.. code-block:: bash

   gbs project [-f FILE] clean [OPTIONS]

**Options:**

``--dry-run``
    Show what would be deleted without deleting.

**Example:**

.. code-block:: bash

   # Preview what will be deleted
   gbs project clean --dry-run

   # Actually clean
   gbs project clean

Configuration Commands
---------------------

gbs config dump
~~~~~~~~~~~~~~~

Display merged configuration from all sources with origin annotations showing
which config file each entry came from.

.. code-block:: bash

   gbs config dump

gbs config tool
~~~~~~~~~~~~~~~

List configured tools, optionally filtered by an identifier.

.. code-block:: bash

   gbs config tool [IDENTIFIER]

**Arguments:**

``IDENTIFIER`` (optional)
    Tool identifier in the form ``name[:variant][@version]``. Any
    component may be omitted; each specified component filters by
    exact equality — the same rule ``GBSConfig.get_tool`` uses at
    build time. A leading ``:`` or ``@`` means "any name" (e.g.
    ``@2026-03-24`` matches every tool tagged with that version).

    With no argument, every configured tool is listed.

**Output**

Each entry shows the tool's full identifier, an origin annotation
pointing at the config file (and, for tools expanded from a
``toolchains:`` entry, the ``via`` toolchain identifier), and the
raw config keys. The ``(default)`` marker sits on the first
occurrence of a given name — that is the entry an unqualified
lookup would return.

**Examples:**

.. code-block:: bash

   # List every configured tool
   gbs config tool

   # All yosys entries
   gbs config tool yosys

   # Just one specific variant of yosys
   gbs config tool yosys:apio-2026

   # Any tool tagged with a given version
   gbs config tool @2026-03-24

gbs config toolchain
~~~~~~~~~~~~~~~~~~~~

List configured toolchains, optionally filtered by an identifier.
Each result shows the toolchain's options and every tool it
expanded into.

.. code-block:: bash

   gbs config toolchain [IDENTIFIER]

**Arguments:**

``IDENTIFIER`` (optional)
    Toolchain identifier in the form ``type[:variant]``. Filtering
    follows the same exact-match rule as ``gbs config tool``; a
    leading ``:`` means "any type".

**Examples:**

.. code-block:: bash

   # List every configured toolchain
   gbs config toolchain

   # All apio toolchains
   gbs config toolchain apio

   # A specific apio entry
   gbs config toolchain apio:apio-2026

openxc7 Commands
----------------

Utilities for the Xilinx Series-7 flow (see :doc:`backends/openxc7`).

.. code-block:: text

   gbs openxc7 [-t TOOL] chipdb ...

**Group options:**

``-t, --tool IDENTIFIER``
    Anchor tool identifier picking which openxc7 install to work
    with. Defaults to ``bbasm``; add ``:variant`` or ``@version`` to
    disambiguate when several apio installs are configured (e.g.
    ``-t bbasm:apio-2026``). Follows the same
    ``name[:variant][@version]`` syntax as ``gbs config tool``.

gbs openxc7 chipdb build
~~~~~~~~~~~~~~~~~~~~~~~~

Assemble a nextpnr-xilinx chipdb ``.bin`` for a Series-7 part that
the openxc7 apio package does not pre-ship.

.. code-block:: bash

   gbs openxc7 chipdb build PART [OPTIONS]

**Arguments:**

``PART``
    Xilinx part in vivado form ``xc<name>-<speed><package>``, e.g.
    ``xc7a35t-1cpg236`` (Basys 3) or ``xc7a35t-1csg324`` (Arty
    A7-35T). Chipdb files are keyed by ``<name><package>`` alone;
    the speed grade is used by ``bbaexport.py`` but stripped from
    the output filename because nextpnr-xilinx looks it up without.

**Options:**

``--output PATH``
    Destination for the ``.bin`` file. Defaults to
    ``<install>/chipdb/<name><package>.bin`` so nextpnr-xilinx picks
    it up automatically on the next project build.

``--keep-bba``
    Keep the intermediate ``.bba`` text file (roughly 250 MB per
    part) next to the ``.bin``. Removed by default.

**Behaviour:**

The command runs two subprocesses sequentially, streaming their
output to the terminal:

1. ``bbaexport.py`` — reads prjxray-db and nextpnr-xilinx-meta,
   emits a ``.bba`` text file describing the target device.
2. ``bbasm --le`` — assembles the ``.bba`` into the little-endian
   ``.bin`` chipdb.

Expect a few minutes of CPU per part. The install root is derived
from the anchor tool's executable path (all openxc7 binaries live at
``<root>/bin/``), so no extra config is required beyond having the
apio toolchain enabled.

**Examples:**

.. code-block:: bash

   # Build a Basys 3 chipdb next to the openxc7 install (default)
   gbs openxc7 chipdb build xc7a35t-1cpg236

   # Build an Arty A7-35T chipdb, write outside the apio tree
   gbs openxc7 chipdb build xc7a35t-1csg324 --output /tmp/arty.bin

   # Pick a specific apio install when multiple variants exist
   gbs openxc7 -t bbasm:apio-2026 chipdb build xc7a35t-1cpg236

Partition Commands
------------------

gbs partition validate
~~~~~~~~~~~~~~~~~~~~~~

Check dependency tracking and syntax of one partition, without building
a project. Typical use is syntax-checking VHDL written for another
backend with GHDL, which analyzes far faster than the target tool
starts up.

.. code-block:: bash

   gbs partition validate [OPTIONS] LIBRARY.PARTITION

Analysis only: nothing is elaborated, simulated or synthesized. The
partition is looked up in the repositories declared in the GBS
configuration and, when the current directory has one, the project
file. A project file is optional here; the configuration repositories
stand on their own.

**Options:**

``--file PATH``
    Project file to take repositories from (auto-discovered otherwise).
    Given on the ``partition`` group, before the subcommand.

``-o, --output PATH``
    Where the report goes. ``-`` (the default) prints it on stdout,
    where it is the only thing written, so it can be piped into a YAML
    parser.

``-b, --backend NAME``
    Restrict validation to one backend. Full backend name or any
    unambiguous substring of it.

``-f, --filter VAR=VALUE``
    Filter variable for partition expansion. Outranks the variables the
    validating pass contributes. May be given multiple times.

``-c, --config KEY=VALUE``
    Backend configuration override. Unlike ``-f`` this reaches the tool
    invocation: ``-c vhdl_standard=2008`` both selects the 2008 sources
    and runs the analyzer with that standard. May be given multiple
    times.

**Exit status** is 0 when the analysis succeeded, warnings included, and
1 when it reported errors, when the dependency tree did not resolve, or
when the partition does not exist.

The YAML report lists the applied filter variables, the contributing
backends, the dependency tree with each partition's sources, the compile
order, the diagnostics per file, and the resolved files no validator in
the plan reads — so a clean report never suggests more coverage than it
has.

.. code-block:: bash

   gbs partition validate mylib.mypart -f vendor=xilinx -c vhdl_standard=2008

Repository Commands
-------------------

This set of commands is used for inspecting gbs builtin repository
metadata and definition format.

gbs repo list
~~~~~~~~~~~~~

List contents of a repository.

.. code-block:: bash

   gbs repo list PATH

**Arguments:**

``PATH``
    Path to repository root or definition file.

**Example:**

.. code-block:: bash

   gbs repo list /path/to/my_lib/repository.gbs.yaml

   # Output:
   # Repository: my_lib
   # Root: /path/to/my_lib
   #
   # Libraries (42):
   #   nsl_data
   #     Partitions (8):
   #       bytestream (3 source files)
   #       crc (5 source files)
   #       ...

gbs repo validate
~~~~~~~~~~~~~~~~~

Validate repository definitions.

.. code-block:: bash

   gbs repo validate PATH

Checks for:

- Valid YAML syntax
- Library and partition structure
- Missing sources or dependencies

**Example:**

.. code-block:: bash

   gbs repo validate /path/to/my_lib/repository.gbs.yaml

   # Output:
   # Repository: my_lib
   # Libraries: 3
   # Partitions: 12
   #
   # ✓ Repository is valid

Project File Discovery
----------------------

When ``-f`` is not specified, GBS auto-discovers project files:

1. Look for ``*.gbs.yaml`` files in current directory
2. If exactly one found, use it
3. If none or multiple found, show error

This allows simple usage:

.. code-block:: bash

   cd my_project/
   gbs project build   # Uses the only *.gbs.yaml file

Exit Codes
----------

======  ============================================
Code    Meaning
======  ============================================
0       Success
1       Error (build failure, missing files, etc.)
======  ============================================

Environment Variables
---------------------

``GBS_LOG_LEVEL``
    Override log level (DEBUG, INFO, WARNING, ERROR).

``GBS_NO_COLOR``
    Disable colored output.

Logging
-------

GBS writes detailed logs to ``gbs-build/logs/`` in the current directory:

- ``gbs.log``: Current session log
- ``gbs.TIMESTAMP.log``: Archived logs

Use ``--log-dir`` to change the log directory.

Log levels:

- Default: Only ERROR to console
- ``-v``: INFO and above to console
- ``-d``: DEBUG and above to console

All levels are always written to log files.
