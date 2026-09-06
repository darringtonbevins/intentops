"""Hardware detection -- measure the host the node is actually on.

PURPOSE. Cross-platform hardware profiling for the first phase of genesis,
stdlib only, no third-party dependency, read-only: nothing here mutates the
system. A node's operating mode is chosen from what the machine IS, not from a
policy file describing what somebody expected it to be.

WRITE MODEL. None. Every function is a pure read; the caller owns whatever it
persists.

FALLBACKS ARE DECLARED, NOT SILENT. Every detector has a stated fallback and
``detect_hardware`` never raises -- a genesis that dies because a GPU query
timed out is worse than one that records "no GPU detected". But the fallbacks
are *values that mean not-detected*, and callers must read them that way:
``gpu_name is None`` means "nothing found by these probes", never "no GPU
exists", and ``ram_mb == 0.0`` means the read failed.

BLIND SPOTS.

1. CPU count comes from the process's view. Inside a container that can be the
   HOST's core count rather than the cgroup quota, so a containerised node may
   over-report what it can spend.
2. GPU detection tries a vendor query tool and then a Linux device tree. A GPU
   present with neither is invisible, and VRAM is unknown on the second path.
3. Network detection opens ONE outbound TCP connection to a configurable probe
   address. It is off by default in ``detect_hardware`` for exactly that
   reason: a detection routine that phones out without being asked is not a
   read-only probe from the operator's point of view. A False result means
   "that address was unreachable", not "no network".
4. Power source is unknown on platforms with no supported query. "unknown" is
   a state, never a default of "ac".
"""

from __future__ import annotations

import ctypes
import logging
import os
import platform
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "HardwareProfile", "detect_cpu", "detect_ram", "detect_gpu",
    "detect_network", "detect_power_source", "detect_hardware",
    "DEFAULT_NETWORK_PROBE",
]

#: The address ``detect_network`` dials when asked to. A caller may substitute a
#: host inside its own estate; there is deliberately no hidden default beyond
#: this one constant, and the probe is opt-in.
DEFAULT_NETWORK_PROBE: Tuple[str, int] = ("1.1.1.1", 53)

POWER_SOURCES = ("ac", "battery", "unknown")


@dataclass
class HardwareProfile:
    """What was detected. Every field carries its not-detected value."""

    cpu_cores: int = 1
    cpu_model: str = "unknown"
    ram_mb: float = 0.0
    gpu_name: Optional[str] = None
    gpu_vram_mb: float = 0.0
    network_probed: bool = False        # was the probe even run?
    has_network: bool = False           # meaningless unless network_probed
    power_source: str = "unknown"
    platform: str = ""

    def summary(self) -> str:
        gpu = f"{self.gpu_name} ({self.gpu_vram_mb:.0f}MB)" if self.gpu_name else "none detected"
        net = ("yes" if self.has_network else "no") if self.network_probed \
            else "not probed"
        return (f"CPU: {self.cpu_cores} cores ({self.cpu_model}) | "
                f"RAM: {self.ram_mb:.0f}MB | GPU: {gpu} | Net: {net} | "
                f"Power: {self.power_source} | Platform: {self.platform}")


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------


def detect_cpu() -> Tuple[int, str]:
    """``(core_count, model_name)``. Fallback: ``(1, "unknown")``."""
    cores = os.cpu_count() or 1
    model = "unknown"
    if sys.platform == "linux":
        model = _cpu_model_linux()
    elif sys.platform == "win32":
        model = platform.processor() or "unknown"
    elif sys.platform == "darwin":
        model = _cpu_model_macos()
    if not model or model == "unknown":
        model = platform.processor() or "unknown"
    return cores, model


def _cpu_model_linux() -> str:
    try:
        cpuinfo = Path("/proc/cpuinfo")
        if cpuinfo.exists():
            for line in cpuinfo.read_text().splitlines():
                if line.startswith("model name"):
                    parts = line.split(":", 1)
                    if len(parts) == 2:
                        return parts[1].strip()
    except OSError as exc:
        logger.debug("cpu model read failed: %s", exc)
    return "unknown"


def _cpu_model_macos() -> str:
    try:
        result = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("cpu model sysctl failed: %s", exc)
    return "unknown"


# ---------------------------------------------------------------------------
# RAM
# ---------------------------------------------------------------------------


def detect_ram() -> float:
    """Total physical memory in MB. ``0.0`` means the read FAILED."""
    if sys.platform == "win32":
        return _ram_win32()
    if sys.platform == "linux":
        return _ram_linux()
    if sys.platform == "darwin":
        return _ram_macos()
    return 0.0


def _ram_win32() -> float:
    try:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]

        mem = MEMORYSTATUSEX()
        mem.dwLength = ctypes.sizeof(mem)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
            return mem.ullTotalPhys / (1024 * 1024)
    except (OSError, AttributeError, ValueError) as exc:
        logger.debug("physical memory query failed: %s", exc)
    return 0.0


