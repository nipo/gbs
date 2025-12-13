"""Feedback rendering backends"""

from .base import FeedbackBackend
from .simple import SimpleBackend
from .file import FileBackend

# Try to import RichBackend (optional dependency)
try:
    from .rich import RichBackend, is_rich_available
    __all__ = ["FeedbackBackend", "SimpleBackend", "FileBackend", "RichBackend", "is_rich_available"]
except ImportError:
    __all__ = ["FeedbackBackend", "SimpleBackend", "FileBackend"]
