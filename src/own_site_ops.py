# -*- coding: utf-8 -*-
"""
Authorized own-site OPS / red-team style checks.

HARD RULE: only hosts in allowed_domains (your sites) are probed.
This is for defensive QA on assets you own — not third-party attacks.
"""
from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

LogFn = Callable[[str], None]


# Common surface map for own-site recon (discovery only, no exploit payloads)
DEFAULT_PATH_MAP = [
    "/",
    "/robots.txt",
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/favicon.ico",
    "/.well-known/security.txt",
    "/.well-known/change-password",
    "/.well-known/assetlinks.json",
    "/.well-known/apple-app-site-association",
    "/api",
    "/api/v1",
    "/api/v2",
    "/api/health",
    "/api/status",
    "/api/docs",
    "/health",
    "/healthz",
    "/ready",
    "/readyz",
    "/livez",
    "/status",
    "/ping",
    "/version",
    "/login",
    "/signin",
    "/signup",
    "/register",
    "/logout",
    "/admin",
    "/admin/login",
    "/administrator",
    "/wp-admin",
    "/wp-login.php",
    "/wp-json/",
    "/xmlrpc.php",
    "/dashboard",
    "/console",
    "/panel",
    "/manage",
    "/graphql",
    "/graphiql",
    "/playground",
    "/swagger",
    "/swagger-ui",
    "/swagger-ui.html",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/openapi.yaml",
    "/v1/api-docs",
    "/.env",
    "/.env.local",
    "/.env.production",
    "/.git/HEAD",
    "/.git/config",
    "/.svn/entries",
    "/.DS_Store",
    "/backup",
    "/backup.zip",
    "/backup.tar.gz",
    "/db.sql",
    "/dump.sql",
    "/phpinfo.php",
    "/info.php",
    "/server-status",
    "/server-info",
    "/actuator",
    "/actuator/health",
    "/actuator/env",
    "/actuator/mappings",
    "/metrics",
    "/prometheus",
    "/debug",
    "/debug/pprof",
    "/trace",
    "/test",
    "/staging",
    "/dev",
    "/internal",
    "/private",
    "/config",
    "/config.json",
    "/app.config",
    "/crossdomain.xml",
    "/clientaccesspolicy.xml",
    "/package.json",
    "/composer.json",
    "/web.config",
    "/elmah.axd",
    "/trace.axd",
    "/_debug",
    "/__debug__",
    "/cgi-bin/",
    "/tmp/",
    "/old/",
    "/bak/",
    "/api/user",
    "/api/users",
    "/api/auth",
    "/api/session",
    "/oauth/token",
    "/.aws/credentials",
]


SECURITY_HEADERS = [
    "strict-transport-security",
    "content-security-policy",
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
    "permissions-policy",
    "cross-origin-opener-policy",
    "cross-origin-resource-policy",
]


def _normalize_host(value: str) -> str:
    v = (value or "").strip().lower()
    if not v:
        return ""
    if "://" in v:
        try:
            v = urlparse(v).netloc or v
        except Exception:
            pass
    v = v.split("/")[0].split(":")[0]
    if v.startswith("www."):
        v = v[4:]
    return v


def host_allowed(host: str, allowed: List[str]) -> bool:
    h = _normalize_host(host)
    if not h or not allowed:
        return False
    for d in allowed:
        d = _normalize_host(d)
        if not d:
            continue
        if h == d or h.endswith("." + d):
            return True
    return False


def assert_url_allowed(url: str, allowed: List[str]) -> str:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = parsed.netloc or parsed.path
    if not host_allowed(host, allowed):
        raise PermissionError(
            f"차단: 허용 도메인 외 대상 '{host}'. 자사 도메인만 OPS 가능합니다."
        )
    scheme = parsed.scheme or "https"
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return f"{scheme}://{_normalize_host(host)}{path}"


