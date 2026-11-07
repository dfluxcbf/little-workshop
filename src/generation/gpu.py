"""How busy the GPU is, for whoever is about to use it (docs/pillars/generation.md, "GPU usage").

The GPU is shared: ComfyUI renders the queue's pictures on it, and the Unity Editor, Blender, the desktop and
anything an agent starts use it too. Nothing here locks it. A sample says how much it is used and by whom, and
the caller decides whether to go ahead, wait or do something else first.

The numbers are Windows' own performance counters (the ones Task Manager reads), taken through one PowerShell
call over WSL interop: each GPU engine's utilization per process, each process's dedicated and shared memory,
and each adapter's. A GPU's utilization is its busiest engine's, the sum of every process on that engine, as
Task Manager shows it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Callable

LEVELS = ("idle", "light", "busy", "saturated")

# One sample of the counters, the names of the processes they mention, the adapters' memory and the RAM
# (Windows lets an adapter share up to half of it).
SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$c = (Get-Counter '\GPU Engine(*)\Utilization Percentage', '\GPU Process Memory(*)\Dedicated Usage',
      '\GPU Process Memory(*)\Shared Usage', '\GPU Adapter Memory(*)\Dedicated Usage',
      '\GPU Adapter Memory(*)\Shared Usage').CounterSamples | Where-Object { $_.CookedValue -gt 0 } |
      ForEach-Object { @{ p = $_.Path; v = $_.CookedValue } }
$ids = $c | ForEach-Object { if ($_.p -match 'pid_(\d+)_') { [int]$Matches[1] } } | Sort-Object -Unique
$procs = if ($ids) { Get-Process -Id $ids | ForEach-Object { @{ id = $_.Id; name = $_.ProcessName } } } else { @() }
$adapters = Get-ItemProperty 'HKLM:\SYSTEM\ControlSet001\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0*' |
    Where-Object { $_.'HardwareInformation.qwMemorySize' } |
    ForEach-Object { @{ name = $_.DriverDesc; dedicated = $_.'HardwareInformation.qwMemorySize' } }
@{ samples = @($c); processes = @($procs); adapters = @($adapters);
   ram = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory } | ConvertTo-Json -Depth 4 -Compress
"""

_LUID = r"(luid_0x[0-9a-f]+_0x[0-9a-f]+)_phys_(\d+)"
ENGINE = re.compile(r"\\gpu engine\(pid_(\d+)_" + _LUID + r"_eng_(\d+)_engtype_([^)]*)\)\\utilization percentage$")
PROCESS_MEMORY = re.compile(r"\\gpu process memory\(pid_(\d+)_" + _LUID + r"\)\\(dedicated|shared) usage$")
ADAPTER_MEMORY = re.compile(r"\\gpu adapter memory\(" + _LUID + r"\)\\(dedicated|shared) usage$")


class GpuUnavailable(Exception):
    pass


def parse(raw: dict) -> dict:
    """The script's answer -> the busiest adapter's utilization, memory and processes (without the level)."""
    names = {int(p["id"]): p["name"] for p in raw.get("processes") or [] if isinstance(p, dict) and "id" in p}
    engines: dict[tuple, dict] = {}  # (adapter, engine) -> {type, total, by pid}
    processes: dict[tuple, dict] = {}  # (adapter, pid) -> {utilization, dedicated, shared}
    adapters: dict[str, dict] = {}
    for sample in raw.get("samples") or []:
        path, value = str(sample.get("p", "")).lower(), float(sample.get("v") or 0.0)
        if match := ENGINE.search(path):
            pid, adapter, kind = int(match[1]), f"{match[2]}_phys_{match[3]}", match[5]
            engine = engines.setdefault((adapter, match[4]), {"type": kind, "total": 0.0, "by_pid": {}})
            engine["total"] += value
            engine["by_pid"][pid] = engine["by_pid"].get(pid, 0.0) + value
        elif match := PROCESS_MEMORY.search(path):
            entry = processes.setdefault((f"{match[2]}_phys_{match[3]}", int(match[1])), {"utilization": 0.0, "dedicated": 0, "shared": 0})
            entry[match[4]] += int(value)
        elif match := ADAPTER_MEMORY.search(path):
            adapters.setdefault(f"{match[1]}_phys_{match[2]}", {"dedicated": 0, "shared": 0})[match[3]] += int(value)
    for (adapter, _), engine in engines.items():
        adapters.setdefault(adapter, {"dedicated": 0, "shared": 0})
        for pid, value in engine["by_pid"].items():
            entry = processes.setdefault((adapter, pid), {"utilization": 0.0, "dedicated": 0, "shared": 0})
            entry["utilization"] = max(entry["utilization"], value)
    if not adapters:
        raise GpuUnavailable("Windows reported no GPU counters")

    def load(adapter: str) -> tuple:
        busiest = max((e["total"] for (a, _), e in engines.items() if a == adapter), default=0.0)
        return busiest, adapters[adapter]["dedicated"]

    adapter = max(adapters, key=load)
    by_type: dict[str, float] = {}
    for (a, _), engine in engines.items():
        if a == adapter:
            by_type[engine["type"]] = round(min(100.0, max(by_type.get(engine["type"], 0.0), engine["total"])), 1)
    listed = [{"pid": pid, "name": names.get(pid, "?"), "utilization_percent": round(min(100.0, entry["utilization"]), 1),
               "dedicated_bytes": entry["dedicated"], "shared_bytes": entry["shared"]}
              for (a, pid), entry in processes.items() if a == adapter]
    listed.sort(key=lambda p: (p["utilization_percent"], p["dedicated_bytes"] + p["shared_bytes"]), reverse=True)
    installed = [a for a in raw.get("adapters") or [] if isinstance(a, dict)]
    ram = int(raw.get("ram") or 0)
    return {
        "adapter": " / ".join(sorted({str(a.get("name")) for a in installed})) or adapter,
        "utilization_percent": round(min(100.0, load(adapter)[0]), 1),
        "engines_percent": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
        "memory": {
            "dedicated_used_bytes": adapters[adapter]["dedicated"],
            "dedicated_total_bytes": max((int(a.get("dedicated") or 0) for a in installed), default=0) or None,
            "shared_used_bytes": adapters[adapter]["shared"],
            "shared_total_bytes": ram // 2 or None,
        },
        "processes": listed,
    }


