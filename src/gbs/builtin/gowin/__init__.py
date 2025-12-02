"""Gowin Backend"""
from .backend import GowinBackend

def get_backend():
    return GowinBackend()

__all__ = ["GowinBackend", "get_backend"]
