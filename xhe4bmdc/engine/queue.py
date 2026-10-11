"""Job scheduler: queue, parallel workers, retry, cancellation, persistence."""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

from xhe4bmdc.engine.models import JOB_STATES, Job
from xhe4bmdc.engine.probe import ProbeResult, probe_media
from xhe4bmdc.engine.runner import CancelToken
from xhe4bmdc.engine.store import Store
from xhe4bmdc.pipeline.transcode import Transcoder

# Retryable error substrings (transient issues); everything else needs user action
_RETRYABLE = ("启动进程失败", "暂时", "timeout", "timed out", "资源暂时不可用",
              "device", "暂时性", "interrupted")


class Engine:
    """Process-wide singleton coordinating all export jobs."""

    def __init__(self, ffmpeg: str, ffprobe: str, xaacdec: str,
                 store: Store, parallelism: int = 2) -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.xaacdec = xaacdec
        self.store = store
        self.parallelism = max(1, parallelism)
        self._probes: dict[str, ProbeResult] = {}
        self._jobs: dict[str, Job] = {}
        self._cancels: dict[str, CancelToken] = {}
        self._lock = threading.RLock()
        self._workers = ThreadPoolExecutor(max_workers=self.parallelism,
                                           thread_name_prefix="xhe4bmdc-worker")
        self._notify: list[Callable[[], None]] = []
        self._log_listeners: list[Callable[[str, str], None]] = []  # (job_id, line)

        from xhe4bmdc.engine.command import available_encoders
        self.encoders = available_encoders(ffmpeg)

        # queue-level resume
        for job in store.resume_on_startup():
            self._jobs[job.id] = job

    # ---------------- notifications ----------------

    def add_notify(self, fn: Callable[[], None]) -> None:
        self._notify.append(fn)

    def add_log_listener(self, fn: Callable[[str, str], None]) -> None:
        self._log_listeners.append(fn)

    def _changed(self) -> None:
        for fn in list(self._notify):
            try:
                fn()
            except Exception:  # noqa: BLE001 — UI callback must never kill engine
                pass

    def _emit_log(self, job_id: str, line: str) -> None:
        for fn in list(self._log_listeners):
            try:
                fn(job_id, line)
            except Exception:  # noqa: BLE001
                pass

    # ---------------- probing ----------------

    def probe(self, path: str) -> ProbeResult:
        pr = probe_media(self.ffprobe, path)
        with self._lock:
            self._probes[path] = pr
        return pr

    def get_probe(self, path: str) -> Optional[ProbeResult]:
        with self._lock:
            return self._probes.get(path)

    # ---------------- job management ----------------

    def create_job(self, source_path: str, project_id: str,
                   options, output_dir: str, output_name: str = "") -> Job:
        pr = self.get_probe(source_path) or self.probe(source_path)
        job = Job(
            id=uuid.uuid4().hex[:12],
            project_id=project_id,
            source_path=source_path,
            output_name=output_name or Path(source_path).name,
            display_name=Path(source_path).name,
            is_xhe=pr.is_xhe,
            options=options,
            output_dir=output_dir)
        with self._lock:
            self._jobs[job.id] = job
        self.store.upsert_job(job)
        return job

    def enqueue(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state == "running":
                return
            job.state = "queued"
            job.error = None
            job.progress = 0.0
            job.stage = ""
            self._cancels[job_id] = CancelToken()
        self.store.upsert_job(job)
        self._workers.submit(self._run_job, job_id)
        self._changed()

    def retry(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.attempts += 1
        self.enqueue(job_id)

    def cancel(self, job_id: str) -> None:
        with self._lock:
            tok = self._cancels.get(job_id)
            job = self._jobs.get(job_id)
        if tok:
            tok.cancel()
        if job and job.state in ("queued",):
            job.state = "canceled"
            self.store.upsert_job(job)
            self._changed()

    def cancel_all(self) -> None:
        for jid in list(self._cancels):
            self.cancel(jid)

    def remove_job(self, job_id: str) -> None:
        self.cancel(job_id)
        with self._lock:
            self._jobs.pop(job_id, None)
            self._cancels.pop(job_id, None)
        self.store.delete_jobs([job_id])
        self._changed()

    # ---------------- worker ----------------

    def _run_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            cancel = self._cancels.get(job_id)
        if job is None or cancel is None:
            return
        job.state = "running"
        job.started_at = time.time()
        job.error = None
        self.store.upsert_job(job)
        self._changed()

        probe = self.get_probe(job.source_path) or self.probe(job.source_path)
        transcoder = Transcoder(self.ffmpeg, self.ffprobe, self.xaacdec, self.encoders)

        def on_log(line: str) -> None:
            self._emit_log(job_id, line)

        try:
            out = transcoder.run(
                job, probe, cancel,
                on_progress=lambda f: (self.store.upsert_job(job), self._changed_throttled()),
                on_stage=lambda s: self._changed(),
                on_log=on_log)
            job.output_path = str(out)
            job.state = "done"
        except Exception as exc:  # noqa: BLE001 — failure must not kill the worker
            msg = str(exc)
            if cancel.cancelled or "已取消" in msg:
                job.state = "canceled"
                job.error = "已取消"
            else:
                job.state = "failed"
                job.error = msg
        finally:
            job.finished_at = time.time()
            with self._lock:
                self._cancels.pop(job_id, None)
            self.store.upsert_job(job)
            self._changed()

    # throttle DB writes during progress callbacks
    _last_persist = 0.0

    def _changed_throttled(self) -> None:
        now = time.time()
        if now - self._last_persist > 0.5:
            self._last_persist = now
            self._changed()

    # ---------------- queries ----------------

    def all_jobs(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: (j.project_id, j.display_name))

    def jobs_of(self, project_id: str) -> list[Job]:
        with self._lock:
            return sorted((j for j in self._jobs.values() if j.project_id == project_id),
                          key=lambda j: j.display_name)

    def get_job(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def full_log(self, job_id: str) -> str:
        job = self.get_job(job_id)
        if job is None:
            return "任务不存在"
        logdir = Path(job.output_dir) / ".xhe4bmdc-work" / "logs"
        f = logdir / f"{job_id}.log"
        if f.exists():
            return f.read_text(encoding="utf-8", errors="replace")[-8000:]
        return job.log_text or "(无日志)"

    def shutdown(self) -> None:
        self.cancel_all()
        self._workers.shutdown(wait=True, cancel_futures=True)
        self.store.close()
