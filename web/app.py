# -*- coding: utf-8 -*-
"""
FastAPI web UI for Octo Google Site Automation.

Bind to 127.0.0.1 only (local control panel).
Core automation still uses Octo Browser + Playwright CDP.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import sys
import time
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

# ensure project root on path when launched as module
_WEB_DIR = Path(__file__).resolve().parent
_ROOT = _WEB_DIR.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from web.config_io import (  # noqa: E402
    build_accounts,
    load_or_default,
    normalize_config,
    parse_proxies,
    persist_config,
    public_config_view,
    secret_validation,
)
from web.job_manager import JobManager  # noqa: E402
from src.octo_client import OctoClient, OctoError  # noqa: E402

APP_VERSION = "2.3.1"
BASE_DIR = _ROOT

manager = JobManager(BASE_DIR)


def _read_env_file(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    if not path.is_file():
        return data
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            data[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return data


def _load_web_auth() -> Tuple[Optional[str], Optional[str]]:
    """
    Web UI login credentials.
    Priority:
      1) OCTO_WEB_USER / OCTO_WEB_PASSWORD env
      2) web_auth.env file next to project root (KEY=VALUE lines)
    If either is empty → auth disabled (local open mode).
    """
    user = (os.environ.get("OCTO_WEB_USER") or "").strip()
    password = (os.environ.get("OCTO_WEB_PASSWORD") or "").strip()
    if user and password:
        return user, password

    data = _read_env_file(BASE_DIR / "web_auth.env")
    user = (data.get("OCTO_WEB_USER") or user).strip()
    password = (data.get("OCTO_WEB_PASSWORD") or password).strip()
    if user and password:
        return user, password
    return None, None


def _load_agent_token() -> str:
    """Shared secret for Windows Agent EXE (no browser password needed)."""
    tok = (os.environ.get("OCTO_AGENT_TOKEN") or "").strip()
    if tok:
        return tok
    data = _read_env_file(BASE_DIR / "web_auth.env")
    tok = (data.get("OCTO_AGENT_TOKEN") or "").strip()
    if tok:
        return tok
    # also allow config.json (server-side only, gitignored)
    try:
        cfg_path = BASE_DIR / "config.json"
        if cfg_path.is_file():
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            tok = str(cfg.get("agent_token") or "").strip()
            if tok:
                return tok
    except Exception:
        pass
    return ""


def _agent_token_ok(request: Request) -> bool:
    expected = _load_agent_token()
    if not expected:
        return False
    got = (request.headers.get("X-Agent-Token") or "").strip()
    if not got:
        # also allow query for simple tools
        got = (request.query_params.get("agent_token") or "").strip()
    if not got:
        return False
    return secrets.compare_digest(got, expected)


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """HTTP Basic Auth for the whole panel when credentials are configured."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path or ""
        # health can stay public for uptime checks
        if path in ("/api/health", "/favicon.ico"):
            return await call_next(request)

        # Windows Agent EXE uses X-Agent-Token (no web password)
        if path.startswith("/api/agent/") and _agent_token_ok(request):
            return await call_next(request)

        user, password = _load_web_auth()
        if not user or not password:
            return await call_next(request)

        header = request.headers.get("Authorization") or ""
        ok = False
        if header.startswith("Basic "):
            try:
                raw = base64.b64decode(header[6:].strip()).decode("utf-8")
                u, p = raw.split(":", 1)
                ok = secrets.compare_digest(u, user) and secrets.compare_digest(p, password)
            except Exception:
                ok = False

        if not ok:
            return Response(
                content="Authentication required",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Octo Web Panel"'},
                media_type="text/plain",
            )
        return await call_next(request)


app = FastAPI(
    title="Octo Google Site Automation",
    version=APP_VERSION,
    docs_url="/api/docs",
    redoc_url=None,
)

app.add_middleware(BasicAuthMiddleware)

app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))


