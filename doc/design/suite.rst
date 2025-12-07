========================================
Suite: Multi-Project Build Orchestration
========================================

:Author: GBS Development Team
:Status: Design
:Created: 2025-01-15

Overview
========

The **Suite** feature enables GBS to orchestrate builds across multiple projects, providing
comprehensive CI/CD integration with JUnit output, parallel execution, and intelligent
file-based filtering.

Motivation
==========

CI/CD Requirements
------------------

Modern hardware development requires:

* Building and testing multiple related projects together
* Generating machine-readable test reports (JUnit XML)
* Skipping unaffected projects when only specific files change
* Parallel execution to reduce CI pipeline time
* Comprehensive logging and result aggregation

Current Limitations
-------------------

Today, GBS operates on single projects. For CI/CD scenarios with multiple projects:

* Manual orchestration required (scripts, Makefiles)
* No standard output format for CI tools
* No automatic detection of which projects need rebuilding
* Difficult to track results across multiple builds
* No built-in parallelization across projects

Goals
=====

Primary Goals
-------------

1. **Multi-Project Orchestration**: Define and build collections of related projects
2. **CI/CD Integration**: Generate JUnit XML and JSON summaries
3. **Intelligent Filtering**: Skip projects unaffected by file changes
4. **Parallel Execution**: Build multiple projects simultaneously
5. **Result Aggregation**: Collect and organize build outputs and logs

Non-Goals
---------

