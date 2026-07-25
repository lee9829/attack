# -*- coding: utf-8 -*-
"""
Windows Local Octo Agent
------------------------
PC에서 Octo Browser를 켠 뒤 이 에이전트를 실행하면,
VPS 웹사이트(Start 버튼) 작업을 받아 로컬 Octo API로 실행합니다.

  1) Octo Browser 실행 + 로그인
  2) 이 스크립트 실행 (start_agent.bat)
  3) 브라우저에서 http://서버:8787 접속 → 시작

환경변수:
  AGENT_SERVER   기본 http://66.29.149.197:8787
  AGENT_NAME     기본 windows-pc
  AGENT_USER     웹 Basic Auth 아이디 (있으면)
  AGENT_PASS     웹 Basic Auth 비밀번호
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# project root on path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SERVER = (os.environ.get("AGENT_SERVER") or "http://66.29.149.197:8787").rstrip("/")
NAME = os.environ.get("AGENT_NAME") or "windows-pc"
USER = os.environ.get("AGENT_USER") or "admin"
PASS = os.environ.get("AGENT_PASS") or ""
POLL = float(os.environ.get("AGENT_POLL") or "2")


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    if USER and PASS:
        s.auth = (USER, PASS)
    return s


def heartbeat(s: requests.Session) -> None:
    r = s.post(f"{SERVER}/api/agent/heartbeat", json={"name": NAME}, timeout=15)
    r.raise_for_status()


def pull_job(s: requests.Session) -> Optional[Dict[str, Any]]:
    r = s.get(f"{SERVER}/api/agent/next-job", timeout=30)
    r.raise_for_status()
    data = r.json()
    return data.get("job")


def push_log(s: requests.Session, msg: str) -> None:
    try:
        s.post(f"{SERVER}/api/agent/log", json={"msg": msg}, timeout=15)
    except Exception:
        print(msg)


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


def run_job(s: requests.Session, job: Dict[str, Any]) -> None:
    from src.runner import AccountJob, JobRunner
    from src.proxy_manager import parse_proxy_text

    cfg = dict(job.get("config") or {})
    cfg["browser_engine"] = "octo"
    cfg["dry_run"] = bool(job.get("dry_run"))
    # ensure local API points at this PC
    cfg.setdefault("local_base", "http://127.0.0.1:58888/api")

    accounts_raw = list(job.get("accounts") or [])
    accounts: List[AccountJob] = []
    for i, r in enumerate(accounts_raw):
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
    proxies, _errs = parse_proxy_text(
        px_text, default_type=str(cfg.get("proxy_type") or "http")
    )

    def log(msg: str) -> None:
        line = str(msg).rstrip()
        print(line)
        push_log(s, line)

    log(f"[Agent] 작업 시작 id={job.get('id')} accounts={len(accounts)} proxies={len(proxies)}")
    try:
        runner = JobRunner(
            cfg,
            ROOT,
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
        err = f"{exc}\n{traceback.format_exc()[-800:]}"
        log(f"[Agent] 실패: {exc}")
        finish(s, ok=False, error=str(exc), result={"error": err})


def main() -> int:
    print("=" * 50)
    print(" Octo Windows Agent")
    print(f" Server: {SERVER}")
    print(f" Name:   {NAME}")
    print(" 1) Octo Browser 실행·로그인 확인")
    print(" 2) 웹에서 browser_engine=agent 로 시작")
    print("=" * 50)
    s = _session()
    while True:
        try:
            heartbeat(s)
            job = pull_job(s)
            if job:
                run_job(s, job)
            else:
                time.sleep(POLL)
        except KeyboardInterrupt:
            print("\n[Agent] 종료")
            return 0
        except Exception as exc:
            print(f"[Agent] 연결/오류: {exc}")
            time.sleep(max(3.0, POLL))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
