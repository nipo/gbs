# GBS: Gateware Build System

## Project definition

GBS is a build system for gateware projects.  Gateware is a type of
software that describes hardware configuration.  It can either be
targetting FPGAs or ASIC.  Input language is usually a hardware
description language like VHDL or Verilog.  Depending on the target
where to run the code, tools may need other types of sideband
information like placement and constraints files.

This build infrastructure is not fitting usual software-oriented build
tools. That's why we are creating a new one.

GBS aims to build "projects" that reference library code organized in
"repositories".

## Data Models

### Repository Source

A "repository" is a set of "libraries" grouped together as a coherent
set of functionalities. This is a source tree. GBS may handle more
than one repository and will index them all together.

Each "library" can be split in "partitions".  For VHDL, usually, a
partition is accompanied by a package declaration file (something that
looks like a header file in C). This is not necessarily the case for
other HDLs.

For each "partition", there can be an arbitrary set of sourcecode
"files". Partitions can also express dependencies to other partitions in
the code base.

For each source file is attached the file "language" type and variant
(if any), and some "filter" criteria to tell when to include or
exclude this file from the build.

Note libraries provide the symbol name scoping (in terms of language
identifier visibility rules). Partitions are simply an arbitrary way
of splitting code base in group of related features for limiting the
quantity of compiled code, but do not change anything to language
scoping.

### Project Definition

Project definition provides:

* Target toolsuite,

* Output format,

* Tools configuration (optimization rules, etc),

* A root project-specific library with one root partition,

* A design entity name we should consider as entry point for the
  design (usually called "topcell" in gateware),

The root partition will depend on other partitions in the
repositories.

Other build constraints files can be seen as normal source files, part
of the root partition.

### Build File Set

From project definition, GBS traverses the libraries, indexes the
needed library partitions, sorts them in dependency order.

Depending on project definition, some files can be filtered in or
out. This allows to create vendor-specific implementation of some
features and keep vendor specific constructs isolated in disjoint
files.

In the end, there must be a Direct Acyclic Graph of libraries we can
order as a list of library compilation order. Inside libraries, we can
create an ordered list of partitions to build. This shall be passed
down to the build backend.

## Input file formats

### Repository Definition

Repository root may define a set of libraries to index. It may use
glob matching for easier management and auto discovery.

GBS defines its "native" repository definition file based on YAML. We
may have plugins that register new ways to index a repository.

### Library Definition

Once a repository root targets a set of libraries, each library should
define a set of partitions definitions to load.

GBS defines its "native" library definition file based on YAML. We may
have plugins that register new ways to define a library.

### Partition Definition

Once a library targets a set of partitions, each partition should
define a set of files, their format, filter constraints (if any), and
dependencies to other partitions (by name).

GBS defines its "native" partition definition file based on YAML. We
may have plugins that register new ways to define a partition.

### Project Definition

User may specify project in a GBS-specific file. It shall be
YAML-based and contain all necessary configuration.

There will be 2 main parts in this file:

* Root library definition and its one (or many) partitions. Data model
  is the same as a "normal" partition definition in a repository tree.

* Toolsuite definition and configuration (data model will be partly
  toolsuite-specific).

## Backend Definition

Backends are defined by a python package name (that will be
imported). It may be native in gbs, in a `gbs.contrib` namespace
package, or in a third-party package. User is responsible for
installation of packages he references.

Backend defines the data model for its configuration in the project
defintion. It filters the whole build file set and does something
useful with it.

## Helpers

GBS will provide helpers for common build sequencer patterns

### Task management

Tasks will define their inputs and outputs. They can either be virtual
or backed by files.  If file-based, file modification timestamp should
be evaluated to decide whether a task is to be redone or not.

Tasks should have an asyncio-based execution model.

### Task Scheduling And Parallel Execution

Using asyncio semaphore, we'll be able to limit parallel execution to
a bounded value. Using tasks waiting on other's completion, build
order will be easily tracked.

### Long-Run Interactive I/O With A Third-Party Tool

Many EDA tools are TCL-based.  They usually provide a command line
where various tasks can be performed.  Our GBS tasks may consist of
commands that should be sent to a long-running interpreter we create
and monitor in background.

## Introspection

GBS will come with a command line able to:

* Manipulate a project

  * Build it,

  * Query its status,

  * Clean artifacts,

  * List partitions used, inspect build file set compilation order,
    etc.
  
* Query repositories

  * List libraries, partitions and files,

  * Query evaluation of dependency traversal given some filter values.

## Source Filtering

Filtering can happen:

* At the file level,

* At the partition level,

* At the dependency level (a partition may conditionally depend on
  antoher).

Filters are matching rules on a set of variables that are defined at
project time.  Naming for the variable is free.  Compilation backends
should clearly enumerate the variables they will provide for
repository dependency resolution.

# Project Management

Project is done from scratch. It should be well structured with:

* Documentation (inline, separate document). Docs should use Sphinx.

* unit tests (for data model loading, task dependency managent, etc).

* an extensible code base, pluggable.

* use of a logging system from day one. User should be able to run
  with limited verbosity but inspect logs post-mortem. We should
  systematically log to a file. This should go through the common
  logging system and not be reimplemented everywhere.

* Have a clean git repository structure.

* As this is a WIP, backwards compatibility or API stability is not
  needed, anywhere.
