# -*- coding: utf-8 -*-
"""
OctoAgent — 통합 Windows 매크로 연동 프로그램

- 사이트(START/STOP)와 항상 연결 유지 (별도 heartbeat 스레드)
- 작업 중에도 연결이 끊기지 않음
- Octo Local 상태 표시
- 크래시 시 자동 재연결
- 사이트 위젯에 프로필/IP/클릭 실시간 전송
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional


def _bootstrap_path() -> Path:
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        if str(meipass) not in sys.path:
            sys.path.insert(0, str(meipass))
        return Path(sys.executable).resolve().parent
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


ROOT = _bootstrap_path()

try:
    from agent.embedded_config import (  # type: ignore
        AGENT_SERVER as EMB_SERVER,
        AGENT_TOKEN as EMB_TOKEN,
        AGENT_NAME as EMB_NAME,
    )
except Exception:
    try:
        from embedded_config import (  # type: ignore
            AGENT_SERVER as EMB_SERVER,
            AGENT_TOKEN as EMB_TOKEN,
            AGENT_NAME as EMB_NAME,
        )
    except Exception:
        EMB_SERVER = "http://66.29.149.197:8787"
        EMB_TOKEN = ""
        EMB_NAME = "windows-pc"

import requests  # noqa: E402

SERVER = (os.environ.get("AGENT_SERVER") or EMB_SERVER or "http://66.29.149.197:8787").rstrip("/")
NAME = os.environ.get("AGENT_NAME") or EMB_NAME or "windows-pc"
TOKEN = (os.environ.get("AGENT_TOKEN") or EMB_TOKEN or "").strip()
POLL = float(os.environ.get("AGENT_POLL") or "1.5")
HB_EVERY = float(os.environ.get("AGENT_HB") or "8")
USE_GUI = os.environ.get("AGENT_NO_GUI", "").strip() not in ("1", "true", "yes")


class AgentCore:
    def __init__(self) -> None:
        self.server = SERVER
        self.name = NAME
        self.token = TOKEN
        self._stop = threading.Event()
        self._job_lock = threading.Lock()
        self._job_thread: Optional[threading.Thread] = None
        self._busy = False
        self._status = "시작 중…"
        self._octo = "확인 중"
        self._last_err = ""
        self._ui_q: queue.Queue = queue.Queue()
        self._session = self._new_session()
        self._log_path = self._work_dir() / "agent.log"

    def _new_session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update({"Content-Type": "application/json"})
        if self.token:
            s.headers["X-Agent-Token"] = self.token
        # keep-alive
        s.headers["Connection"] = "keep-alive"
        return s

    def _work_dir(self) -> Path:
        if getattr(sys, "frozen", False):
            d = Path(sys.executable).resolve().parent / "octo-agent-data"
        else:
            d = ROOT / "logs" / "agent-work"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def log(self, msg: str) -> None:
        line = str(msg).rstrip()
        print(line, flush=True)
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(time.strftime("%H:%M:%S ") + line + "\n")
        except Exception:
            pass
        self._ui_q.put(("log", line))
        try:
            self._session.post(
                f"{self.server}/api/agent/log",
                json={"msg": line},
                timeout=8,
            )
        except Exception:
            pass

    def set_status(self, text: str) -> None:
        self._status = text
        self._ui_q.put(("status", text))

    def check_octo(self) -> bool:
        try:
            r = requests.get("http://127.0.0.1:58888/api/username", timeout=3)
            if r.status_code >= 400:
                self._octo = "오프라인"
                self._ui_q.put(("octo", self._octo))
                return False
            data = r.json() if r.text else {}
            user = ""
            if isinstance(data, dict):
                user = str(data.get("username") or data.get("user") or "")
            self._octo = f"로그인OK · {user}" if user else "Local OK"
            self._ui_q.put(("octo", self._octo))
            return True
        except Exception:
            self._octo = "오프라인 (Octo 실행·로그인 필요)"
            self._ui_q.put(("octo", self._octo))
            return False

    def heartbeat(self) -> bool:
        try:
            r = self._session.post(
                f"{self.server}/api/agent/heartbeat",
                json={"name": self.name, "octo": self._octo, "busy": self._busy},
                timeout=12,
            )
            r.raise_for_status()
            return True
        except Exception as exc:
            self._last_err = str(exc)
            # refresh session on network glitch
            self._session = self._new_session()
            return False

    def pull_job(self) -> Optional[Dict[str, Any]]:
        try:
            r = self._session.get(
                f"{self.server}/api/agent/next-job",
                params={"name": self.name},
                timeout=25,
            )
            r.raise_for_status()
            return r.json().get("job")
        except Exception as exc:
            self._last_err = str(exc)
            return None

    def release_job(self) -> None:
        """Return job to server queue (soft retry — do not mark run failed)."""
        for attempt in range(3):
            try:
                self._session.post(
                    f"{self.server}/api/agent/release",
                    json={},
                    timeout=15,
                ).raise_for_status()
                return
            except Exception as exc:
                self._last_err = str(exc)
                time.sleep(0.8 * (attempt + 1))
                self._session = self._new_session()
        self.log(f"[Agent] release 실패: {self._last_err}")

    def finish(
        self,
        *,
        ok: bool,
        result: Optional[Dict[str, Any]] = None,
        error: str = "",
    ) -> None:
        for attempt in range(5):
            try:
                self._session.post(
                    f"{self.server}/api/agent/finish",
                    json={"ok": ok, "result": result or {}, "error": error},
                    timeout=45,
                ).raise_for_status()
                return
            except Exception as exc:
                self._last_err = str(exc)
                time.sleep(1.5 * (attempt + 1))
                self._session = self._new_session()
        self.log(f"[Agent] finish 전송 실패: {self._last_err}")

    def run_job(self, job: Dict[str, Any]) -> None:
        from src.runner import AccountJob, JobRunner
        from src.proxy_manager import parse_proxy_text

        cfg = dict(job.get("config") or {})
        cfg["browser_engine"] = "octo"
        cfg["dry_run"] = bool(job.get("dry_run"))
        cfg.setdefault("local_base", "http://127.0.0.1:58888/api")
        # 50 프로필까지 동시/웨이브 운용 (웹 설정 존중, 하한만 보정)
        try:
            pj = int(cfg.get("parallel_jobs") or 1)
            if pj < 1:
                pj = 10
            cfg["parallel_jobs"] = min(pj, 50)
        except Exception:
            cfg["parallel_jobs"] = 10
        cfg["parallel_jobs_max"] = max(int(cfg.get("parallel_jobs_max") or 50), 50)
        try:
            st = float(cfg.get("stagger_start_sec") or 0)
            if st > 2.0 and int(cfg.get("parallel_jobs") or 1) >= 10:
                cfg["stagger_start_sec"] = 0.5
        except Exception:
            cfg["stagger_start_sec"] = 0.5
        # 섬세 증거 로그 기본 ON
        sf = dict(cfg.get("search_flow") or {})
        sf.setdefault("single_click", True)
        sf.setdefault("keyword_rotate", True)
        sf.setdefault("keyword_shuffle", True)
        cfg["search_flow"] = sf
        # 레이드 클릭 작업에서 OPS swarm/hammer 자동 OFF (디도스성 경로스캔 방지)
        ops = dict(cfg.get("ops") or {})
        if ops.get("enabled") and str(ops.get("mode") or "").lower() in (
            "swarm",
            "hammer",
            "blitz",
            "full",
        ):
            # 웹에서 실수로 OPS 켜진 채 START 해도 클릭 매크로 우선
            if not ops.get("force_ops_with_browser"):
                ops["enabled"] = False
                ops["run_http_ops"] = False
                self.log(
                    "[사람] OPS 스웜/해머는 끄고, 구글 검색·클릭 레이드만 진행합니다."
                )
        cfg["ops"] = ops

        accounts: List[AccountJob] = []
        for i, r in enumerate(list(job.get("accounts") or [])):
            accounts.append(
                AccountJob(
                    email=str(r.get("email") or ""),
                    password=str(r.get("password") or ""),
                    profile_title=str(r.get("profile_title") or f"agent-{i+1}"),
                    notes=str(r.get("notes") or ""),
                    otp_secret=str(r.get("otp_secret") or ""),
                    otp_url=str(r.get("otp_url") or ""),
                    otp_selector=str(r.get("otp_selector") or ""),
                )
            )
        px_text = str(job.get("proxies_text") or cfg.get("proxies_text") or "")
        proxies, _ = parse_proxy_text(
            px_text, default_type=str(cfg.get("proxy_type") or "http")
        )

        def log(msg: str) -> None:
            self.log(msg)

        def on_progress(info: Dict[str, Any]) -> None:
            for _ in range(3):
                try:
                    self._session.post(
                        f"{self.server}/api/agent/progress",
                        json=dict(info or {}),
                        timeout=8,
                    )
                    return
                except Exception:
                    time.sleep(0.4)
                    self._session = self._new_session()

        if not self.check_octo():
            self.log(
                "[Agent] Octo Local 미연결 — 큐로 반환. "
                "Octo 실행·로그인하면 자동 재시도됩니다."
            )
            self.set_status("Octo 대기 · 작업 보류")
            self.release_job()
            time.sleep(8)
            return

        self.log(
            f"[Agent] 작업 시작 id={job.get('id')} accounts={len(accounts)} "
            f"proxies={len(proxies)} parallel={cfg.get('parallel_jobs')}"
        )
        self.set_status(f"실행 중 · 동시 {cfg.get('parallel_jobs')}")
        try:
            runner = JobRunner(
                cfg,
                self._work_dir(),
                proxies=proxies,
                accounts=accounts,
                log=log,
                proxy_start_index=int(job.get("proxy_start_index") or 0),
                on_job_progress=on_progress,
            )
            result = runner.run_all()
            self.log(
                f"[Agent] 완료 success={result.get('success')} fail={result.get('fail')} "
                f"cancelled={result.get('cancelled')}"
            )
            self.finish(ok=True, result=result)
            self.set_status("대기 · 사이트 START 대기")
        except Exception as exc:
            err = f"{exc}"
            self.log(f"[Agent] 실패: {exc}")
            self.log(traceback.format_exc()[-500:])
            self.finish(
                ok=False,
                error=err,
                result={"trace": traceback.format_exc()[-800:]},
            )
            self.set_status("오류 후 대기")

    def _job_worker(self, job: Dict[str, Any]) -> None:
        try:
            self.run_job(job)
        finally:
            with self._job_lock:
                self._busy = False
                self._job_thread = None

    def _hb_loop(self) -> None:
        while not self._stop.is_set():
            self.check_octo()
            ok = self.heartbeat()
            if not ok:
                self.set_status(f"재연결 중… {self._last_err[:60]}")
            elif not self._busy:
                self.set_status("연결됨 · 대기 중")
            self._stop.wait(HB_EVERY)

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not self._busy:
                    job = self.pull_job()
                    if job:
                        with self._job_lock:
                            if self._busy:
                                time.sleep(POLL)
                                continue
                            self._busy = True
                            t = threading.Thread(
                                target=self._job_worker,
                                args=(job,),
                                daemon=True,
                                name="octo-job",
                            )
                            self._job_thread = t
                            t.start()
                time.sleep(POLL)
            except Exception as exc:
                self.log(f"[Agent] poll 오류: {exc}")
                time.sleep(3)

    def stop(self) -> None:
        self._stop.set()

    def start_background(self) -> None:
        threading.Thread(target=self._hb_loop, daemon=True, name="hb").start()
        threading.Thread(target=self._poll_loop, daemon=True, name="poll").start()


def run_gui(core: AgentCore) -> int:
    import tkinter as tk
    from tkinter import scrolledtext

    root = tk.Tk()
    root.title("Octo 소탕 에이전트 · 보안팀")
    root.geometry("620x520")
    root.configure(bg="#0e1218")
    try:
        root.attributes("-topmost", True)
        root.after(1800, lambda: root.attributes("-topmost", False))
    except Exception:
        pass

    fg = "#e8eef8"
    muted = "#8b95a8"
    accent = "#3dd68c"
    blue = "#5b9dff"

    hdr = tk.Label(
        root,
        text="Octo 소탕 연동 (게임 오토처럼 · 끄지 마세요)",
        font=("Segoe UI", 13, "bold"),
        bg="#0e1218",
        fg=accent,
    )
    hdr.pack(pady=(12, 2))

    lbl_server = tk.Label(
        root,
        text=f"통제 사이트: {core.server}",
        bg="#0e1218",
        fg=muted,
        font=("Segoe UI", 9),
    )
    lbl_server.pack()

    var_status = tk.StringVar(value="시작 중…")
    var_octo = tk.StringVar(value="Octo: 확인 중")
    lbl_status = tk.Label(
        root, textvariable=var_status, bg="#0e1218", fg=fg, font=("Segoe UI", 11, "bold")
    )
    lbl_status.pack(pady=(10, 2))
    lbl_octo = tk.Label(
        root, textvariable=var_octo, bg="#0e1218", fg="#f0b429", font=("Segoe UI", 10)
    )
    lbl_octo.pack()

    guide = tk.Label(
        root,
        text=(
            "실행: ① Octo Browser 로그인  ② 이 창 유지  "
            "③ 웹에서 START  ④ 프로필=검색=1클릭 자동"
        ),
        bg="#0e1218",
        fg=blue,
        font=("Segoe UI", 9),
        wraplength=580,
        justify="center",
    )
    guide.pack(pady=(6, 4), padx=10)

    log_box = scrolledtext.ScrolledText(
        root,
        height=16,
        bg="#080a0e",
        fg="#c9d2e0",
        insertbackground=fg,
        font=("Consolas", 9),
        relief="flat",
    )
    log_box.pack(fill="both", expand=True, padx=12, pady=10)

    def append_log(line: str) -> None:
        log_box.insert("end", line + "\n")
        log_box.see("end")
        # keep last ~500 lines
        try:
            if float(log_box.index("end-1c").split(".")[0]) > 600:
                log_box.delete("1.0", "100.0")
        except Exception:
            pass

    def pump() -> None:
        try:
            while True:
                kind, payload = core._ui_q.get_nowait()
                if kind == "log":
                    append_log(payload)
                elif kind == "status":
                    var_status.set(payload)
                elif kind == "octo":
                    var_octo.set("Octo: " + payload)
        except queue.Empty:
            pass
        if not core._stop.is_set():
            root.after(200, pump)

    def on_close() -> None:
        append_log("[Agent] 창 닫힘 — 연결 종료 (웹 START 불가)")
        core.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    append_log(f"Server={core.server}")
    append_log(f"Token={'내장OK' if core.token else '없음 — EXE 재빌드 필요'}")
    append_log("=== 정확한 실행 순서 ===")
    append_log("1) Octo Browser 실행 + 로그인 (Local API 켜짐)")
    append_log("2) 이 에이전트 창 유지 (연결 하트비트)")
    append_log("3) 웹 통제판 로그인 → 계정/검색어/타겟/프록시")
    append_log("4) ▶ START → Octo 프로필이 다양 검색 후 1클릭")
    append_log("5) 웹 위젯에서 프로필·IP·클릭 URL 실시간 확인")
    append_log("========================")

    core.start_background()
    pump()
    root.mainloop()
    return 0


def run_console(core: AgentCore) -> int:
    print("=" * 52, flush=True)
    print("  Octo 통합 에이전트 (중단 방지 · 자동 재연결)", flush=True)
    print(f"  Server : {core.server}", flush=True)
    print(f"  Token  : {'OK' if core.token else '없음'}", flush=True)
    print("  이 창을 닫지 마세요", flush=True)
    print("=" * 52, flush=True)
    core.start_background()
    try:
        while not core._stop.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        core.stop()
    return 0


def main() -> int:
    core = AgentCore()
    if not core.token:
        print("[Agent] AGENT_TOKEN 없음 — EXE 재빌드 필요", flush=True)
    if USE_GUI:
        try:
            return run_gui(core)
        except Exception as exc:
            print(f"[Agent] GUI 실패 → 콘솔 모드: {exc}", flush=True)
    return run_console(core)


if __name__ == "__main__":
    # outer supervisor: never exit silently
    while True:
        try:
            code = main()
            if code == 0:
                break
        except KeyboardInterrupt:
            break
        except Exception as exc:
            print(f"[Agent] 치명 오류 재시작: {exc}", flush=True)
            time.sleep(3)
    raise SystemExit(0)
