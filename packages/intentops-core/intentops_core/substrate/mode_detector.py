"""Mode detection -- a pure function from a hardware profile to an operating mode.

PURPOSE. Genesis phase zero measures the host; this maps what it measured onto
one of five operating modes, each declaring the service set a node of that size
can carry. It is arithmetic over thresholds, never a judgement call and never a
model call: the same profile always yields the same mode, on any node.

WRITE MODEL. None. Pure function, no I/O, no clock.

WHAT A MODE IS, AND IS NOT. A mode is a RESOURCE POSTURE. It is never an
authorization change: the tier ladder, the reaches-reality classifier and every
human gate are mode-independent, and no mode grants a node anything it could not
do in another. ``suggested_local_model`` is a SUGGESTION for a node that chooses
to run local inference; nothing in the seed calls a model, and a node may
declare a different one or none.

THRESHOLDS (stated so they can be argued with):

=========  ====================================================================
FULL       RAM >= 32 GB, GPU VRAM >= 16 GB, and network confirmed
STANDARD   RAM >= 16 GB and network confirmed
MINIMAL    RAM >= 8 GB
SURVIVAL   everything else, including every case where RAM could not be read
STEALTH    only when explicitly forced -- never inferred
=========  ====================================================================

BLIND SPOTS.

1. Network is a REQUIREMENT for the two largest modes, and an unprobed profile
   reports ``has_network False``. A node whose substrate phase did not probe the
   network therefore reads at most MINIMAL. That is deliberate: inferring
   connectivity that was never measured would size a node on an assumption.
2. RAM of ``0.0`` means the read failed, and a failed read falls to SURVIVAL.
   Sizing UP on an unreadable measurement is the dangerous direction.
3. Nothing here measures sustained throughput, thermal headroom, or disk. Two
   machines with identical RAM and VRAM read identically and may not behave so.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Tuple

from intentops_core.substrate.hardware_detector import HardwareProfile

__all__ = [
    "SeedMode", "ModeConfig", "MODE_CONFIGS", "detect_mode", "get_mode_config",
    "list_modes", "THRESHOLDS",
]


class SeedMode(str, Enum):
    """Operating modes, ordered by resource requirement (highest first)."""

    FULL = "full"
    STANDARD = "standard"
    MINIMAL = "minimal"
    SURVIVAL = "survival"
    STEALTH = "stealth"


@dataclass(frozen=True)
class ModeConfig:
    mode: SeedMode
    suggested_local_model: str
    services: Tuple[str, ...]
    description: str

    def summary(self) -> str:
        return (f"{self.mode.value}: {self.description} | suggested local "
                f"model: {self.suggested_local_model} | services: "
                f"{', '.join(self.services)}")


#: Thresholds, as data rather than as literals buried in a branch, so a node can
#: read what it was sized against.
THRESHOLDS: Dict[str, Dict[str, float]] = {
    "full": {"ram_mb": 32_000, "gpu_vram_mb": 16_000},
    "standard": {"ram_mb": 16_000},
    "minimal": {"ram_mb": 8_000},
}


MODE_CONFIGS: Dict[SeedMode, ModeConfig] = {
    SeedMode.FULL: ModeConfig(
        mode=SeedMode.FULL,
        suggested_local_model="a large local instruct model",
        services=("api", "knowledge", "pipeline", "interface"),
        description="full operation -- all services, room for a large model"),
    SeedMode.STANDARD: ModeConfig(
        mode=SeedMode.STANDARD,
        suggested_local_model="a mid-size local instruct model",
        services=("api", "knowledge", "pipeline"),
        description="standard operation -- core services, mid-size model"),
    SeedMode.MINIMAL: ModeConfig(
        mode=SeedMode.MINIMAL,
        suggested_local_model="a small local instruct model",
        services=("cli", "knowledge"),
        description="minimal compute -- command line and knowledge only"),
    SeedMode.SURVIVAL: ModeConfig(
        mode=SeedMode.SURVIVAL,
        suggested_local_model="the smallest local model available, or none",
        services=("cli",),
        description="survival -- command line only, offline-capable"),
    SeedMode.STEALTH: ModeConfig(
        mode=SeedMode.STEALTH,
        suggested_local_model="the smallest local model available, or none",
        services=("cli",),
        description="memory-only posture -- minimal footprint, forced never inferred"),
}


def detect_mode(hw: HardwareProfile, force_stealth: bool = False) -> ModeConfig:
    """Select the operating mode from a hardware profile.

    STEALTH is never inferred: a node does not decide on its own to leave less
    trace than its operator asked for.
    """
    if force_stealth:
        mode = SeedMode.STEALTH
    elif (hw.ram_mb >= THRESHOLDS["full"]["ram_mb"]
            and hw.gpu_vram_mb >= THRESHOLDS["full"]["gpu_vram_mb"]
            and hw.has_network):
        mode = SeedMode.FULL
    elif hw.ram_mb >= THRESHOLDS["standard"]["ram_mb"] and hw.has_network:
        mode = SeedMode.STANDARD
    elif hw.ram_mb >= THRESHOLDS["minimal"]["ram_mb"]:
        mode = SeedMode.MINIMAL
    else:
        mode = SeedMode.SURVIVAL
    return MODE_CONFIGS[mode]


def get_mode_config(mode: SeedMode) -> ModeConfig:
    """Look up one mode. Raises ``KeyError`` on an unknown mode rather than
    returning a default -- a mode nobody declared is a hard exit."""
    return MODE_CONFIGS[mode]


def list_modes() -> List[ModeConfig]:
    """Every mode, highest resource requirement first."""
    return [MODE_CONFIGS[m] for m in SeedMode]
