"""Exercise read-only Linux diagnostics using fictional metadata and a disposable process."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import diagnose_runtime as diagnostic


@pytest.mark.parametrize("value", ["0", "-1", "abc"])
def test_pid_requires_explicit_positive_integer(value: str) -> None:
    with pytest.raises(SystemExit):
        _ = diagnostic.parser().parse_args(["--pid", value])


@pytest.mark.parametrize("value", ["0", "-1", "60.1", "nan", "inf", "abc"])
def test_sample_time_is_bounded(value: str) -> None:
    with pytest.raises(SystemExit):
        _ = diagnostic.parser().parse_args(["--pid", "1", "--seconds", value])


def test_target_required_and_cache_count_bounded() -> None:
    with pytest.raises(SystemExit):
        _ = diagnostic.main([])
    with pytest.raises(SystemExit):
        _ = diagnostic.main(["--pid", "1", *(["--cache-path", "/fictional"] * 9)])


def proc_stat(pid: int, parent: int, ticks: int, started: int = 10) -> str:
    fields = ["S", str(parent), *("0" for _ in range(20))]
    fields[11], fields[12] = str(ticks), "2"
    fields[19], fields[21] = str(started), "3"
    return f"{pid} (fictional secret ) name) " + " ".join(fields)


def write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(value)


def test_tree_keeps_descendants_without_exposing_names(tmp_path: Path) -> None:
    write(tmp_path / "11/stat", proc_stat(11, 1, 8))
    write(tmp_path / "12/stat", proc_stat(12, 11, 3))
    write(tmp_path / "13/stat", proc_stat(13, 12, 1))
    write(tmp_path / "14/stat", proc_stat(14, 1, 9))
    write(tmp_path / "15/stat", "invalid")
    tree = diagnostic.process_tree(11, tmp_path)
    assert set(tree) == {11, 12, 13}
    assert tree[11].cpu_ticks == 10
    assert "secret" not in repr(tree)


def test_non_utf8_unrelated_process_name_does_not_abort(tmp_path: Path) -> None:
    write(tmp_path / "11/stat", proc_stat(11, 1, 8))
    write(tmp_path / "12/stat", proc_stat(12, 1, 3))
    _ = (tmp_path / "12/stat").write_bytes(proc_stat(12, 1, 3).encode().replace(b"secret", b"\xff"))
    assert set(diagnostic.process_tree(11, tmp_path)) == {11}


def test_cpu_uses_stable_identities_only(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshots = iter(
        [
            {
                11: diagnostic.Process(11, 1, 10, 100, 1000),
                12: diagnostic.Process(12, 11, 20, 30, 2000),
            },
            {
                11: diagnostic.Process(11, 1, 10, 120, 1500),
                12: diagnostic.Process(12, 11, 25, 9000, 3000),
            },
        ]
    )
    clock = iter([100.0, 102.0])

    def next_snapshot(*_args: object) -> dict[int, diagnostic.Process]:
        return next(snapshots)

    def no_sleep(_seconds: float) -> None:
        pass

    monkeypatch.setattr(diagnostic, "process_tree", next_snapshot)
    monkeypatch.setattr(diagnostic.time, "sleep", no_sleep)
    monkeypatch.setattr(diagnostic.time, "monotonic", lambda: next(clock))
    result = diagnostic.tree_sample(11, 2)
    assert result["processes_measured_for_cpu"] == 1
    assert result["cpu_seconds"] == round(20 / int(os.sysconf("SC_CLK_TCK")), 4)
    assert result["rss_bytes_at_end"] == 4500


def test_reused_target_pid_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshots = iter(
        [
            {11: diagnostic.Process(11, 1, 10, 100, 1000)},
            {11: diagnostic.Process(11, 1, 11, 120, 1500)},
        ]
    )

    def next_snapshot(*_args: object) -> dict[int, diagnostic.Process]:
        return next(snapshots)

    def no_sleep(_seconds: float) -> None:
        pass

    monkeypatch.setattr(diagnostic, "process_tree", next_snapshot)
    monkeypatch.setattr(diagnostic.time, "sleep", no_sleep)
    with pytest.raises(ValueError, match="changed identity"):
        _ = diagnostic.tree_sample(11, 0.1)


def fake_host(root: Path) -> Path:
    proc = root / "proc"
    write(proc / "meminfo", "MemTotal: 8192 kB\nMemAvailable: 6144 kB\nSwapFree: 0 kB\n")
    write(proc / "11/status", "Cpus_allowed_list:\t0-3\n")
    return proc


def test_v2_ancestor_headroom_and_cpu_quota(tmp_path: Path) -> None:
    proc = fake_host(tmp_path)
    mount = tmp_path / "cgroup"
    write(proc / "11/cgroup", "0::/app\n")
    write(proc / "self/mountinfo", f"1 0 0:1 / {mount} rw - cgroup2 cgroup rw\n")
    write(mount / "memory.max", "4000000")
    write(mount / "memory.current", "3800000")
    write(mount / "cpu.max", "50000 100000")
    write(mount / "app/memory.max", "3000000")
    write(mount / "app/memory.current", "1000000")
    write(mount / "app/cpu.max", "200000 100000")
    result = diagnostic.resources(11, proc)
    assert result["visible_cgroup_versions"] == [2]
    assert result["effective_memory_capacity_bytes"] == 3000000
    assert result["estimated_memory_headroom_bytes"] == 200000
    assert result["effective_cpu_capacity_upper_bound_cores"] == 0.5


def test_v1_unlimited_memory_and_split_controllers(tmp_path: Path) -> None:
    proc = fake_host(tmp_path)
    memory, cpu = tmp_path / "memory", tmp_path / "cpu"
    write(proc / "11/cgroup", "5:memory:/app\n4:cpu,cpuacct:/app\n")
    write(
        proc / "self/mountinfo",
        f"1 0 0:1 / {memory} rw - cgroup cgroup rw,memory\n"
        f"2 0 0:2 / {cpu} rw - cgroup cgroup rw,cpu,cpuacct\n",
    )
    write(memory / "memory.limit_in_bytes", str((1 << 63) - 4096))
    write(memory / "memory.usage_in_bytes", "12345")
    write(memory / "app/memory.limit_in_bytes", "1000000")
    write(memory / "app/memory.usage_in_bytes", "200000")
    write(cpu / "app/cpu.cfs_quota_us", "75000")
    write(cpu / "app/cpu.cfs_period_us", "100000")
    result = diagnostic.resources(11, proc)
    assert result["visible_cgroup_versions"] == [1]
    assert result["estimated_memory_headroom_bytes"] == 800000
    assert result["effective_cpu_capacity_upper_bound_cores"] == 0.75


def test_mount_root_mapping_and_invisible_membership(tmp_path: Path) -> None:
    proc = fake_host(tmp_path)
    mount = tmp_path / "group"
    write(proc / "self/mountinfo", f"1 0 0:1 /space {mount} rw - cgroup2 cgroup rw\n")
    write(proc / "11/cgroup", "0::/space/app\n")
    scopes = diagnostic.cgroup_scopes(11, proc)
    assert scopes[0][1] == [mount / "app", mount]
    write(proc / "11/cgroup", "0::/../../outside\n")
    assert diagnostic.cgroup_scopes(11, proc) == []


def test_v1_nonhierarchical_parent_does_not_limit_child(tmp_path: Path) -> None:
    proc = fake_host(tmp_path)
    memory = tmp_path / "memory"
    write(proc / "11/cgroup", "5:memory:/app\n")
    write(proc / "self/mountinfo", f"1 0 0:1 / {memory} rw - cgroup cgroup rw,memory\n")
    write(memory / "memory.use_hierarchy", "0")
    write(memory / "memory.limit_in_bytes", "100")
    write(memory / "memory.usage_in_bytes", "99")
    write(memory / "app/memory.limit_in_bytes", "1000000")
    write(memory / "app/memory.usage_in_bytes", "200000")
    result = diagnostic.resources(11, proc)
    assert result["estimated_memory_headroom_bytes"] == 800000


def test_unresolved_cgroups_do_not_claim_host_ram_is_available(tmp_path: Path) -> None:
    proc = fake_host(tmp_path)
    write(proc / "self/mountinfo", "1 0 0:1 /.. /sys/fs/cgroup rw - cgroup2 cgroup rw\n")
    write(proc / "11/cgroup", "0::/\n")
    result = diagnostic.resources(11, proc)
    assert result["memory_limit_observation"] == "unavailable"
    assert result["effective_memory_capacity_bytes"] is None
    assert result["estimated_memory_headroom_bytes"] is None


def test_missing_cgroup_usage_is_reported(tmp_path: Path) -> None:
    proc = fake_host(tmp_path)
    mount = tmp_path / "cgroup"
    write(proc / "11/cgroup", "0::/\n")
    write(proc / "self/mountinfo", f"1 0 0:1 / {mount} rw - cgroup2 cgroup rw\n")
    write(mount / "memory.max", "1000")
    result = diagnostic.resources(11, proc)
    assert result["memory_scopes_missing_usage"] == 1
    assert result["effective_memory_capacity_bytes"] == 1000
    assert result["estimated_memory_headroom_bytes"] is None


def test_cache_metadata_is_bounded_skips_links_and_closes_scandir(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    write(cache / "private-name.txt", "fictional-secret-contents")
    os.link(cache / "private-name.txt", cache / "duplicate.txt")
    (cache / "loop").symlink_to(cache, target_is_directory=True)
    descriptors = Path("/proc/self/fd")
    before = len(list(descriptors.iterdir())) if descriptors.is_dir() else None
    for _ in range(10):
        incomplete = diagnostic.cache_size(cache, entry_limit=1)
        assert not incomplete.complete
        assert incomplete.entries == 1
    if before is not None:
        assert len(list(descriptors.iterdir())) == before
    result = diagnostic.cache_size(cache)
    assert result.complete and result.files == 1
    assert result.skipped_symlinks == 1
    assert result.logical_bytes == len("fictional-secret-contents")
    assert "private-name" not in repr(result)
    assert not diagnostic.cache_size(tmp_path / "missing").complete


def test_cache_budget_also_bounds_pending_empty_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "empty").mkdir()
    clock = iter([0.0, 0.0, 0.0, 3.0])
    monkeypatch.setattr(diagnostic.time, "monotonic", lambda: next(clock))
    result = diagnostic.cache_size(tmp_path, seconds=2)
    assert not result.complete
    assert result.entries == 1


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux /proc diagnostic")
def test_cli_reads_only_safe_metadata_and_leaves_target_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    read_text = Path.read_text
    reads: list[Path] = []

    def checked_read(path: Path, *args: object, **kwargs: object) -> str:
        reads.append(path)
        assert path.name not in {"cmdline", "environ"}
        assert path.is_relative_to("/proc") or path.is_relative_to("/sys/fs/cgroup")
        return read_text(path)

    monkeypatch.setattr(Path, "read_text", checked_read)
    with subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "fictional-commandline-secret"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) as process:
        try:
            assert (
                diagnostic.main(
                    [
                        "--pid",
                        str(process.pid),
                        "--seconds",
                        "0.1",
                        "--state-path",
                        str(tmp_path),
                    ]
                )
                == 0
            )
            assert process.poll() is None
            output = capsys.readouterr().out
            assert "fictional-commandline-secret" not in output
            assert isinstance(json.loads(output), dict)
            assert reads
        finally:
            process.terminate()
            _ = process.wait(timeout=5)