def _ram_linux() -> float:
    try:
        meminfo = Path("/proc/meminfo")
        if meminfo.exists():
            for line in meminfo.read_text().splitlines():
                if line.startswith("MemTotal:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return float(parts[1]) / 1024  # kB -> MB
    except (OSError, ValueError) as exc:
        logger.debug("meminfo read failed: %s", exc)
    return 0.0


def _ram_macos() -> float:
    try:
        result = subprocess.run(["sysctl", "-n", "hw.memsize"],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip()) / (1024 * 1024)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        logger.debug("memory sysctl failed: %s", exc)
    return 0.0


# ---------------------------------------------------------------------------
# GPU
# ---------------------------------------------------------------------------


def detect_gpu() -> Tuple[Optional[str], float]:
    """``(gpu_name_or_None, vram_mb)``. ``None`` means these probes found
    nothing -- see blind spot 2."""
    name, vram = _gpu_vendor_query()
    if name:
        return name, vram
    if sys.platform == "linux":
        name = _gpu_linux_devicetree()
        if name:
            return name, 0.0
    return None, 0.0


def _gpu_vendor_query() -> Tuple[Optional[str], float]:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().splitlines()[0].split(",")
            if len(parts) >= 2:
                return parts[0].strip(), float(parts[1].strip())
    except FileNotFoundError:
        logger.debug("no vendor GPU query tool on PATH")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        logger.debug("vendor GPU query failed: %s", exc)
    return None, 0.0


def _gpu_linux_devicetree() -> Optional[str]:
    try:
        drm_path = Path("/sys/class/drm")
        if not drm_path.exists():
            return None
        for card_dir in sorted(drm_path.iterdir()):
            if not (card_dir.name.startswith("card")
                    and card_dir.name[-1].isdigit()):
                continue
            vendor_file = card_dir / "device" / "vendor"
            if vendor_file.exists():
                vendor = vendor_file.read_text().strip()
                return {
                    "0x10de": "NVIDIA GPU",
                    "0x1002": "AMD GPU",
                    "0x8086": "Intel GPU",
                }.get(vendor, f"GPU (vendor {vendor})")
    except OSError as exc:
        logger.debug("device-tree GPU detection failed: %s", exc)
    return None


# ---------------------------------------------------------------------------
# network
# ---------------------------------------------------------------------------


def detect_network(probe: Tuple[str, int] = DEFAULT_NETWORK_PROBE,
                   timeout: float = 3.0) -> bool:
    """Open ONE outbound TCP connection to ``probe`` and close it.

    False means that address was unreachable, which is not the same as "no
    network". Opt-in: ``detect_hardware`` does not call this unless asked.
    """
    try:
        sock = socket.create_connection(probe, timeout=timeout)
        sock.close()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# power
# ---------------------------------------------------------------------------


def detect_power_source() -> str:
    """One of ``POWER_SOURCES``. ``"unknown"`` is a state, not a default of
    ``"ac"``."""
    if sys.platform == "win32":
        return _power_win32()
    if sys.platform == "linux":
        return _power_linux()
    return "unknown"


def _power_win32() -> str:
    try:
        class SYSTEM_POWER_STATUS(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_byte),
                ("BatteryFlag", ctypes.c_byte),
                ("BatteryLifePercent", ctypes.c_byte),
                ("SystemStatusFlag", ctypes.c_byte),
                ("BatteryLifeTime", ctypes.c_ulong),
                ("BatteryFullLifeTime", ctypes.c_ulong),
            ]

        sps = SYSTEM_POWER_STATUS()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(sps)):
            if sps.ACLineStatus == 1:
                return "ac"
            if sps.ACLineStatus == 0:
                return "battery"
    except (OSError, AttributeError, ValueError) as exc:
        logger.debug("power status query failed: %s", exc)
    return "unknown"


def _power_linux() -> str:
    try:
        ps_path = Path("/sys/class/power_supply")
        if not ps_path.exists():
            return "unknown"
        for supply in ps_path.iterdir():
            type_file = supply / "type"
            if not type_file.exists():
                continue
            supply_type = type_file.read_text().strip()
            if supply_type == "Mains":
                online_file = supply / "online"
                if online_file.exists():
                    return "ac" if online_file.read_text().strip() == "1" else "battery"
            elif supply_type == "Battery":
                status_file = supply / "status"
                if status_file.exists():
                    status = status_file.read_text().strip()
                    if status in ("Charging", "Full", "Not charging"):
                        return "ac"
                    if status == "Discharging":
                        return "battery"
    except OSError as exc:
        logger.debug("power supply detection failed: %s", exc)
    return "unknown"


# ---------------------------------------------------------------------------
# the profile
# ---------------------------------------------------------------------------


def detect_hardware(*, probe_network: bool = False,
                    network_probe: Tuple[str, int] = DEFAULT_NETWORK_PROBE
                    ) -> HardwareProfile:
    """Run detection and return a complete profile. Never raises.

    ``probe_network`` is OPT-IN: a detection routine that dials out without
    being asked is not a read-only probe from the operator's point of view.
    ``network_probed`` records whether it ran, so ``has_network is False`` can
    never be mistaken for a measurement that was never taken.

    The HOSTNAME is deliberately absent from the profile: it is an identifier of
    the operator's machine, not a capability, and nothing downstream chooses a
    mode from it.
    """
    cpu_cores, cpu_model = detect_cpu()
    gpu_name, gpu_vram_mb = detect_gpu()
    has_network = detect_network(network_probe) if probe_network else False
    return HardwareProfile(
        cpu_cores=cpu_cores,
        cpu_model=cpu_model,
        ram_mb=detect_ram(),
        gpu_name=gpu_name,
        gpu_vram_mb=gpu_vram_mb,
        network_probed=bool(probe_network),
        has_network=has_network,
        power_source=detect_power_source(),
        platform=sys.platform,
    )
