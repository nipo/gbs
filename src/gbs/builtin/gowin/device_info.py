from __future__ import annotations
import logging
from pathlib import Path
import csv
from dataclasses import dataclass
from typing import Optional


@dataclass
class DeviceInfo:
    """Gowin device information parsed from CSV"""
    part: str
    family: str
    revision: str
    package: str
    voltage: str
    speed: str
    klut_count: Optional[str] = None  # "15k", "60k", "138k" for SerDes tool selection

    @property
    def part_group(self) -> str:
        """Part group for set_device command"""
        return self.family

    @property
    def part_number(self) -> str:
        """Part number for set_device command"""
        return self.device

    def matches(self, device: str) -> bool:
        terms = device.split('#')
        device = terms[0]

        if self.part.lower() != device.lower():
            return False
        
        if len(terms) > 1 and terms[1].lower() != self.revision.lower():
            return False

        return True
    
    @classmethod
    def from_csv(cls, line):
        return cls(
            part = line[1],
            family = line[3],
            revision = line[5],
            package = line[6],
            voltage = line[7],
            speed = line[8],
            klut_count = cls._extract_klut_count(line[1])
            )

    @staticmethod
    def _extract_klut_count(device: str) -> Optional[str]:
        """Extract klut count category from device name for SerDes tool selection

        Gowin 5-series SerDes tool is named serdes_toml_to_csr_<klut>.bin where
        klut is "15k", "60k", or "138k" based on device capacity.

        Args:
            device: Device part number (e.g., "GW5AT-LV60PG484AC1/I0")

        Returns:
            klut category string ("15k", "60k", "138k") or None if not applicable
        """
        device_upper = device.upper()

        # Only GW5A/GW5AT series have SerDes
        if not (device_upper.startswith("GW5A") or device_upper.startswith("GW5AT")):
            return None

        # Extract the numeric part after GW5A/GW5AT-LV
        # Examples: GW5AT-LV60... -> 60, GW5A-LV25... -> 25
        import re
        match = re.search(r'GW5AT?-.V(\d+)', device_upper)
        if not match:
            return None

        return f"{match.group(1)}k"

def get_device_info(gowin_path: Path, device: str) -> DeviceInfo:
    """Parse Gowin device CSV to get full device information

    Args:
        gowin_path: Path to Gowin installation
        device: Device part number from project config (e.g., "GW5AT-LV60PG484AC1/I0")

    Returns:
        DeviceInfo instance
    """
    csv_path = gowin_path / "IDE" / "data" / "device" / "device_info.csv"

    if not csv_path.exists():
        raise NotImplementedError("Cannot work without device_info.csv")

    matching = []
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)

        # Find matching row (match column 2 with device)
        for row in reader:
            dev = DeviceInfo.from_csv(row)

            if dev.matches(device):
                matching.append(dev)
                
    if len(matching) < 1:
        raise ValueError(f"Bad part number {device}")

    if len(matching) > 1:
        raise ValueError(f"Ambiguous part number {device}, use {device}#<revision>")

    return matching[0]
