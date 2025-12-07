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
]
