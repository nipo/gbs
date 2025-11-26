# GBS: Gateware Build System

A build system for gateware projects targeting FPGAs and ASICs.

## Overview

GBS provides a flexible, extensible build infrastructure for hardware description language (HDL) projects. It supports:

- Multiple repositories and libraries
- Dependency management with topological sorting
- Conditional source filtering based on target configuration
- Pluggable backend architecture for different toolchains
- Asyncio-based task execution with parallel builds

## Installation

```bash
pip install -e .
```

For development:

```bash
pip install -e ".[dev]"
```

## Quick Start

```bash
# Validate a repository
gbs repo validate /path/to/repo

# List libraries in a repository
gbs repo list /path/to/repo

# Build a project
gbs build project.yaml
```

## Documentation

See the `doc/` directory for detailed documentation:

- [GBS Overview](doc/gbs_overview.md) - Project goals and architecture
- [Implementation Plan](doc/plan/implementation_plan.md) - Development roadmap
- [Filter Design](doc/plan/filter_design.md) - Conditional filtering design

## Requirements

- Python 3.13+

## Development Status

⚠️ **Alpha** - This project is in early development. API stability is not guaranteed.

## License

MIT
