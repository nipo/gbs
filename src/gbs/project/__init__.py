"""Project model"""
from .model import ProjectModel as Project, OutputGroup, OutputFile

__all__ = ["Project", "ProjectModel", "OutputGroup", "OutputFile"]

# Alias for backward compatibility
ProjectModel = Project
