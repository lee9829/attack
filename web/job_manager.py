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
        # Windows agent (local Octo) bridge
        self._agent_online = False
        self._agent_last_seen = 0.0
        self._agent_name = ""
        self._agent_job: Optional[Dict[str, Any]] = None  # pending/leased payload
        self._agent_mode = False  # current run delegated to agent

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
            online = self._agent_online and (time.time() - self._agent_last_seen) < 45
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
                "agent": {
                    "online": online,
                    "name": self._agent_name,
                    "last_seen": self._agent_last_seen,
                    "pending_job": bool(self._agent_job),
                    "mode": self._agent_mode,
                },
            }

    def is_running(self) -> bool:
        with self._lock:
            if self._agent_mode and self.state.running:
                return True
            return self.state.running and bool(
                self._worker and self._worker.is_alive()
            )

    def agent_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            online = self._agent_online and (time.time() - self._agent_last_seen) < 45
            return {
                "online": online,
                "name": self._agent_name,
                "last_seen": self._agent_last_seen,
                "pending_job": bool(self._agent_job),
                "mode": self._agent_mode,
            }

    def agent_heartbeat(self, name: str = "windows") -> Dict[str, Any]:
        with self._lock:
            self._agent_online = True
            self._agent_last_seen = time.time()
            self._agent_name = (name or "windows").strip() or "windows"
        return self.agent_snapshot()

    def agent_pull_job(self) -> Optional[Dict[str, Any]]:
        """Windows agent pulls one pending job (if any)."""
        with self._lock:
            if not self.state.running or not self._agent_mode:
                return None
            if self._cancel.is_set():
                return None
            job = self._agent_job
            if not job or job.get("leased"):
                return None
            job["leased"] = True
            job["leased_at"] = time.time()
            self.state.status = "Agent running (local Octo)…"
            self.state.progress = {"phase": "agent", "engine": "agent"}
            # return copy without mutating shared unexpectedly
            return dict(job)

    def agent_push_log(self, msg: str) -> None:
        self.log(str(msg))

    def agent_finish(
        self,
        *,
        ok: bool,
        result: Optional[Dict[str, Any]] = None,
        error: str = "",
    ) -> None:
        with self._lock:
            self.state.last_result = result
            if error:
                self.state.error = error
                self.state.status = f"오류: {error}"
            else:
                r = result or {}
                self.state.status = (
                    f"완료 성공={r.get('success')} 실패={r.get('fail')} "
                    f"취소={r.get('cancelled')}"
                )
            self.state.running = False
            self.state.finished_at = time.time()
            self._agent_job = None
            self._agent_mode = False
            self._worker = None
        self.log(
            f"[Agent] 작업 종료 ok={ok} "
            f"result={result} error={error or '-'}"
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
        engine = str(cfg.get("browser_engine") or "auto").strip().lower()

        # Delegate to Windows agent (local Octo Browser on PC)
        if engine in ("agent", "windows", "remote_octo", "local_agent"):
            online = self.agent_snapshot().get("online")
            if not online:
                with self._lock:
                    self.state.running = False
                    self.state.error = "Windows 에이전트 오프라인"
                    self.state.status = "오류: Windows 에이전트가 연결되어 있지 않습니다"
                raise RuntimeError(
                    "Windows 에이전트가 오프라인입니다. "
                    "PC에서 Octo 실행 후 agent\\start_agent.bat 을 실행하세요."
                )
            # serialize accounts for agent
            acc_rows = []
            for a in accounts or []:
                acc_rows.append(
                    {
                        "email": a.email,
                        "password": a.password,
                        "profile_title": a.profile_title,
                        "notes": a.notes,
                        "otp_secret": a.otp_secret,
                        "otp_url": a.otp_url,
                        "otp_selector": a.otp_selector,
                    }
                )
            # force agent-side runner to use Octo Local on the PC
            cfg_agent = dict(cfg)
            cfg_agent["browser_engine"] = "octo"
            cfg_agent["octo_auto_login"] = True
            cfg_agent["allow_cloud_only"] = False
            job = {
                "id": f"job-{int(time.time())}",
                "config": cfg_agent,
                "accounts": acc_rows,
                "proxies_text": str(cfg.get("proxies_text") or ""),
                "proxy_start_index": proxy_start_index,
                "dry_run": dry_run,
                "leased": False,
            }
            with self._lock:
                self._agent_mode = True
                self._agent_job = job
                self.state.status = "대기: Windows 에이전트가 작업을 가져가는 중…"
                self.state.progress = {"phase": "agent_queue", "engine": "agent"}
            self.log(
                "[Agent] 작업을 Windows 에이전트 큐에 등록 "
                f"(accounts={len(acc_rows)} dry_run={dry_run})"
            )
            return

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
        with self._lock:
            if self._agent_mode:
                self.state.running = False
                self.state.status = "중지 요청 (에이전트)"
                self.state.finished_at = time.time()
                self._agent_job = None
                self._agent_mode = False
                self.log("[Agent] 웹에서 중지 요청")
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
