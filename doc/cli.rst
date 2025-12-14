Command Line Interface
======================

GBS provides a CLI for building projects and inspecting repositories.

General Options
---------------

.. code-block:: text

   gbs [OPTIONS] COMMAND [ARGS]...

   Options:
     --version                  Show version and exit
     -C, --directory DIRECTORY  Change to directory before executing command
     -v, --verbose              Enable verbose output (INFO level)
     -d, --debug                Enable debug output (DEBUG level)
     -P, --no-progress          Disable progress bars
     --log-dir PATH             Custom directory for log files (default: .gbs/logs)
     --help                     Show help and exit

``-C, --directory DIRECTORY``
    Change to the specified directory before executing the command. All relative
    paths (project files, logs, etc.) will be resolved relative to this directory.
    Logs are written to ``<directory>/.gbs/logs/``.

``-P, --no-progress``
    Disable progress bars in terminal output.

Log files are written to ``.gbs/logs/`` by default. Use ``--verbose`` or
``--debug`` for console output; otherwise only errors are shown.

Project Commands
----------------

gbs project build
~~~~~~~~~~~~~~~~~

Build a project.

.. code-block:: bash

   gbs project [-f FILE] build [OPTIONS]

**Options:**

``-f, --file PATH``
    Project file path. If not specified, auto-discovers ``*.gbs.yaml``
    in current directory.

``-j, --jobs N``
    Maximum number of parallel tasks. Overrides ``max_parallel`` from config files.
    Must be >= 1.

**Examples:**

.. code-block:: bash

   # Build with auto-discovered project file
   gbs project build

   # Build specific project file
   gbs project -f my_project.gbs.yaml build

   # Build with debug output
   gbs -d project build

   # Build with 8 parallel jobs
   gbs project build -j 8

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

Repository Commands
-------------------

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

   gbs repo list /path/to/nsl

   # Output:
   # Repository: nsl
   # Root: /path/to/nsl
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

   gbs repo validate /path/to/my_lib

   # Output:
   # Repository: my_lib
   # Libraries: 3
   # Partitions: 12
   #
   # ✓ Repository is valid

gbs repo query
~~~~~~~~~~~~~~

Query dependency traversal with filter variables.

.. code-block:: bash

   gbs repo query PATH -p PARTITION [-f VAR=VALUE]...

**Arguments:**

``PATH``
    Repository path.

``-p, --partition PARTITION``
    Starting partition (format: ``library.partition``).

``-f, --filter VAR=VALUE``
    Filter variable (can be repeated).

**Example:**

.. code-block:: bash

   gbs repo query /path/to/nsl -p nsl_data.text -f vendor=xilinx

   # Output:
   # Query: nsl_data.text
   # Filters: {'vendor': 'xilinx'}
   #
   # Dependency tree (4 partitions):
   #
   #   nsl_data.bytestream
   #     Sources: 3 files
   #
   #   nsl_data.crc
   #     Sources: 2 files
   #     Depends on: nsl_data.bytestream
   #
   # → nsl_data.text
   #     Sources: 4 files
   #     Depends on: nsl_data.bytestream, nsl_data.crc
   #
   # Build order: nsl_data.bytestream → nsl_data.crc → nsl_data.text

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

GBS writes detailed logs to ``.gbs/logs/`` in the current directory:

- ``gbs.log``: Current session log
- ``gbs.TIMESTAMP.log``: Archived logs

Use ``--log-dir`` to change the log directory.

Log levels:

- Default: Only ERROR to console
- ``-v``: INFO and above to console
- ``-d``: DEBUG and above to console

All levels are always written to log files.
