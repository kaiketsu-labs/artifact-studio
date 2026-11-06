"""Read Linux process, cgroup and explicit storage metadata without loading the app.

Run with an explicit application PID. Nothing is installed, changed or signalled;
process command lines, environments and user file contents are never read.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import stat
import sys
import time
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

PROC = Path("/proc")
CACHE_ENTRY_LIMIT = 50_000
CACHE_SCAN_SECONDS = 2.0


@dataclass(frozen=True)
class Process:
    pid: int
    parent: int
    started: int
    cpu_ticks: int
    rss_bytes: int


@dataclass(frozen=True)
class CgroupMount:
    root: PurePosixPath
    path: Path
    controllers: frozenset[str]
    version: int


def positive_pid(value: str) -> int:
    try:
        result = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("PID must be a positive integer") from error
    if result < 1:
        raise argparse.ArgumentTypeError("PID must be a positive integer")
    return result


def sample_seconds(value: str) -> float:
    try:
        result = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("seconds must be between 0.1 and 60") from error
    if not math.isfinite(result) or not 0.1 <= result <= 60:
        raise argparse.ArgumentTypeError("seconds must be between 0.1 and 60")
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    _ = result.add_argument("--pid", type=positive_pid, required=True, help="application PID")
    _ = result.add_argument(
        "--seconds", type=sample_seconds, default=2.0, help="sample interval, 0.1..60 (default 2)"
    )
    _ = result.add_argument(
        "--state-path", type=Path, help="existing state directory: disk free only"
    )
    _ = result.add_argument(
        "--cache-path",
        type=Path,
        action="append",
        default=[],
        help="explicit cache directory: bounded metadata scan; repeat up to eight times",
    )
    return result


def process_stat(text: str, page_size: int) -> Process:
    """Parse fields after the final ')' without retaining or emitting the process name."""
    prefix, separator, suffix = text.rpartition(")")
    if not separator:
        raise ValueError("invalid process stat")
    fields = suffix.split()
    return Process(
        pid=int(prefix.split("(", 1)[0]),
        parent=int(fields[1]),
        started=int(fields[19]),
        cpu_ticks=int(fields[11]) + int(fields[12]),
        rss_bytes=max(0, int(fields[21])) * page_size,
    )


def process_tree(pid: int, proc: Path = PROC) -> dict[int, Process]:
    processes: dict[int, Process] = {}
    page_size = int(os.sysconf("SC_PAGE_SIZE"))
    for item in proc.iterdir():
        if not item.name.isdecimal():
            continue
        try:
            process = process_stat((item / "stat").read_text(errors="replace"), page_size)
        except OSError, ValueError, IndexError:
            continue  # Processes may exit or become inaccessible during enumeration.
        processes[process.pid] = process
    if pid not in processes:
        raise ValueError("target PID is unavailable or its process metadata is unreadable")
    children: dict[int, list[int]] = {}
    for process in processes.values():
        children.setdefault(process.parent, []).append(process.pid)
    selected: dict[int, Process] = {}
    pending = [pid]
    while pending:
        child = pending.pop()
        if child in selected:
            continue
        selected[child] = processes[child]
        pending.extend(children.get(child, []))
    return selected


def tree_sample(pid: int, seconds: float, proc: Path = PROC) -> dict[str, object]:
    first = process_tree(pid, proc)
    started = time.monotonic()
    time.sleep(seconds)
    second = process_tree(pid, proc)
    elapsed = time.monotonic() - started
    if first[pid].started != second[pid].started:
        raise ValueError("target PID changed identity during sampling")
    common = {
        key for key in first.keys() & second.keys() if first[key].started == second[key].started
    }
    ticks = sum(max(0, second[key].cpu_ticks - first[key].cpu_ticks) for key in common)
    cpu_seconds = ticks / int(os.sysconf("SC_CLK_TCK"))
    return {
        "pid": pid,
        "elapsed_seconds": round(elapsed, 4),
        "processes_at_start": len(first),
        "processes_at_end": len(second),
        "processes_measured_for_cpu": len(common),
        "rss_bytes_at_start": sum(item.rss_bytes for item in first.values()),
        "rss_bytes_at_end": sum(item.rss_bytes for item in second.values()),
        "cpu_seconds": round(cpu_seconds, 4),
        "cpu_percent_one_core": round(cpu_seconds / elapsed * 100, 2),
    }


def read_optional(path: Path) -> str | None:
    try:
        return path.read_text(errors="replace").strip()
    except OSError:
        return None


def memory_info(proc: Path = PROC) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in (read_optional(proc / "meminfo") or "").splitlines():
        key, _, value = line.partition(":")
        fields = value.split()
        if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"} and fields:
            try:
                values[key] = int(fields[0]) * 1024
            except ValueError:
                continue
    return values


def cpu_set(value: str) -> set[int]:
    selected: set[int] = set()
    for item in value.strip().split(","):
        if not item:
            continue
        low, separator, high = item.partition("-")
        first, last = int(low), int(high) if separator else int(low)
        if first < 0 or last < first or last > 1_000_000:
            raise ValueError("invalid CPU set")
        selected.update(range(first, last + 1))
    return selected


def unescape_mount(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), value)


def cgroup_mounts(proc: Path = PROC) -> list[CgroupMount]:
    mounts: list[CgroupMount] = []
    for line in (read_optional(proc / "self/mountinfo") or "").splitlines():
        left, separator, right = line.partition(" - ")
        fields, trailing = left.split(), right.split()
        if not separator or len(fields) < 5 or len(trailing) < 3:
            continue
        if trailing[0] not in {"cgroup", "cgroup2"}:
            continue
        mounts.append(
            CgroupMount(
                root=PurePosixPath(unescape_mount(fields[3])),
                path=Path(unescape_mount(fields[4])),
                controllers=frozenset(trailing[2].split(",")),
                version=2 if trailing[0] == "cgroup2" else 1,
            )
        )
    return mounts


def cgroup_scopes(pid: int, proc: Path = PROC) -> list[tuple[CgroupMount, list[Path]]]:
    """Resolve visible target memberships and every visible limiting ancestor."""
    scopes: list[tuple[CgroupMount, list[Path]]] = []
    mounts = cgroup_mounts(proc)
    for line in (read_optional(proc / str(pid) / "cgroup") or "").splitlines():
        fields = line.split(":", 2)
        if len(fields) != 3:
            continue
        controllers = frozenset(fields[1].split(",")) if fields[1] else frozenset[str]()
        membership = PurePosixPath(fields[2])
        if not membership.is_absolute() or ".." in membership.parts:
            continue
        for mount in mounts:
            if mount.version != (1 if controllers else 2):
                continue
            if controllers and not controllers.intersection(mount.controllers):
                continue
            try:
                relative = membership.relative_to(mount.root)
            except ValueError:
                continue
            path = mount.path / relative
            ancestors = [path]
            while path != mount.path:
                path = path.parent
                ancestors.append(path)
            scopes.append((mount, ancestors))
    return scopes


def number(path: Path) -> int | None:
    value = read_optional(path)
    if value is None or value == "max":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def resources(pid: int, proc: Path = PROC) -> dict[str, object]:
    host = memory_info(proc)
    total = host.get("MemTotal")
    available = host.get("MemAvailable")
    limits: list[int] = []
    headrooms: list[int] = []
    quotas: list[float] = []
    allowed: set[int] | None = None
    for line in (read_optional(proc / str(pid) / "status") or "").splitlines():
        if line.startswith("Cpus_allowed_list:"):
            with suppress(ValueError):
                allowed = cpu_set(line.partition(":")[2])
    scopes = cgroup_scopes(pid, proc)
    readable_memory_scopes = 0
    incomplete_memory_scopes = 0
    memory_scopes: list[dict[str, int | None]] = []
    for mount, ancestors in scopes:
        for path in ancestors:
            memory = mount.version == 2 or "memory" in mount.controllers
            if mount.version == 1 and path != ancestors[0]:
                memory = memory and number(path / "memory.use_hierarchy") != 0
            if memory:
                limit_path = path / (
                    "memory.max" if mount.version == 2 else "memory.limit_in_bytes"
                )
                current_path = path / (
                    "memory.current" if mount.version == 2 else "memory.usage_in_bytes"
                )
                raw_limit = read_optional(limit_path)
                limit, current = number(limit_path), number(current_path)
                if raw_limit == "max" or (limit is not None and limit >= 0):
                    readable_memory_scopes += 1
                memory_scopes.append(
                    {
                        "version": mount.version,
                        "limit_bytes": limit
                        if limit is not None and 0 <= limit < (1 << 60)
                        else None,
                        "usage_bytes": current,
                    }
                )
                if limit is not None and 0 <= limit < (1 << 60):
                    limits.append(limit)
                    if current is not None:
                        headrooms.append(max(0, limit - current))
                    else:
                        incomplete_memory_scopes += 1
            if mount.version == 2:
                raw_cpu = (read_optional(path / "cpu.max") or "").split()
                try:
                    if len(raw_cpu) == 2 and raw_cpu[0] != "max" and int(raw_cpu[1]) > 0:
                        quotas.append(int(raw_cpu[0]) / int(raw_cpu[1]))
                except ValueError:
                    pass
                cpus = read_optional(path / "cpuset.cpus.effective")
            else:
                quota, period = (
                    number(path / "cpu.cfs_quota_us"),
                    number(path / "cpu.cfs_period_us"),
                )
                if quota is not None and quota >= 0 and period is not None and period > 0:
                    quotas.append(quota / period)
                cpus = read_optional(path / "cpuset.cpus")
            if cpus:
                try:
                    selected = cpu_set(cpus)
                    if selected:
                        allowed = selected if allowed is None else allowed.intersection(selected)
                except ValueError:
                    pass
    capacities = limits + ([total] if total is not None else [])
    remaining = headrooms + ([available] if available is not None else [])
    cpu_capacities = quotas + ([float(len(allowed))] if allowed is not None else [])
    if os.cpu_count() is not None:
        cpu_capacities.append(float(os.cpu_count() or 1))
    return {
        "host_memory_bytes": host,
        "visible_cgroup_versions": sorted({mount.version for mount, _ in scopes}),
        "readable_memory_scopes": readable_memory_scopes,
        "visible_memory_scopes": memory_scopes,
        "memory_scopes_missing_usage": incomplete_memory_scopes,
        "smallest_visible_memory_limit_bytes": min(limits) if limits else None,
        "memory_limit_observation": "visible_scopes" if readable_memory_scopes else "unavailable",
        "effective_memory_capacity_bytes": (
            min(capacities) if capacities and readable_memory_scopes else None
        ),
        "estimated_memory_headroom_bytes": (
            min(remaining)
            if remaining and readable_memory_scopes and not incomplete_memory_scopes
            else None
        ),
        "smallest_visible_cpu_quota_cores": min(quotas) if quotas else None,
        "allowed_cpu_count": len(allowed) if allowed is not None else None,
        "effective_cpu_capacity_upper_bound_cores": min(cpu_capacities) if cpu_capacities else None,
    }


@dataclass
class CacheSize:
    path: str
    files: int = 0
    entries: int = 0
    logical_bytes: int = 0
    allocated_bytes: int = 0
    skipped_symlinks: int = 0
    errors: int = 0
    complete: bool = True


def cache_size(
    path: Path,
    *,
    entry_limit: int = CACHE_ENTRY_LIMIT,
    seconds: float = CACHE_SCAN_SECONDS,
) -> CacheSize:
    """Scan metadata only, skipping symlinks and stopping at either work budget."""
    result = CacheSize(path=str(path))
    pending = [path]
    seen: set[tuple[int, int]] = set()
    deadline = time.monotonic() + seconds
    while pending:
        if result.entries >= entry_limit or time.monotonic() >= deadline:
            result.complete = False
            return result
        directory = pending.pop()
        try:
            if not stat.S_ISDIR(directory.lstat().st_mode):
                result.errors += 1
                continue
            with os.scandir(directory) as entries:
                for entry in entries:
                    if result.entries >= entry_limit or time.monotonic() >= deadline:
                        result.complete = False
                        return result
                    result.entries += 1
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        result.errors += 1
                        continue
                    if stat.S_ISLNK(info.st_mode):
                        result.skipped_symlinks += 1
                    elif stat.S_ISDIR(info.st_mode):
                        pending.append(Path(entry.path))
                    elif stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) not in seen:
                        seen.add((info.st_dev, info.st_ino))
                        result.files += 1
                        result.logical_bytes += info.st_size
                        result.allocated_bytes += info.st_blocks * 512
        except OSError:
            result.errors += 1
    result.complete = result.complete and not result.errors
    return result


def disk_free(path: Path) -> dict[str, object]:
    try:
        usage = shutil.disk_usage(path)
        return {"path": str(path), "total_bytes": usage.total, "free_bytes": usage.free}
    except OSError:
        return {"path": str(path), "error": "path unavailable or filesystem metadata unreadable"}


def main(argv: list[str] | None = None) -> int:
    arguments = parser()
    options = arguments.parse_args(argv)
    pid: int = options.pid
    seconds: float = options.seconds
    state: Path | None = options.state_path
    caches: list[Path] = options.cache_path
    if len(caches) > 8:
        arguments.error("at most eight --cache-path options are supported")
    if not sys.platform.startswith("linux"):
        print("This read-only diagnostic requires Linux /proc.", file=sys.stderr)
        return 1
    try:
        sample = tree_sample(pid, seconds)
        resource = resources(pid)
    except (OSError, ValueError) as error:
        message = str(error) if isinstance(error, ValueError) else "process metadata unavailable"
        print(message, file=sys.stderr)
        return 1
    report: dict[str, object] = {
        "process_tree": sample,
        "resources": resource,
        "state_disk": disk_free(state) if state is not None else None,
        "caches": [asdict(cache_size(path)) for path in caches],
        "limitations": [
            "Two endpoint samples; transient processes and peaks between samples can be missed.",
            "RSS can double-count shared pages; CPU counts identities present at both endpoints.",
            "Descendants can exit, reparent or become unreadable; this is not a peak profile.",
            "Cgroup values include other processes in that group and visible ancestors.",
            "Hidden/unreadable ancestor limits are unknown; headroom is an estimate, not a budget.",
            "Headroom uses host MemAvailable and visible hard limits; reclaim/swap may differ.",
            "CPU quota capacity is a ceiling, not a reservation or a measure of host contention.",
            "Cache totals exclude symlinks and may be partial or race concurrent writes.",
            "Cache scans stop after 50000 entries or 2 seconds; filesystem calls can block longer.",
        ],
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
