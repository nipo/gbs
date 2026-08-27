"""GBS Suite - Multi-project orchestration

This module provides support for building and testing multiple GBS projects together.
Useful for CI/CD integration and regression testing.
"""

from .model import (
    Suite, SuiteSettings, ProjectReference,
    FilterSettings, OutputSettings,
    SuiteStatus, ProjectStatus,
    ProjectResult, SuiteResult
)
from .loader import load_suite, LoadError
from .executor import SuiteExecutor, ExecutionError
from .output_inventory import SuiteOutputInventory
from .formatters import write_junit_xml, write_summary_json

__all__ = [
    # Models
    'Suite',
    'SuiteSettings',
    'ProjectReference',
    'FilterSettings',
    'OutputSettings',
    'SuiteStatus',
    'ProjectStatus',
    'ProjectResult',
    'SuiteResult',

    # Loader
    'load_suite',
    'LoadError',

    # Executor
    'SuiteExecutor',
    'ExecutionError',

    # Introspection
    'SuiteOutputInventory',

    # Formatters
    'write_junit_xml',
    'write_summary_json',
]
