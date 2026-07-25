# -*- coding: utf-8 -*-
"""Background job runner state for the web UI."""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from src.proxy_manager import Proxy
from src.runner import AccountJob, JobRunner


@dataclass
class JobState:
    running: bool = False
    dry_run: bool = False
    status: str = "Ready"
    progress: Dict[str, Any] = field(default_factory=dict)
    last_result: Optional[Dict[str, Any]] = None
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0


class JobManager:
    """Thread-safe job + log buffer used by the FastAPI app."""

    def __init__(self, base_dir: Path, *, max_log_lines: int = 4000):
        self.base_dir = Path(base_dir)
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._runner: Optional[JobRunner] = None
        self._logs: Deque[Dict[str, Any]] = deque(maxlen=max_log_lines)
        self._seq = 0
        self.state = JobState()
        # Manual 2FA (web): code submitted via API
        self._otp_event = threading.Event()
        self._otp_code: Optional[str] = None
        self._otp_prompt: str = ""

    # ── logging ──────────────────────────────────────────────
    def log(self, msg: str) -> None:
        line = str(msg).rstrip()
        with self._lock:
            self._seq += 1
            entry = {
                "id": self._seq,
                "ts": time.time(),
                "msg": line,
            }
            self._logs.append(entry)

    def get_logs_since(self, after_id: int = 0) -> List[Dict[str, Any]]:
        with self._lock:
            return [e for e in self._logs if e["id"] > after_id]

    def clear_logs(self) -> None:
        with self._lock:
            self._logs.clear()

    # ── 2FA bridge ───────────────────────────────────────────
    def ask_2fa(self, prompt: str) -> Optional[str]:
        """Called from job thread when manual OTP is needed."""
        with self._lock:
            self._otp_prompt = prompt or "2FA 코드를 입력하세요"
            self._otp_code = None
            self.state.progress = {
                **(self.state.progress or {}),
                "phase": "wait_2fa",
                "otp_prompt": self._otp_prompt,
            }
        self.log(f"[2FA] 수동 입력 대기: {self._otp_prompt}")
        self._otp_event.clear()
        # Wait up to 5 minutes
        ok = self._otp_event.wait(timeout=300)
        if not ok or self._cancel.is_set():
            self.log("[2FA] 시간 초과 또는 취소")
            return None
        with self._lock:
            code = self._otp_code
            self._otp_code = None
            self._otp_prompt = ""
            if self.state.progress.get("phase") == "wait_2fa":
                self.state.progress["phase"] = "browser"
        return code

    def submit_2fa(self, code: str) -> bool:
        code = (code or "").strip()
        if not code:
            return False
        with self._lock:
            self._otp_code = code
        self._otp_event.set()
        self.log(f"[2FA] 웹에서 코드 제출됨 ({len(code)}자)")
        return True

    # ── lifecycle ────────────────────────────────────────────
    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "running": self.state.running,
                "dry_run": self.state.dry_run,
                "status": self.state.status,
                "progress": dict(self.state.progress or {}),
                "last_result": self.state.last_result,
                "error": self.state.error,
                "started_at": self.state.started_at,
                "finished_at": self.state.finished_at,
                "otp_prompt": self._otp_prompt,
                "log_count": len(self._logs),
                "latest_log_id": self._seq,
            }

    def is_running(self) -> bool:
        with self._lock:
            return self.state.running and bool(
                self._worker and self._worker.is_alive()
            )

    def start(
        self,
        config: Dict[str, Any],
        *,
        proxies: Optional[List[Proxy]] = None,
        accounts: Optional[List[AccountJob]] = None,
        dry_run: bool = False,
        proxy_start_index: int = 0,
    ) -> None:
        if self.is_running():
            raise RuntimeError("이미 작업이 실행 중입니다. 중지 후 다시 시도하세요.")

        self._cancel.clear()
        self._otp_event.set()  # clear any stale wait
        with self._lock:
            self.state = JobState(
                running=True,
                dry_run=dry_run,
                status="Starting…",
                started_at=time.time(),
            )
            self.state.error = ""
            self.state.last_result = None

        cfg = dict(config)
        cfg["dry_run"] = dry_run

        def on_progress(info: Dict[str, Any]) -> None:
            with self._lock:
                prev = dict(self.state.progress or {})
                merged = {**prev, **dict(info or {})}
                if "active_jobs" in (info or {}):
                    merged["active_jobs"] = list(info.get("active_jobs") or [])
                self.state.progress = merged
                phase = info.get("phase")
                par = info.get("parallel") or merged.get("parallel") or 1
                if phase == "session_start":
                    self.state.status = (
                        f"동시 {par} · 큐 {info.get('total')} 시작"
                    )
                elif phase == "start":
                    n_act = len(
                        info.get("active_jobs") or merged.get("active_jobs") or []
                    )
                    self.state.status = (
                        f"동시 {par} · 활성 {n_act} · "
                        f"J{info.get('job')}/{info.get('total') or '?'}"
                    )
                elif phase == "done_one":
                    self.state.status = (
                        f"동시 {par} · 성공 {info.get('success')} "
                        f"실패 {info.get('fail')}"
                    )
                elif phase == "session_done":
                    self.state.status = (
                        f"완료 성공={info.get('success')} "
                        f"실패={info.get('fail')} (동시 {par})"
                    )

        def work() -> None:
            runner: Optional[JobRunner] = None
            try:
                self.log(
                    "[Start] "
                    + ("미리보기(DRY RUN)" if dry_run else "LIVE 실행")
                )
                runner = JobRunner(
                    cfg,
                    self.base_dir,
                    proxies=proxies,
                    accounts=accounts,
                    log=self.log,
                    should_cancel=self._cancel.is_set,
                    proxy_start_index=proxy_start_index,
                    ask_2fa=self.ask_2fa,
                    on_job_progress=on_progress,
                )
                with self._lock:
                    self._runner = runner
                    self.state.status = "Running"
                result = runner.run_all()
                with self._lock:
                    self.state.last_result = result
                    self.state.status = (
                        f"완료 성공={result.get('success')} "
                        f"실패={result.get('fail')} "
                        f"취소={result.get('cancelled')}"
                    )
                self.log(
                    f"[Finish] 성공={result.get('success')} "
                    f"실패={result.get('fail')} "
                    f"취소={result.get('cancelled')} "
                    f"총={result.get('total')}"
                )
            except Exception as exc:
                with self._lock:
                    self.state.error = str(exc)
                    self.state.status = f"오류: {exc}"
                self.log(f"[Error] {exc}")
            finally:
                with self._lock:
                    self.state.running = False
                    self.state.finished_at = time.time()
                    self._runner = None
                    self._worker = None

        t = threading.Thread(target=work, daemon=True, name="web-job")
        with self._lock:
            self._worker = t
        t.start()

    def stop(self) -> None:
        self._cancel.set()
        self._otp_event.set()
        self.log("[STOP] 긴급 중지 요청")
        runner = None
        with self._lock:
            runner = self._runner
            self.state.status = "중지 중…"
            # unlock UI-facing running flag early
            self.state.running = False

        def force() -> None:
            try:
                if runner is not None:
                    self.log("[STOP] 실행 중 프로필 force stop…")
                    runner.stop_started(force=True)
                    self.log("[STOP] 프로필 중지 완료")
            except Exception as exc:
                self.log(f"[STOP] 프로필 중지 경고: {exc}")
            with self._lock:
                self.state.status = "중지됨"
                self.state.finished_at = time.time()

        threading.Thread(target=force, daemon=True).start()
