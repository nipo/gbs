"""GBS: Gateware Build System

A build system for FPGA and ASIC gateware projects.
"""

__version__ = "0.1.0"

# Extend __path__ to enable namespace packages for plugins
# This allows gbs.plugin to be a namespace package that can be extended
# by other packages (like gbs-plugin-nsl)
import sys
from pathlib import Path

# Add any sys.path entries that contain gbs/ to our __path__
# This enables PEP 420 style namespace packages for plugins
__path__ = __import__('pkgutil').extend_path(__path__, __name__)