@dataclass
class Finding:
    severity: str  # critical | high | medium | low | info
    category: str
    title: str
    detail: str
    url: str = ""
    evidence: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OpsReport:
    started_at: str = ""
    finished_at: str = ""
    mode: str = "recon"
    allowed_domains: List[str] = field(default_factory=list)
    base_urls: List[str] = field(default_factory=list)
    findings: List[Finding] = field(default_factory=list)
    path_hits: List[Dict[str, Any]] = field(default_factory=list)
    load_stats: Dict[str, Any] = field(default_factory=dict)
    header_matrix: Dict[str, Any] = field(default_factory=dict)
    cookie_audit: List[Dict[str, Any]] = field(default_factory=list)
    score: int = 100
    summary: str = ""

    def add(self, f: Finding) -> None:
        self.findings.append(f)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "mode": self.mode,
            "allowed_domains": self.allowed_domains,
            "base_urls": self.base_urls,
            "findings": [f.to_dict() for f in self.findings],
            "path_hits": self.path_hits,
            "load_stats": self.load_stats,
            "header_matrix": self.header_matrix,
            "cookie_audit": self.cookie_audit,
            "score": self.score,
            "summary": self.summary,
            "counts": self.severity_counts(),
        }

    def severity_counts(self) -> Dict[str, int]:
        c = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        for f in self.findings:
            c[f.severity] = c.get(f.severity, 0) + 1
        return c

    def recompute_score(self) -> int:
        score = 100
        for f in self.findings:
            if f.severity == "critical":
                score -= 25
            elif f.severity == "high":
                score -= 12
            elif f.severity == "medium":
                score -= 6
            elif f.severity == "low":
                score -= 2
        self.score = max(0, min(100, score))
        return self.score


def _http_get(
    url: str,
    *,
    timeout: float = 12.0,
    headers: Optional[Dict[str, str]] = None,
    method: str = "GET",
) -> Dict[str, Any]:
    req_headers = {
        "User-Agent": (
            "OctoOwnSiteOps/2.2 (+authorized-own-site-qa; local)"
        ),
        "Accept": "*/*",
    }
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers, method=method)
    ctx = ssl.create_default_context()
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            body = resp.read(256_000)
            elapsed = (time.perf_counter() - t0) * 1000
            return {
                "ok": True,
                "url": url,
                "final_url": resp.geturl(),
                "status": int(resp.status),
                "headers": {k.lower(): v for k, v in resp.headers.items()},
                "body": body,
                "elapsed_ms": round(elapsed, 1),
                "error": "",
            }
    except urllib.error.HTTPError as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        try:
            body = exc.read(64_000)
        except Exception:
            body = b""
        return {
            "ok": False,
            "url": url,
            "final_url": url,
            "status": int(exc.code),
            "headers": {k.lower(): v for k, v in (exc.headers.items() if exc.headers else [])},
            "body": body,
            "elapsed_ms": round(elapsed, 1),
            "error": str(exc),
        }
    except Exception as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        return {
            "ok": False,
            "url": url,
            "final_url": url,
            "status": 0,
            "headers": {},
            "body": b"",
            "elapsed_ms": round(elapsed, 1),
            "error": str(exc),
        }


def _decode_body(body: bytes) -> str:
    if not body:
        return ""
    for enc in ("utf-8", "cp949", "latin-1"):
        try:
            return body.decode(enc)
        except Exception:
            continue
    return body.decode("utf-8", errors="replace")


def audit_security_headers(url: str, headers: Dict[str, str], report: OpsReport) -> None:
    missing = [h for h in SECURITY_HEADERS if h not in headers]
    present = {h: headers[h] for h in SECURITY_HEADERS if h in headers}
    report.header_matrix[url] = {"present": present, "missing": missing}

    if "strict-transport-security" not in headers and url.startswith("https://"):
        report.add(
            Finding(
                "high",
                "headers",
                "HSTS 없음",
                "Strict-Transport-Security 헤더가 없습니다.",
                url=url,
            )
        )
    if "content-security-policy" not in headers:
        report.add(
            Finding(
                "medium",
                "headers",
                "CSP 없음",
                "Content-Security-Policy 미설정 — XSS 방어 약화 가능.",
                url=url,
            )
        )
    if "x-frame-options" not in headers and "content-security-policy" not in headers:
        report.add(
            Finding(
                "medium",
                "headers",
                "클릭재킹 완화 약함",
                "X-Frame-Options / frame-ancestors 없음.",
                url=url,
            )
        )
    if "x-content-type-options" not in headers:
        report.add(
            Finding(
                "low",
                "headers",
                "X-Content-Type-Options 없음",
                "nosniff 미설정.",
                url=url,
            )
        )
    server = headers.get("server") or headers.get("x-powered-by")
    if server:
        report.add(
            Finding(
                "info",
                "fingerprint",
                "서버 지문 노출",
                f"Server/X-Powered-By: {server}",
                url=url,
                evidence=server,
            )
        )


