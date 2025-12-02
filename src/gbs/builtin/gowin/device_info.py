from __future__ import annotations
import logging
from pathlib import Path
import csv

def parse_device_csv(gowin_path: Path, device: str, logger: logging.Logger) -> tuple[str, str]:
    """Parse Gowin device CSV to get device characteristics and set_device parameters

    Args:
        gowin_path: Path to Gowin installation
        device: Device part number from project config (e.g., "GW1NR-LV9QN88PC6/I5")
        logger: Logger for warnings and errors

    Returns:
        Tuple of (part_group, part_number) for set_device command
    """
    csv_path = gowin_path / "IDE" / "data" / "device" / "device_info.csv"

    if not csv_path.exists():
        logger.warning(f"Device CSV not found at {csv_path}, using device as-is")
        return ("FPGA", device)  # Fallback

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
                    family = row[3].strip() if len(row) > 3 else ""      # Column 4 (index 3)

                    logger.info(f"Device info: {device} -> family={family}")

                    # For set_device command, use family as part_group
                    # and full device as part_number
                    # (Gowin expects: set_device -name <family> <full_part_number>)
                    return (family, device)

            # Device not found in CSV
            logger.warning(f"Device {device} not found in CSV, using as-is")
            return ("FPGA", device)

    except Exception as e:
        logger.error(f"Error parsing device CSV: {e}")
        return ("FPGA", device)  # Fallback
