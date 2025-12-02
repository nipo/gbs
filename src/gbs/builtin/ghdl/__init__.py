"""GHDL Backend"""
from .backend import GHDLBackend

def get_backend():
    return GHDLBackend()

__all__ = ["GHDLBackend", "get_backend"]