def audit_set_cookie(url: str, headers: Dict[str, str], report: OpsReport) -> None:
    # urllib may collapse set-cookie; also check raw-like keys
    raw_vals: List[str] = []
    for k, v in headers.items():
        if k == "set-cookie":
            raw_vals.append(v)
    for sc in raw_vals:
        parts = [p.strip() for p in sc.split(";")]
        if not parts:
            continue
        name = parts[0].split("=", 1)[0]
        flags = {p.lower().split("=", 1)[0]: p for p in parts[1:]}
        entry = {
            "url": url,
            "name": name,
            "httponly": "httponly" in flags,
            "secure": "secure" in flags,
            "samesite": flags.get("samesite", ""),
        }
        report.cookie_audit.append(entry)
        if not entry["httponly"]:
            report.add(
                Finding(
                    "medium",
                    "cookie",
                    f"HttpOnly 없음: {name}",
                    "JS에서 세션 쿠키 탈취 위험 (XSS 시).",
                    url=url,
                    evidence=sc[:200],
                )
            )
        if url.startswith("https://") and not entry["secure"]:
            report.add(
                Finding(
                    "medium",
                    "cookie",
                    f"Secure 없음: {name}",
                    "HTTPS 사이트에서 Secure 플래그 없음.",
                    url=url,
                    evidence=sc[:200],
                )
            )


SENSITIVE_BODY_PATTERNS = [
    (r"(?i)aws_access_key_id|AKIA[0-9A-Z]{16}", "critical", "AWS 키 형태 문자열"),
    (r"(?i)-----BEGIN (RSA |EC )?PRIVATE KEY-----", "critical", "프라이빗 키 노출"),
    (r"(?i)(api[_-]?key|secret[_-]?key)\s*[:=]\s*['\"][^'\"]{8,}", "high", "API 시크릿 형태"),
    (r"(?i)mongodb(\+srv)?://[^\s\"']+", "high", "DB 커넥션 문자열"),
    (r"(?i)password\s*[:=]\s*['\"][^'\"]{4,}", "medium", "password 할당 노출 가능"),
    (r"(?i)<title>\s*index of\s*/", "high", "디렉터리 리스팅"),
    (r"(?i)phpinfo\(\)|PHP Version", "high", "phpinfo 노출"),
    (r"(?i)stack trace|traceback \(most recent", "medium", "스택트레이스 노출"),
]


def scan_body_leaks(url: str, body: str, report: OpsReport) -> None:
    sample = body[:120_000]
    for pattern, sev, title in SENSITIVE_BODY_PATTERNS:
        m = re.search(pattern, sample)
        if m:
            report.add(
                Finding(
                    sev,
                    "leak",
                    title,
                    f"응답 본문에서 민감 패턴 감지: {m.group(0)[:80]}",
                    url=url,
                    evidence=m.group(0)[:120],
                )
            )


def extract_forms_and_links(base_url: str, html: str, allowed: List[str]) -> Dict[str, Any]:
    forms = []
    for m in re.finditer(
        r"<form\b([^>]*)>(.*?)</form>", html, flags=re.I | re.S
    ):
        attrs, inner = m.group(1), m.group(2)
        action_m = re.search(r'action=["\']([^"\']*)["\']', attrs, re.I)
        method_m = re.search(r'method=["\']([^"\']*)["\']', attrs, re.I)
        action = action_m.group(1) if action_m else ""
        method = (method_m.group(1) if method_m else "get").upper()
        inputs = re.findall(r'<input\b[^>]*name=["\']([^"\']+)["\']', inner, re.I)
        full = urljoin(base_url, action or ".")
        try:
            host = urlparse(full).netloc
            if host_allowed(host, allowed):
                forms.append(
                    {
                        "action": full,
                        "method": method,
                        "inputs": inputs[:30],
                    }
                )
        except Exception:
            pass

    links: List[str] = []
    for m in re.finditer(r'href=["\']([^"\'#]+)', html, re.I):
        href = m.group(1).strip()
        if href.lower().startswith("javascript:"):
            continue
        full = urljoin(base_url, href)
        try:
            if host_allowed(urlparse(full).netloc, allowed):
                links.append(full.split("#")[0])
        except Exception:
            pass
    # unique preserve order
    seen: Set[str] = set()
    uniq_links = []
    for u in links:
        if u not in seen:
            seen.add(u)
            uniq_links.append(u)
    return {"forms": forms, "links": uniq_links[:200]}


