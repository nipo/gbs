"""Feedback rendering backends"""

from .base import FeedbackBackend
from .simple import SimpleBackend

# Try to import RichBackend (optional dependency)
try:
    from .rich import RichBackend, is_rich_available
    __all__ = ["FeedbackBackend", "SimpleBackend", "RichBackend", "is_rich_available"]
except ImportError:
    __all__ = ["FeedbackBackend", "SimpleBackend"]
