from __future__ import annotations
import logging
from pathlib import Path
import csv
from dataclasses import dataclass
from typing import Optional


@dataclass
class DeviceInfo:
    """Gowin device information parsed from CSV"""
    family: str
    device: str
    klut_count: Optional[str] = None  # "15k", "60k", "138k" for SerDes tool selection

    @property
    def part_group(self) -> str:
        """Part group for set_device command"""
        return self.family

    @property
    def part_number(self) -> str:
        """Part number for set_device command"""
        return self.device


def parse_device_csv(gowin_path: Path, device: str, logger: logging.Logger) -> tuple[str, str]:
    """Parse Gowin device CSV to get device characteristics and set_device parameters

    Args:
        gowin_path: Path to Gowin installation
        device: Device part number from project config (e.g., "GW1NR-LV9QN88PC6/I5")
        logger: Logger for warnings and errors

    Returns:
        Tuple of (part_group, part_number) for set_device command
    """
    info = get_device_info(gowin_path, device, logger)
    return (info.part_group, info.part_number)


def get_device_info(gowin_path: Path, device: str, logger: logging.Logger) -> DeviceInfo:
    """Parse Gowin device CSV to get full device information

    Args:
        gowin_path: Path to Gowin installation
        device: Device part number from project config (e.g., "GW5AT-LV60PG484AC1/I0")
        logger: Logger for warnings and errors

    Returns:
        DeviceInfo with family, device, and optional klut_count for SerDes
    """
    csv_path = gowin_path / "IDE" / "data" / "device" / "device_info.csv"

    if not csv_path.exists():
        logger.warning(f"Device CSV not found at {csv_path}, using device as-is")
        return DeviceInfo(family="FPGA", device=device)

    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)

            # Find matching row (match column 2 with device)
            for row in reader:
                if len(row) < 10:
                    continue  # Skip malformed rows

                # Column 2 (index 1) is the device part number
                if row[1].strip() == device.strip():
                    # Extract device characteristics
                    family = row[3].strip() if len(row) > 3 else ""

                    # Determine klut_count from device name for SerDes tool
                    # GW5A/GW5AT devices with SerDes need this for tool selection
                    klut_count = _extract_klut_count(device)

                    logger.info(f"Device info: {device} -> family={family}, klut={klut_count}")

                    return DeviceInfo(
                        family=family,
                        device=device,
                        klut_count=klut_count
                    )

            # Device not found in CSV
            logger.warning(f"Device {device} not found in CSV, using as-is")
            return DeviceInfo(family="FPGA", device=device)

    except Exception as e:
        logger.error(f"Error parsing device CSV: {e}")
        return DeviceInfo(family="FPGA", device=device)


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
    match = re.search(r'GW5AT?-LV(\d+)', device_upper)
    if not match:
        return None

    capacity = int(match.group(1))

    # Map capacity to klut category
    if capacity <= 25:
        return "15k"
    elif capacity <= 60:
        return "60k"
    else:
        return "138k"