def probe_paths(
    base: str,
    allowed: List[str],
    paths: List[str],
    *,
    workers: int = 8,
    timeout: float = 10.0,
    log: Optional[LogFn] = None,
) -> List[Dict[str, Any]]:
    base = assert_url_allowed(base if base.endswith("/") else base + "/", allowed)
    origin = base.rstrip("/")
    _log = log or (lambda m: None)
    hits: List[Dict[str, Any]] = []

    def one(path: str) -> Optional[Dict[str, Any]]:
        if not path.startswith("/"):
            path = "/" + path
        url = origin + path
        try:
            assert_url_allowed(url, allowed)
        except PermissionError:
            return None
        r = _http_get(url, timeout=timeout)
        status = int(r.get("status") or 0)
        interesting = status in (200, 201, 204, 301, 302, 401, 403, 500) or (
            status != 404 and status != 0
        )
        if not interesting:
            return None
        return {
            "path": path,
            "url": url,
            "status": status,
            "elapsed_ms": r.get("elapsed_ms"),
            "length": len(r.get("body") or b""),
            "server": (r.get("headers") or {}).get("server", ""),
        }

    _log(f"[OPS] path recon {len(paths)} paths @ {origin} workers={workers}")
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 32))) as pool:
        futs = {pool.submit(one, p): p for p in paths}
        for fut in as_completed(futs):
            try:
                row = fut.result()
            except Exception:
                row = None
            if row:
                hits.append(row)
                _log(f"[OPS] HIT {row['status']} {row['path']} ({row['elapsed_ms']}ms)")
    hits.sort(key=lambda x: (x.get("status") or 0, x.get("path") or ""))
    return hits


def hammer_load(
    url: str,
    allowed: List[str],
    *,
    requests_n: int = 50,
    workers: int = 10,
    timeout: float = 15.0,
    log: Optional[LogFn] = None,
    methods: Optional[List[str]] = None,
) -> Dict[str, Any]:
    url = assert_url_allowed(url, allowed)
    _log = log or (lambda m: None)
    methods = [m.upper() for m in (methods or ["GET"])]
    _log(
        f"[OPS] HAMMER load {requests_n} req · workers={workers} "
        f"methods={methods} → {url}"
    )
    results: List[Dict[str, Any]] = []

    def one(i: int) -> Dict[str, Any]:
        method = methods[i % len(methods)]
        r = _http_get(url, timeout=timeout, method=method)
        return {
            "i": i,
            "method": method,
            "status": r.get("status"),
            "elapsed_ms": r.get("elapsed_ms"),
            "ok": r.get("ok"),
            "error": r.get("error") or "",
            "length": len(r.get("body") or b""),
        }

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 64))) as pool:
        futs = [pool.submit(one, i) for i in range(requests_n)]
        for fut in as_completed(futs):
            results.append(fut.result())
    total_ms = (time.perf_counter() - t0) * 1000
    statuses: Dict[str, int] = {}
    times = []
    bytes_total = 0
    for r in results:
        k = str(r.get("status") or 0)
        statuses[k] = statuses.get(k, 0) + 1
        if r.get("elapsed_ms"):
            times.append(float(r["elapsed_ms"]))
        bytes_total += int(r.get("length") or 0)
    times.sort()
    p50 = times[int(len(times) * 0.50)] if times else 0
    p95 = times[int(len(times) * 0.95) - 1] if times else 0
    p99 = times[int(len(times) * 0.99) - 1] if times else 0
    stats = {
        "url": url,
        "requests": requests_n,
        "workers": workers,
        "methods": methods,
        "wall_ms": round(total_ms, 1),
        "rps": round(requests_n / (total_ms / 1000.0), 2) if total_ms > 0 else 0,
        "status_counts": statuses,
        "bytes_total": bytes_total,
        "latency_ms": {
            "min": round(min(times), 1) if times else 0,
            "avg": round(sum(times) / len(times), 1) if times else 0,
            "p50": round(p50, 1),
            "p95": round(p95, 1),
            "p99": round(p99, 1),
            "max": round(max(times), 1) if times else 0,
        },
        "errors": sum(1 for r in results if not r.get("ok")),
    }
    _log(
        f"[OPS] HAMMER done rps={stats['rps']} p50={stats['latency_ms']['p50']}ms "
        f"p95={stats['latency_ms']['p95']}ms p99={stats['latency_ms']['p99']}ms "
        f"errors={stats['errors']} bytes≈{bytes_total} statuses={statuses}"
    )
    return stats


