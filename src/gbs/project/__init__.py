"""GBS Project module

Provides project data models and execution functionality.
"""

# Data models
from .model import ProjectModel, OutputGroup, OutputFile

# Project execution and loading
from .loader import Project, LoadError

__all__ = [
    # Data models
    "ProjectModel",
    "OutputGroup",
    "OutputFile",
    # Execution
    "Project",
    "LoadError",
]
