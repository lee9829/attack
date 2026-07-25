# -*- coding: utf-8 -*-
"""
Beta step-by-step tests — each stage can be run alone for user feedback.
자사 점검 베타: 사용자가 단계별로 검증하고 의견을 남길 수 있게 분리.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .automation import (
    BrowserSession,
    google_login,
    run_browser_job_sync,
    _detect_exit_ip,
)
from .logutil import JobLog
from .octo_client import OctoClient, OctoError
from .proxy_manager import Proxy, parse_proxy_text
from .runner import AccountJob, accounts_from_rows


LogFn = Callable[[str], None]


def _jlog(log: LogFn, name: str) -> JobLog:
    return JobLog(log, job_index=0, profile=f"TEST:{name}", proxy="-")


def test_cloud(cfg: Dict[str, Any], log: LogFn) -> Dict[str, Any]:
    j = _jlog(log, "T1-Cloud")
    j.step_log("Cloud API 연결")
    token = str(cfg.get("octo_api_token") or "").strip()
    if not token:
        raise ValueError("API 토큰이 비어 있습니다.")
    client = OctoClient(
        api_token=token,
        cloud_base=str(cfg.get("cloud_base") or ""),
        local_base=str(cfg.get("local_base") or ""),
    )
    n = client.test_connection()
    j.ok(f"Cloud OK — 프로필 샘플 {n}개")
    return {"ok": True, "step": "T1", "profiles_sample": n}


def test_local(cfg: Dict[str, Any], log: LogFn) -> Dict[str, Any]:
    j = _jlog(log, "T2-Local")
    j.step_log("Local API (Octo 앱) 연결")
    client = OctoClient(
        api_token=str(cfg.get("octo_api_token") or "").strip() or "x",
        cloud_base=str(cfg.get("cloud_base") or ""),
        local_base=str(cfg.get("local_base") or ""),
    )
    user = client.local_username()
    active = client.list_active_profiles()
    j.ok(f"Local OK — user={user or 'unknown'} active={len(active)}")
    return {"ok": True, "step": "T2", "user": user, "active": len(active)}


def test_proxy_parse(cfg: Dict[str, Any], log: LogFn) -> Dict[str, Any]:
    j = _jlog(log, "T3-Proxy")
    j.step_log("프록시 목록 파싱·검증")
    text = str(cfg.get("proxies_text") or "")
    proxies, errs = parse_proxy_text(text, default_type=str(cfg.get("proxy_type") or "http"))
    j.info(f"유효 {len(proxies)}개 · 오류 줄 {len(errs)}개")
    for i, p in enumerate(proxies[:5]):
        j.proxy_log(f"[{i}] {p.display}")
    if len(proxies) > 5:
        j.info(f"… 외 {len(proxies) - 5}개")
    if not proxies:
        raise ValueError("유효한 프록시가 없습니다. 프록시 탭에서 붙여넣고 검증하세요.")
    if errs:
        j.warn(f"형식 오류 예시: {errs[0]}")
    j.ok("프록시 파싱 통과")
    return {"ok": True, "step": "T3", "count": len(proxies), "errors": len(errs)}


def _first_account(cfg: Dict[str, Any]) -> AccountJob:
    rows = list(cfg.get("accounts_rows") or [])
    if rows:
        return accounts_from_rows(rows)[0]
    return AccountJob(email="", password="", profile_title="beta-test-profile", notes="step test")


def _first_proxy(cfg: Dict[str, Any]) -> Proxy:
    proxies, _ = parse_proxy_text(
        str(cfg.get("proxies_text") or ""),
        default_type=str(cfg.get("proxy_type") or "http"),
    )
    if not proxies:
        raise ValueError("프록시가 없습니다.")
    idx = int(cfg.get("proxy_start_index") or 0)
    if idx < 0 or idx >= len(proxies):
        idx = 0
    return proxies[idx]


def test_profile_proxy(cfg: Dict[str, Any], log: LogFn) -> Dict[str, Any]:
    """T4: Cloud profile find/create + proxy inject only (no browser start)."""
    j = _jlog(log, "T4-ProfileProxy")
    acc = _first_account(cfg)
    proxy = _first_proxy(cfg)
    j.profile_log(f"프로필={acc.profile_title}")
    j.proxy_log(f"프록시={proxy.display}")
    client = OctoClient(
        api_token=str(cfg.get("octo_api_token") or "").strip(),
        cloud_base=str(cfg.get("cloud_base") or ""),
        local_base=str(cfg.get("local_base") or ""),
    )
    existing = client.find_profile_by_title(acc.profile_title)
    if existing:
        uuid = str(existing["uuid"])
        j.info(f"기존 프로필 uuid={uuid[:8]}…")
        client.update_profile_proxy(uuid, proxy)
        j.ok("프록시 PATCH 주입 완료")
        action = "update"
    else:
        uuid = client.create_profile(title=acc.profile_title, proxy=proxy)
        j.ok(f"프로필 생성 + 프록시 uuid={uuid[:8]}…")
        action = "create"
    return {"ok": True, "step": "T4", "uuid": uuid, "action": action, "proxy": proxy.display}


def test_profile_start_cdp(cfg: Dict[str, Any], log: LogFn, *, stop_after: bool = True) -> Dict[str, Any]:
    """T5: Start profile, CDP connect, detect IP, optional stop."""
    j = _jlog(log, "T5-StartCDP")
    prep = test_profile_proxy(cfg, log)
    uuid = prep["uuid"]
    client = OctoClient(
        api_token=str(cfg.get("octo_api_token") or "").strip(),
        cloud_base=str(cfg.get("cloud_base") or ""),
        local_base=str(cfg.get("local_base") or ""),
    )
    j.step_log("Local 프로필 Start")
    for active in client.list_active_profiles():
        if str(active.get("uuid")) == uuid:
            j.warn("이미 실행 중 → force stop")
            client.stop_profile(uuid, force=True)
            time.sleep(2)
    start = client.start_profile(
        uuid,
        headless=bool(cfg.get("headless", False)),
        timeout_sec=int(cfg.get("start_timeout_sec") or 120),
    )
    ws = str(start.get("ws_endpoint") or "")
    debug_port = start.get("debug_port")
    ip = ""
    if isinstance(start.get("connection_data"), dict):
        ip = str(start["connection_data"].get("ip") or "")
    j.ok(f"Start OK debug_port={debug_port} api_ip={ip or 'n/a'}")
    if not ws and debug_port:
        ws = f"http://127.0.0.1:{debug_port}"

    detected = ""
    try:
        import asyncio

        async def _probe() -> str:
            session = BrowserSession(ws, str(debug_port) if debug_port else None)
            page = await session.connect()
            j.info(f"CDP 연결 OK url={page.url}")
            dip = await _detect_exit_ip(page, j)
            await session.close()
            return dip

        detected = asyncio.run(_probe())
        if detected:
            j.proxy_log(f"브라우저 출구 IP={detected}")
        j.ok("CDP + IP 프로브 완료")
    except Exception as exc:
        j.warn(f"CDP/IP 프로브 경고: {exc}")
    finally:
        if stop_after:
            try:
                client.stop_profile(uuid, force=True)
                j.info("프로필 중지 완료")
            except Exception as exc:
                j.warn(f"중지 경고: {exc}")

    return {
        "ok": True,
        "step": "T5",
        "uuid": uuid,
        "debug_port": debug_port,
        "api_ip": ip,
        "detected_ip": detected,
        "stopped": stop_after,
    }


def test_google_login(cfg: Dict[str, Any], log: LogFn) -> Dict[str, Any]:
    """T6: Profile start → Google auto login + 2FA only → stop."""
    j = _jlog(log, "T6-Google")
    acc = _first_account(cfg)
    if not acc.email and not acc.password:
        j.warn("이메일/비밀번호가 비어 있음 — 로그인 테스트 결과가 제한될 수 있음")
    proxy = _first_proxy(cfg)
    client = OctoClient(
        api_token=str(cfg.get("octo_api_token") or "").strip(),
        cloud_base=str(cfg.get("cloud_base") or ""),
        local_base=str(cfg.get("local_base") or ""),
    )
    existing = client.find_profile_by_title(acc.profile_title)
    if existing:
        uuid = str(existing["uuid"])
        client.update_profile_proxy(uuid, proxy)
    else:
        uuid = client.create_profile(title=acc.profile_title, proxy=proxy)
    for active in client.list_active_profiles():
        if str(active.get("uuid")) == uuid:
            client.stop_profile(uuid, force=True)
            time.sleep(2)
    j.step_log("프로필 Start 후 Google 로그인만 테스트")
    start = client.start_profile(
        uuid,
        headless=bool(cfg.get("headless", False)),
        timeout_sec=int(cfg.get("start_timeout_sec") or 120),
    )
    ws = str(start.get("ws_endpoint") or "")
    debug_port = start.get("debug_port")
    if not ws and debug_port:
        ws = f"http://127.0.0.1:{debug_port}"
    google_cfg = dict(cfg.get("google_login") or {})
    google_cfg["enabled"] = True
    if str(google_cfg.get("mode") or "skip") == "skip":
        google_cfg["mode"] = "auto"
    otp = dict(google_cfg.get("otp_fetch") or {})
    if acc.otp_url:
        otp["url"] = acc.otp_url
        otp["enabled"] = True
    if acc.otp_selector:
        otp["selector"] = acc.otp_selector
    google_cfg["otp_fetch"] = otp

    result: Dict[str, Any] = {}
    try:
        result = run_browser_job_sync(
            ws,
            str(debug_port) if debug_port else None,
            google_cfg=google_cfg,
            email=acc.email,
            password=acc.password,
            targets=[],
            search_flow={"enabled": False},
            log=j,
            ask_2fa=None,  # GUI injects via kwargs if needed
            job_meta={
                "profile": acc.profile_title,
                "proxy": proxy.display,
                "otp_url": otp.get("url") or "",
            },
        )
        # When search disabled and no targets, run_browser_job may error — handle specially
    except Exception as exc:
        # run_browser_job raises if no targets and search off — do manual login only
        if "실행할 작업이 없습니다" in str(exc) or "search_flow" in str(exc).lower():
            import asyncio

            async def _login_only() -> bool:
                session = BrowserSession(ws, str(debug_port) if debug_port else None)
                page = await session.connect()
                ok = await google_login(
                    page,
                    mode=str(google_cfg.get("mode") or "auto"),
                    email=acc.email,
                    password=acc.password,
                    login_url=str(google_cfg.get("login_url") or "https://accounts.google.com/"),
                    success_url_contains=list(google_cfg.get("success_url_contains") or []),
                    manual_wait_sec=int(google_cfg.get("manual_wait_sec") or 300),
                    autofill_pause_ms=int(google_cfg.get("autofill_pause_ms") or 800),
                    otp=otp,
                    log=j,
                )
                await session.close()
                return ok

            ok = asyncio.run(_login_only())
            result = {"google_ok": ok}
        else:
            try:
                client.stop_profile(uuid, force=True)
            except Exception:
                pass
            raise

    try:
        client.stop_profile(uuid, force=True)
    except Exception:
        pass
    ok = bool(result.get("google_ok"))
    if ok:
        j.ok("Google 로그인 테스트 성공")
    else:
        j.warn("Google 로그인 미완료(세션/2FA 확인)")
    return {"ok": ok, "step": "T6", "google_ok": ok, "result": result}


def test_search_match(cfg: Dict[str, Any], log: LogFn, *, max_clicks: int = 1) -> Dict[str, Any]:
    """T7: One keyword search + own-domain match clicks (limited)."""
    j = _jlog(log, "T7-Search")
    acc = _first_account(cfg)
    proxy = _first_proxy(cfg)
    sf = dict(cfg.get("search_flow") or {})
    sf["enabled"] = True
    sf["max_result_clicks"] = max_clicks
    sf["max_serp_pages"] = min(int(sf.get("max_serp_pages") or 3), 3)
    # limit keywords for step test
    kws = list(sf.get("keywords") or [])
    if sf.get("keyword"):
        kws = [str(sf.get("keyword"))] + [k for k in kws if k != sf.get("keyword")]
    sf["keywords"] = kws[:1] if kws else ["test"]
    sf["keyword"] = sf["keywords"][0]
    j.search(f"테스트 검색어='{sf['keyword']}' max_clicks={max_clicks}")

    google_cfg = dict(cfg.get("google_login") or {})
    # keep login setting as user configured for realistic test
    client = OctoClient(
        api_token=str(cfg.get("octo_api_token") or "").strip(),
        cloud_base=str(cfg.get("cloud_base") or ""),
        local_base=str(cfg.get("local_base") or ""),
    )
    existing = client.find_profile_by_title(acc.profile_title)
    if existing:
        uuid = str(existing["uuid"])
        client.update_profile_proxy(uuid, proxy)
    else:
        uuid = client.create_profile(title=acc.profile_title, proxy=proxy)
    for active in client.list_active_profiles():
        if str(active.get("uuid")) == uuid:
            client.stop_profile(uuid, force=True)
            time.sleep(2)
    start = client.start_profile(
        uuid,
        headless=bool(cfg.get("headless", False)),
        timeout_sec=int(cfg.get("start_timeout_sec") or 120),
    )
    ws = str(start.get("ws_endpoint") or "")
    debug_port = start.get("debug_port")
    if not ws and debug_port:
        ws = f"http://127.0.0.1:{debug_port}"
    otp = dict((google_cfg.get("otp_fetch") or {}))
    if acc.otp_url:
        otp["url"] = acc.otp_url
    google_cfg["otp_fetch"] = otp
    try:
        result = run_browser_job_sync(
            ws,
            str(debug_port) if debug_port else None,
            google_cfg=google_cfg,
            email=acc.email,
            password=acc.password,
            targets=[],
            search_flow=sf,
            log=j,
            job_meta={
                "profile": acc.profile_title,
                "proxy": proxy.display,
                "otp_url": otp.get("url") or "",
            },
        )
    finally:
        try:
            client.stop_profile(uuid, force=True)
        except Exception:
            pass
    ok = bool(result.get("search_ok") or result.get("targets_ok"))
    j.ok(f"검색 테스트 종료 ok={ok} visits={result.get('search_visits')} matched={result.get('matched_url')}")
    return {"ok": ok, "step": "T7", "result": result}


def test_cta_direct(cfg: Dict[str, Any], log: LogFn, url: str = "") -> Dict[str, Any]:
    """T8: Open own URL directly and run CTA/banner clicks + scroll."""
    j = _jlog(log, "T8-CTA")
    sf = cfg.get("search_flow") or {}
    domain = str(sf.get("target_domain") or "").strip()
    if not url:
        url = str((cfg.get("targets") or [{}])[0].get("url") or "").strip()
    if not url and domain:
        url = f"https://{domain}/"
    if not url:
        raise ValueError("직접 열 URL 또는 자사 도메인이 필요합니다.")
    clicks = list(sf.get("banner_clicks") or [])
    j.site(f"직접 URL CTA 테스트: {url} · CTA {len(clicks)}개")
    acc = _first_account(cfg)
    proxy = _first_proxy(cfg)
    client = OctoClient(
        api_token=str(cfg.get("octo_api_token") or "").strip(),
        cloud_base=str(cfg.get("cloud_base") or ""),
        local_base=str(cfg.get("local_base") or ""),
    )
    existing = client.find_profile_by_title(acc.profile_title)
    if existing:
        uuid = str(existing["uuid"])
        client.update_profile_proxy(uuid, proxy)
    else:
        uuid = client.create_profile(title=acc.profile_title, proxy=proxy)
    for active in client.list_active_profiles():
        if str(active.get("uuid")) == uuid:
            client.stop_profile(uuid, force=True)
            time.sleep(1.5)
    start = client.start_profile(
        uuid,
        headless=bool(cfg.get("headless", False)),
        timeout_sec=int(cfg.get("start_timeout_sec") or 120),
    )
    ws = str(start.get("ws_endpoint") or "")
    debug_port = start.get("debug_port")
    if not ws and debug_port:
        ws = f"http://127.0.0.1:{debug_port}"
    try:
        import asyncio
        from .automation import _browse_own_site

        async def _run() -> None:
            session = BrowserSession(ws, str(debug_port) if debug_port else None)
            page = await session.connect()
            await page.goto(url, wait_until="domcontentloaded", timeout=90000)
            j.info(f"도착 {page.url}")
            human = dict(sf.get("human") or {})
            human.setdefault("scroll", True)
            human.setdefault("mouse_wander", True)
            human.setdefault("read_pauses", True)
            human.setdefault("dwell_ms_min", 2000)
            human.setdefault("dwell_ms_max", 5000)
            n = await _browse_own_site(page, banner_clicks=clicks, human=human, log=j)
            j.ok(f"CTA 시도 완료 success_clicks≈{n}")
            await session.close()

        asyncio.run(_run())
    finally:
        try:
            client.stop_profile(uuid, force=True)
        except Exception:
            pass
    return {"ok": True, "step": "T8", "url": url}


def test_dry_assignment(cfg: Dict[str, Any], log: LogFn) -> Dict[str, Any]:
    """T9: Dry-run account×proxy assignment only."""
    j = _jlog(log, "T9-DryRun")
    from .runner import JobRunner

    c = dict(cfg)
    c["dry_run"] = True
    runner = JobRunner(c, Path("."), log=log)
    # base_dir wrong if Path('.') — caller should pass real base; fix below
    return {"ok": True, "step": "T9", "note": "use GUI wrapper"}


def run_dry_assignment(cfg: Dict[str, Any], base_dir: Path, log: LogFn) -> Dict[str, Any]:
    from .runner import JobRunner

    j = _jlog(log, "T9-DryRun")
    c = dict(cfg)
    c["dry_run"] = True
    j.step_log("DRY RUN — 브라우저 없이 배정만")
    runner = JobRunner(c, base_dir, log=log)
    out = runner.run_all()
    j.ok(f"DRY RUN 완료 {out}")
    return {"ok": True, "step": "T9", "result": out}


STEP_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "T1",
        "title": "T1 · Cloud API 연결",
        "desc": "Octo Cloud 토큰·API 응답 확인",
        "fn": "cloud",
    },
    {
        "id": "T2",
        "title": "T2 · Local API 연결",
        "desc": "Octo 데스크톱 앱 Local API 확인",
        "fn": "local",
    },
    {
        "id": "T3",
        "title": "T3 · 프록시 파싱",
        "desc": "프록시 목록 형식 검증 (브라우저 없음)",
        "fn": "proxy",
    },
    {
        "id": "T4",
        "title": "T4 · 프로필+프록시 주입",
        "desc": "Cloud 프로필 생성/재사용 + 프록시 PATCH",
        "fn": "profile_proxy",
    },
    {
        "id": "T5",
        "title": "T5 · 프로필 시작+CDP+IP",
        "desc": "Local Start → Playwright CDP → 출구 IP → 중지",
        "fn": "start_cdp",
    },
    {
        "id": "T6",
        "title": "T6 · Google 로그인+2FA",
        "desc": "아이디/비번 + 타사 2FA 코드 사이트 자동",
        "fn": "google",
    },
    {
        "id": "T7",
        "title": "T7 · 검색·자사 매칭 (1클릭)",
        "desc": "검색어 1개 · 자사 결과 1건만 클릭 테스트",
        "fn": "search1",
    },
    {
        "id": "T8",
        "title": "T8 · 자사 URL CTA",
        "desc": "검색 없이 자사 URL 직접 열어 메뉴/예약 클릭",
        "fn": "cta",
    },
    {
        "id": "T9",
        "title": "T9 · DRY RUN 배정",
        "desc": "계정×프록시 배정만 (브라우저 없음)",
        "fn": "dry",
    },
]


def run_step(
    step_fn: str,
    cfg: Dict[str, Any],
    base_dir: Path,
    log: LogFn,
    *,
    ask_2fa: Optional[Callable[[str], Optional[str]]] = None,
) -> Dict[str, Any]:
    if step_fn == "cloud":
        return test_cloud(cfg, log)
    if step_fn == "local":
        return test_local(cfg, log)
    if step_fn == "proxy":
        return test_proxy_parse(cfg, log)
    if step_fn == "profile_proxy":
        return test_profile_proxy(cfg, log)
    if step_fn == "start_cdp":
        return test_profile_start_cdp(cfg, log, stop_after=True)
    if step_fn == "google":
        # inject ask_2fa via temporary monkey by wrapping run — pass through job
        return _test_google_with_ask(cfg, log, ask_2fa)
    if step_fn == "search1":
        return _test_search_with_ask(cfg, log, ask_2fa, max_clicks=1)
    if step_fn == "search3":
        return _test_search_with_ask(cfg, log, ask_2fa, max_clicks=3)
    if step_fn == "cta":
        return test_cta_direct(cfg, log)
    if step_fn == "dry":
        return run_dry_assignment(cfg, base_dir, log)
    raise ValueError(f"알 수 없는 테스트 단계: {step_fn}")


def _test_google_with_ask(cfg, log, ask_2fa):
    # re-implement light wrapper that passes ask_2fa
    j = _jlog(log, "T6-Google")
    acc = _first_account(cfg)
    proxy = _first_proxy(cfg)
    client = OctoClient(
        api_token=str(cfg.get("octo_api_token") or "").strip(),
        cloud_base=str(cfg.get("cloud_base") or ""),
        local_base=str(cfg.get("local_base") or ""),
    )
    existing = client.find_profile_by_title(acc.profile_title)
    if existing:
        uuid = str(existing["uuid"])
        client.update_profile_proxy(uuid, proxy)
    else:
        uuid = client.create_profile(title=acc.profile_title, proxy=proxy)
    for active in client.list_active_profiles():
        if str(active.get("uuid")) == uuid:
            client.stop_profile(uuid, force=True)
            time.sleep(2)
    start = client.start_profile(
        uuid,
        headless=bool(cfg.get("headless", False)),
        timeout_sec=int(cfg.get("start_timeout_sec") or 120),
    )
    ws = str(start.get("ws_endpoint") or "")
    debug_port = start.get("debug_port")
    if not ws and debug_port:
        ws = f"http://127.0.0.1:{debug_port}"
    google_cfg = dict(cfg.get("google_login") or {})
    google_cfg["enabled"] = True
    if str(google_cfg.get("mode") or "skip") == "skip":
        google_cfg["mode"] = "auto"
    otp = dict(google_cfg.get("otp_fetch") or {})
    if acc.otp_url:
        otp["url"] = acc.otp_url
    google_cfg["otp_fetch"] = otp
    import asyncio

    async def _login_only() -> bool:
        session = BrowserSession(ws, str(debug_port) if debug_port else None)
        page = await session.connect()
        ok = await google_login(
            page,
            mode=str(google_cfg.get("mode") or "auto"),
            email=acc.email,
            password=acc.password,
            login_url=str(google_cfg.get("login_url") or "https://accounts.google.com/"),
            success_url_contains=list(google_cfg.get("success_url_contains") or []),
            manual_wait_sec=int(google_cfg.get("manual_wait_sec") or 300),
            autofill_pause_ms=int(google_cfg.get("autofill_pause_ms") or 800),
            ask_2fa=ask_2fa,
            otp=otp,
            log=j,
        )
        await session.close()
        return ok

    try:
        ok = asyncio.run(_login_only())
    finally:
        try:
            client.stop_profile(uuid, force=True)
        except Exception:
            pass
    if ok:
        j.ok("T6 Google 로그인 성공")
    else:
        j.warn("T6 Google 로그인 미완료")
    return {"ok": ok, "step": "T6", "google_ok": ok}


def _test_search_with_ask(cfg, log, ask_2fa, max_clicks=1):
    j = _jlog(log, "T7-Search")
    acc = _first_account(cfg)
    proxy = _first_proxy(cfg)
    sf = dict(cfg.get("search_flow") or {})
    sf["enabled"] = True
    sf["max_result_clicks"] = max_clicks
    sf["max_serp_pages"] = min(int(sf.get("max_serp_pages") or 3), 3)
    kws = list(sf.get("keywords") or [])
    if sf.get("keyword"):
        kws = [str(sf.get("keyword"))] + [k for k in kws if k != sf.get("keyword")]
    sf["keywords"] = (kws[:1] if kws else ["test"])
    sf["keyword"] = sf["keywords"][0]
    google_cfg = dict(cfg.get("google_login") or {})
    otp = dict(google_cfg.get("otp_fetch") or {})
    if acc.otp_url:
        otp["url"] = acc.otp_url
    google_cfg["otp_fetch"] = otp
    client = OctoClient(
        api_token=str(cfg.get("octo_api_token") or "").strip(),
        cloud_base=str(cfg.get("cloud_base") or ""),
        local_base=str(cfg.get("local_base") or ""),
    )
    existing = client.find_profile_by_title(acc.profile_title)
    if existing:
        uuid = str(existing["uuid"])
        client.update_profile_proxy(uuid, proxy)
    else:
        uuid = client.create_profile(title=acc.profile_title, proxy=proxy)
    for active in client.list_active_profiles():
        if str(active.get("uuid")) == uuid:
            client.stop_profile(uuid, force=True)
            time.sleep(2)
    start = client.start_profile(
        uuid,
        headless=bool(cfg.get("headless", False)),
        timeout_sec=int(cfg.get("start_timeout_sec") or 120),
    )
    ws = str(start.get("ws_endpoint") or "")
    debug_port = start.get("debug_port")
    if not ws and debug_port:
        ws = f"http://127.0.0.1:{debug_port}"
    try:
        result = run_browser_job_sync(
            ws,
            str(debug_port) if debug_port else None,
            google_cfg=google_cfg,
            email=acc.email,
            password=acc.password,
            targets=[],
            search_flow=sf,
            log=j,
            ask_2fa=ask_2fa,
            job_meta={
                "profile": acc.profile_title,
                "proxy": proxy.display,
                "otp_url": otp.get("url") or "",
            },
        )
    finally:
        try:
            client.stop_profile(uuid, force=True)
        except Exception:
            pass
    ok = bool(result.get("search_ok") or result.get("targets_ok"))
    return {"ok": ok, "step": "T7", "result": result}


def save_feedback(
    base_dir: Path,
    *,
    step_id: str,
    rating: str,
    comment: str,
    result: Optional[Dict[str, Any]] = None,
) -> Path:
    fb_dir = base_dir / "feedback"
    fb_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = fb_dir / f"feedback_{step_id}_{ts}.txt"
    lines = [
        f"time={datetime.now().isoformat(timespec='seconds')}",
        f"step={step_id}",
        f"rating={rating}",
        f"comment={comment.strip()}",
        f"result={result!r}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