def multi_hammer(
    urls: List[str],
    allowed: List[str],
    *,
    requests_n: int = 40,
    workers: int = 12,
    timeout: float = 15.0,
    log: Optional[LogFn] = None,
) -> Dict[str, Any]:
    """Hammer multiple own-site URLs (split budget across targets)."""
    _log = log or (lambda m: None)
    clean: List[str] = []
    for u in urls:
        try:
            clean.append(assert_url_allowed(u, allowed))
        except PermissionError:
            _log(f"[OPS] multi-hammer skip blocked: {u}")
    if not clean:
        return {"targets": [], "combined": {}}
    per = max(5, requests_n // max(1, len(clean)))
    per_workers = max(2, workers // max(1, min(len(clean), 4)))
    targets = []
    total_req = 0
    total_err = 0
    rps_sum = 0.0
    for u in clean[:12]:
        st = hammer_load(
            u,
            allowed,
            requests_n=per,
            workers=per_workers,
            timeout=timeout,
            log=_log,
        )
        targets.append(st)
        total_req += int(st.get("requests") or 0)
        total_err += int(st.get("errors") or 0)
        rps_sum += float(st.get("rps") or 0)
    combined = {
        "urls": len(targets),
        "requests": total_req,
        "errors": total_err,
        "rps_sum": round(rps_sum, 2),
    }
    _log(f"[OPS] MULTI-HAMMER combined={combined}")
    return {"targets": targets, "combined": combined}


def run_recon(
    domains: List[str],
    *,
    base_paths: Optional[List[str]] = None,
    extra_paths: Optional[List[str]] = None,
    path_workers: int = 10,
    log: Optional[LogFn] = None,
) -> OpsReport:
    _log = log or print
    report = OpsReport(
        started_at=datetime.now(timezone.utc).isoformat(),
        mode="recon",
        allowed_domains=[_normalize_host(d) for d in domains if _normalize_host(d)],
    )
    if not report.allowed_domains:
        raise ValueError("자사 도메인이 필요합니다 (allowed_domains).")

    paths = list(base_paths or DEFAULT_PATH_MAP)
    for p in extra_paths or []:
        p = (p or "").strip()
        if p and p not in paths:
            paths.append(p if p.startswith("/") else "/" + p)

    for dom in report.allowed_domains:
        for scheme in ("https", "http"):
            base = f"{scheme}://{dom}/"
            report.base_urls.append(base)
            _log(f"[OPS] RECON seed {base}")
            home = _http_get(base, timeout=15)
            if home.get("status") == 0 and scheme == "https":
                continue
            if home.get("status") == 0:
                report.add(
                    Finding(
                        "high",
                        "availability",
                        "홈 접속 실패",
                        home.get("error") or "연결 실패",
                        url=base,
                    )
                )
                continue

            headers = home.get("headers") or {}
            audit_security_headers(base, headers, report)
            audit_set_cookie(base, headers, report)
            body = _decode_body(home.get("body") or b"")
            scan_body_leaks(base, body, report)

            if home.get("status") and int(home["status"]) >= 500:
                report.add(
                    Finding(
                        "high",
                        "availability",
                        f"홈 5xx ({home['status']})",
                        "홈페이지 서버 오류",
                        url=base,
                    )
                )

            extracted = extract_forms_and_links(base, body, report.allowed_domains)
            for form in extracted["forms"]:
                report.add(
                    Finding(
                        "info",
                        "surface",
                        f"폼 발견 {form['method']}",
                        f"action={form['action']} inputs={','.join(form['inputs'][:8])}",
                        url=form["action"],
                    )
                )
                # password field without https
                if any("pass" in i.lower() for i in form["inputs"]):
                    if form["action"].startswith("http://"):
                        report.add(
                            Finding(
                                "critical",
                                "transport",
                                "비밀번호 폼이 HTTP",
                                "로그인/비밀번호 입력이 평문 HTTP.",
                                url=form["action"],
                            )
                        )

            # interesting tech signals
            if "wp-content" in body or "wordpress" in body.lower():
                report.add(
                    Finding(
                        "info",
                        "fingerprint",
                        "WordPress 시그널",
                        "본문에 WP 흔적.",
                        url=base,
                    )
                )

            hits = probe_paths(
                base,
                report.allowed_domains,
                paths,
                workers=path_workers,
                log=_log,
            )
            report.path_hits.extend(hits)
            for h in hits:
                st = int(h.get("status") or 0)
                path = h.get("path") or ""
                if path in ("/.env", "/.git/HEAD", "/phpinfo.php", "/db.sql", "/backup.zip"):
                    if st == 200:
                        report.add(
                            Finding(
                                "critical",
                                "exposure",
                                f"민감 경로 공개: {path}",
                                f"status=200 length={h.get('length')}",
                                url=h.get("url") or "",
                            )
                        )
                elif path in ("/admin", "/wp-admin", "/console", "/dashboard") and st in (
                    200,
                    301,
                    302,
                ):
                    report.add(
                        Finding(
                            "medium",
                            "surface",
                            f"관리 표면 후보: {path}",
                            f"status={st}",
                            url=h.get("url") or "",
                        )
                    )
                elif st == 500:
                    report.add(
                        Finding(
                            "medium",
                            "stability",
                            f"5xx on {path}",
                            "서버 오류 응답",
                            url=h.get("url") or "",
                        )
                    )

            # prefer https success then stop http duplicate noise
            if scheme == "https" and int(home.get("status") or 0) > 0:
                break

    report.recompute_score()
    counts = report.severity_counts()
    report.summary = (
        f"score={report.score} critical={counts['critical']} high={counts['high']} "
        f"medium={counts['medium']} low={counts['low']} paths={len(report.path_hits)}"
    )
    report.finished_at = datetime.now(timezone.utc).isoformat()
    _log(f"[OPS] RECON complete · {report.summary}")
    return report


def run_ops_suite(cfg: Dict[str, Any], *, log: Optional[LogFn] = None) -> Dict[str, Any]:
    """
    cfg.ops:
      mode: recon | hammer | full
      path_workers, hammer_requests, hammer_workers
      extra_paths[]
      skip_hammer: bool
    domains from search_flow.allowed_domains / target_domain
    """
    _log = log or print
    ops = dict(cfg.get("ops") or {})
    mode = str(ops.get("mode") or "full").lower()
    sf = cfg.get("search_flow") or {}
    domains = list(sf.get("allowed_domains") or [])
    td = str(sf.get("target_domain") or "").strip()
    if td:
        domains.insert(0, td)
    # unique
    seen: Set[str] = set()
    doms = []
    for d in domains:
        n = _normalize_host(str(d))
        if n and n not in seen:
            seen.add(n)
            doms.append(n)

    extra = list(ops.get("extra_paths") or [])
    extra_text = str(ops.get("extra_paths_text") or "").strip()
    if extra_text:
        for ln in extra_text.replace(",", "\n").splitlines():
            ln = ln.strip()
            if ln:
                extra.append(ln)

    report: Optional[OpsReport] = None
    if mode in ("recon", "full", "swarm", "hammer", "blitz"):
        # hammer-only still does light recon on home unless skip
        if mode != "hammer" or not ops.get("skip_recon", False):
            report = run_recon(
                doms,
                extra_paths=extra,
                path_workers=int(ops.get("path_workers") or 10),
                log=_log,
            )
        else:
            report = OpsReport(
                started_at=datetime.now(timezone.utc).isoformat(),
                mode=mode,
                allowed_domains=doms,
            )

    assert report is not None
    report.mode = mode

    do_hammer = mode in ("hammer", "full", "swarm", "blitz") and not ops.get("skip_hammer")
    if do_hammer and doms:
        req_n = int(ops.get("hammer_requests") or 80)
        wrk = int(ops.get("hammer_workers") or 20)
        # blitz/swarm get higher defaults
        if mode in ("blitz", "swarm", "full"):
            req_n = max(req_n, int(ops.get("hammer_requests") or 100))
            wrk = max(wrk, int(ops.get("hammer_workers") or 24))
        intensity = max(1, min(int(ops.get("intensity") or 3), 5))
        req_n = int(req_n * (0.7 + 0.15 * intensity))
        wrk = min(64, int(wrk * (0.8 + 0.1 * intensity)))

        target = str(ops.get("hammer_url") or f"https://{doms[0]}/")
        multi_urls = [target]
        # fan-out: hammer home + interesting path hits
        for h in (report.path_hits or [])[:8]:
            u = h.get("url")
            st = int(h.get("status") or 0)
            if u and st in (200, 301, 302, 401, 403) and u not in multi_urls:
                multi_urls.append(u)
        # extra explicit hammer URLs
        for ln in str(ops.get("hammer_urls_text") or "").splitlines():
            ln = ln.strip()
            if ln and ln not in multi_urls:
                multi_urls.append(ln)

        try:
            if len(multi_urls) > 1 and ops.get("multi_hammer", True):
                multi = multi_hammer(
                    multi_urls,
                    doms,
                    requests_n=req_n,
                    workers=wrk,
                    log=_log,
                )
                report.load_stats = {
                    "multi": multi.get("combined"),
                    "targets": multi.get("targets"),
                    "rps": (multi.get("combined") or {}).get("rps_sum"),
                    "requests": (multi.get("combined") or {}).get("requests"),
                    "errors": (multi.get("combined") or {}).get("errors"),
                    "latency_ms": ((multi.get("targets") or [{}])[0] or {}).get(
                        "latency_ms"
                    )
                    or {},
                    "status_counts": {},
                    "url": target,
                }
                # merge status counts
                sc_all: Dict[str, int] = {}
                for t in multi.get("targets") or []:
                    for k, v in (t.get("status_counts") or {}).items():
                        sc_all[k] = sc_all.get(k, 0) + int(v)
                report.load_stats["status_counts"] = sc_all
                stats = report.load_stats
            else:
                stats = hammer_load(
                    target,
                    doms,
                    requests_n=req_n,
                    workers=wrk,
                    log=_log,
                    methods=["GET", "HEAD"] if intensity >= 4 else ["GET"],
                )
                report.load_stats = stats

            if stats.get("errors", 0) > max(2, int(stats.get("requests", 1) * 0.2)):
                report.add(
                    Finding(
                        "high",
                        "load",
                        "부하 시 오류 비율 높음",
                        f"errors={stats.get('errors')} / {stats.get('requests')}",
                        url=target,
                    )
                )
            p95 = float((stats.get("latency_ms") or {}).get("p95") or 0)
            if p95 > 3000:
                report.add(
                    Finding(
                        "medium",
                        "load",
                        "p95 지연 높음",
                        f"p95={p95}ms under hammer",
                        url=target,
                    )
                )
            sc = stats.get("status_counts") or {}
            five = sum(int(v) for k, v in sc.items() if str(k).startswith("5"))
            if five:
                report.add(
                    Finding(
                        "high",
                        "load",
                        "부하 중 5xx",
                        f"5xx count={five}",
                        url=target,
                    )
                )
        except PermissionError as exc:
            report.add(
                Finding("critical", "policy", "도메인 차단", str(exc), url=target)
            )

    report.recompute_score()
    counts = report.severity_counts()
    report.summary = (
        f"mode={mode} score={report.score} "
        f"C={counts['critical']} H={counts['high']} M={counts['medium']} "
        f"paths={len(report.path_hits)} load_rps={(report.load_stats or {}).get('rps', '-')}"
    )
    report.finished_at = datetime.now(timezone.utc).isoformat()
    return report.to_dict()


def save_report(report: Dict[str, Any], base_dir: Path) -> Path:
    out_dir = Path(base_dir) / "logs" / "ops_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"ops_{ts}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # compact latest
    latest = out_dir / "latest.json"
    latest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ── Playwright-side aggressive on-site actions (called from automation) ──

AGGRESSIVE_PRESETS = {
    "normal": {
        "dwell_scale": 1.0,
        "scroll_boost": 0,
        "revisit_boost": 0,
        "max_clicks_boost": 0,
        "internal_click": False,
        "form_probe": False,
        "link_spray": 0,
        "asset_walk": 0,
        "deep_scroll": False,
    },
    "swarm": {
        "dwell_scale": 0.45,
        "scroll_boost": 3,
        "revisit_boost": 2,
        "max_clicks_boost": 2,
        "internal_click": True,
        "form_probe": False,
        "link_spray": 5,
        "asset_walk": 2,
        "deep_scroll": True,
    },
    "hammer": {
        "dwell_scale": 0.25,
        "scroll_boost": 6,
        "revisit_boost": 3,
        "max_clicks_boost": 3,
        "internal_click": True,
        "form_probe": True,
        "link_spray": 10,
        "asset_walk": 4,
        "deep_scroll": True,
    },
    "blitz": {
        # max pressure on allowed own domains only
        "dwell_scale": 0.15,
        "scroll_boost": 8,
        "revisit_boost": 4,
        "max_clicks_boost": 4,
        "internal_click": True,
        "form_probe": True,
        "link_spray": 16,
        "asset_walk": 8,
        "deep_scroll": True,
    },
    "stealth_probe": {
        "dwell_scale": 0.75,
        "scroll_boost": 2,
        "revisit_boost": 0,
        "max_clicks_boost": 1,
        "internal_click": True,
        "form_probe": True,
        "link_spray": 3,
        "asset_walk": 1,
        "deep_scroll": False,
    },
}


def resolve_ops_preset(ops_cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    ops = dict(ops_cfg or {})
    name = str(ops.get("browser_preset") or ops.get("preset") or "normal").lower()
    if name == "full":
        name = "blitz"
    base = dict(AGGRESSIVE_PRESETS.get(name) or AGGRESSIVE_PRESETS["normal"])
    # allow overrides
    for k in base:
        if k in ops:
            base[k] = ops[k]
    intensity = int(ops.get("intensity") or (1 if name == "normal" else 3))
    intensity = max(1, min(intensity, 5))
    # scale spray/boosts by intensity (1=soft … 5=max)
    scale = 0.6 + 0.2 * intensity  # 0.8 … 1.6
    for key in ("link_spray", "asset_walk", "scroll_boost", "revisit_boost", "max_clicks_boost"):
        try:
            base[key] = int(round(float(base.get(key) or 0) * scale))
        except (TypeError, ValueError):
            pass
    if intensity >= 5 and name in ("hammer", "blitz", "swarm"):
        base["dwell_scale"] = min(float(base.get("dwell_scale") or 1), 0.2)
        base["form_probe"] = True
        base["deep_scroll"] = True
    base["name"] = name
    base["intensity"] = intensity
    return base


async def aggressive_on_site(
    page,
    *,
    allowed_domains: List[str],
    preset: Dict[str, Any],
    log=print,
    traffic=None,
) -> Dict[str, Any]:
    """
    Extra own-site pressure after landing: internal links, light form focus (no submit of junk).
    Domain-locked. Optionally records per-action traffic via TrafficTracker.
    """
    from playwright.async_api import Page  # type: ignore

    result = {
        "links_clicked": 0,
        "forms_seen": 0,
        "blocked": 0,
        "assets": 0,
        "scrolls": 0,
    }
    if not isinstance(page, Page):
        return result

    host = _normalize_host(urlparse(page.url).netloc)
    if not host_allowed(host, allowed_domains):
        log(f"[OPS] 차단 — 허용 외 호스트 {host}")
        result["blocked"] = 1
        return result

    intensity = int(preset.get("intensity") or 3)

    if preset.get("deep_scroll"):
        try:
            steps = 3 + intensity + int(preset.get("scroll_boost") or 0) // 2
            for i in range(min(steps, 20)):
                await page.evaluate(
                    "() => window.scrollBy(0, Math.floor(window.innerHeight * 0.7))"
                )
                await page.wait_for_timeout(120 + 40 * intensity)
                result["scrolls"] += 1
            log(f"[OPS] deep scroll steps={result['scrolls']}")
        except Exception as exc:
            log(f"[OPS] scroll: {exc}")

    spray = int(preset.get("link_spray") or 0)
    if spray > 0:
        if traffic is not None:
            try:
                traffic.mark_click("ops_link_spray", url=page.url)
            except Exception:
                pass
        try:
            hrefs = await page.eval_on_selector_all(
                "a[href]",
                "els => els.map(e => e.href).filter(Boolean).slice(0, 120)",
            )
        except Exception:
            hrefs = []
        clicked = 0
        for href in hrefs or []:
            if clicked >= spray:
                break
            try:
                h = _normalize_host(urlparse(str(href)).netloc)
                if not host_allowed(h, allowed_domains):
                    continue
                await page.goto(str(href), wait_until="domcontentloaded", timeout=20000)
                clicked += 1
                log(f"[OPS] internal spray → {href}")
                await page.wait_for_timeout(120 + 60 * intensity)
            except Exception as exc:
                log(f"[OPS] spray skip: {exc}")
        result["links_clicked"] = clicked
        if traffic is not None:
            try:
                traffic.end_click()
            except Exception:
                pass
        try:
            await page.go_back(timeout=10000)
        except Exception:
            pass

    # lightweight same-origin asset HEAD/GET via page.evaluate fetch (still domain-locked)
    asset_n = int(preset.get("asset_walk") or 0)
    if asset_n > 0:
        try:
            assets = await page.eval_on_selector_all(
                "img[src], script[src], link[href]",
                """els => els.map(e => e.src || e.href).filter(Boolean).slice(0, 40)""",
            )
            n = 0
            for a in assets or []:
                if n >= asset_n:
                    break
                try:
                    h = _normalize_host(urlparse(str(a)).netloc)
                    if h and not host_allowed(h, allowed_domains):
                        continue
                    await page.evaluate(
                        """async (u) => {
                            try { await fetch(u, {method:'GET', mode:'no-cors', credentials:'omit'}); }
                            catch(e) {}
                        }""",
                        str(a),
                    )
                    n += 1
                except Exception:
                    pass
            result["assets"] = n
            if n:
                log(f"[OPS] asset walk {n} (same-site only)")
        except Exception as exc:
            log(f"[OPS] asset walk: {exc}")

    if preset.get("form_probe"):
        try:
            n = await page.locator("form").count()
            result["forms_seen"] = n
            if n:
                log(f"[OPS] forms on page: {n}")
                inp = page.locator(
                    "form input[type='text'], form input[type='search'], form input:not([type])"
                ).first
                if await inp.count() > 0:
                    await inp.click(timeout=2000)
                    await inp.fill("ops-probe", timeout=2000)
                    log("[OPS] form field probe fill (no submit)")
        except Exception as exc:
            log(f"[OPS] form probe: {exc}")

    log(
        f"[OPS] pressure done links={result['links_clicked']} "
        f"forms={result['forms_seen']} assets={result['assets']} "
        f"scrolls={result['scrolls']} preset={preset.get('name')}"
    )
    return result
