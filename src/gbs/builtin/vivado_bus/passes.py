"""Vivado bus definition pass definitions."""

from __future__ import annotations

from ...base import BasePass
from ...protocol import Dispatcher
from .dispatcher import VivadoBusPackageDispatcher, VivadoBusTranspileDispatcher


class VivadoBusTranspilePass(BasePass):
    """Generate IP-XACT bus definition XML from YAML descriptions.

    Each ``vivado-bus-yaml`` source produces a busDefinition
    (``<name>.xml``) and an abstractionDefinition (``<name>_rtl.xml``)
    as ``vivado-bus-definition`` files, ready for the bus package pass
    or the Vivado IP packaging pass. Runs pure Python; no tool needed.

    Input types: vivado-bus-yaml
    Output types: vivado-bus-definition
    """
    name = "vivado-bus-transpile"
    input_types = {"vivado-bus-yaml"}
    output_types = {"vivado-bus-definition"}

    def dispatchers(self, context) -> list[Dispatcher]:
        return [VivadoBusTranspileDispatcher(context)]


class VivadoBusPackagePass(BasePass):
    """Bundle IP-XACT bus definitions into a repository archive.

    Collects every ``vivado-bus-definition`` file into a flat directory
    suitable as a Vivado ``ip_repo_paths`` entry, delivered as a zip
    archive or a directory. Runs pure Python; no tool needed.

    Input types: vivado-bus-definition
    Output types: vivado-bus-zip, vivado-bus-dir
    """
    name = "vivado-bus-package"
    input_types = {"vivado-bus-definition"}
    output_types = {"vivado-bus-zip", "vivado-bus-dir"}

    def dispatchers(self, context) -> list[Dispatcher]:
        return [VivadoBusPackageDispatcher(context)]
