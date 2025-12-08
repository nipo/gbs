Suite API Reference
===================

The suite module provides functionality for orchestrating multiple GBS projects
in test suites, with support for parallel execution, dependency management,
and CI/CD integration.

Overview
--------

The suite system consists of:

- **Model**: Data structures for suite configuration and execution results
- **Loader**: Loading and validating suite files
- **Executor**: Running suites with parallel project execution
- **Formatters**: Output formatting (JUnit XML, summary JSON)

Suite Model
-----------

.. automodule:: gbs.suite.model
   :members:
   :undoc-members:
   :show-inheritance:

Suite Loader
------------

.. automodule:: gbs.suite.loader
   :members:
   :undoc-members:
   :show-inheritance:

Suite Executor
--------------

.. automodule:: gbs.suite.executor
   :members:
   :undoc-members:
   :show-inheritance:

Output Formatters
-----------------

JUnit Formatter
~~~~~~~~~~~~~~~

.. automodule:: gbs.suite.formatters.junit
   :members:
   :undoc-members:
   :show-inheritance:

Summary Formatter
~~~~~~~~~~~~~~~~~

.. automodule:: gbs.suite.formatters.summary
   :members:
   :undoc-members:
   :show-inheritance:

Key Classes
-----------

SuiteSettings
~~~~~~~~~~~~~

Global configuration for suite execution including parallelism settings,
failure handling, and output options.

.. code-block:: python

   from gbs.suite.model import SuiteSettings, OutputSettings, FilterSettings

   settings = SuiteSettings(
       max_parallel_projects=4,
       max_parallel_tasks=8,
       stop_on_failure=False,
       output=OutputSettings(
           junit_xml=Path("results.xml"),
           save_logs=True,
       ),
       filter=FilterSettings(
           enabled=True,
           base_commit="origin/main"
       )
   )

ProjectReference
~~~~~~~~~~~~~~~~

Represents a project within a suite, including its path, dependencies,
and execution status.

.. code-block:: python

   from gbs.suite.model import ProjectReference

   project = ProjectReference(
       name="uart_test",
       path=Path("tests/uart"),
       depends_on=["common"],
       output_groups=["simulation"]
   )

SuiteResult
~~~~~~~~~~~

Execution results for the entire suite, including per-project results,
timing information, and overall status.

.. code-block:: python

   # After executing a suite
   result = await suite.execute()

   print(f"Suite: {result.suite_name}")
   print(f"Status: {result.status.value}")
   print(f"Duration: {result.duration:.2f}s")
   print(f"Projects: {result.success_count} success, {result.failure_count} failed")

Usage Examples
--------------

Loading a Suite
~~~~~~~~~~~~~~~

.. code-block:: python

   from pathlib import Path
   from gbs.suite.loader import load_suite

   # Load suite from file
   suite = load_suite(Path("tests/suite.gbs.yaml"))

   # Access suite configuration
   print(f"Suite: {suite.name}")
   print(f"Projects: {len(suite.projects)}")

Executing a Suite
~~~~~~~~~~~~~~~~~

.. code-block:: python

   from gbs.suite.executor import SuiteExecutor
   from gbs.suite.model import SuiteSettings, OutputSettings

   # Configure output
   settings = SuiteSettings(
       max_parallel_projects=4,
       output=OutputSettings(
           junit_xml=Path("test-results.xml"),
           save_logs=True
       )
   )

   # Create executor
   executor = SuiteExecutor(suite, settings)

   # Run suite
   result = await executor.execute()

   # Check results
   if result.status == SuiteStatus.SUCCESS:
       print("All projects passed!")
   else:
       print(f"Suite failed: {result.failure_count} projects")

File-Based Filtering
~~~~~~~~~~~~~~~~~~~~

Smart test selection based on changed files:

.. code-block:: python

   from gbs.suite.model import FilterSettings

   # Filter based on git diff
   filter_settings = FilterSettings(
       enabled=True,
       base_commit="origin/main",
       target_commit="HEAD"
   )

   settings = SuiteSettings(filter=filter_settings)
   result = await executor.execute()

   # Only projects affected by changes are built
   for project_result in result.project_results:
       if project_result.status == ProjectStatus.SKIPPED:
           print(f"Skipped {project_result.project_name} (no relevant changes)")

Dependency Management
~~~~~~~~~~~~~~~~~~~~~

Projects can depend on other projects:

.. code-block:: yaml

   # suite.gbs.yaml
   projects:
     - name: common
       path: libs/common

     - name: uart
       path: peripherals/uart
       depends_on: [common]

     - name: system
       path: system
       depends_on: [uart, common]

The executor automatically:

- Builds projects in dependency order
- Runs independent projects in parallel
- Skips dependent projects if dependencies fail

See Also
--------

- :doc:`../suite` - User guide for test suites
- :doc:`../design/suite` - Architecture and design documentation
