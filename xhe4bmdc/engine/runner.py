"""Subprocess runner: ffmpeg execution with progress parsing and cancellation."""
from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from xhe4bmdc.engine.probe import _creationflags


@dataclass
class RunResult:
    returncode: int
    log: str
    progress_last: float = 0.0      # seconds of output produced
    cancelled: bool = False


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


def run_with_progress(cmd: list[str], total_duration: float,
                      cancel: CancelToken, log_tail: deque[str],
                      env: dict | None = None,
                      on_time=None) -> RunResult:
    """Run a child process, parsing `-progress pipe:1` output.

    total_duration: source duration in seconds (0 => indeterminate progress).
    on_time(out_time_seconds) is invoked on every progress update if given.
    """
    creationflags = _creationflags()
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
            errors="replace", env=env, creationflags=creationflags)
    except OSError as exc:
        log_tail.append(f"[runner] 启动进程失败: {exc}")
        return RunResult(-1, "\n".join(log_tail))

    out_time_us = 0.0
    cancelled = False

    def pump_stderr() -> None:
        nonlocal cancelled
        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.rstrip()
            if line:
                log_tail.append(line)
                if len(log_tail) > 4000:
                    log_tail.popleft()
            if cancel.cancelled and not cancelled:
                cancelled = True
                _graceful_terminate(proc)

    t = threading.Thread(target=pump_stderr, daemon=True)
    t.start()

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line or "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key == "out_time_us" and val and val != "N/A":
            try:
                out_time_us = float(val) / 1_000_000.0
                if on_time:
                    on_time(out_time_us)
            except ValueError:
                pass
        if cancel.cancelled and not cancelled:
            cancelled = True
            _graceful_terminate(proc)

    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
    t.join(timeout=10)

    if cancel.cancelled:
        cancelled = True
    return RunResult(proc.returncode, "\n".join(log_tail), out_time_us, cancelled)


def _graceful_terminate(proc: subprocess.Popen) -> None:
    """Two-stage termination: polite, then forceful after a grace period."""
    try:
        proc.terminate()
    except OSError:
        return
    try:
        proc.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        proc.kill()
    except OSError:
        pass


def verify_output(ffprobe: str, path: Path, expect_duration: float,
                  tolerance: float = 0.5) -> tuple[bool, str]:
    """Post-encode verification: container parses and duration is sane."""
    import json
    cmd = [ffprobe, "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120,
                              creationflags=_creationflags())
    except subprocess.TimeoutExpired:
        return False, "验证超时"
    if proc.returncode != 0:
        return False, f"输出无法解析: {proc.stderr.strip()[-200:]}"
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return False, "输出探测返回非法 JSON"
    streams: list[dict] = list(data.get("streams") or [])
    for group in data.get("stream_groups") or []:
        streams.extend(group.get("streams") or [])
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    has_video = any(s.get("codec_type") == "video" and
                    not s.get("disposition", {}).get("attached_pic")
                    for s in streams)
    if not has_audio:
        return False, "输出缺少音频流"
    if expect_duration > 0:
        try:
            dur = float(data.get("format", {}).get("duration") or 0)
        except ValueError:
            dur = 0
        if dur <= 0:
            return False, "输出时长为 0"
        if abs(dur - expect_duration) > max(tolerance, expect_duration * 0.05):
            return False, f"输出时长 {dur:.2f}s 与源 {expect_duration:.2f}s 偏差过大"
    _ = has_video
    return True, ""
