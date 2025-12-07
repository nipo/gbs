"""Suite output formatters

Formatters for generating CI/CD-friendly output from suite results.
"""

from .junit import write_junit_xml
from .summary import write_summary_json

__all__ = [
    'write_junit_xml',
    'write_summary_json',
]
