Test Suites
===========

.. note::
   Suite functionality is currently in development. Phase 1 (Core Infrastructure)
   has been completed. This documentation covers the planned full feature set.

GBS supports organizing multiple projects into test suites for CI/CD integration
and regression testing. A suite allows you to build multiple projects in parallel,
with intelligent filtering based on changed files, and generate standard test reports.

Overview
--------

A **Suite** is a collection of GBS projects that should be built and tested together.
Suites are defined in a ``suite.gbs.yaml`` file and provide:

- Parallel execution across multiple projects
- Dependency management between projects
- File-based filtering to skip unchanged projects
- JUnit XML output for CI/CD integration
- Comprehensive logging and error reporting

Basic Suite File
----------------

A minimal suite definition:

.. code-block:: yaml

   name: my_test_suite
   description: Regression tests for my gateware components

   projects:
     - name: uart
       path: ./projects/uart

     - name: spi_master
       path: ./projects/spi

     - name: system
       path: ./projects/system
       depends_on: [uart, spi_master]

Suite Configuration
-------------------

Settings
~~~~~~~~

Global suite settings control execution behavior:

.. code-block:: yaml

   settings:
     # Maximum number of projects to build in parallel
     max_parallel_projects: 4

     # Override max_parallel for all projects
     max_parallel_tasks: 8

     # Stop entire suite on first failure
     stop_on_failure: false

     # Continue building other projects after error
     continue_on_error: true

Project References
~~~~~~~~~~~~~~~~~~

Each project in a suite is a **ProjectReference** with the following options:

.. code-block:: yaml

   projects:
     - name: unique_name          # Required: Unique identifier
       path: ./path/to/project    # Required: Path to project file or directory

       # Optional: Select specific output groups to build
       output_groups: [simulation, bitstream]

       # Optional: Override parallelism for this project
       max_parallel: 4

       # Optional: Dependencies (build order)
       depends_on: [other_project]

       # Optional: Tags for filtering
       tags: [uart, peripheral]

       # Optional: Skip this project
       skip: false

Path Resolution
~~~~~~~~~~~~~~~

Project paths are resolved relative to the suite file directory:

- Absolute paths: Used as-is
- Relative paths: Resolved from suite file location

Example::

   /workspace/
     suite.gbs.yaml         # Suite file
     projects/
       uart/
         project.gbs.yaml
       spi/
         project.gbs.yaml

Suite configuration:

.. code-block:: yaml

   projects:
     - name: uart
       path: ./projects/uart          # Resolves to /workspace/projects/uart

     - name: spi
       path: /abs/path/to/spi         # Absolute path

Dependencies
~~~~~~~~~~~~

Projects can declare dependencies to control build order:

.. code-block:: yaml

   projects:
     - name: core
       path: ./core

     - name: peripheral
       path: ./peripheral
       depends_on: [core]

     - name: system
       path: ./system
       depends_on: [core, peripheral]

Build order is determined by topological sort. Circular dependencies are detected
and reported as errors.

File-Based Filtering
--------------------

.. note::
   File-based filtering is planned for Phase 3.

Intelligent filtering allows suites to skip projects unaffected by changes.
This is particularly useful in CI/CD to avoid rebuilding unrelated projects.

Configuration
~~~~~~~~~~~~~

.. code-block:: yaml

   settings:
     filter:
       enabled: true

       # Option 1: File list
       file_list: changed_files.txt

       # Option 2: Explicit file list
       files:
         - src/uart/uart.vhd
         - src/common/types.vhd

       # Option 3: Git diff
       base_commit: HEAD~1
       target_commit: HEAD

How It Works
~~~~~~~~~~~~

1. GBS loads each project and runs the planning phase
2. Planning resolves all source files used by each project
3. Projects are compared against the changed file list
4. Projects with no overlapping files are skipped
5. Projects with dependencies on non-skipped projects are built

This approach is accurate because it uses actual build plans rather than heuristics.

Output Configuration
--------------------

.. note::
   Output generation is planned for Phase 4.

Suites can generate multiple output formats:

.. code-block:: yaml

   settings:
     output:
       # JUnit XML for CI/CD integration
       junit_xml: test-results/junit.xml

       # JSON summary
       summary_json: test-results/summary.json

       # Individual project logs
       log_dir: test-results/logs
       save_logs: true
       log_level: INFO

       # Lines to include in failure output (0 = all)
       tail_lines: 100

JUnit XML Format
~~~~~~~~~~~~~~~~

The JUnit XML output follows the standard format supported by most CI/CD systems:

