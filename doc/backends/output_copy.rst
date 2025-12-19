Output Copy Backend
===================

The output_copy backend copies build artifacts from the build directory to final output locations.

Overview
--------

The output_copy backend is a generic utility that runs late in the build process to copy generated files to their final destinations specified in the project's output group configuration. It's the last step in the build pipeline, running after all compilation and transformation backends.

How It Works
------------

The output_copy backend works backwards from unsatisfied outputs:

1. Identifies output resources that don't have producers yet
2. Searches for matching source files in the build graph by ``file_type``
3. Creates copy tasks from sources to final destinations
4. Preserves file metadata using ``shutil.copy2``

For example, if your project specifies:

.. code-block:: yaml

   outputs:
     - type: vivado-bitstream
       path: firmware/final.bit

And Vivado generated ``build/design.bit``, the output_copy backend will copy it to ``firmware/final.bit``.

Configuration
-------------

No configuration needed! The output_copy backend is a generic dispatcher that activates automatically based on output group specifications.

Example Project
---------------

Basic output copying:

.. code-block:: yaml

   name: fpga_project
   root_library_name: work

   output_groups:
     - name: synthesis
       topcell: top_module
       outputs:
         # ISE generates bitstream in build/
         - type: ise-bitstream
           path: release/firmware.bit

         # Copy timing report to release/
         - type: ise-timing-report
           path: release/timing.txt

   root_partition_template:
     dependencies:
       - library.partition

In this example:

1. ISE backend generates files in the build directory
2. output_copy backend copies them to ``release/`` directory

Multiple Outputs
----------------

You can specify multiple output copies of the same file:

.. code-block:: yaml

   outputs:
     - type: vivado-bitstream
       path: build/design.bit

     - type: vivado-bitstream
       path: release/firmware_v1.2.bit

     - type: vivado-bitstream
       path: dist/fpga.bit

The output_copy backend will copy the same source bitstream to all three locations.

Build Directory Structure
--------------------------

Typical build flow:

.. code-block:: text

   Work Directory (.gbs/work/)
   ├── intermediate files (analysis, synthesis)
   └── generated outputs (bitstreams, reports)
        ↓ (output_copy backend)
   Output Paths (as specified in project)
   ├── firmware/design.bit
   ├── reports/timing.txt
   └── dist/compressed.bit.gz

The output_copy backend bridges the gap between the build directory and your specified output paths.

File Type Matching
------------------

The backend matches files by ``file_type``, not filename. This allows flexible output naming:

.. code-block:: yaml

   # ISE generates "design.bit" internally
   # Output copy renames it during copy
   outputs:
     - type: ise-bitstream
       path: product/my_custom_name_v2.bit

The backend finds the file with ``type: ise-bitstream`` and copies it to the specified path.

Multiple Matches
----------------

If multiple files match the same type, the backend issues a warning and uses the first match:

.. code-block:: text

   WARNING: Multiple files of type 'ise-bitstream' found, using first: build/design1.bit

To avoid ambiguity, ensure your build produces only one file of each type that you reference in outputs.

Clean Behavior
--------------

The output_copy backend tracks all files it copies and includes them in clean operations:

.. code-block:: bash

   # Clean removes both build directory and copied outputs
   gbs project clean

Paths cleaned:

- Build working directory (``.gbs/work/``)
- All output files copied by this backend

Implementation Details
----------------------

The output_copy backend uses:

- ``shutil.copy2()`` for file copying (preserves metadata)
- ``asyncio.to_thread()`` for async execution
- Thread pool to avoid blocking the event loop
- Build context semaphore for parallelism control

The ``CopyTask`` class:

- Takes source and destination ``Resource`` objects
- Creates parent directories automatically
- Removes existing destination file before copying
- Preserves file timestamps and permissions

Use Cases
---------

**Release Organization:**
  Copy build artifacts to a release directory:

  .. code-block:: yaml

     outputs:
       - type: vivado-bitstream
         path: release/v1.2.3/firmware.bit
       - type: vivado-timing-report
         path: release/v1.2.3/timing.rpt

**Multi-Target Distribution:**
  Copy the same bitstream to multiple distribution points:

  .. code-block:: yaml

     outputs:
       - type: ice40-bitstream
         path: firmware/bootloader.bin
       - type: ice40-bitstream
         path: web/downloads/latest.bin
       - type: ice40-bitstream
         path: dist/release.bin

**Custom Naming:**
  Rename outputs for specific deployment requirements:

  .. code-block:: yaml

     outputs:
       - type: gowin-fs
         path: images/fpga_config_12345.fs

**Artifact Archival:**
  Copy reports and outputs to timestamped archives:

  .. code-block:: yaml

     outputs:
       - type: vivado-usage-report
         path: archive/2024-01-15/utilization.rpt

Same-Path Optimization
----------------------

If the source and destination paths are identical (after resolving symlinks), the backend skips the copy operation:

.. code-block:: text

   DEBUG: Source and destination are the same: build/design.bit, skip

This prevents unnecessary copying when the output path matches the build path.

Requirements
------------

- Python 3.13+ (uses ``asyncio.to_thread``)
- No external tools required (uses Python's built-in ``shutil``)
- Write permissions for destination directories

See Also
--------

- :doc:`compress` - Compression backend (runs before output_copy)
- Python shutil documentation: https://docs.python.org/3/library/shutil.html