# ── request models ───────────────────────────────────────────
class ConfigBody(BaseModel):
    data: Dict[str, Any] = Field(default_factory=dict)


class StartBody(BaseModel):
    config: Dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False
    proxy_start_index: int = 0


class ProxyValidateBody(BaseModel):
    text: str = ""
    proxy_type: str = "http"


class AccountParseBody(BaseModel):
    text: str = ""


class TwoFABody(BaseModel):
    code: str = ""


class CookieParseBody(BaseModel):
    text: str = ""
    domain: str = ""
    url: str = ""


class OpsRunBody(BaseModel):
    config: Dict[str, Any] = Field(default_factory=dict)
    mode: str = "full"


class BulkParseBody(BaseModel):
    text: str = ""


class TestConnBody(BaseModel):
    octo_api_token: str = ""
    cloud_base: str = "https://app.octobrowser.net/api/v2/automation"
    local_base: str = "http://127.0.0.1:58888/api"
    octo_email: str = ""
    octo_password: str = ""
    octo_auto_login: bool = True


# ── pages ────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "version": APP_VERSION,
            "title": "Octo Google Site Automation",
        },
    )


# ── config ───────────────────────────────────────────────────
@app.get("/api/config")
async def api_get_config():
    cfg = load_or_default(BASE_DIR)
    return {"ok": True, "config": public_config_view(cfg)}