.. code-block:: xml

   <?xml version="1.0" encoding="UTF-8"?>
   <testsuites name="my_test_suite" tests="3" failures="0" errors="0" time="42.5">
     <testsuite name="uart" tests="1" failures="0" errors="0" time="10.2">
       <testcase classname="uart" name="build" time="10.2">
         <system-out>Build log output...</system-out>
       </testcase>
     </testsuite>
     <!-- More test suites... -->
   </testsuites>

Summary JSON Format
~~~~~~~~~~~~~~~~~~~

The JSON summary provides programmatic access to results:

.. code-block:: json

   {
     "suite": "my_test_suite",
     "status": "success",
     "duration": 42.5,
     "total_projects": 3,
     "successful": 3,
     "failed": 0,
     "errors": 0,
     "skipped": 0,
     "projects": [
       {
         "name": "uart",
         "status": "success",
         "duration": 10.2,
         "log_file": "test-results/logs/uart.log"
       }
     ]
   }

Command Line Interface
----------------------

.. note::
   CLI integration is planned for Phase 5.

Building a Suite
~~~~~~~~~~~~~~~~

Build all projects in a suite::

   gbs suite build suite.gbs.yaml

With options::

   gbs suite build suite.gbs.yaml \
     --jobs 8 \
     --output junit.xml \
     --log-dir logs/

Filtering Projects
~~~~~~~~~~~~~~~~~~

Filter based on changed files::

   # From file
   gbs suite build suite.gbs.yaml --filter changed_files.txt

   # From git diff
   gbs suite build suite.gbs.yaml --filter-git HEAD~1

   # Explicit files
   gbs suite build suite.gbs.yaml --filter-files src/uart.vhd src/types.vhd

List Projects
~~~~~~~~~~~~~

List all projects in a suite::

   gbs suite list suite.gbs.yaml

Show detailed information::

   gbs suite list suite.gbs.yaml --verbose

Clean Suite
~~~~~~~~~~~

Clean all projects in a suite::

   gbs suite clean suite.gbs.yaml

CI/CD Integration
-----------------

.. note::
   Full CI/CD examples will be provided in Phase 5.

GitHub Actions Example
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

   name: GBS Test Suite
   on: [push, pull_request]

   jobs:
     test:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v3
           with:
             fetch-depth: 0  # Need full history for git diff

         - name: Setup Python
           uses: actions/setup-python@v4
           with:
             python-version: '3.11'

         - name: Install GBS
           run: pip install gbs

         - name: Run Test Suite
           run: |
             gbs suite build suite.gbs.yaml \
               --filter-git origin/main \
               --output test-results/junit.xml \
               --log-dir test-results/logs

         - name: Publish Test Results
           uses: EnricoMi/publish-unit-test-result-action@v2
           if: always()
           with:
             files: test-results/junit.xml

GitLab CI Example
~~~~~~~~~~~~~~~~~

.. code-block:: yaml

   test:
     image: python:3.11
     script:
       - pip install gbs
       - gbs suite build suite.gbs.yaml
           --filter-git $CI_MERGE_REQUEST_DIFF_BASE_SHA
           --output junit.xml
     artifacts:
       reports:
         junit: junit.xml

Advanced Usage
--------------

Tags and Filtering
~~~~~~~~~~~~~~~~~~

.. note::
   Tag filtering is a future enhancement.

Projects can be tagged for selective execution:

.. code-block:: yaml

   projects:
     - name: uart
       path: ./uart
       tags: [peripheral, serial]

     - name: spi
       path: ./spi
       tags: [peripheral, serial]

     - name: system
       path: ./system
       tags: [integration]

Command line filtering::

   # Build only peripherals
   gbs suite build suite.gbs.yaml --tags peripheral

   # Build everything except integration tests
   gbs suite build suite.gbs.yaml --exclude-tags integration

Matrix Builds
~~~~~~~~~~~~~

.. note::
   Matrix builds are a future enhancement.

For testing multiple configurations:

.. code-block:: yaml

   settings:
     matrix:
       vendor: [xilinx, intel]
       optimization: [speed, area]

   projects:
     - name: processor
       path: ./processor
       matrix_vars:
         vendor: ${vendor}
         optimization: ${optimization}

This would generate 4 build combinations.

API Reference
-------------

For programmatic usage, see the :doc:`api/suite` module documentation.

See Also
--------

- :doc:`project_file` - Individual project configuration
- :doc:`cli` - Command line interface reference
- :doc:`design/suite` - Suite feature design document