def memory_fraction(memory: dict) -> float | None:
    """Dedicated and shared memory together against both totals: an integrated GPU spills from its small
    dedicated part into the shared one, so the dedicated part alone overstates how full it is."""
    total = (memory["dedicated_total_bytes"] or 0) + (memory["shared_total_bytes"] or 0)
    return (memory["dedicated_used_bytes"] + memory["shared_used_bytes"]) / total if total else None


def level(sample: dict, thresholds: dict) -> tuple[str, list[str]]:
    """idle, light, busy or saturated, from the busiest engine and the memory in use, with the reasons."""
    util, fraction = sample["utilization_percent"], memory_fraction(sample["memory"])
    rank = 0
    for name in ("light", "busy", "saturated"):
        if util >= float(thresholds[f"{name}_utilization_percent"]) or (
                fraction is not None and fraction >= float(thresholds[f"{name}_memory_fraction"])):
            rank = LEVELS.index(name)
    memory = sample["memory"]
    used = (memory["dedicated_used_bytes"] + memory["shared_used_bytes"]) / 2**30
    reasons = [f"the busiest engine is at {util:.0f}%",
               f"GPU memory in use: {used:.1f} GiB" + (f", {fraction:.0%} of what it may use" if fraction is not None else "")]
    return LEVELS[rank], reasons


def powershell_path(configured: str | None) -> str | None:
    if configured and shutil.which(configured):
        return shutil.which(configured)
    return shutil.which("powershell.exe")


def run_script(executable: str, timeout: float) -> dict:
    try:
        result = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-Command", SCRIPT], capture_output=True,
                                text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GpuUnavailable(f"could not read the GPU counters: {exc}") from exc
    try:
        return json.loads(result.stdout.strip() or "null") or {}
    except ValueError as exc:
        raise GpuUnavailable(f"the GPU counters answered something unreadable: {(result.stderr or result.stdout)[:300]}") from exc


class Sampler:
    """One sample at a time, kept `cache_seconds` so that callers asking together share it."""

    def __init__(self, *, run: Callable[[float], dict] | None = None, clock: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.run = run
        self.clock = clock
        self.now = now
        self.lock = threading.Lock()
        self.last: tuple[float, dict] | None = None

    def cached(self, max_age: float) -> dict | None:
        last = self.last
        return last[1] if last is not None and self.clock() - last[0] <= max_age else None

    def sample(self, config: dict, *, max_age: float | None = None, enrich: Callable[[dict], None] = lambda sample: None) -> dict:
        """Raises GpuUnavailable. `enrich` adds to a new sample before it is kept (who the processes are)."""
        settings = config["gpu"]
        max_age = float(settings["cache_seconds"]) if max_age is None else max_age
        with self.lock:
            if (hit := self.cached(max_age)) is not None:
                return hit
            if self.run is not None:
                raw = self.run(float(settings["sample_timeout_seconds"]))
            else:
                executable = powershell_path(settings.get("powershell"))
                if executable is None:
                    raise GpuUnavailable("powershell.exe is not reachable (this is not WSL, or its Windows interop is off)")
                raw = run_script(executable, float(settings["sample_timeout_seconds"]))
            sample = parse(raw)
            sample["measured_at"] = self.now().isoformat(timespec="seconds")
            enrich(sample)
            self.last = (self.clock(), sample)
            return sample
