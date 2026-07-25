# -*- coding: utf-8 -*-
"""
Windows Local Octo Agent (script or EXE)

PC에서 Octo Browser 실행 후 이 프로그램만 켜 두면
사이트(Start) 작업을 받아 로컬 Octo로 실행합니다.

EXE 빌드 시 agent/embedded_config.py 에 서버·토큰이 박힙니다.
→ 상대방은 비번 입력 없이 더블클릭만 하면 됩니다.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional


def _bootstrap_path() -> Path:
    if getattr(sys, "frozen", False):
        # PyInstaller: modules in _MEIPASS, work dir next to exe
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

SERVER = (os.environ.get("AGENT_SERVER") or EMB_SERVER or "http://66.29.149.197:8787").rstrip(
    "/"
)
NAME = os.environ.get("AGENT_NAME") or EMB_NAME or "windows-pc"
TOKEN = (os.environ.get("AGENT_TOKEN") or EMB_TOKEN or "").strip()
POLL = float(os.environ.get("AGENT_POLL") or "2")


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    if TOKEN:
        s.headers["X-Agent-Token"] = TOKEN
    return s


def heartbeat(s: requests.Session) -> None:
    r = s.post(f"{SERVER}/api/agent/heartbeat", json={"name": NAME}, timeout=15)
    r.raise_for_status()


def pull_job(s: requests.Session) -> Optional[Dict[str, Any]]:
    r = s.get(f"{SERVER}/api/agent/next-job", timeout=30)
    r.raise_for_status()
    return r.json().get("job")


def push_log(s: requests.Session, msg: str) -> None:
    try:
        s.post(f"{SERVER}/api/agent/log", json={"msg": msg}, timeout=15)
    except Exception:
        print(msg, flush=True)


def finish(
    s: requests.Session,
    *,
    ok: bool,
    result: Optional[Dict[str, Any]] = None,
    error: str = "",
) -> None:
    s.post(
        f"{SERVER}/api/agent/finish",
        json={"ok": ok, "result": result or {}, "error": error},
        timeout=30,
    ).raise_for_status()


def _work_dir() -> Path:
    if getattr(sys, "frozen", False):
        d = Path(sys.executable).resolve().parent / "octo-agent-data"
    else:
        d = ROOT / "logs" / "agent-work"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_job(s: requests.Session, job: Dict[str, Any]) -> None:
    from src.runner import AccountJob, JobRunner
    from src.proxy_manager import parse_proxy_text

    cfg = dict(job.get("config") or {})
    cfg["browser_engine"] = "octo"
    cfg["dry_run"] = bool(job.get("dry_run"))
    cfg.setdefault("local_base", "http://127.0.0.1:58888/api")

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
        line = str(msg).rstrip()
        print(line, flush=True)
        push_log(s, line)

    log(
        f"[Agent] 작업 시작 id={job.get('id')} "
        f"accounts={len(accounts)} proxies={len(proxies)}"
    )
    try:
        runner = JobRunner(
            cfg,
            _work_dir(),
            proxies=proxies,
            accounts=accounts,
            log=log,
            proxy_start_index=int(job.get("proxy_start_index") or 0),
        )
        result = runner.run_all()
        log(
            f"[Agent] 완료 success={result.get('success')} fail={result.get('fail')} "
            f"cancelled={result.get('cancelled')}"
        )
        finish(s, ok=True, result=result)
    except Exception as exc:
        log(f"[Agent] 실패: {exc}")
        finish(s, ok=False, error=str(exc), result={"trace": traceback.format_exc()[-600:]})


def main() -> int:
    print("=" * 52, flush=True)
    print("  Octo Agent (Windows)", flush=True)
    print(f"  Server : {SERVER}", flush=True)
    print(f"  Name   : {NAME}", flush=True)
    print(f"  Token  : {'OK(내장)' if TOKEN else '없음 — 서버 연동 실패 가능'}", flush=True)
    print("  1) Octo Browser 실행·로그인", flush=True)
    print("  2) 이 창 유지", flush=True)
    print("  3) 사이트에서 엔진=agent 로 [시작]", flush=True)
    print("=" * 52, flush=True)

    if not TOKEN:
        print("[Agent] 경고: AGENT_TOKEN 이 비어 있습니다. EXE를 다시 빌드하세요.", flush=True)

    s = _session()
    fails = 0
    while True:
        try:
            heartbeat(s)
            fails = 0
            job = pull_job(s)
            if job:
                run_job(s, job)
            else:
                time.sleep(POLL)
        except KeyboardInterrupt:
            print("\n[Agent] 종료", flush=True)
            return 0
        except Exception as exc:
            fails += 1
            print(f"[Agent] 연결 오류 ({fails}): {exc}", flush=True)
            time.sleep(min(15, 2 + fails))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
