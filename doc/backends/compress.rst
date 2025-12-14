Compress Backend
================

The compress backend provides automatic file compression using type suffixes.

Overview
--------

The compress backend is a generic utility that automatically compresses build outputs based on file type suffixes. Instead of creating separate tasks for compression, you simply add compression suffixes (like ``+gzip``) to your output types, and GBS handles the compression automatically.

Supported Compression
---------------------

Currently supported compression formats:

- ``+gzip``: Gzip compression using Python's gzip module

Compression suffixes can be chained with other transforms for multi-step processing.

How It Works
------------

The compress backend works backwards from unsatisfied outputs:

1. Scans for outputs with compression suffixes (e.g., ``ise-bitstream+gzip``)
2. Strips the last transform suffix to get the source type (e.g., ``ise-bitstream``)
3. Locates the source file in the build graph
4. Creates a compression task from source to compressed output

For chained transforms like ``+gzip+base64``, each iteration handles the outermost transform first, creating intermediate goals for inner transforms.

Configuration
-------------

No configuration needed! The compress backend is a generic dispatcher that activates automatically when you use compression suffixes in output types.

Example Project
---------------

Simple gzip compression:

.. code-block:: yaml

   name: fpga_project
   root_library_name: work

   output_groups:
     - name: synthesis
       topcell: top_module
       outputs:
         - type: ise-bitstream
           path: build/firmware.bit
         - type: ise-bitstream+gzip
           path: build/firmware.bit.gz

   root_partition_template:
     dependencies:
       - library.partition

In this example, GBS will:

1. Generate ``firmware.bit`` (ISE bitstream)
2. Automatically compress it to ``firmware.bit.gz``

Chained Transforms
------------------

You can chain multiple transforms (though currently only gzip is implemented):

.. code-block:: yaml

   outputs:
     - type: gowin-fs+gzip
       path: build/firmware.fs.gz

The compress dispatcher handles one transform at a time, working from the outermost suffix inward. For ``+gzip+base64``:

1. First iteration: create intermediate goal for ``gowin-fs+gzip``
2. Second iteration: compress ``gowin-fs`` → ``gowin-fs+gzip``
3. Third iteration: encode ``gowin-fs+gzip`` → ``gowin-fs+gzip+base64``

Intermediate Files
------------------

When the source file isn't available yet, the compress backend creates intermediate output goals. For example, if you request ``ise-bitstream+gzip`` but ``ise-bitstream`` doesn't exist yet:

1. Compress backend creates intermediate goal: ``ise-bitstream``
2. ISE backend satisfies that goal
3. On next iteration, compress backend compresses it

This allows the build system to automatically satisfy dependencies.

File Extensions
---------------

The compress backend adds appropriate file extensions:

- ``+gzip``: Adds ``.gz`` extension

For example:
  - Source: ``firmware.bit`` (type: ``ise-bitstream``)
  - Compressed: ``firmware.bit.gz`` (type: ``ise-bitstream+gzip``)

Dispatcher Priority
-------------------

The compress backend runs at priority 850, which means:

- Runs **after** main compilation backends (priority 1000-2000)
- Runs **before** output-copy backend (priority 800)

This ensures compressed files are available before final output copying.

Use Cases
---------

**Firmware Distribution:**
  Compress bitstreams for network distribution:

  .. code-block:: yaml

     outputs:
       - type: vivado-bitstream+gzip
         path: dist/firmware.bit.gz

**Embedded Systems:**
  Reduce storage size for bitstreams stored in flash:

  .. code-block:: yaml

     outputs:
       - type: ice40-bitstream+gzip
         path: firmware/compressed.bin.gz

**Build Artifacts:**
  Compress large intermediate files to save disk space:

  .. code-block:: yaml

     outputs:
       - type: vivado-netlist-edif+gzip
         path: artifacts/netlist.edif.gz

Implementation Details
----------------------

The compress backend uses Python's ``gzip`` module for compression. Compression runs in a thread pool to avoid blocking the async event loop.

The ``GzipTask`` class:

- Takes a source ``Resource`` and destination ``Resource``
- Compresses using ``gzip.open()`` with ``shutil.copyfileobj()``
- Runs asynchronously via ``asyncio.to_thread()``
- Respects the build context's parallelism semaphore

Extending the Backend
---------------------

To add new compression formats, register them in ``COMPRESSION_HANDLERS`` dictionary:

.. code-block:: python

   COMPRESSION_HANDLERS = {
       "gzip": (GzipTask, ".gz"),
       "bzip2": (Bzip2Task, ".bz2"),  # Example extension
   }

Each handler provides:

- Task class implementing the compression
- File extension to append

Requirements
------------

- Python 3.13+ (uses ``asyncio.to_thread``)
- No external tools required (uses Python's built-in ``gzip`` module)

See Also
--------

- :doc:`output_copy` - Copies outputs to final locations (runs after compression)
- Python gzip documentation: https://docs.python.org/3/library/gzip.html
