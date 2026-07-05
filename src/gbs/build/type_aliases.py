"""Terminal-output type alias registry.

Terminal file types (the ones users write in ``outputs:`` blocks)
converged on a small canonical vocabulary — ``bitstream``,
``synthesis-report``, ``pnr-report``, ``simulator`` — so a project
can move between backends without renaming its output goals. Every
backend that produced a vendor-prefixed variant now advertises the
canonical name alongside the legacy one, and OUTPUT resources
created from a user goal are aliased in both directions so
dispatchers keep finding them regardless of which name the user
wrote.
"""

from __future__ import annotations

# Canonical terminal type -> list of legacy aliases that used to name
# equivalent artifacts across backends. Keep alphabetical within each
# list to make future additions easy to spot.
CANONICAL_TO_LEGACY: dict[str, tuple[str, ...]] = {
    "bitstream": (
        "ecp5-bitstream",
        "gowin-fs",
        "ice40-bitstream",
        "ise-bitstream",
        "quartus-sof",
        "vivado-bitstream",
        "xilinx-bitstream",
    ),
    "synthesis-report": (
        "diamond-synthesis-report",
        "gowin-synthesis-report",
        "ise-synthesis-report",
        "quartus-synthesis-report",
        "vivado-synthesis-report",
        "yosys-synthesis-report",
    ),
    "pnr-report": (
        "diamond-pnr-report",
        "gowin-pnr-report",
        "ise-pnr-report",
        "nextpnr-pnr-report",
        "quartus-pnr-report",
        "vivado-pnr-report",
    ),
    "simulator": (
        "ghdl-simulator",
        "nvc-simulator",
    ),
}

# Reverse map for quick "which canonical does this legacy belong to?"
# lookups.
LEGACY_TO_CANONICAL: dict[str, str] = {
    legacy: canonical
    for canonical, legacies in CANONICAL_TO_LEGACY.items()
    for legacy in legacies
}


def sibling_aliases(file_type: str) -> set[str]:
    """Return every other name (canonical + siblings) equivalent to
    ``file_type`` in the terminal-type alias registry.

    - When ``file_type`` is a canonical name, returns every legacy
      alias registered under it.
    - When ``file_type`` is a legacy alias, returns the canonical
      name and every sibling legacy under the same canonical.
    - When ``file_type`` is not a registered terminal type, returns
      an empty set.
    """
    if file_type in CANONICAL_TO_LEGACY:
        return set(CANONICAL_TO_LEGACY[file_type])
    canonical = LEGACY_TO_CANONICAL.get(file_type)
    if canonical is None:
        return set()
    siblings = {canonical, *CANONICAL_TO_LEGACY[canonical]}
    siblings.discard(file_type)
    return siblings
