"""The memory-aware ingest width guard.

``--ingest-jobs`` is a CEILING, not a target: the guard may lower it to fit the
host and must never raise it. The asymmetry is what lets one command be correct
on a 7 GB runner, a 16 GB runner and a 24 GB box container - so it is worth
pinning in both directions, along with the container-limit case, which is the
one that actually bites (inside Docker /proc/meminfo reports the HOST's RAM, so
sizing against it gets you OOM-killed by the cgroup instead of the kernel).
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "palette"))

from hafs_render import generate_hafs_plots as gen


@pytest.fixture
def mem(monkeypatch):
    """Pretend the host has `avail` MB usable."""
    def _set(avail):
        monkeypatch.setattr(gen, "_available_mb", lambda: avail)
    return _set


def test_ample_memory_honours_the_request(mem):
    mem(32_000)
    assert gen._fit_ingest_width(8) == 8


def test_tight_memory_lowers_the_width(mem):
    # 7 GB runner: (7000 - 1024) / 2300 = 2 workers
    mem(7000)
    assert gen._fit_ingest_width(8) == 2


def test_16gb_runner_supports_four(mem):
    # (16000 - 1024) / 2300 = 6 -> capped by the request
    mem(16_000)
    assert gen._fit_ingest_width(4) == 4


def test_box_container_supports_eight(mem):
    # the hafs-worker container's 24 GB limit: (24576 - 1024) / 2300 = 10
    mem(24_576)
    assert gen._fit_ingest_width(8) == 8


def test_never_raises_above_the_request(mem):
    mem(64_000)
    for req in (1, 2, 3, 4):
        assert gen._fit_ingest_width(req) == req, "the flag is a ceiling"


def test_always_leaves_at_least_one_worker(mem):
    mem(1200)          # less than one frame's budget after the reserve
    assert gen._fit_ingest_width(8) == 1


def test_unknown_memory_is_a_guard_not_a_gate(mem):
    mem(None)
    assert gen._fit_ingest_width(6) == 6


def test_serial_request_is_untouched(mem):
    mem(500)
    assert gen._fit_ingest_width(1) == 1


def test_container_limit_beats_a_larger_host(monkeypatch, tmp_path):
    """The case this exists for: a 24 GB container on a 31 GB box must size to
    24, not 31."""
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       32000000 kB\n"
                       "MemAvailable:   31000000 kB\n")
    real_read = pathlib.Path.read_text

    def fake_read(self, *a, **kw):
        if str(self) == "/proc/meminfo":
            return meminfo.read_text()
        if str(self) == "/sys/fs/cgroup/memory.max":
            return str(24 * 1024 * 1024 * 1024)      # 24 GB container
        raise OSError("not mocked")

    monkeypatch.setattr(pathlib.Path, "read_text", fake_read)
    try:
        assert gen._available_mb() == 24 * 1024
    finally:
        monkeypatch.setattr(pathlib.Path, "read_text", real_read)


def test_unlimited_cgroup_falls_back_to_host(monkeypatch, tmp_path):
    real_read = pathlib.Path.read_text

    def fake_read(self, *a, **kw):
        if str(self) == "/proc/meminfo":
            return "MemAvailable:   8000000 kB\n"
        if str(self) == "/sys/fs/cgroup/memory.max":
            return "max"
        raise OSError("not mocked")

    monkeypatch.setattr(pathlib.Path, "read_text", fake_read)
    try:
        assert gen._available_mb() == 8000000 // 1024
    finally:
        monkeypatch.setattr(pathlib.Path, "read_text", real_read)