@app.post("/api/config")
async def api_save_config(body: ConfigBody):
    try:
        cfg = normalize_config(body.data)
        persist_config(BASE_DIR, cfg)
        manager.log("[Save] 설정 저장 완료 (config.json / proxies / accounts / domains / keywords)")
        return {"ok": True, "config": public_config_view(cfg)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── helpers ──────────────────────────────────────────────────
@app.post("/api/proxies/validate")
async def api_validate_proxies(body: ProxyValidateBody):
    proxies, errs = parse_proxies(body.text, body.proxy_type)
    return {
        "ok": True,
        "count": len(proxies),
        "proxies": [
            {
                "display": p.display,
                "host": p.host,
                "port": p.port,
                "type": p.type,
                "has_auth": bool(p.login),
            }
            for p in proxies
        ],
        "errors": errs[:50],
    }


@app.post("/api/accounts/parse")
async def api_parse_accounts(body: AccountParseBody):
    from src.runner import parse_account_bulk_text

    rows = parse_account_bulk_text(body.text or "")
    return {"ok": True, "count": len(rows), "rows": rows}


@app.post("/api/cookies/parse")
async def api_parse_cookies(body: CookieParseBody):
    from src.automation import parse_cookie_payload

    cookies = parse_cookie_payload(
        body.text or "",
        default_domain=(body.domain or "").strip(),
        default_url=(body.url or "").strip(),
    )
    return {
        "ok": True,
        "count": len(cookies),
        "cookies": [
            {
                "name": c.get("name"),
                "value": c.get("value"),
                "domain": c.get("domain"),
                "url": c.get("url"),
                "path": c.get("path"),
            }
            for c in cookies
        ],
    }


@app.post("/api/bulk/parse")
async def api_bulk_parse(body: BulkParseBody):
    from src.bulk_targets import parse_bulk_text

    bulk = parse_bulk_text(body.text or "")
    sample = []
    sample.extend(f"domain:{d}" for d in bulk.domains[:8])
    sample.extend(f"url:{u}" for u in bulk.full_urls[:8])
    sample.extend(f"path:{p}" for p in sorted(bulk.paths_exact)[:8])
    sample.extend(f"re:{r}" for r in bulk.path_regexes[:5])
    return {
        "ok": True,
        "stats": bulk.stats(),
        "sample": sample[:30],
        "domains_count": len(bulk.domains),
    }


@app.post("/api/ops/run")
async def api_ops_run(body: OpsRunBody):
    """HTTP-level recon/hammer against allowlisted own domains only."""
    try:
        from src.own_site_ops import run_ops_suite, save_report

        cfg = normalize_config(body.config or {})
        ops = dict(cfg.get("ops") or {})
        if body.mode:
            ops["mode"] = body.mode
        ops["enabled"] = True
        cfg["ops"] = ops
        sf = cfg.get("search_flow") or {}
        if not (sf.get("allowed_domains") or sf.get("target_domain")):
            raise HTTPException(
                status_code=400,
                detail="자사 도메인을 먼저 설정하세요 (허용 도메인 잠금).",
            )
        manager.log(f"[OPS] HTTP suite 시작 mode={ops.get('mode')}")
        report = run_ops_suite(cfg, log=manager.log)
        path = save_report(report, BASE_DIR)
        manager.log(f"[OPS] 리포트: {path} · {report.get('summary')}")
        return {"ok": True, "report": report, "path": str(path)}
    except HTTPException:
        raise
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        manager.log(f"[OPS] 실패: {exc}")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/ops/latest")
async def api_ops_latest():
    latest = BASE_DIR / "logs" / "ops_reports" / "latest.json"
    if not latest.is_file():
        return {"ok": True, "report": None}
    try:
        data = json.loads(latest.read_text(encoding="utf-8"))
        return {"ok": True, "report": data}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/test-connection")
async def api_test_connection(body: TestConnBody):
    token = (body.octo_api_token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="API 토큰을 입력하세요.")
    try:
        client = OctoClient(
            api_token=token,
            cloud_base=body.cloud_base.strip(),
            local_base=body.local_base.strip(),
        )
        n = client.test_connection()
        local_msg = ""
        local_ok = False
        saved = load_or_default(BASE_DIR)
        engine = str(saved.get("browser_engine") or "auto").strip().lower()
        try:
            email = (body.octo_email or "").strip()
            password = body.octo_password or ""
            if not email or not password:
                email = email or str(saved.get("octo_email") or "").strip()
                password = password or str(saved.get("octo_password") or "")
            sess = client.ensure_local_session(
                email=email,
                password=password,
                auto_login=bool(body.octo_auto_login),
            )
            local_ok = True
            local_msg = (
                f"Local OK (user={sess.get('username') or 'ok'} · "
                f"{sess.get('action') or 'ready'})"
            )
        except OctoError as exc:
            local_msg = f"Local OFF: {exc}"
            if engine in ("playwright", "pw", "chromium", "server", "auto"):
                local_msg = (
                    "Local OFF → 서버 엔진(Playwright+프록시)으로 Google 클릭 가능. "
                    f"(engine={engine or 'auto'})"
                )
        if local_ok:
            status = f"● Online — Cloud OK · {local_msg}"
        elif engine in ("playwright", "pw", "chromium", "server", "auto"):
            status = f"● Online — Cloud OK · Server engine ready ({local_msg})"
        else:
            status = f"● Cloud OK · {local_msg}"
        manager.log(f"[Test] Cloud OK (profiles sample {n}) · {local_msg}")
        return {
            "ok": True,
            "cloud_ok": True,
            "local_ok": local_ok,
            "profiles_sample": n,
            "status": status,
            "message": local_msg,
        }
    except Exception as exc:
        manager.log(f"[Test] fail: {exc}")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── jobs ─────────────────────────────────────────────────────
@app.get("/api/status")
async def api_status():
    return {"ok": True, **manager.snapshot()}


@app.post("/api/start")
async def api_start(body: StartBody):
    if manager.is_running():
        raise HTTPException(
            status_code=409,
            detail="이미 실행 중입니다. 중지 후 다시 시도하세요.",
        )
    try:
        cfg = normalize_config(body.config or {})
        proxies, errs = parse_proxies(
            str(cfg.get("proxies_text") or ""),
            str(cfg.get("proxy_type") or "http"),
        )
        if errs:
            manager.log(f"[Proxy] 형식 오류 {len(errs)}건 (무시)")
        if not proxies:
            raise HTTPException(status_code=400, detail="유효한 프록시가 없습니다.")
        engine = str(cfg.get("browser_engine") or "auto").strip().lower()
        if engine not in ("playwright", "pw", "chromium", "server"):
            if not str(cfg.get("octo_api_token") or "").strip():
                raise HTTPException(status_code=400, detail="Octo API 토큰을 입력하세요.")

        accounts = build_accounts(cfg)
        if not accounts:
            # allow profile-only rows for agent/playwright search jobs
            rows = list(cfg.get("accounts_rows") or [])
            if rows:
                from src.runner import AccountJob

                accounts = [
                    AccountJob(
                        email=str(r.get("email") or ""),
                        password=str(r.get("password") or ""),
                        profile_title=str(r.get("profile_title") or f"job-{i+1}"),
                        notes=str(r.get("notes") or ""),
                        otp_secret=str(r.get("otp_secret") or ""),
                    )
                    for i, r in enumerate(rows)
                ]
        if not accounts:
            raise HTTPException(
                status_code=400,
                detail="계정이 없습니다. email|비밀번호|2FA시크릿 또는 프로필 행을 추가하세요.",
            )

        g = cfg.get("google_login") or {}
        if g.get("enabled") and g.get("mode") == "auto":
            if not any(a.email for a in accounts):
                raise HTTPException(
                    status_code=400,
                    detail="자동 로그인에는 이메일이 필요합니다.",
                )

        sf = cfg.get("search_flow") or {}
        if sf.get("enabled"):
            has_kw = bool(sf.get("keywords") or str(sf.get("keyword") or "").strip())
            has_dom = bool(
                sf.get("allowed_domains")
                or str(sf.get("target_domain") or "").strip()
            )
            if not has_kw:
                raise HTTPException(status_code=400, detail="검색어를 입력하세요.")
            if not has_dom:
                raise HTTPException(status_code=400, detail="자사 도메인을 입력하세요.")
        elif not cfg.get("targets"):
            raise HTTPException(
                status_code=400,
                detail="검색 ON 또는 직접 URL targets 가 필요합니다.",
            )

        # persist before start
        persist_config(BASE_DIR, cfg)
        manager.log(
            f"[Save] 시작 전 저장 · 계정 {len(accounts)} · 프록시 {len(proxies)} · "
            f"검색어 {len(sf.get('keywords') or [])}"
        )

        v = secret_validation(accounts)
        if v.get("invalid"):
            for x in v["invalid"][:5]:
                manager.log(f"[2FA WARN] {x.get('email')}: {x.get('reason')}")

        start_idx = int(body.proxy_start_index or cfg.get("proxy_start_index") or 0)
        manager.start(
            cfg,
            proxies=proxies,
            accounts=accounts,
            dry_run=bool(body.dry_run),
            proxy_start_index=start_idx,
        )
        return {"ok": True, "status": manager.snapshot()}
    except HTTPException:
        raise
    except Exception as exc:
        manager.log(f"[Start Error] {exc}")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/stop")
async def api_stop():
    manager.stop()
    return {"ok": True, "status": manager.snapshot()}


# ── Windows agent (local Octo on PC) ─────────────────────────
class AgentHelloBody(BaseModel):
    name: str = "windows"
    octo: str = ""
    busy: bool = False


class AgentLogBody(BaseModel):
    msg: str = ""


class AgentFinishBody(BaseModel):
    ok: bool = True
    result: Dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class AgentProgressBody(BaseModel):
    phase: str = ""
    job: int = 0
    email: str = ""
    profile: str = ""
    ip: str = ""
    proxy: str = ""
    keyword: str = ""
    matched_url: str = ""
    google: str = ""
    action: str = ""
    has_2fa: bool = False
    parallel: int = 1
    loop: int = 0
    success: int = 0
    fail: int = 0
    total: int = 0
    active_jobs: list = Field(default_factory=list)


@app.post("/api/agent/heartbeat")
async def api_agent_heartbeat(body: AgentHelloBody):
    snap = manager.agent_heartbeat(body.name, busy=bool(body.busy), octo=body.octo or "")
    return {"ok": True, "agent": snap, "server_time": time.time()}


@app.get("/api/agent/status")
async def api_agent_status():
    return {"ok": True, "agent": manager.agent_snapshot(), "status": manager.snapshot()}


@app.get("/api/agent/next-job")
async def api_agent_next_job(request: Request):
    """Windows agent polls this to receive a job queued by the web Start button."""
    # keep online without overwriting friendly agent name when empty
    name = (request.query_params.get("name") or "").strip()
    manager.agent_heartbeat(name or None)
    job = manager.agent_pull_job()
    if not job:
        return {"ok": True, "job": None}
    manager.log(f"[Agent] 작업 전달 → {job.get('id')}")
    return {"ok": True, "job": job}


@app.post("/api/agent/log")
async def api_agent_log(body: AgentLogBody):
    if body.msg:
        manager.agent_push_log(body.msg)
    return {"ok": True}


@app.post("/api/agent/progress")
async def api_agent_progress(body: AgentProgressBody):
    manager.agent_push_progress(body.model_dump())
    return {"ok": True}


@app.post("/api/agent/release")
async def api_agent_release():
    """Agent could not start (e.g. Octo offline) — put job back in queue."""
    manager.agent_release_job()
    return {"ok": True, "agent": manager.agent_snapshot()}


@app.post("/api/agent/finish")
async def api_agent_finish(body: AgentFinishBody):
    manager.agent_finish(ok=bool(body.ok), result=body.result or {}, error=body.error or "")
    return {"ok": True, "status": manager.snapshot()}


@app.post("/api/2fa")
async def api_submit_2fa(body: TwoFABody):
    if not manager.submit_2fa(body.code):
        raise HTTPException(status_code=400, detail="코드를 입력하세요.")
    return {"ok": True}


@app.get("/api/logs")
async def api_logs(after: int = 0):
    return {"ok": True, "logs": manager.get_logs_since(after)}


@app.post("/api/logs/clear")
async def api_clear_logs():
    manager.clear_logs()
    return {"ok": True}


@app.get("/api/logs/stream")
async def api_logs_stream(after: int = 0):
    """Server-Sent Events for live logs + status."""

    async def gen():
        last = after
        while True:
            logs = manager.get_logs_since(last)
            snap = manager.snapshot()
            if logs:
                last = logs[-1]["id"]
            payload = {"logs": logs, "status": snap}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            if not snap.get("running") and not logs:
                # keep stream alive with heartbeat
                await asyncio.sleep(1.0)
            else:
                await asyncio.sleep(0.4)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/health")
async def health():
    user, password = _load_web_auth()
    return {
        "ok": True,
        "version": APP_VERSION,
        "mode": "web",
        "auth_enabled": bool(user and password),
    }


def create_app(base_dir: Optional[Path] = None) -> FastAPI:
    global BASE_DIR, manager
    if base_dir is not None:
        BASE_DIR = Path(base_dir)
        manager = JobManager(BASE_DIR)
    return app


def run_web(
    base_dir: Optional[Path] = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    open_browser: bool = True,
) -> None:
    """Entry used by main.py."""
    import uvicorn

    global BASE_DIR, manager
    if base_dir is not None:
        BASE_DIR = Path(base_dir)
        manager = JobManager(BASE_DIR)

    url = f"http://{host}:{port}/"
    print(f"[Web] Octo Automation v{APP_VERSION}")
    print(f"[Web] 브라우저에서 열기: {url}")
    wu, wp = _load_web_auth()
    if wu and wp:
        print(f"[Web] 로그인 보호 ON (user={wu})")
    else:
        print("[Web] 로그인 보호 OFF (OCTO_WEB_USER/PASSWORD 미설정)")
    print("[Web] 중지: Ctrl+C")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    # Pass app object (not import string) so JobManager/BASE_DIR stay bound
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    run_web(BASE_DIR)