* Running actual tests (that's for backend-specific test runners)
* Replacing CI/CD systems (we integrate with them)
* Version control integration (we accept file lists from any source)
* Distributed builds across machines

Architecture
============

Terminology
-----------

Suite
    A collection of projects to build together, defined in ``suite.gbs.yaml``

Project Reference
    A reference to a project within a suite, optionally with output group selection
    and parameter overrides

Build Plan
    The result of project planning, including resolved source files (already exists in GBS)

Suite Executor
    Orchestrates building multiple projects in parallel with result collection

Components
----------

::

    ┌─────────────┐
    │ suite.gbs.  │
    │   yaml      │
    └──────┬──────┘
           │
           ▼
    ┌─────────────┐      ┌──────────────┐
    │ Suite       │─────▶│ ProjectRef   │ (multiple)
    │ Loader      │      │              │
    └──────┬──────┘      └──────┬───────┘
           │                    │
           ▼                    ▼
    ┌─────────────┐      ┌──────────────┐
    │ Suite       │      │ Project      │
    │ Executor    │◄─────│ (existing)   │
    └──────┬──────┘      └──────────────┘
           │
           ├──────────────┬──────────────┬──────────────┐
           ▼              ▼              ▼              ▼
    ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
    │ JUnit    │   │ Summary  │   │ Console  │   │ Log      │
    │ Formatter│   │ JSON     │   │ Reporter │   │ Capture  │
    └──────────┘   └──────────┘   └──────────┘   └──────────┘

Data Flow
---------

1. **Load Suite**: Parse ``suite.gbs.yaml`` and validate all project references
2. **Filter Projects**: If changed files provided, determine which projects need building
3. **Sort by Dependencies**: Topologically sort projects based on ``depends_on``
4. **Execute in Parallel**: Build projects respecting dependencies and ``max_parallel``
5. **Capture Results**: Collect stdout/stderr, timing, and status for each build
6. **Generate Outputs**: Write JUnit XML, summary JSON, and organize logs

Data Models
===========

Core Types
----------

Suite Definition
^^^^^^^^^^^^^^^^

.. code-block:: python

    @dataclass
    class Suite:
        """A suite of projects to build"""
        name: str
        description: Optional[str] = None
        settings: SuiteSettings = field(default_factory=SuiteSettings)
        projects: list[ProjectReference] = field(default_factory=list)

Suite Settings
^^^^^^^^^^^^^^

.. code-block:: python

    @dataclass
    class SuiteSettings:
        """Suite-level build settings"""
        max_parallel: int = 4  # Max parallel projects
        output_dir: Path = Path("suite-results")
        continue_on_error: bool = True  # Keep building if one fails
        capture: CaptureSettings = field(default_factory=CaptureSettings)
        junit: JUnitSettings = field(default_factory=JUnitSettings)
        filter: Optional[FilterSettings] = None

Capture Settings
^^^^^^^^^^^^^^^^

.. code-block:: python

    @dataclass
    class CaptureSettings:
        """Output capture configuration"""
        stdout: bool = True
        stderr: bool = True
        log_level: str = "INFO"  # DEBUG, INFO, WARNING, ERROR
        max_lines: int = 1000  # 0 = unlimited

JUnit Settings
^^^^^^^^^^^^^^

.. code-block:: python

    @dataclass
    class JUnitSettings:
        """JUnit XML output configuration"""
        enabled: bool = True
        output_file: Path = Path("results.xml")
        suite_name: Optional[str] = None  # Defaults to suite.name
        include_stdout: bool = True
        include_stderr: bool = True

Filter Settings
^^^^^^^^^^^^^^^

.. code-block:: python

    @dataclass
    class FilterSettings:
        """File-based project filtering"""
        paths_file: Optional[Path] = None  # File with changed paths
        paths: list[str] = field(default_factory=list)  # Explicit paths
        mode: str = "skip_unaffected"  # skip_unaffected | only_affected
        always_rebuild_on: list[str] = field(default_factory=lambda: [
            "**/*.gbs.yaml",
            "**/suite.gbs.yaml"
        ])

Project Reference
^^^^^^^^^^^^^^^^^

.. code-block:: python

    @dataclass
    class ProjectReference:
        """Reference to a project in the suite"""
        path: Path  # To project file or directory
        name: Optional[str] = None  # Override project name
        output_groups: Optional[list[str]] = None  # Subset (None = all)
        filter_vars: dict[str, Any] = field(default_factory=dict)
        max_parallel: Optional[int] = None  # Override for this project
        enabled: bool = True
        depends_on: list[str] = field(default_factory=list)

File Format
===========

Suite Definition File
---------------------

``suite.gbs.yaml``:

.. code-block:: yaml

    name: my_hardware_suite
    description: Complete verification suite for FPGA designs

    settings:
      # Parallel execution
      max_parallel: 4

      # Output organization
      output_dir: suite-results
      continue_on_error: true

      # Log capture
      capture:
        stdout: true
        stderr: true
        log_level: INFO
        max_lines: 1000

      # JUnit output
      junit:
        enabled: true
        output_file: results.xml
        suite_name: ${name}
        include_stdout: true
        include_stderr: true

      # File-based filtering
      filter:
        mode: skip_unaffected
        always_rebuild_on:
          - "**/*.gbs.yaml"
          - "**/*.tcl"
          - "constraints/**"

    # Project list
    projects:
      # Simple reference (all output groups)
      - path: ./projects/design_a/project.gbs.yaml

      # With customization
      - path: ./projects/design_b/project.gbs.yaml
        name: design_b_sim
        output_groups:
          - simulation
        enabled: true

      # Directory reference (finds project.gbs.yaml)
      - path: ./projects/design_c
        output_groups:
          - simulation
          - synthesis
        max_parallel: 2

      # With filter variable overrides
      - path: ./projects/parameterized
        name: param_variant_a
        output_groups: [test]
        filter_vars:
          variant: a
          debug: 1

      # Same project, different config
      - path: ./projects/parameterized
        name: param_variant_b
        output_groups: [test]
        filter_vars:
          variant: b

      # With dependencies
      - path: ./projects/library
        name: common_lib

      - path: ./projects/app_design
        depends_on: [common_lib]

      - path: ./projects/test_design
        depends_on: [app_design, common_lib]

Output Structure
================

Directory Layout
----------------

::

    suite-results/
    ├── results.xml              # JUnit XML
    ├── summary.json             # Machine-readable summary
    ├── suite.log                # Suite-level log
    ├── design_a/
    │   ├── simulation/
    │   │   ├── build.log       # Captured stdout/stderr
    │   │   ├── gbs.log         # GBS internal log
    │   │   └── outputs/        # Symlinks to actual outputs
    │   └── synthesis/
    │       └── ...
    ├── design_b_sim/
    │   └── simulation/
    │       └── ...
    └── design_c/
        ├── simulation/
        └── synthesis/

JUnit XML Format
----------------

.. code-block:: xml

    <testsuites name="my_hardware_suite" tests="5" failures="1" errors="0"
                skipped="1" time="123.45">

      <!-- Successful build -->
      <testsuite name="design_a" tests="2" failures="0" errors="0" time="45.2">
        <testcase name="simulation" classname="design_a" time="23.1">
          <system-out><![CDATA[
            Loading project: project.gbs.yaml
            Planning build for 1 output group(s)...
            Build complete!
          ]]></system-out>
        </testcase>
        <testcase name="synthesis" classname="design_a" time="22.1"/>
      </testsuite>

      <!-- Failed build -->
      <testsuite name="design_b_sim" tests="1" failures="1" errors="0" time="34.5">
        <testcase name="simulation" classname="design_b_sim" time="34.5">
          <failure message="Build failed" type="BuildError"><![CDATA[
            Error: synthesis failed: undefined signal 'clk'
            File: src/top.vhd:45
          ]]></failure>
          <system-out><![CDATA[
            [build output up to failure...]
          ]]></system-out>
        </testcase>
      </testsuite>

      <!-- Skipped (no files changed) -->
      <testsuite name="design_c" tests="2" skipped="2" time="0">
        <testcase name="simulation" classname="design_c">
          <skipped message="No relevant files changed"/>
        </testcase>
        <testcase name="synthesis" classname="design_c">
          <skipped message="No relevant files changed"/>
        </testcase>
      </testsuite>
    </testsuites>

Summary JSON Format
-------------------

.. code-block:: json

    {
      "suite": "my_hardware_suite",
      "start_time": "2025-01-15T10:30:00Z",
      "end_time": "2025-01-15T10:32:03Z",
      "duration_seconds": 123.45,
      "total_projects": 3,
      "total_output_groups": 5,
      "passed": 3,
      "failed": 1,
      "skipped": 1,
      "projects": [
        {
          "name": "design_a",
          "path": "./projects/design_a/project.gbs.yaml",
          "status": "passed",
          "duration": 45.2,
          "output_groups": [
            {
              "name": "simulation",
              "status": "passed",
              "duration": 23.1,
              "output_dir": "suite-results/design_a/simulation"
            },
            {
              "name": "synthesis",
              "status": "passed",
              "duration": 22.1,
              "output_dir": "suite-results/design_a/synthesis"
            }
          ]
        },
        {
          "name": "design_b_sim",
          "path": "./projects/design_b/project.gbs.yaml",
          "status": "failed",
          "duration": 34.5,
          "output_groups": [
            {
              "name": "simulation",
              "status": "failed",
              "error": "BuildError: synthesis failed",
              "duration": 34.5,
              "output_dir": "suite-results/design_b_sim/simulation"
            }
          ]
        },
        {
          "name": "design_c",
          "status": "skipped",
          "reason": "No relevant files changed",
          "output_groups": []
        }
      ]
    }

API Design
==========

Project Extensions
------------------

Add methods to ``Project`` class for CI filtering:

.. code-block:: python

    class Project:
        async def get_source_files(
            self,
            output_group_names: Optional[list[str]] = None
        ) -> dict[str, set[Path]]:
            """Get source files for output groups without building

            Runs planning phase only to extract source file lists.
            Useful for determining if a project needs rebuilding.

            Args:
                output_group_names: Which output groups to plan for (None = all)

            Returns:
                Dict mapping output_group_name -> set of source file paths

            Example:
                >>> sources = await project.get_source_files(["simulation"])
                >>> sources
                {'simulation': {Path('src/top.vhd'), Path('src/uart.vhd')}}
            """

        async def needs_rebuild(
            self,
            changed_files: set[Path],
            output_group_names: Optional[list[str]] = None,
            always_rebuild_patterns: Optional[list[str]] = None
        ) -> tuple[bool, str]:
            """Check if project needs rebuild based on changed files

            Args:
                changed_files: Set of changed file paths (absolute)
                output_group_names: Which output groups to check
                always_rebuild_patterns: Glob patterns that always trigger rebuild

            Returns:
                (needs_rebuild, reason) tuple

            Example:
                >>> needs, reason = await project.needs_rebuild(
                ...     changed_files={Path("src/uart.vhd").resolve()},
                ...     always_rebuild_patterns=["**/*.gbs.yaml"]
                ... )
                >>> needs, reason
                (True, "Source file changed in simulation: uart.vhd")
            """

Suite Executor
--------------

.. code-block:: python

    class SuiteExecutor:
        """Orchestrates building multiple projects"""

        async def build_suite(
            self,
            suite: Suite,
            changed_files: Optional[set[Path]] = None
        ) -> SuiteResults:
            """Build all enabled projects in suite

            Args:
                suite: Suite definition
                changed_files: Optional set of changed files for filtering

            Returns:
                SuiteResults with build status, timing, and captured output
            """

        async def _load_project(
            self,
            proj_ref: ProjectReference
        ) -> Project:
            """Load and configure a project from reference"""

        async def _filter_projects(
            self,
            projects: list[tuple[ProjectReference, Project]],
            changed_files: set[Path],
            filter_settings: FilterSettings
        ) -> list[tuple[ProjectReference, Project]]:
            """Filter projects based on changed files"""

        async def _sort_by_dependencies(
            self,
            projects: list[tuple[ProjectReference, Project]],
            suite: Suite
        ) -> list[tuple[ProjectReference, Project]]:
            """Topologically sort projects by dependencies"""

        async def _build_projects_parallel(
            self,
            projects: list[tuple[ProjectReference, Project]],
            settings: SuiteSettings
        ):
            """Build projects in parallel respecting dependencies"""

Formatters
----------

.. code-block:: python

    class JUnitFormatter:
        """Generate JUnit XML from suite results"""

        def format(self, results: SuiteResults) -> str:
            """Generate JUnit XML string"""

    class SummaryFormatter:
        """Generate JSON summary from suite results"""

        def format(self, results: SuiteResults) -> str:
            """Generate JSON summary string"""

Implementation Plan
===================

Phase 1: Core Infrastructure (Week 1) - ✓ COMPLETED
-------------------------------------------------------

**Status**: Completed 2025-01-15

**Goal**: Basic suite definition and loading

Deliverables:
~~~~~~~~~~~~~

1. ✓ Data models (``src/gbs/suite/model.py``)

   * ``Suite``, ``SuiteSettings``, ``ProjectReference``
   * ``FilterSettings``, ``OutputSettings``
   * ``ProjectResult``, ``SuiteResult``
   * Enum types for status tracking

2. ✓ Suite loader (``src/gbs/suite/loader.py``)

   * Parse ``suite.gbs.yaml``
   * Validate project paths and required fields
   * Resolve relative paths from suite directory
   * Validate dependencies and detect cycles
   * Comprehensive error handling

3. ✓ Documentation

   * Sphinx user guide (``doc/suite.rst``)
   * API reference (``doc/api/index.rst``)
   * Design document in Architecture section

All tests pass (93/93). Existing projects build successfully.

Commit: 7316cc5 "suite: Add Phase 1 - Core Infrastructure"

Phase 2: Project API Extensions - ✓ COMPLETED
------------------------------------------------

**Status**: Completed 2025-01-15

**Goal**: Add project methods for source file extraction and filtering

Deliverables:
~~~~~~~~~~~~~

1. ✓ Project API extensions (``src/gbs/project/project.py``)

   * ``get_source_files()`` method - Extract source files from planning
   * ``needs_rebuild()`` method - Check if changed files affect project

These methods enable intelligent filtering in suite execution by using
actual build planning results rather than heuristics.

All tests pass (93/93). Existing projects build successfully.

Commit: 1839312 "suite: Add Phase 2 - Project API Extensions"

Phase 3: Suite Executor - ✓ COMPLETED
---------------------------------------

**Status**: Completed 2025-01-15

**Goal**: Build multiple projects with parallel execution and dependency management

Deliverables:
~~~~~~~~~~~~~

1. ✓ Suite executor (``src/gbs/suite/executor.py``)

   * Load all projects from suite definition
   * Topological sort for dependency ordering
   * Dependency level grouping for parallel execution
   * File-based filtering with change detection
   * Semaphore-based parallelism control
   * Comprehensive result tracking and timing

Features implemented:
- Respects project dependencies (builds in correct order)
- Parallel execution within dependency levels
- File-based filtering skips unaffected projects
- Cascading rebuilds when dependencies change
- Configurable stop-on-failure behavior
- Per-project and suite-level parallelism limits

All tests pass (93/93). Existing projects build successfully.

Commit: ec67896 "suite: Add Phase 3 - SuiteExecutor"

Phase 3: Output Formatters (Week 3)
------------------------------------

**Goal**: Generate CI-friendly output formats

Deliverables:
~~~~~~~~~~~~~

1. JUnit formatter (``src/gbs/suite/formatters/junit.py``)

   * Generate valid JUnit XML
   * Include captured logs
   * Map failures correctly

2. Summary formatter (``src/gbs/suite/formatters/summary.py``)

   * Generate JSON summary
   * Include all timing and status data

3. Log capture and management

   * Respect ``max_lines`` limits
   * Organize by project and output group
   * Create directory structure

4. Tests

   * JUnit XML validation
   * JSON schema validation
   * Log capture limits

Phase 4: File-Based Filtering (Week 4)
---------------------------------------

**Goal**: Skip projects unaffected by file changes

Deliverables:
~~~~~~~~~~~~~

1. Project extensions

   * ``needs_rebuild()`` method
   * Source file extraction from planning

2. Filter integration in executor

   * Process ``changed_files`` parameter
   * Apply ``always_rebuild_patterns``
   * Log skip reasons

3. Git integration helper

   * Parse ``git diff`` output
   * Handle various git ref formats

4. Tests

   * File filtering logic
   * Pattern matching
   * Skip detection

Phase 5: CLI Integration (Week 5)
----------------------------------

**Goal**: Expose suite functionality via CLI

Deliverables:
~~~~~~~~~~~~~

1. Suite command group (``src/gbs/cli/suite.py``)

   * ``gbs suite build``
   * ``gbs suite list``
   * ``gbs suite validate``
   * ``gbs suite clean``

2. CLI options

   * ``--changed-files FILE``
   * ``--git-diff REF``
   * ``--include-pattern PATTERN``
   * ``-j N``

3. Help documentation

   * Command help text
   * Usage examples

4. Tests

   * CLI invocation
   * Option parsing
   * Error messages

Phase 6: Advanced Features (Week 6)
------------------------------------

**Goal**: Parallelism and dependencies

Deliverables:
~~~~~~~~~~~~~

1. Parallel execution

   * Respect ``max_parallel`` limit
   * Two-level parallelism (projects and output groups)
   * Shared semaphore management

2. Dependency support

   * Topological sort
   * Transitive dependency detection
   * Cyclic dependency errors

3. Enhanced output

   * Better progress reporting
   * Parallel-safe logging
   * Result streaming

4. Tests

   * Parallel builds
   * Dependency ordering
   * Semaphore limits

Usage Examples
==============

Basic Suite Build
-----------------

.. code-block:: bash

    # Build entire suite
    gbs suite build suite.gbs.yaml

    # Results in:
    # - suite-results/results.xml (JUnit)
    # - suite-results/summary.json
    # - suite-results/*/build.log

CI/CD Integration
-----------------

GitHub Actions:

.. code-block:: yaml

    - name: Build hardware suite
      run: |
        gbs suite build \
          --git-diff ${{ github.event.before }} \
          suite.gbs.yaml

    - name: Publish test results
      uses: EnricoMi/publish-unit-test-result-action@v2
      if: always()
      with:
        files: suite-results/results.xml

GitLab CI:

.. code-block:: yaml

    test:
      script:
        - gbs suite build --git-diff $CI_MERGE_REQUEST_DIFF_BASE_SHA suite.gbs.yaml
      artifacts:
        reports:
          junit: suite-results/results.xml
        paths:
          - suite-results/

Custom Change Detection
-----------------------

.. code-block:: bash

    # Manual file list
    cat > changed.txt << EOF
    src/common/uart.vhd
    lib/fifo/fifo.v
    project_a/top.vhd
    EOF

    gbs suite build --changed-files changed.txt suite.gbs.yaml

    # From stdin
    find src/ -name "*.vhd" -newer .last-build | \
      gbs suite build --changed-files - suite.gbs.yaml

Parallel Control
----------------

.. code-block:: bash

    # Override suite max_parallel
    gbs suite build -j 8 suite.gbs.yaml

    # With filtering
    gbs suite build -j 8 --git-diff origin/main suite.gbs.yaml

Validation
----------

.. code-block:: bash

    # Check suite definition
    gbs suite validate suite.gbs.yaml

    # List what would be built
    gbs suite list suite.gbs.yaml

    # With filtering
    gbs suite list --git-diff origin/main suite.gbs.yaml

Future Enhancements
===================

Planned Features
----------------

1. **Artifact Collection**

   .. code-block:: yaml

       settings:
         artifacts:
           - pattern: "**/*.fs"
             destination: artifacts/bitstreams/
           - pattern: "**/timing-report.txt"

2. **Matrix Builds**

   .. code-block:: yaml

       projects:
         - path: ./design
           matrix:
             variant: [a, b, c]
             debug: [0, 1]
           # Creates 6 builds: a-0, a-1, b-0, b-1, c-0, c-1

3. **Workspace Support**

   .. code-block:: yaml

       workspace:
         suites:
           - verification.suite.yaml
           - synthesis.suite.yaml
           - timing.suite.yaml

4. **Result Caching**

   * Cache planning results
   * Incremental rebuilds
   * Shared output directories

5. **Distributed Builds**

   * Build coordination protocol
   * Remote executor support
   * Result aggregation

6. **Enhanced Reporting**

   * HTML dashboards
   * Trend analysis
   * Resource utilization graphs

Considered but Deferred
-----------------------

* **Test execution**: Left to backend-specific test runners
* **Version pinning**: Use git submodules or repository configs
* **Cloud integration**: Use standard CI/CD tools
* **Interactive mode**: Suite is batch-oriented for CI

References
==========

Related Documents
-----------------

* ``build_system.rst`` - Core build system design
* ``configuration.rst`` - Configuration precedence
* ``plugins.rst`` - Backend extension mechanism

External Standards
------------------

* `JUnit XML Format <https://llg.cubic.org/docs/junit/>`_
* `JSON Schema <https://json-schema.org/>`_
* `Test Anything Protocol <https://testanything.org/>`_

Open Questions
==============

1. **Timeout handling**: Should individual projects have timeouts?

2. **Retry logic**: Should failed builds be retried automatically?

3. **Notification**: Should suite send notifications (email, Slack) on completion?

4. **Resource limits**: Should we enforce memory/disk limits per project?

5. **Lock files**: Should suite generate lock files for reproducibility?

These will be addressed during implementation based on user feedback.

Revision History
================

.. list-table::
   :header-rows: 1

   * - Version
     - Date
     - Author
     - Changes
   * - 1.0
     - 2025-01-15
     - GBS Team
     - Initial design document
