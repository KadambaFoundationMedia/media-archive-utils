"""Scale the CPU and copy worker pools with the computer's load (Settings: autoscale)."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import threading
import time

import psutil

log = logging.getLogger(__name__)

BUSY_CPU = 85.0  # percent: above this a worker is taken away
SPARE_CPU = 60.0  # percent: below this (with a job waiting) a worker is added
LOW_MEMORY = 10.0  # percent of memory still available: below this a worker is taken away
SPARE_MEMORY = 20.0  # percent of memory still available: needed to add a worker


def next_limit(current: int, maximum: int, cpu: float, memory_free: float, waiting: bool) -> int:
    """How many workers of a pool may run next: one step up or down, between 1 and `maximum`."""
    if cpu > BUSY_CPU or memory_free < LOW_MEMORY:
        return max(1, current - 1)
    if waiting and cpu < SPARE_CPU and memory_free > SPARE_MEMORY:
        return min(maximum, current + 1)
    return current


_cache: dict = {"at": 0.0, "data": None}
_lock = threading.Lock()


def _gpu_percent() -> float | None:
    """GPU use in percent: Apple GPUs through ioreg (no admin rights), NVIDIA through
    nvidia-smi; None when neither answers."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(
                ["ioreg", "-r", "-d", "1", "-w0", "-c", "IOAccelerator"],
                capture_output=True,
                text=True,
                timeout=3,
            ).stdout
            found = re.findall(r'"Device Utilization %"\s*=\s*(\d+)', out)
            return float(max(map(int, found))) if found else None
        if shutil.which("nvidia-smi"):
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=3,
            ).stdout
            values = [float(v) for v in out.split() if v.replace(".", "", 1).isdigit()]
            return max(values) if values else None
    except (OSError, subprocess.SubprocessError, ValueError):
        log.debug("GPU use could not be read", exc_info=True)
    return None


def _cpu_temperature() -> float | None:
    """CPU temperature in degrees C where the system reports it (Linux, some Windows PCs); a Mac
    does not without admin rights."""
    reader = getattr(psutil, "sensors_temperatures", None)
    if reader is None:
        return None
    try:
        readings = [t.current for items in reader().values() for t in items if t.current]
    except Exception:
        return None
    return max(readings) if readings else None


def snapshot() -> dict:
    """What the header bars show. Read at most every 1.5 s, however many pages ask."""
    with _lock:
        if _cache["data"] is not None and time.monotonic() - _cache["at"] < 1.5:
            return _cache["data"]
        memory = psutil.virtual_memory()
        data = {
            "cpu": psutil.cpu_percent(interval=None),
            "memory": memory.percent,
            "memory_free": memory.available * 100.0 / memory.total,
            "gpu": _gpu_percent(),
            "temp": _cpu_temperature(),
        }
        _cache.update(at=time.monotonic(), data=data)
        return data


def level(kind: str, value: float) -> str:
    """Bar color: green, orange or red."""
    warm, hot = (70.0, 85.0) if kind == "temp" else (60.0, 85.0)
    return "success" if value < warm else "warning" if value < hot else "danger"


def sample() -> tuple[float, float]:
    """CPU use (percent) and the share of memory still available."""
    data = snapshot()
    return data["cpu"], data["memory_free"]
