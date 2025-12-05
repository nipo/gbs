API Reference
=============

This section provides API documentation from the GBS source code.

.. note::

   Full API documentation is generated using Sphinx autodoc. To build
   the complete API docs, install GBS with development dependencies and
   run ``make html`` in the ``docs/`` directory.

Core Modules
------------

The following modules form the core of GBS:

**gbs.repository.model**
    Data model for repositories, libraries, partitions, and source files.

**gbs.project.model**
    Project and output group data structures.

**gbs.build.task**
    AsyncIO-based task system (Task, Resource, VirtualResource).

**gbs.build.context**
    Build context and file set management.

**gbs.planner.passes**
    Pass system for build planning.

**gbs.planner.planner**
    Build planner that finds transformation paths.

**gbs.backend.protocol**
    Backend protocol and base classes.

**gbs.backend.dispatcher**
    Dispatcher system for build execution.

Module Documentation
--------------------

Repository Model
~~~~~~~~~~~~~~~~

.. automodule:: gbs.repository.model
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:

Project Model
~~~~~~~~~~~~~

.. automodule:: gbs.project.model
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:

Build Task System
~~~~~~~~~~~~~~~~~

.. automodule:: gbs.build.task
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:

Build Context
~~~~~~~~~~~~~

.. automodule:: gbs.build.context
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:

Pass System
~~~~~~~~~~~

.. automodule:: gbs.planner.passes
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:

Build Planner
~~~~~~~~~~~~~

.. automodule:: gbs.planner.planner
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:

Backend Protocol
~~~~~~~~~~~~~~~~

.. automodule:: gbs.backend.protocol
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:

Dispatcher System
~~~~~~~~~~~~~~~~~

.. automodule:: gbs.backend.dispatcher
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:
