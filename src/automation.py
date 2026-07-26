from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import random
import re
import struct
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from playwright.async_api import Browser, Locator, Page, async_playwright

# prompt message -> code string or None (cancel / timeout)
Ask2FAFn = Callable[[str], Optional[str]]

# Google Ads / SERP display path separators (title-side small path)
_PATH_SEP_RE = re.compile(r"\s*[›»>／/\\]\s*")


def _human_delay(ms: int) -> None:
    if ms > 0:
        time.sleep(ms / 1000.0)


async def _human_delay_async(ms: int) -> None:
    if ms > 0:
        await asyncio.sleep(ms / 1000.0)


async def _rand_delay(lo_ms: int, hi_ms: int) -> None:
    lo = max(0, int(lo_ms))
    hi = max(lo, int(hi_ms))
    await _human_delay_async(random.randint(lo, hi))


def _normalize_domain(value: str) -> str:
    v = (value or "").strip().lower()
    if not v:
        return ""
    if "://" in v:
        try:
            v = urlparse(v).netloc or v
        except Exception:
            pass
    v = v.split("/")[0]
    v = v.split(":")[0]
    if v.startswith("www."):
        v = v[4:]
    return v


def _host_matches_domain(host: str, domain: str) -> bool:
    h = _normalize_domain(host)
    d = _normalize_domain(domain)
    if not h or not d:
        return False
    return h == d or h.endswith("." + d)


def _extract_real_url(href: str) -> str:
    """Resolve Google redirect /url?q=… links to the real destination."""
    if not href:
        return ""
    try:
        parsed = urlparse(href)
        if "google." in (parsed.netloc or "") and parsed.path.startswith("/url"):
            qs = parse_qs(parsed.query)
            for key in ("q", "url"):
                if key in qs and qs[key]:
                    return unquote(qs[key][0])
        return href
    except Exception:
        return href


def _normalize_path_pattern(token: str) -> str:
    """
    Normalize user path patterns and Google display paths.
    Examples:
      promo  → /promo
      /promo/sale → /promo/sale
      example.com › promo › sale → /promo/sale
      https://x.com/a/b → /a/b
    """
    raw = (token or "").strip().lower()
    if not raw:
        return ""
    # full URL → path only
    if "://" in raw:
        try:
            p = urlparse(raw)
            raw = p.path or "/"
        except Exception:
            pass
    # breadcrumb → slash (› » >)
    raw = _PATH_SEP_RE.sub("/", raw)
    # www.host/path or host/path → /path
    if re.match(r"^(www\.)?[a-z0-9.-]+\.[a-z]{2,}(/|$)", raw):
        parts = raw.split("/", 1)
        raw = "/" + parts[1] if len(parts) > 1 else "/"
    raw = raw.strip()
    if not raw or raw == "/":
        return "/"
    if not raw.startswith("/"):
        raw = "/" + raw
    raw = re.sub(r"/+", "/", raw)
    if len(raw) > 1:
        raw = raw.rstrip("/")
    return raw


def _url_path_only(url: str) -> str:
    try:
        path = urlparse(url or "").path or "/"
    except Exception:
        path = "/"
    return _normalize_path_pattern(path)


def _looks_like_regex(pattern: str) -> bool:
    """Heuristic: treat as regex if marked or contains regex metacharacters."""
    p = (pattern or "").strip()
    if not p:
        return False
    if p.lower().startswith("re:") or p.startswith("/") and p.endswith("/") and len(p) > 2:
        return True
    # common regex markers users type for paths
    return bool(re.search(r"[\\^$*+?{}\[\]|()]", p))


def _strip_regex_marker(pattern: str) -> str:
    p = (pattern or "").strip()
    if p.lower().startswith("re:"):
        return p[3:].strip()
    if len(p) >= 2 and p.startswith("/") and p.endswith("/"):
        return p[1:-1]
    return p


def _compile_path_regexes(patterns: List[str]) -> List[re.Pattern]:
    out: List[re.Pattern] = []
    for raw in patterns:
        s = _strip_regex_marker(str(raw or "").strip())
        if not s:
            continue
        try:
            out.append(re.compile(s, re.I))
        except re.error:
            # fallback: escape as literal substring
            try:
                out.append(re.compile(re.escape(s), re.I))
            except re.error:
                continue
    return out


def _path_regex_hits(
    url_path: str,
    display_path: str,
    real_url: str,
    regexes: List[re.Pattern],
) -> bool:
    if not regexes:
        return False
    up = _url_path_only(url_path if url_path.startswith("/") else f"/{url_path}")
    # also test raw path without heavy normalize
    try:
        raw_path = urlparse(real_url or "").path or "/"
    except Exception:
        raw_path = up
    dp = (display_path or "").strip()
    candidates = [up, raw_path, real_url or "", dp, _PATH_SEP_RE.sub("/", dp)]
    for rx in regexes:
        for c in candidates:
            if c and rx.search(c):
                return True
    return False


def _path_pattern_hits(url_path: str, display_path: str, patterns: List[str]) -> bool:
    """
    True if any pattern matches the real URL path or Google title-side display path.
    Match rules (any):
      - regex if pattern looks like regex or re: prefix
      - exact path equality
      - path starts with pattern + /
      - pattern substring appears in path or display text
    """
    if not patterns:
        return False
    up = _normalize_path_pattern(url_path) or "/"
    dp_raw = (display_path or "").strip().lower()
    dp = _normalize_path_pattern(display_path) if display_path else ""
    # also keep a flat form of display for loose match (› stripped)
    dp_flat = _PATH_SEP_RE.sub("/", dp_raw).replace(" ", "")
    up_flat = up.replace(" ", "")

    plain: List[str] = []
    regex_pats: List[str] = []
    for pat in patterns:
        raw = (pat or "").strip()
        if not raw:
            continue
        if _looks_like_regex(raw):
            regex_pats.append(raw)
        else:
            plain.append(raw)

    if regex_pats:
        rxs = _compile_path_regexes(regex_pats)
        # reconstruct a pseudo-url for path-only regex
        fake = up
        if _path_regex_hits(up, display_path, fake, rxs):
            return True

    for pat in plain:
        p = _normalize_path_pattern(pat)
        if not p:
            continue
        p_flat = p.replace(" ", "")
        p_bare = p.lstrip("/")
        # exact / prefix path match
        if up == p or up.startswith(p + "/"):
            return True
        if dp and (dp == p or dp.startswith(p + "/")):
            return True
        # substring (user may type only segment: promo, landing)
        if p_bare and (p_bare in up_flat or p_bare in dp_flat):
            return True
        if p_flat and p_flat != "/" and (p_flat in up_flat or p_flat in dp_flat):
            return True
    return False


def _path_filter_allows(
    real_url: str,
    display_path: str,
    *,
    path_targets: List[str],
    path_exclude: List[str],
    path_regex: Optional[re.Pattern] = None,
    path_regexes: Optional[List[re.Pattern]] = None,
    url_regex: Optional[re.Pattern] = None,
    require_regex: bool = False,
    paths_exact_set: Optional[set] = None,
    full_url_set: Optional[set] = None,
    require_path_or_regex: bool = False,
) -> Tuple[bool, str]:
    """
    Apply path_exclude (block) then path_targets / path_regex / url_regex whitelist.
    When require_regex and any regex is set, URL/path must match at least one regex.
    paths_exact_set / full_url_set: O(1) bulk URL list (thousands~tens of thousands).
    """
    url_path = _url_path_only(real_url)
    if path_exclude and _path_pattern_hits(url_path, display_path, path_exclude):
        return False, f"제외 path 매칭 path={url_path} display='{(display_path or '')[:60]}'"

    # bulk full URL exact
    if full_url_set:
        full = (real_url or "").lower().split("#")[0].rstrip("/")
        bare = full.split("://", 1)[-1]
        if full in full_url_set or bare in full_url_set:
            return True, "bulk_full_url"
        # also try without www
        if bare.startswith("www."):
            if bare[4:] in full_url_set:
                return True, "bulk_full_url"

    # bulk exact path O(1)
    exact_hit = bool(paths_exact_set and url_path in paths_exact_set)

    rxs: List[re.Pattern] = list(path_regexes or [])
    if path_regex is not None:
        rxs.append(path_regex)
    has_regex = bool(rxs) or bool(url_regex)
    has_targets = bool(path_targets) or bool(paths_exact_set)

    regex_ok = True
    if has_regex:
        ok_path_rx = bool(rxs) and _path_regex_hits(url_path, display_path, real_url, rxs)
        ok_url_rx = bool(url_regex) and bool(url_regex.search(real_url or ""))
        regex_ok = ok_path_rx or ok_url_rx

    # plain path_targets (prefix/substring) — skip huge lists if exact set already covered
    target_hit = exact_hit
    if not target_hit and path_targets:
        # if path_targets is huge (>2000) only check small regex/plain via limited sample
        if len(path_targets) > 2000 and paths_exact_set is not None:
            target_hit = exact_hit
        else:
            target_hit = _path_pattern_hits(url_path, display_path, path_targets)

    if require_regex and has_regex:
        if not regex_ok and not exact_hit:
            return (
                False,
                f"정규식/주소목록 미매칭 path={url_path} url={(real_url or '')[:80]}",
            )
        return True, "regex_or_exact"

    if require_path_or_regex and (has_regex or has_targets or full_url_set):
        if not (regex_ok or target_hit or exact_hit):
            return False, f"path/URL 목록 미매칭 path={url_path}"

    if has_targets and not target_hit and not exact_hit:
        if not (has_regex and regex_ok):
            return False, f"타겟 path 아님 path={url_path} display='{(display_path or '')[:60]}'"

    if has_regex and require_regex and not regex_ok and not exact_hit:
        return False, f"정규식 미매칭 path={url_path}"

    return True, ""


async def _extract_display_path(locator: Locator) -> str:
    """
    Read the small path Google shows next to the title (organic cite / ads path).
    e.g. www.example.com › promo › sale  or  example.com/promo/sale
    """
    try:
        text = await locator.evaluate(
            """(el) => {
                const roots = [];
                let n = el;
                for (let i = 0; i < 10 && n; i++) {
                    roots.push(n);
                    n = n.parentElement;
                }
                const pick = (root) => {
                    if (!root || !root.querySelectorAll) return '';
                    // classic cite / ad display URL
                    const sels = [
                        'cite',
                        '[data-dtld]',
                        '.ylgVCe',
                        '.qLRx3b',
                        '.Zu0yb',
                        '.f',
                        'span.VuuXrf',
                        'span[role="text"]',
                    ];
                    for (const sel of sels) {
                        const nodes = root.querySelectorAll(sel);
                        for (const c of nodes) {
                            const t = (c.innerText || c.textContent || '').replace(/\\s+/g, ' ').trim();
                            if (!t || t.length < 3) continue;
                            // looks like domain/path or breadcrumb path
                            if (/[›»>]/.test(t) || /\\//.test(t) || /\\.[a-z]{2,}/i.test(t)) {
                                return t.slice(0, 220);
                            }
                        }
                    }
                    // data-dtld attribute (ads final domain)
                    const dt = root.querySelector('[data-dtld]');
                    if (dt) {
                        const v = dt.getAttribute('data-dtld') || '';
                        if (v) return v.slice(0, 220);
                    }
                    return '';
                };
                for (const r of roots) {
                    const t = pick(r);
                    if (t) return t;
                }
                return '';
            }"""
        )
        return (text or "").strip()
    except Exception:
        return ""


def extract_totp_secret(raw: str) -> str:
    """
    Extract base32 TOTP secret from common paste formats used with 2fa-auth.com:
      - plain: Z665ORJWBCNHL6L3LIFU7XAESEOVLDZK
      - pipe:  note|id|SECRET|cookies...
      - otpauth://totp/...?secret=XXXX&...
      - spaces / dashes mixed in
    """
    s = (raw or "").strip()
    if not s:
        return ""
    # otpauth URL
    if s.lower().startswith("otpauth://"):
        try:
            q = parse_qs(urlparse(s).query)
            sec = (q.get("secret") or [""])[0]
            if sec:
                return re.sub(r"[\s\-]", "", sec).upper()
        except Exception:
            pass
    # 2fa-auth.com multi-field paste: a|b|SECRET|c_user=...
    if "|" in s:
        parts = [p.strip() for p in s.split("|") if p.strip()]
        # pick longest base32-looking segment
        best = ""
        for p in parts:
            cand = re.sub(r"[\s\-]", "", p).upper()
            # strip non-base32
            cand = re.sub(r"[^A-Z2-7=]", "", cand)
            if len(cand) >= 16 and len(cand) > len(best):
                # skip obvious cookie blobs
                if "CUSER" in cand or "XS=" in p.upper():
                    continue
                best = cand
        if best:
            return best.rstrip("=")
    # plain secret
    cand = re.sub(r"[\s\-]", "", s).upper()
    cand = re.sub(r"[^A-Z2-7=]", "", cand)
    return cand.rstrip("=")


def generate_totp(secret: str, *, digits: int = 6, period: int = 30) -> Optional[str]:
    """
    RFC 6238 TOTP — same algorithm as Google Authenticator / 2fa-auth.com.
    Accepts plain base32, pipe paste from 2fa-auth.com, or otpauth:// URL.
    """
    raw = extract_totp_secret(secret)
    if not raw:
        return None
    pad = (-len(raw)) % 8
    if pad:
        raw = raw + ("=" * pad)
    try:
        key = base64.b32decode(raw, casefold=True)
    except Exception:
        return None
    if not key:
        return None
    digits = 6 if digits not in (6, 7, 8) else digits
    period = period if period > 0 else 30
    counter = int(time.time()) // int(period)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    code_int %= 10**digits
    return f"{code_int:0{digits}d}"


def totp_seconds_remaining(period: int = 30) -> int:
    period = period if period > 0 else 30
    return int(period) - (int(time.time()) % int(period))


async def generate_totp_stable(
    secret: str,
    *,
    digits: int = 6,
    period: int = 30,
    min_remaining: int = 3,
    log=print,
) -> Optional[str]:
    """
    Generate TOTP but if the code is about to roll (< min_remaining sec),
    wait for the next window so Google does not reject a near-expired code.
    """
    remain = totp_seconds_remaining(period)
    if remain < max(1, int(min_remaining)):
        wait_ms = (remain + 1) * 1000
        log(f"[2FA] 코드 만료 임박 ({remain}s) → 다음 창까지 {remain+1}s 대기")
        await _human_delay_async(wait_ms)
    code = generate_totp(secret, digits=digits, period=period)
    if code:
        log(
            f"[2FA] TOTP={code} · 남은시간≈{totp_seconds_remaining(period)}s"
        )
    return code


def parse_cookie_payload(
    raw: Any,
    *,
    default_domain: str = "",
    default_url: str = "",
) -> List[Dict[str, Any]]:
    """
    Normalize cookie paste formats into Playwright add_cookies() items.

    Supported:
      - JSON array of cookie objects (Playwright / Chrome / EditThisCookie)
      - JSON object { "cookies": [ ... ] }
      - Netscape-ish / header style lines: name=value
      - multi-line name=value; Path=/; Domain=.example.com
    Own-site QA only — inject into Octo browser context you control.
    """
    items: List[Any] = []
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        if isinstance(raw.get("cookies"), list):
            items = raw["cookies"]
        elif raw.get("name"):
            items = [raw]
        else:
            # map name -> value
            items = [{"name": k, "value": str(v)} for k, v in raw.items() if k]
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        if text[0] in "[{":
            try:
                parsed = json.loads(text)
                return parse_cookie_payload(
                    parsed, default_domain=default_domain, default_url=default_url
                )
            except json.JSONDecodeError:
                pass
        # header / line forms
        for line in text.replace(";", "\n").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.lower().startswith("set-cookie"):
                continue
            if "=" not in line:
                continue
            # skip attributes
            key = line.split("=", 1)[0].strip().lower()
            if key in ("path", "domain", "expires", "max-age", "secure", "httponly", "samesite"):
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            value = value.strip().strip('"')
            if name:
                items.append({"name": name, "value": value})
    else:
        return []

    def_domain = _normalize_domain(default_domain)
    if not def_domain and default_url:
        try:
            def_domain = _normalize_domain(urlparse(default_url).netloc)
        except Exception:
            def_domain = ""

    out: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or it.get("Name") or "").strip()
        if not name:
            continue
        value = it.get("value")
        if value is None:
            value = it.get("Value")
        value = "" if value is None else str(value)

        cookie: Dict[str, Any] = {"name": name, "value": value}

        # domain / url
        domain = str(it.get("domain") or it.get("Domain") or "").strip()
        url = str(it.get("url") or it.get("Url") or "").strip()
        if domain:
            cookie["domain"] = domain if domain.startswith(".") else domain
        elif def_domain:
            cookie["domain"] = def_domain
        elif url:
            cookie["url"] = url
        elif default_url:
            cookie["url"] = default_url
        else:
            # cannot set without domain or url
            continue

        path = str(it.get("path") or it.get("Path") or "/").strip() or "/"
        cookie["path"] = path

        if "expires" in it and it["expires"] is not None:
            try:
                cookie["expires"] = float(it["expires"])
            except (TypeError, ValueError):
                pass
        elif "expirationDate" in it and it["expirationDate"] is not None:
            try:
                cookie["expires"] = float(it["expirationDate"])
            except (TypeError, ValueError):
                pass

        secure = it.get("secure")
        if secure is None:
            secure = it.get("Secure")
        if secure is not None:
            cookie["secure"] = bool(secure)

        http_only = it.get("httpOnly")
        if http_only is None:
            http_only = it.get("HttpOnly")
        if http_only is not None:
            cookie["httpOnly"] = bool(http_only)

        same_site = it.get("sameSite") or it.get("SameSite")
        if same_site:
            ss = str(same_site)
            # Playwright: Strict | Lax | None
            low = ss.lower()
            if low in ("strict", "lax", "none"):
                cookie["sameSite"] = low.capitalize() if low != "none" else "None"
            elif ss in ("Strict", "Lax", "None"):
                cookie["sameSite"] = ss

        out.append(cookie)
    return out


def _normalize_cookie_when(value: str) -> str:
    when = (value or "after_connect").strip().lower()
    aliases = {
        "start": "after_connect",
        "connect": "after_connect",
        "cdp": "after_connect",
        "serp": "before_search",
        "pre_search": "before_search",
        "landing": "on_site",
        "site": "on_site",
        "after_click": "on_site",
        "login": "replace_login",
        "skip_google": "replace_login",
        "session": "replace_login",
    }
    return aliases.get(when, when)


async def inject_cookies(
    page: Page,
    cookies_cfg: Optional[Dict[str, Any]],
    *,
    log=print,
    phase: str = "after_connect",
) -> int:
    """
    Inject cookies into the current Octo browser context (own-site QA).

    cookies_cfg.when:
      - after_connect  : CDP 연결 직후
      - before_search  : Google 검색 직전
      - on_site        : 자사 사이트 랜딩 직후
      - replace_login  : 연결 직후 주입 + Google 로그인 단계 스킵(세션 쿠키 테스트)
    """
    cfg = dict(cookies_cfg or {})
    if not cfg.get("enabled"):
        return 0
    when = _normalize_cookie_when(str(cfg.get("when") or "after_connect"))
    # replace_login fires at after_connect
    effective_when = "after_connect" if when == "replace_login" else when
    if phase != effective_when:
        return 0

    domain = str(cfg.get("domain") or "").strip()
    url = str(cfg.get("url") or "").strip()
    raw = cfg.get("cookies")
    if raw is None or raw == "":
        raw = cfg.get("json") or cfg.get("text") or cfg.get("payload")

    cookies = parse_cookie_payload(raw, default_domain=domain, default_url=url)
    if not cookies:
        log("[쿠키] 주입할 쿠키가 없습니다 (형식 확인)")
        return 0

    context = page.context
    try:
        if cfg.get("clear_first"):
            try:
                await context.clear_cookies()
                log("[쿠키] 기존 쿠키 삭제 후 주입")
            except Exception as exc:
                log(f"[쿠키] clear 경고: {exc}")
        await context.add_cookies(cookies)
        log(
            f"[쿠키] ★ 주입 완료 {len(cookies)}개 · when={when} · "
            f"domain/url={domain or url or '-'}"
        )
        # warm navigation so site JS sees session (not for on_site — already there)
        warm = str(cfg.get("warm_url") or url or "").strip()
        if (
            warm
            and cfg.get("warm_navigate", True)
            and phase in ("after_connect", "before_search")
        ):
            try:
                await page.goto(warm, wait_until="domcontentloaded", timeout=45000)
                log(f"[쿠키] 워밍 URL 이동: {warm}")
            except Exception as exc:
                log(f"[쿠키] 워밍 이동 경고: {exc}")
        return len(cookies)
    except Exception as exc:
        log(f"[쿠키] 주입 실패: {exc}")
        return 0


class BrowserSession:
    def __init__(
        self,
        ws_endpoint: str = "",
        debug_port: Optional[str] = None,
        *,
        engine: str = "octo",
        proxy: Optional[Dict[str, Any]] = None,
        headless: bool = True,
    ):
        self.ws_endpoint = ws_endpoint or ""
        self.debug_port = str(debug_port) if debug_port else None
        self.engine = (engine or "octo").strip().lower()
        self.proxy = dict(proxy or {})
        self.headless = bool(headless)
        self._pw = None
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self._owned_browser = False  # True when we launched Chromium (not Octo CDP)

    async def connect(self) -> Page:
        self._pw = await async_playwright().start()
        if self.engine in ("playwright", "pw", "chromium", "server"):
            return await self._launch_playwright()
        return await self._connect_cdp()

    async def _connect_cdp(self) -> Page:
        endpoint = self.ws_endpoint
        try:
            self.browser = await self._pw.chromium.connect_over_cdp(endpoint)
        except Exception:
            if self.debug_port:
                self.browser = await self._pw.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{self.debug_port}"
                )
            else:
                raise
        self._owned_browser = False

        contexts = self.browser.contexts
        if contexts and contexts[0].pages:
            self.page = contexts[0].pages[0]
        elif contexts:
            self.page = await contexts[0].new_page()
        else:
            context = await self.browser.new_context()
            self.page = await context.new_page()
        return self.page

    async def _launch_playwright(self) -> Page:
        """
        Ubuntu/VPS path: launch Chromium with HTTP(S)/SOCKS proxy.
        Used when Octo Local Client cannot run (e.g. missing SSE4 CPU).
        """
        launch_kwargs: Dict[str, Any] = {
            "headless": self.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        }
        proxy_cfg: Dict[str, Any] = {}
        host = str(self.proxy.get("host") or "").strip()
        port = self.proxy.get("port")
        ptype = str(self.proxy.get("type") or "http").lower()
        if host and port:
            if ptype in ("socks", "socks5"):
                server = f"socks5://{host}:{int(port)}"
            elif ptype == "https":
                server = f"https://{host}:{int(port)}"
            else:
                server = f"http://{host}:{int(port)}"
            proxy_cfg["server"] = server
            login = str(self.proxy.get("login") or "")
            password = str(self.proxy.get("password") or "")
            if login:
                proxy_cfg["username"] = login
                proxy_cfg["password"] = password
            launch_kwargs["proxy"] = proxy_cfg

        self.browser = await self._pw.chromium.launch(**launch_kwargs)
        self._owned_browser = True
        context = await self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="ko-KR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        self.page = await context.new_page()
        return self.page

    async def live_page(self, log=print) -> Page:
        """Return a usable page; recover if the previous tab was closed (Octo)."""
        self.page = await ensure_live_page(self.page, self.browser, log)
        return self.page

    async def close(self) -> None:
        # Octo CDP: disconnect only. Playwright-launched: close browser process.
        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass


async def ensure_live_page(
    page: Optional[Page],
    browser: Optional[Browser],
    log=print,
) -> Page:
    """
    Octo 탭이 닫히거나 CDP 페이지가 무효화되면 새/기존 탭을 다시 잡습니다.
    검색 실패 'Target page has been closed' 방지 핵심.
    """
    def _ok(p: Optional[Page]) -> bool:
        if p is None:
            return False
        try:
            if p.is_closed():
                return False
            _ = p.url
            return True
        except Exception:
            return False

    if _ok(page):
        return page  # type: ignore[return-value]

    log("[브라우저] ⚠ 현재 탭이 닫혔거나 무효입니다 → 복구 시도")
    if browser is None:
        raise RuntimeError("브라우저(CDP) 연결이 없습니다. 프로필 Start 상태를 확인하세요.")

    try:
        contexts = browser.contexts or []
    except Exception as exc:
        raise RuntimeError(f"브라우저 컨텍스트 조회 실패: {exc}") from exc

    for ctx in contexts:
        try:
            for p in list(ctx.pages):
                if _ok(p):
                    try:
                        await p.bring_to_front()
                    except Exception:
                        pass
                    log(f"[브라우저] 기존 탭 재사용 url={p.url}")
                    return p
        except Exception:
            continue

    # open a fresh tab
    try:
        if contexts:
            p = await contexts[0].new_page()
        else:
            ctx = await browser.new_context()
            p = await ctx.new_page()
        log("[브라우저] 새 탭을 열어 검색/작업을 계속합니다")
        return p
    except Exception as exc:
        raise RuntimeError(f"새 탭 생성 실패: {exc}") from exc


# ---------------------------------------------------------------------------
# Google login helpers
# ---------------------------------------------------------------------------

SUCCESS_URL_HINTS = (
    "myaccount.google.com",
    "mail.google.com",
    "accounts.google.com/b/",
    "drive.google.com",
    "workspace.google.com",
    "ogs.google.com",
)

SIGNIN_URL_HINTS = (
    "servicelogin",
    "/signin/",
    "challenge/",
    "identifier",
    "accounts.google.com/v3/signin",
    "accounts.google.com/InteractiveLogin",
    "accounts.google.com/AccountChooser",
)

DISMISS_BUTTON_TEXTS = [
    "다음에",
    "나중에",
    "나중에 하기",
    "지금은 안 함",
    "아니요",
    "취소",
    "Not now",
    "Skip",
    "No thanks",
    "Cancel",
    "Remind me later",
    "Don't turn on",
    "사용 안함",
    "사용 안 함",
    "건너뛰기",
]

# Prefer authenticator app code entry over phone prompt / security key
TOTP_OPTION_TEXTS = [
    "Google Authenticator",
    "인증 앱",
    "인증 앱에서",
    "인증 앱에서 코드 받기",
    "인증번호 받기",
    "인증 코드 입력",
    "인증 코드 사용",
    "Get a verification code from the Google Authenticator app",
    "Google Authenticator app",
    "Authenticator app",
    "Authenticator",
    "인증 앱의 일회용 비밀번호",
    "일회용 비밀번호",
    "Enter a code",
    "인증 코드",
    "앱에서 코드",
    "Use your authenticator app",
    "Verification code from authenticator app",
]

TRY_ANOTHER_WAY_TEXTS = [
    "다른 방법 시도",
    "다른 방법을 시도",
    "Try another way",
    "More ways to verify",
    "다른 방법으로 인증",
    "다른 옵션",
    "다른 방법",
    "I can't use my phone",
    "전화기를 사용할 수 없음",
]


def _copy_text_clipboard(text: str) -> bool:
    """Best-effort copy 2FA code to Windows clipboard (for paste / user visibility)."""
    text = (text or "").strip()
    if not text:
        return False
    try:
        import subprocess

        # Windows: clip.exe
        p = subprocess.run(
            ["clip"],
            input=text.encode("utf-16le"),
            check=False,
            capture_output=True,
            timeout=3,
        )
        if p.returncode == 0:
            return True
    except Exception:
        pass
    try:
        import ctypes

        CF_UNICODETEXT = 13
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        user32.OpenClipboard(0)
        user32.EmptyClipboard()
        data = text.encode("utf-16le") + b"\x00\x00"
        h = kernel32.GlobalAlloc(0x0042, len(data))
        ptr = kernel32.GlobalLock(h)
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(h)
        user32.SetClipboardData(CF_UNICODETEXT, h)
        user32.CloseClipboard()
        return True
    except Exception:
        return False


async def url_suggests_google_logged_in(url: str) -> bool:
    u = (url or "").lower()
    if any(x in u for x in SIGNIN_URL_HINTS):
        if "myaccount.google.com" not in u and "mail.google.com" not in u:
            return False
    if any(x in u for x in SUCCESS_URL_HINTS):
        return True
    if "accountchooser" in u:
        return True
    return False


async def _page_text_snippet(page: Page, limit: int = 400) -> str:
    try:
        body = page.locator("body")
        if await body.count() == 0:
            return ""
        text = await body.inner_text(timeout=3000)
        return re.sub(r"\s+", " ", text or "")[:limit]
    except Exception:
        return ""


async def detect_google_login_problem(page: Page) -> str:
    """
    구글 로그인 실패 원인을 한국어로 설명 (비개발자용).
    빈 문자열이면 특별한 차단 문구 없음.
    """
    try:
        url = (page.url or "").lower()
        snippet = await _page_text_snippet(page, 900)
        s = (snippet or "").lower()
        raw = snippet or ""
    except Exception:
        return ""

    rules = [
        (
            lambda: "captcha" in s
            or "recaptcha" in s
            or "로봇이 아닙니다" in raw
            or "i'm not a robot" in s,
            "로봇 확인(캡차) 화면입니다. Octo 창에서 체크를 직접 눌러야 할 수 있습니다.",
        ),
        (
            lambda: "wrong password" in s
            or "incorrect password" in s
            or "비밀번호가 잘못" in raw
            or "잘못된 비밀번호" in raw
            or "비밀번호를 잘못" in raw,
            "비밀번호가 틀렸습니다. 계정 표의 비밀번호를 확인하세요.",
        ),
        (
            lambda: "couldn't find your google account" in s
            or "couldn’t find your google account" in s
            or "계정을 찾을 수 없" in raw
            or "등록되지 않은 이메일" in raw
            or "couldn't find your account" in s,
            "구글 계정을 찾을 수 없습니다. 이메일이 맞는지 확인하세요.",
        ),
        (
            lambda: "too many failed attempts" in s
            or "여러 번 시도" in raw
            or "나중에 다시 시도" in raw
            or "try again later" in s,
            "실패 횟수가 많아 구글이 잠시 막고 있습니다. 잠시 후 다시 시도하세요.",
        ),
        (
            lambda: "unusual activity" in s
            or "suspicious" in s
            or "비정상" in raw
            or "의심스러운" in raw
            or "confirm it's you" in s
            or "본인 확인" in raw
            or "verify it's you" in s,
            "구글 보안 추가 확인(본인 확인)이 필요합니다. 전화·기기 확인이 뜰 수 있습니다.",
        ),
        (
            lambda: "this browser or app may not be secure" in s
            or "안전하지 않을 수 있습니다" in raw
            or "브라우저가 안전하지" in raw,
            "이 브라우저는 안전하지 않다는 구글 경고입니다. Octo 프로필/핑거프린트를 바꿔 보세요.",
        ),
        (
            lambda: "phone" in s and ("verify" in s or "number" in s)
            or "전화번호" in raw
            or "문자 메시지" in raw,
            "전화/문자 인증 단계입니다. 시크릿(TOTP)만으로는 통과 못 할 수 있습니다.",
        ),
        (
            lambda: "account disabled" in s or "사용 중지" in raw or "사용 정지" in raw,
            "계정이 사용 중지된 상태입니다.",
        ),
        (
            lambda: "challenge" in url and "totp" not in url and "pwd" not in url,
            "추가 보안 확인 화면입니다. 2FA 앱 코드 또는 다른 확인이 필요할 수 있습니다.",
        ),
    ]
    for pred, msg in rules:
        try:
            if pred():
                return msg
        except Exception:
            continue
    return ""


async def is_google_logged_in(page: Page, *, probe: bool = False) -> bool:
    if await url_suggests_google_logged_in(page.url):
        u = (page.url or "").lower()
        if "accountchooser" in u:
            email = page.locator('input[type="email"], input[name="identifier"]')
            try:
                if await email.count() > 0 and await email.first.is_visible():
                    return False
            except Exception:
                pass
            return True
        return True

    if not probe:
        return False

    try:
        await page.goto(
            "https://accounts.google.com/",
            wait_until="domcontentloaded",
            timeout=45000,
        )
        await _human_delay_async(500)
        if await url_suggests_google_logged_in(page.url):
            return True
        u = (page.url or "").lower()
        if "myaccount.google.com" in u:
            return True
        email = page.locator('input[type="email"], input[name="identifier"]')
        if await email.count() > 0 and await email.first.is_visible():
            return False
        challenge = page.locator(
            'input[type="password"], input[name="Passwd"], [data-challengetype]'
        )
        if await challenge.count() == 0:
            snippet = (await _page_text_snippet(page)).lower()
            if any(
                k in snippet
                for k in ("google 계정", "google account", "내 계정", "my account")
            ):
                return True
        return False
    except Exception:
        return False


async def _first_visible(locator: Locator, timeout_ms: int = 2500) -> Optional[Locator]:
    try:
        count = await locator.count()
        for i in range(min(count, 12)):
            el = locator.nth(i)
            try:
                if await el.is_visible(timeout=400):
                    return el
            except Exception:
                continue
        first = locator.first
        await first.wait_for(state="visible", timeout=timeout_ms)
        return first
    except Exception:
        return None


async def _click_if_visible(page: Page, selector: str, *, timeout_ms: int = 1500) -> bool:
    try:
        loc = page.locator(selector)
        el = await _first_visible(loc, timeout_ms=timeout_ms)
        if el is None:
            return False
        await el.scroll_into_view_if_needed()
        await el.click(timeout=5000)
        return True
    except Exception:
        return False


async def _click_button_by_texts(
    page: Page, texts: List[str], *, timeout_ms: int = 1200
) -> Optional[str]:
    for text in texts:
        candidates = [
            page.get_by_role("button", name=re.compile(f"^{re.escape(text)}$", re.I)),
            page.get_by_role("link", name=re.compile(f"^{re.escape(text)}$", re.I)),
            page.locator(f"button:has-text('{text}')"),
            page.locator(f"div[role='button']:has-text('{text}')"),
            page.locator(f"span:has-text('{text}')"),
            page.locator(f"li:has-text('{text}')"),
            page.locator(f"div[data-challengetype]:has-text('{text}')"),
        ]
        for loc in candidates:
            try:
                el = await _first_visible(loc, timeout_ms=min(800, timeout_ms))
                if el is None:
                    continue
                await el.scroll_into_view_if_needed()
                await el.click(timeout=5000)
                return text
            except Exception:
                continue
    return None


async def _type_human(
    page: Page, locator: Locator, text: str, *, delay: int = 55, fast: bool = False
) -> None:
    """Type into a field. fast=True uses fill (훨씬 빠름, 로그인용)."""
    await locator.click(timeout=8000)
    await _human_delay_async(80 if fast else 200)
    try:
        await locator.fill("")
    except Exception:
        try:
            await locator.click(click_count=3)
            await page.keyboard.press("Backspace")
        except Exception:
            pass
    if fast:
        try:
            await locator.fill(text)
            # fire events for Google SPA
            try:
                await locator.evaluate(
                    """(node, v) => {
                        node.value = v;
                        node.dispatchEvent(new Event('input', {bubbles:true}));
                        node.dispatchEvent(new Event('change', {bubbles:true}));
                    }""",
                    text,
                )
            except Exception:
                pass
            return
        except Exception:
            pass
    await locator.type(text, delay=max(15, int(delay)))


async def _fill_email(page: Page, email: str, pause_ms: int, log) -> bool:
    email_input = page.locator(
        'input[type="email"], input[name="identifier"], #identifierId'
    )
    el = await _first_visible(email_input, timeout_ms=5000)
    if el is None:
        log("[사람] 이메일 입력칸을 화면에서 찾지 못했습니다.")
        return False
    log(f"[사람] 이메일을 자동으로 넣습니다: {email}")
    # 고속 fill (한 글자씩 치지 않음)
    await _type_human(page, el, email, delay=12, fast=True)
    await _human_delay_async(max(60, min(int(pause_ms), 200)))
    clicked = await _click_if_visible(page, "#identifierNext", timeout_ms=800)
    if not clicked:
        clicked_text = await _click_button_by_texts(
            page, ["다음", "Next", "계속", "Continue"], timeout_ms=700
        )
        clicked = bool(clicked_text)
    if not clicked:
        await page.keyboard.press("Enter")
    # 비밀번호 화면 전환 — 빠르게 폴링
    for _ in range(12):
        await _human_delay_async(150)
        try:
            problem = await detect_google_login_problem(page)
            if problem and ("계정" in problem or "이메일" in problem):
                log(f"[사람] 구글이 막았습니다: {problem}")
                return False
            pw = page.locator(
                'input[type="password"], input[name="Passwd"], input[name="password"]'
            )
            if await pw.count() > 0 and await pw.first.is_visible():
                log("[사람] 비밀번호 화면으로 넘어갔습니다.")
                break
            u = (page.url or "").lower()
            if "challenge/pwd" in u or "password" in u:
                break
        except Exception:
            break
    return True


async def _fill_password(page: Page, password: str, pause_ms: int, log) -> bool:
    pw_input = page.locator(
        'input[type="password"], input[name="Passwd"], input[name="password"]'
    )
    el = await _first_visible(pw_input, timeout_ms=7000)
    if el is None:
        log("[사람] 비밀번호 입력칸을 찾지 못했습니다.")
        return False
    log("[사람] 비밀번호를 자동으로 넣습니다.")
    await _type_human(page, el, password, delay=12, fast=True)
    await _human_delay_async(max(60, min(int(pause_ms), 200)))
    clicked = await _click_if_visible(page, "#passwordNext", timeout_ms=800)
    if not clicked:
        clicked_text = await _click_button_by_texts(
            page,
            ["다음", "Next", "로그인", "Sign in", "계속", "Continue"],
            timeout_ms=700,
        )
        clicked = bool(clicked_text)
    if not clicked:
        await page.keyboard.press("Enter")
    # 2FA/성공/오류 화면 전환
    for _ in range(14):
        await _human_delay_async(160)
        try:
            problem = await detect_google_login_problem(page)
            if problem and ("비밀번호" in problem or "로봇" in problem or "보안" in problem):
                log(f"[사람] 구글이 막았습니다: {problem}")
                return False
            if await is_google_logged_in(page, probe=False):
                break
            if await _find_2fa_code_input(page) is not None:
                log("[사람] 2단계 인증(코드) 화면이 나왔습니다.")
                break
            u = (page.url or "").lower()
            if any(x in u for x in ("challenge", "totp", "signin/v2/challenge", "myaccount")):
                break
        except Exception:
            break
    return True


async def _select_account_chooser(page: Page, email: str, log) -> bool:
    u = (page.url or "").lower()
    try:
        chooser_hint = "accountchooser" in u or await page.locator(
            "[data-identifier], li[data-email], div[data-email]"
        ).count() > 0
    except Exception:
        chooser_hint = "accountchooser" in u
    if not chooser_hint and not email:
        return False

    if email:
        try:
            tile = page.locator(f'[data-identifier="{email}"]')
            if await tile.count() == 0:
                tile = page.locator(f'[data-email="{email}"]')
            if await tile.count() == 0:
                tile = page.locator(f'div[role="link"]:has-text("{email}")')
            if await tile.count() == 0:
                tile = page.locator(f'li:has-text("{email}"), div:has-text("{email}")')
            el = await _first_visible(tile, timeout_ms=1800)
            if el is not None:
                log(f"[Google] 계정 선택: {email}")
                await el.click(timeout=5000)
                await _human_delay_async(600)
                return True
        except Exception:
            pass

    other = await _click_button_by_texts(
        page,
        ["다른 계정 사용", "다른 계정으로 로그인", "Use another account"],
        timeout_ms=1000,
    )
    if other:
        log(f"[Google] '{other}' 클릭")
        await _human_delay_async(500)
        return True
    return False


async def _dismiss_interstitials(page: Page, log, rounds: int = 4) -> None:
    for _ in range(rounds):
        skipped = await _click_button_by_texts(page, DISMISS_BUTTON_TEXTS, timeout_ms=500)
        if skipped:
            log(f"[Google] 중간 화면 닫기: {skipped}")
            await _human_delay_async(450)
            continue

        cont = await _click_button_by_texts(
            page,
            ["예", "Yes", "확인", "I understand", "이해했습니다", "계속", "Continue"],
            timeout_ms=450,
        )
        if cont:
            u = (page.url or "").lower()
            snippet = (await _page_text_snippet(page, 200)).lower()
            if any(
                k in u or k in snippet
                for k in (
                    "speedbump",
                    "saved",
                    "signinoptions",
                    "passkey",
                    "stay signed",
                    "로그인 상태",
                    "보안 설정",
                    "recovery",
                )
            ):
                log(f"[Google] 중간 확인: {cont}")
                await _human_delay_async(1000)
                continue
            await _human_delay_async(400)
            break
        break


def _looks_like_challenge(url: str, snippet: str) -> bool:
    u = (url or "").lower()
    s = (snippet or "").lower()
    keys = (
        "challenge",
        "totp",
        "phone",
        "2-step",
        "2step",
        "two-step",
        "2단계",
        "2 단계",
        "인증",
        "확인 코드",
        "verification",
        "verify it's you",
        "본인 확인",
        "recovery",
        "captcha",
        "recaptcha",
        "unusual activity",
        "suspicious",
    )
    return any(k in u or k in s for k in keys)


async def _find_2fa_code_input(page: Page) -> Optional[Locator]:
    selectors = [
        'input#totpPin',
        'input[name="totpPin"]',
        'input#idvAnyPhonePin',
        'input[name="idvAnyPhonePin"]',
        'input[name="Pin"]',
        'input[name="idvPin"]',
        'input[autocomplete="one-time-code"]',
        'input[id*="totp" i]',
        'input[id*="otp" i]',
        'input[id*="code" i]',
        'input[name*="code" i]',
        'input[aria-label*="코드" i]',
        'input[aria-label*="code" i]',
        'input[aria-label*="OTP" i]',
        'input[placeholder*="코드" i]',
        'input[placeholder*="code" i]',
        'input[type="tel"]',
        'input[inputmode="numeric"]',
        'input[maxlength="6"]',
        'input[maxlength="8"]',
    ]
    for sel in selectors:
        loc = page.locator(sel)
        el = await _first_visible(loc, timeout_ms=700)
        if el is not None:
            # skip password fields
            try:
                typ = (await el.get_attribute("type") or "").lower()
                name = (await el.get_attribute("name") or "").lower()
                if typ == "password" or name in ("passwd", "password"):
                    continue
            except Exception:
                pass
            return el
    return None


async def _click_totp_challenge_tiles(page: Page, log) -> bool:
    """Click Google challenge tiles for authenticator / TOTP (data-challengetype)."""
    # Google often uses data-challengetype: 6 / 9 / TOTP-related
    sels = [
        '[data-challengetype="6"]',
        '[data-challengetype="9"]',
        '[data-challengetype="TOTP"]',
        'div[data-action="selectchallenge"]',
        'li[data-challengetype]',
    ]
    for sel in sels:
        try:
            loc = page.locator(sel)
            n = await loc.count()
            for i in range(min(n, 8)):
                el = loc.nth(i)
                try:
                    if not await el.is_visible(timeout=400):
                        continue
                    txt = ""
                    try:
                        txt = (await el.inner_text(timeout=800) or "").lower()
                    except Exception:
                        pass
                    ctype = (await el.get_attribute("data-challengetype") or "").strip()
                    # prefer authenticator-ish
                    if ctype in ("6", "9") or any(
                        k in txt
                        for k in (
                            "authenticator",
                            "인증 앱",
                            "google authenticator",
                            "일회용",
                            "totp",
                            "앱",
                            "code",
                            "코드",
                        )
                    ):
                        await el.scroll_into_view_if_needed()
                        await el.click(timeout=5000)
                        log(f"[Google] 2FA 방식 타일 클릭 (type={ctype or '?'})")
                        await _human_delay_async(700)
                        return True
                except Exception:
                    continue
        except Exception:
            continue
    return False


async def _prepare_2fa_code_screen(page: Page, log) -> bool:
    """Try to reach an authenticator / code-entry challenge screen (full auto)."""
    if await _find_2fa_code_input(page) is not None:
        return True

    for round_i in range(4):
        if await _find_2fa_code_input(page) is not None:
            return True

        # 1) Direct TOTP option texts
        picked = await _click_button_by_texts(page, TOTP_OPTION_TEXTS, timeout_ms=700)
        if picked:
            log(f"[Google] 2차 인증 방식 선택: {picked}")
            await _human_delay_async(700)
            if await _find_2fa_code_input(page) is not None:
                return True

        # 2) Challenge tiles (phone prompt screens)
        if await _click_totp_challenge_tiles(page, log):
            if await _find_2fa_code_input(page) is not None:
                return True

        # 3) Try another way → then TOTP
        other = await _click_button_by_texts(page, TRY_ANOTHER_WAY_TEXTS, timeout_ms=700)
        if other:
            log(f"[Google] '{other}' 클릭 — 인증 앱 코드 화면으로 이동")
            await _human_delay_async(700)
            if await _click_totp_challenge_tiles(page, log):
                if await _find_2fa_code_input(page) is not None:
                    return True
            picked = await _click_button_by_texts(page, TOTP_OPTION_TEXTS, timeout_ms=1200)
            if picked:
                log(f"[Google] 2차 인증 방식 선택: {picked}")
                await _human_delay_async(700)
            if await _find_2fa_code_input(page) is not None:
                return True
            continue

        if round_i < 3:
            await _human_delay_async(450)
        else:
            break

    return await _find_2fa_code_input(page) is not None


async def _submit_2fa_code(page: Page, code: str, pause_ms: int, log) -> bool:
    """Type 6-digit code into Google TOTP field and submit — fully automatic."""
    clean = re.sub(r"\s+", "", (code or "").strip())
    if not clean:
        return False

    # clipboard copy so user can see/paste if needed
    if _copy_text_clipboard(clean):
        log(f"[2FA] 코드 클립보드 복사 완료: {clean}")

    el = await _find_2fa_code_input(page)
    if el is None:
        await _prepare_2fa_code_screen(page, log)
        el = await _find_2fa_code_input(page)
    if el is None:
        log("[Google] 2FA 입력란을 찾지 못함 — 코드 입력 실패")
        return False

    log(f"[Google] 2FA 코드 자동 입력 중… ({clean})")
    try:
        await el.scroll_into_view_if_needed()
        await el.click(timeout=8000)
        await _human_delay_async(150)
        try:
            await el.fill("")
        except Exception:
            await el.click(click_count=3)
            await page.keyboard.press("Backspace")
        # prefer fill (fast/reliable) then type for events
        try:
            await el.fill(clean)
        except Exception:
            await el.type(clean, delay=40)
        # fire input events
        try:
            await el.evaluate(
                """(node, v) => {
                    node.value = v;
                    node.dispatchEvent(new Event('input', {bubbles:true}));
                    node.dispatchEvent(new Event('change', {bubbles:true}));
                }""",
                clean,
            )
        except Exception:
            pass
    except Exception as exc:
        log(f"[Google] 코드 입력 예외, 키보드 재시도: {exc}")
        try:
            await page.keyboard.type(clean, delay=50)
        except Exception:
            return False

    await _human_delay_async(max(400, int(pause_ms)))
    clicked = await _click_if_visible(
        page,
        "#totpNext, #idvPreregisteredPhoneNext, #smsNext, #idvanyphoneNext, "
        "button[type='button']:has-text('다음'), button:has-text('Next')",
    )
    if not clicked:
        clicked_text = await _click_button_by_texts(
            page,
            ["다음", "Next", "확인", "Verify", "계속", "Continue", "제출", "Submit"],
            timeout_ms=1500,
        )
        clicked = bool(clicked_text)
    if not clicked:
        await page.keyboard.press("Enter")
    log(f"[2FA] Google에 코드 {clean} 제출 완료 — 인증 대기")
    await _human_delay_async(900)
    return True


async def _fetch_otp_from_2fa_auth_com(
    page: Page,
    *,
    secret: str = "",
    url: str = "https://2fa-auth.com/",
    wait_ms: int = 2500,
    log,
) -> Optional[str]:
    """
    Open https://2fa-auth.com/ (or same UI), optionally paste TOTP secret, scrape 6-digit code.
    Prefer local generate_totp(secret) when possible — this is a browser fallback.
    """
    url = (url or "https://2fa-auth.com/").strip() or "https://2fa-auth.com/"
    secret = (secret or "").strip()
    log(f"[Google] 2fa-auth.com 스타일 사이트 열기: {url}")
    context = page.context
    otp_page = await context.new_page()
    try:
        await otp_page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await _human_delay_async(max(1000, int(wait_ms)))

        if secret:
            # fill secret key — site accepts plain base32 or pipe paste
            fill_value = extract_totp_secret(secret) or secret
            filled = False
            for sel in (
                'input[placeholder*="2FA" i]',
                'input[placeholder*="Key" i]',
                'input[placeholder*="secret" i]',
                'input[placeholder*="Passcode" i]',
                'input[name*="secret" i]',
                'input[name*="key" i]',
                'input[type="text"]',
                "textarea",
            ):
                try:
                    loc = otp_page.locator(sel)
                    el = await _first_visible(loc, timeout_ms=1200)
                    if el is None:
                        continue
                    await el.click(timeout=2000)
                    await el.fill("")
                    await el.type(fill_value, delay=15)
                    filled = True
                    log("[Google] 2FA 시크릿 키 입력 완료 (2fa-auth.com)")
                    break
                except Exception:
                    continue
            if filled:
                # click Get Now / Generate
                clicked = await _click_button_by_texts(
                    otp_page,
                    [
                        "Get Now",
                        "Generate",
                        "Get Code",
                        "생성",
                        "코드 받기",
                        "Generate Verification Code",
                    ],
                    timeout_ms=2000,
                )
                if not clicked:
                    await _click_if_visible(
                        otp_page,
                        'button:has-text("Get"), button:has-text("Generate"), '
                        'input[type="submit"], button[type="submit"]',
                    )
                await _human_delay_async(1500)

        text_blob = ""
        # site shows code in result area
        for sel in (
            ".code",
            "#code",
            "[class*='code' i]",
            "[id*='code' i]",
            "h1",
            "h2",
            "h3",
            ".result",
            "#result",
        ):
            try:
                loc = otp_page.locator(sel)
                el = await _first_visible(loc, timeout_ms=800)
                if el is None:
                    continue
                t = (await el.inner_text(timeout=1500) or "").strip()
                if re.search(r"\d{6}", t):
                    text_blob = t
                    break
            except Exception:
                continue
        if not text_blob:
            try:
                text_blob = await otp_page.locator("body").inner_text(timeout=5000)
            except Exception:
                text_blob = ""

        text_blob = re.sub(r"\s+", " ", text_blob or "")
        m = re.search(r"(?<!\d)(\d{6})(?!\d)", text_blob)
        if not m:
            log("[Google] 2fa-auth.com 에서 6자리 코드를 찾지 못함")
            return None
        code = m.group(1)
        log(f"[Google] 2fa-auth.com 코드 수신 ({len(code)}자리)")
        return code
    except Exception as exc:
        log(f"[Google] 2fa-auth.com 오류: {exc}")
        return None
    finally:
        try:
            await otp_page.close()
        except Exception:
            pass
        try:
            await page.bring_to_front()
        except Exception:
            pass


async def _fetch_otp_from_third_party(
    page: Page,
    *,
    url: str,
    selector: str = "",
    regex: str = r"\b(\d{6})\b",
    wait_ms: int = 2500,
    log,
    secret: str = "",
) -> Optional[str]:
    """
    Open user's 2FA code page (third-party / self-hosted TOTP viewer) in a new tab,
    scrape a 6-digit code, close tab, return to Google page.
    2fa-auth.com uses a dedicated flow when secret is provided.
    """
    url = (url or "").strip()
    if not url:
        return None
    low = url.lower()
    if "2fa-auth.com" in low or "2faauth" in low.replace("-", ""):
        return await _fetch_otp_from_2fa_auth_com(
            page, secret=secret, url=url, wait_ms=wait_ms, log=log
        )

    log(f"[Google] 2FA 코드 사이트 열기: {url}")
    context = page.context
    otp_page = await context.new_page()
    try:
        await otp_page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await _human_delay_async(max(800, int(wait_ms)))
        # optional reload for rotating codes
        try:
            await otp_page.reload(wait_until="domcontentloaded", timeout=30000)
            await _human_delay_async(800)
        except Exception:
            pass

        text_blob = ""
        if selector:
            try:
                loc = otp_page.locator(selector)
                el = await _first_visible(loc, timeout_ms=5000)
                if el is not None:
                    text_blob = (await el.inner_text(timeout=3000) or "").strip()
                    if not text_blob:
                        text_blob = (await el.get_attribute("value") or "").strip()
            except Exception as exc:
                log(f"[Google] 2FA selector 실패: {exc}")
        if not text_blob:
            try:
                text_blob = await otp_page.locator("body").inner_text(timeout=5000)
            except Exception:
                try:
                    text_blob = await otp_page.content()
                except Exception:
                    text_blob = ""

        text_blob = re.sub(r"\s+", " ", text_blob or "")
        pattern = regex or r"\b(\d{6})\b"
        try:
            m = re.search(pattern, text_blob)
        except re.error:
            m = re.search(r"\b(\d{6})\b", text_blob)
        if not m:
            # also try 6-8 digit codes without word boundaries
            m = re.search(r"(?<!\d)(\d{6,8})(?!\d)", text_blob)
        if not m:
            log("[Google] 2FA 사이트에서 숫자 코드를 찾지 못함")
            return None
        code = m.group(1) if m.lastindex else m.group(0)
        code = re.sub(r"\s+", "", code)
        log(f"[Google] 2FA 사이트에서 코드 수신 ({len(code)}자리)")
        return code
    except Exception as exc:
        log(f"[Google] 2FA 사이트 오류: {exc}")
        return None
    finally:
        try:
            await otp_page.close()
        except Exception:
            pass
        try:
            await page.bring_to_front()
        except Exception:
            pass


async def _handle_2fa(
    page: Page,
    *,
    email: str,
    ask_2fa: Optional[Ask2FAFn],
    pause_ms: int,
    manual_wait_sec: int,
    log,
    otp_url: str = "",
    otp_selector: str = "",
    otp_regex: str = r"\b(\d{6})\b",
    otp_wait_ms: int = 2500,
    otp_secret: str = "",
) -> str:
    """
    Full-auto 2FA when secret is set:
      1) navigate to authenticator code field
      2) generate TOTP (2fa-auth.com algorithm)
      3) copy to clipboard + type into Google + submit
      4) retry on new 30s window if rejected
    Popup ONLY if no secret / all auto paths fail.
    """
    log("[Google] 2단계 인증 화면 감지 — ★ 자동 인증 시작 (시크릿→코드→입력→제출)")
    ready = await _prepare_2fa_code_screen(page, log)
    if not ready:
        # keep trying a bit — Google UI is slow sometimes
        for _ in range(4):
            await _human_delay_async(1200)
            if await _prepare_2fa_code_screen(page, log):
                ready = True
                break
            if await is_google_logged_in(page, probe=False):
                return "success"
    if not ready:
        log("[Google] 코드 입력란을 찾지 못함 (전화 알림·보안키 화면일 수 있음) — 재시도 중")
        # still try generate + later once field appears

    account_hint = email or "Google 계정"
    prompt = (
        f"【2차 인증 코드 입력 — 자동 실패 시에만】\n\n"
        f"계정: {account_hint}\n\n"
        f"시크릿 자동 입력이 안 됐습니다.\n"
        f"6자리 코드를 붙여넣고 [확인] 하세요."
    )

    # secret 있으면 사이트 탭 열지 않음 (로컬 TOTP만) — 더 빠르고 안정적
    use_site_fallback = bool(otp_url) and not otp_secret
    if otp_secret and not otp_url:
        otp_url = ""  # local only

    max_attempts = 8 if otp_secret else 4
    last_code = ""
    for attempt in range(1, max_attempts + 1):
        if await is_google_logged_in(page, probe=False):
            log("[2FA] 이미 로그인된 상태")
            return "success"
        if await url_suggests_google_logged_in(page.url):
            log("[2FA] 로그인 URL 확인 — 성공")
            return "success"

        if await _find_2fa_code_input(page) is None:
            await _prepare_2fa_code_screen(page, log)

        code: Optional[str] = None

        # 1) Local TOTP — primary full-auto path (RFC6238, Authenticator 호환)
        if otp_secret:
            if last_code and attempt > 1:
                wait_s = totp_seconds_remaining(30) + 1
                log(f"[2FA] 재시도 — 새 코드 창까지 {wait_s}초")
                await _human_delay_async(wait_s * 1000)
            code = await generate_totp_stable(
                otp_secret, min_remaining=3, log=log
            )
            if code:
                if code == last_code:
                    wait_s = totp_seconds_remaining(30) + 1
                    log(f"[2FA] 동일 코드 재생성 방지 — {wait_s}s 대기")
                    await _human_delay_async(wait_s * 1000)
                    code = await generate_totp_stable(
                        otp_secret, min_remaining=5, log=log
                    )
                if code:
                    log(
                        f"[2FA] 자동 코드 생성: {code}  "
                        f"(시도 {attempt}/{max_attempts} · 시크릿 OK)"
                    )
                    last_code = code
            else:
                log("[Google] 2FA 시크릿 디코드 실패 — base32 시크릿 확인")

        # 2) Site fallback only without secret
        if not code and use_site_fallback and otp_url:
            log(f"[Google] 2FA 사이트 조회 시도 {attempt}/{max_attempts}")
            code = await _fetch_otp_from_third_party(
                page,
                url=otp_url,
                selector=otp_selector,
                regex=otp_regex,
                wait_ms=otp_wait_ms,
                log=log,
                secret=otp_secret,
            )
            if code:
                log(f"[2FA] 사이트에서 코드 수신: {code}")

        # 3) Popup — only if no secret or auto exhausted last attempts
        if not code and ask_2fa is not None and (not otp_secret or attempt >= max_attempts - 1):
            log(f"[Google] ★ 2FA 수동 입력 팝업 (자동 실패 · 시도 {attempt})")
            try:
                code = await asyncio.to_thread(ask_2fa, prompt)
                if code:
                    log(f"[2FA] 수동 입력 코드: {code}")
            except Exception as exc:
                log(f"[Google] 코드 요청 오류: {exc}")
                code = None

        if not code:
            if attempt < max_attempts and otp_secret:
                wait_s = min(8, 30 - (int(time.time()) % 30) + 1)
                log(f"[2FA] 코드 없음 — {wait_s}초 후 재시도")
                await _human_delay_async(wait_s * 1000)
                continue
            log("[Google] 2차 인증 코드를 받지 못함")
            return "cancelled" if ask_2fa and not otp_secret else "challenge"

        last_code = code
        ok = await _submit_2fa_code(page, code, pause_ms, log)
        if not ok:
            log("[Google] 코드 입력란 자동 입력 실패 — 화면 재준비")
            await _prepare_2fa_code_screen(page, log)
            await _human_delay_async(1000)
            continue

        # wait for navigation / success
        for _ in range(6):
            await _dismiss_interstitials(page, log, rounds=2)
            if await is_google_logged_in(page, probe=False):
                log(f"[2FA] 인증 성공 · 사용 코드 {code}")
                log("[Google] 2차 인증 통과 — 로그인 성공")
                return "success"
            if await url_suggests_google_logged_in(page.url):
                log(f"[2FA] 인증 성공 · 사용 코드 {code}")
                log("[Google] 2차 인증 통과 — 로그인 성공")
                return "success"
            await _human_delay_async(800)

        snippet = await _page_text_snippet(page)
        still_2fa = await _find_2fa_code_input(page) is not None or _looks_like_challenge(
            page.url, snippet
        )
        if still_2fa:
            log("[Google] 코드 거절 또는 추가 확인 — 다음 코드로 자동 재시도")
            wait_s = 30 - (int(time.time()) % 30) + 1
            wait_s = max(2, min(wait_s, 15))
            await _human_delay_async(wait_s * 1000)
            continue
        # no code field and not logged in — maybe interstitial
        await _dismiss_interstitials(page, log, rounds=4)
        if await is_google_logged_in(page, probe=True):
            log(f"[2FA] 인증 성공 · 사용 코드 {code}")
            return "success"
        break

    deadline = time.time() + min(45, max(15, manual_wait_sec // 8))
    while time.time() < deadline:
        if await is_google_logged_in(page, probe=False):
            log("[2FA] 대기 중 로그인 성공 확인")
            return "success"
        # keep auto-filling if secret present and field reappears
        if otp_secret and await _find_2fa_code_input(page) is not None:
            c = generate_totp(otp_secret)
            if c:
                log(f"[2FA] 대기 중 재입력: {c}")
                await _submit_2fa_code(page, c, pause_ms, log)
        await _human_delay_async(1500)
    return "challenge"


def _otp_kwargs(otp: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    otp = otp or {}
    secret = str(
        otp.get("secret")
        or otp.get("otp_secret")
        or otp.get("totp_secret")
        or otp.get("twofa_secret")
        or ""
    ).strip()
    # normalize 2fa-auth.com paste / otpauth into pure secret for local TOTP
    if secret:
        secret = extract_totp_secret(secret) or secret
    url = str(otp.get("url") or otp.get("otp_url") or "").strip()
    # secret 있으면 실패 시 2fa-auth.com 브라우저 폴백 기본 사용
    if secret and not url:
        url = "https://2fa-auth.com/"
    if not url and bool(otp.get("enabled", True)):
        # 시크릿 없어도 URL만으로 사이트 스크래핑 시도 가능하게 기본값
        pass
    return {
        "otp_url": url,
        "otp_selector": str(otp.get("selector") or otp.get("otp_selector") or "").strip(),
        "otp_regex": str(otp.get("regex") or otp.get("otp_regex") or r"\b(\d{6})\b"),
        "otp_wait_ms": int(otp.get("wait_ms") or otp.get("otp_wait_ms") or 2500),
        "otp_secret": secret,
    }


async def _auto_login_flow(
    page: Page,
    *,
    email: str,
    password: str,
    pause_ms: int,
    log,
    ask_2fa: Optional[Ask2FAFn] = None,
    manual_wait_sec: int = 300,
    otp: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Returns: success | challenge | need_manual | failed | cancelled
    속도: fill 입력 + 짧은 폴링 (불필요한 1~2초 sleep 제거)
    """
    otp_kw = _otp_kwargs(otp)
    pause_ms = max(100, min(int(pause_ms or 350), 600))
    await _select_account_chooser(page, email, log)

    email_field = page.locator(
        'input[type="email"], input[name="identifier"], #identifierId'
    )
    if await email_field.count() > 0:
        try:
            if await email_field.first.is_visible():
                if not email:
                    log("[Google] 이메일이 비어 있음 — 자동 로그인 불가")
                    return "need_manual"
                ok = await _fill_email(page, email, pause_ms, log)
                if not ok:
                    return "need_manual"
        except Exception as exc:
            log(f"[Google] 이메일 단계 경고: {exc}")

    await _dismiss_interstitials(page, log, rounds=1)

    if password:
        pw_field = page.locator(
            'input[type="password"], input[name="Passwd"], input[name="password"]'
        )
        try:
            visible_pw = False
            if await pw_field.count() > 0:
                try:
                    visible_pw = await pw_field.first.is_visible()
                except Exception:
                    visible_pw = False
            # 비밀번호 필드 짧은 폴링 (최대 ~2초)
            if not visible_pw:
                for _ in range(8):
                    await _human_delay_async(250)
                    try:
                        if await pw_field.count() > 0 and await pw_field.first.is_visible():
                            visible_pw = True
                            break
                        if "challenge/pwd" in (page.url or "").lower():
                            visible_pw = True
                            break
                    except Exception:
                        break
            if visible_pw or "challenge/pwd" in (page.url or "").lower():
                ok = await _fill_password(page, password, pause_ms, log)
                if not ok:
                    log("[Google] 비밀번호 필드를 찾지 못함")
        except Exception as exc:
            log(f"[Google] 비밀번호 단계 경고: {exc}")
    else:
        log("[Google] 비밀번호 없음 — 이메일까지만 자동 입력")

    await _dismiss_interstitials(page, log, rounds=2)

    if await is_google_logged_in(page, probe=False):
        return "success"
    if await url_suggests_google_logged_in(page.url):
        return "success"

    # 비밀번호 직후 2FA 화면이 늦게 뜨는 경우 대비 폴링 (짧게)
    has_secret = bool(otp_kw.get("otp_secret"))
    for wait_i in range(6 if has_secret else 3):
        if await is_google_logged_in(page, probe=False):
            return "success"
        snippet = await _page_text_snippet(page)
        has_code = await _find_2fa_code_input(page) is not None
        challenge = _looks_like_challenge(page.url, snippet)
        # 시크릿 있으면 전화 알림 화면도 prepare 로 앱 코드 화면으로 전환
        if has_code or challenge or (has_secret and wait_i >= 0):
            if has_code or challenge or has_secret:
                log("[Google] 비밀번호 이후 2FA/추가확인 — 자동 2FA 진행")
                return await _handle_2fa(
                    page,
                    email=email,
                    ask_2fa=None if has_secret else ask_2fa,  # 시크릿 있으면 팝업 없음
                    pause_ms=pause_ms,
                    manual_wait_sec=manual_wait_sec,
                    log=log,
                    **otp_kw,
                )
        await _human_delay_async(400)

    try:
        pw = page.locator('input[type="password"], input[name="Passwd"]')
        if await pw.count() > 0 and await pw.first.is_visible():
            if password:
                await _fill_password(page, password, pause_ms, log)
                await _dismiss_interstitials(page, log, rounds=2)
                if await is_google_logged_in(page, probe=False):
                    return "success"
                if has_secret or await _find_2fa_code_input(page) is not None:
                    return await _handle_2fa(
                        page,
                        email=email,
                        ask_2fa=None if has_secret else ask_2fa,
                        pause_ms=pause_ms,
                        manual_wait_sec=manual_wait_sec,
                        log=log,
                        **otp_kw,
                    )
            return "need_manual"
    except Exception:
        pass

    # last chance: secret present → force 2FA handler
    if has_secret:
        log("[Google] 시크릿 있음 — 2FA 강제 자동 처리 시도")
        return await _handle_2fa(
            page,
            email=email,
            ask_2fa=None,
            pause_ms=pause_ms,
            manual_wait_sec=manual_wait_sec,
            log=log,
            **otp_kw,
        )

    if await is_google_logged_in(page, probe=True):
        return "success"
    return "need_manual"


async def wait_login_success(
    page: Page,
    *,
    success_url_contains: List[str],
    manual_wait_sec: int,
    log,
    poll_ms: int = 900,
    ask_2fa: Optional[Ask2FAFn] = None,
    email: str = "",
    pause_ms: int = 350,
    otp: Optional[Dict[str, Any]] = None,
) -> bool:
    otp_kw = _otp_kwargs(otp)
    deadline = time.time() + max(30, int(manual_wait_sec))
    while time.time() < deadline:
        url = (page.url or "").lower()
        if any(token in url for token in success_url_contains):
            log("[Google] 로그인 성공으로 판단했습니다.")
            return True
        try:
            if await is_google_logged_in(page, probe=False):
                log("[Google] 로그인 성공으로 판단했습니다.")
                return True
        except Exception:
            pass

        try:
            has_code = await _find_2fa_code_input(page) is not None
            if has_code and (ask_2fa or otp_kw.get("otp_url") or otp_kw.get("otp_secret")):
                status = await _handle_2fa(
                    page,
                    email=email,
                    ask_2fa=ask_2fa,
                    pause_ms=pause_ms,
                    manual_wait_sec=manual_wait_sec,
                    log=log,
                    **otp_kw,
                )
                if status == "success":
                    return True
                if status == "cancelled":
                    return False
        except Exception:
            pass

        try:
            await _dismiss_interstitials(page, log, rounds=1)
        except Exception:
            pass
        await _human_delay_async(poll_ms)

    log("[Google] 제한 시간 내 로그인 확인 실패")
    return False


def _report_google_fail(log, reason: str) -> None:
    """UI 팝업용 마커 + 자연어 로그."""
    reason = (reason or "알 수 없는 이유로 구글 로그인에 실패했습니다.").strip()
    log(f"[사람] 구글 로그인 실패 이유: {reason}")
    log(f"[GOOGLE_FAIL] {reason}")


async def google_login(
    page: Page,
    *,
    mode: str,
    email: str = "",
    password: str = "",
    login_url: str = "https://accounts.google.com/",
    success_url_contains: Optional[List[str]] = None,
    manual_wait_sec: int = 300,
    autofill_pause_ms: int = 800,
    ask_2fa: Optional[Ask2FAFn] = None,
    otp: Optional[Dict[str, Any]] = None,
    log=print,
) -> bool:
    """
    Google 로그인 (고속 자동 입력).

    mode:
      - auto     : 아이디/비번 자동 + 2FA 시크릿 자동
      - autofill : auto 별칭
      - manual   : 브라우저 직접
      - skip     : 생략
    """
    success_url_contains = list(success_url_contains or SUCCESS_URL_HINTS)
    mode = (mode or "auto").strip().lower()
    if mode == "autofill":
        mode = "auto"
    otp = otp or {}
    has_secret = bool(
        otp.get("secret") or otp.get("otp_secret") or otp.get("totp_secret")
    )
    if has_secret:
        log("[사람] 2FA 시크릿이 있어 인증 코드까지 자동으로 넣습니다.")
        ask_2fa = None
    if otp.get("url") or otp.get("otp_url"):
        log(f"[Google] 2FA 보조 URL: {otp.get('url') or otp.get('otp_url')}")

    # 고속: pause 짧게
    autofill_pause_ms = max(50, min(int(autofill_pause_ms or 200), 280))

    if not (email or "").strip() and mode == "auto":
        _report_google_fail(
            log,
            "이메일/비밀번호가 비어 있습니다. 홈 계정 표에 email|비밀번호|2FA시크릿을 넣고 저장하세요.",
        )
        return False

    log(f"[사람] 구글 로그인 페이지를 엽니다… ({email or '계정 미입력'})")
    try:
        await page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
    except Exception as exc:
        _report_google_fail(log, f"로그인 페이지를 열지 못했습니다: {exc}")
        return False
    await _human_delay_async(180)

    if await is_google_logged_in(page, probe=False):
        log("[사람] 이미 구글에 로그인된 상태입니다. 바로 진행합니다.")
        return True

    # 로그인 폼 보이면 probe(goto 추가) 생략 — 속도
    try:
        email_box = page.locator(
            'input[type="email"], input[name="identifier"], #identifierId'
        )
        need_cred = await email_box.count() > 0 and await email_box.first.is_visible()
        if not need_cred:
            pw_box = page.locator(
                'input[type="password"], input[name="Passwd"], input[name="password"]'
            )
            need_cred = await pw_box.count() > 0 and await pw_box.first.is_visible()
        if not need_cred and await is_google_logged_in(page, probe=False):
            log("[사람] 이미 구글에 로그인된 상태입니다.")
            return True
    except Exception:
        pass

    if mode == "skip":
        log("[사람] 구글 로그인 단계를 건너뜁니다.")
        return False

    if mode == "auto":
        if not email and not password:
            _report_google_fail(
                log,
                "자동 로그인인데 계정 정보가 없습니다. email|비밀번호|2FA 를 입력하세요.",
            )
            mode = "manual"
        else:
            log(
                f"[사람] 자동 로그인 시작 — "
                f"{'이메일·' if email else ''}"
                f"{'비밀번호·' if password else ''}"
                f"{'2FA자동' if has_secret else '2FA없음(막힐 수 있음)'}"
                " · 빠르게 입력합니다"
            )
            try:
                status = await _auto_login_flow(
                    page,
                    email=email,
                    password=password,
                    pause_ms=autofill_pause_ms,
                    log=log,
                    ask_2fa=ask_2fa,
                    manual_wait_sec=min(int(manual_wait_sec or 120), 180),
                    otp=otp,
                )
            except Exception as exc:
                _report_google_fail(log, f"자동 로그인 중 오류: {exc}")
                status = "need_manual"

            if status == "success":
                log("[사람] 구글 자동 로그인에 성공했습니다.")
                return True
            if status == "cancelled":
                _report_google_fail(log, "2단계 인증이 취소되어 로그인을 멈췄습니다.")
                return False

            # 구글 화면 문구로 원인 설명
            problem = await detect_google_login_problem(page)
            if problem:
                _report_google_fail(log, problem)
            elif status == "challenge":
                _report_google_fail(
                    log,
                    "2단계 인증(코드)을 통과하지 못했습니다. "
                    "2FA 시크릿이 맞는지, 또는 전화 인증인지 확인하세요.",
                )
            elif status == "failed":
                _report_google_fail(log, "이메일/비밀번호 입력이 거절되었습니다.")
            else:
                _report_google_fail(
                    log,
                    "자동 입력이 끝나지 않았습니다. Octo 창 화면을 확인하세요. "
                    f"(현재 주소: {(page.url or '')[:80]})",
                )

            # 비번/계정 오류면 길게 기다리지 않음
            hard = bool(
                problem
                and any(
                    k in problem
                    for k in ("비밀번호", "계정을 찾을", "비어", "사용 중지", "실패 횟수")
                )
            )
            if hard:
                return False
            # 캡차/본인확인 등만 짧게 수동 기회
            wait_sec = 45 if ("캡차" in (problem or "") or "본인" in (problem or "")) else 25
            if has_secret:
                wait_sec = max(wait_sec, 50)
            ok = await wait_login_success(
                page,
                success_url_contains=success_url_contains,
                manual_wait_sec=wait_sec,
                log=log,
                poll_ms=600,
                ask_2fa=ask_2fa,
                email=email,
                pause_ms=autofill_pause_ms,
                otp=otp,
            )
            if not ok:
                problem2 = await detect_google_login_problem(page)
                if problem2:
                    _report_google_fail(log, problem2)
                else:
                    _report_google_fail(
                        log,
                        "제한 시간 안에 구글 로그인을 끝내지 못했습니다. "
                        "Octo 창을 직접 확인해 주세요.",
                    )
            return ok

    log(
        f"[사람] 수동 로그인: Octo 창에서 직접 로그인해 주세요. 최대 {min(90, manual_wait_sec)}초"
    )
    ok = await wait_login_success(
        page,
        success_url_contains=success_url_contains,
        manual_wait_sec=min(90, max(30, int(manual_wait_sec or 60))),
        log=log,
        poll_ms=700,
        ask_2fa=ask_2fa,
        email=email,
        pause_ms=autofill_pause_ms,
        otp=otp,
    )
    if not ok:
        problem = await detect_google_login_problem(page)
        _report_google_fail(
            log,
            problem
            or "수동 로그인 시간 초과. 이메일·비밀번호·2FA를 확인하세요.",
        )
    return ok


# ---------------------------------------------------------------------------
# Site targets / clicks (non-dev friendly: click by visible text)
# ---------------------------------------------------------------------------

async def _click_by_visible_text(page: Page, text: str) -> bool:
    """Click button/link/element that shows the given text. Non-dev friendly."""
    text = text.strip()
    if not text:
        return False
    pattern = re.compile(re.escape(text), re.I)
    candidates = [
        page.get_by_role("button", name=pattern),
        page.get_by_role("link", name=pattern),
        page.get_by_role("tab", name=pattern),
        page.get_by_text(text, exact=True),
        page.get_by_text(text, exact=False),
        page.locator(f"button:has-text('{text}')"),
        page.locator(f"a:has-text('{text}')"),
        page.locator(f"[role='button']:has-text('{text}')"),
    ]
    for loc in candidates:
        try:
            el = await _first_visible(loc, timeout_ms=1500)
            if el is None:
                continue
            await el.scroll_into_view_if_needed()
            await _human_delay_async(250)
            await el.click(timeout=15000)
            return True
        except Exception:
            continue
    return False


def _compile_regex(pattern: str, *, flags: int = re.I) -> Optional[re.Pattern]:
    p = (pattern or "").strip()
    if not p:
        return None
    try:
        return re.compile(p, flags)
    except re.error:
        return None


async def _human_scroll(
    page: Page,
    *,
    steps: int = 3,
    allow_up: bool = True,
    up_chance: float = 0.22,
) -> None:
    """
    사람처럼 스크롤: 거리·간격·위아래가 매번 다름 (기계 패턴 방지).
    """
    n = max(1, steps + random.randint(-1, 2))
    for i in range(n):
        # 가끔 짧은 멈춤(읽는 척) 후 스크롤
        if random.random() < 0.28:
            await _rand_delay(400, 1600)
        go_up = allow_up and random.random() < up_chance and i > 0
        # 스크롤 거리를 넓게 분산
        if random.random() < 0.15:
            magnitude = random.randint(40, 120)  # 살짝만
        elif random.random() < 0.2:
            magnitude = random.randint(500, 900)  # 확 내림
        else:
            magnitude = random.randint(160, 520)
        delta = -magnitude if go_up else magnitude
        try:
            dx = random.randint(-35, 35)
            await page.mouse.wheel(dx, delta)
        except Exception:
            try:
                await page.evaluate(
                    f"window.scrollBy({random.randint(-15,15)}, {delta})"
                )
            except Exception:
                pass
        # 스크롤 사이 쉬는 시간도 불규칙
        lo, hi = (350, 1400) if not go_up else (250, 900)
        if random.random() < 0.12:
            hi += 1200  # 가끔 오래 멈춤
        await _rand_delay(lo, hi)
        if random.random() < 0.18:
            await _mouse_wander(page, moves=1)


async def _human_move_near(page: Page, locator: Locator) -> None:
    try:
        box = await locator.bounding_box()
        if not box:
            return
        x = box["x"] + box["width"] * random.uniform(0.25, 0.75)
        y = box["y"] + box["height"] * random.uniform(0.25, 0.75)
        await page.mouse.move(x, y, steps=random.randint(8, 18))
        await _rand_delay(80, 220)
    except Exception:
        pass


async def _mouse_wander(page: Page, *, moves: int = 3) -> None:
    try:
        vp = page.viewport_size or {"width": 1280, "height": 800}
        w, h = int(vp.get("width") or 1280), int(vp.get("height") or 800)
        for _ in range(max(1, moves)):
            x = random.randint(int(w * 0.1), int(w * 0.9))
            y = random.randint(int(h * 0.15), int(h * 0.85))
            await page.mouse.move(x, y, steps=random.randint(6, 16))
            await _rand_delay(120, 420)
    except Exception:
        pass


async def _human_idle(page: Page, ms_min: int, ms_max: int) -> None:
    """Idle like reading: micro mouse moves + short waits."""
    total = random.randint(max(0, ms_min), max(ms_min, ms_max))
    spent = 0
    while spent < total:
        chunk = random.randint(400, 1200)
        if random.random() < 0.45:
            await _mouse_wander(page, moves=1)
        await _human_delay_async(min(chunk, total - spent))
        spent += chunk


async def _detect_exit_ip(page: Page, log) -> str:
    """
    Public IP check in a NEW tab so the main Google tab is not broken.
    """
    endpoints = (
        "https://api.ipify.org?format=json",
        "https://ifconfig.me/ip",
    )
    tab = None
    try:
        try:
            tab = await page.context.new_page()
            log("[프록시] 출구 IP 확인용 새 탭 열기")
        except Exception as exc:
            log(f"[프록시] 새 탭 실패 → 현재 탭으로 IP 확인: {exc}")
            tab = page

        for url in endpoints:
            try:
                log(f"[프록시] IP 조회 중… {url}")
                await tab.goto(url, wait_until="domcontentloaded", timeout=25000)
                await _rand_delay(400, 800)
                body = (await tab.locator("body").inner_text(timeout=5000) or "").strip()
                if "ip" in body.lower() and "{" in body:
                    import json as _json

                    data = _json.loads(body)
                    ip = str(data.get("ip") or "").strip()
                else:
                    ip = body.split()[0] if body else ""
                if ip and 3 <= len(ip) <= 45 and any(ch.isdigit() for ch in ip):
                    log(f"[프록시] ★ 브라우저 출구 IP = {ip}  (via {urlparse(url).netloc})")
                    return ip
                log(f"[프록시] IP 파싱 실패 body[:80]={body[:80]!r}")
            except Exception as exc:
                log(f"[프록시] IP 확인 실패 ({urlparse(url).netloc}): {exc}")
        return ""
    finally:
        if tab is not None and tab is not page:
            try:
                await tab.close()
                log("[프록시] IP 확인 탭 닫음 — 본 작업 탭 유지")
            except Exception:
                pass
        try:
            await page.bring_to_front()
        except Exception:
            pass


async def _warmup_via_proxy(page: Page, browser: Optional[Browser], log) -> Page:
    """Open Google home; always return a live page for next search step."""
    log("[흐름] 워밍업: Google 홈 (검색 전 준비)")
    page = await ensure_live_page(page, browser, log)
    try:
        await page.goto(
            "https://www.google.com/webhp?hl=ko",
            wait_until="domcontentloaded",
            timeout=90000,
        )
        log(f"[흐름] Google 홈 도착 url={page.url}")
        await _rand_delay(600, 1200)
        await _dismiss_google_consent(page, log)
        log("[흐름] 워밍업 완료 — 검색 준비")
    except Exception as exc:
        log(f"[흐름] 워밍업 경고: {exc} → 탭 복구 후 계속")
        page = await ensure_live_page(page, browser, log)
    return page


async def _perform_clicks(
    page: Page,
    clicks: List[Dict[str, Any]],
    *,
    log,
    label_prefix: str = "클릭",
) -> int:
    """Run a click chain. Returns number of successful clicks."""
    ok_count = 0
    for j, click in enumerate(clicks, start=1):
        selector = str(click.get("selector") or "").strip()
        text_contains = str(
            click.get("text_contains") or click.get("button_text") or ""
        ).strip()
        optional = bool(click.get("optional", True))
        wait_after = int(click.get("wait_after_ms") or 1000)
        try:
            clicked = False
            label = text_contains or selector or "(빈 클릭)"

            before = page.url
            log(f"[{label_prefix} {j}] 시도: '{label}'  (현재 url={before})")

            if text_contains:
                clicked = await _click_by_visible_text(page, text_contains)
                if clicked:
                    log(f"[{label_prefix} {j}] CLICK OK 글자='{text_contains}'")

            if not clicked and selector:
                locator = page.locator(selector)
                if text_contains:
                    locator = locator.filter(has_text=text_contains)
                count = await locator.count()
                if count == 0:
                    msg = f"[{label_prefix} {j}] 요소 없음: {label}"
                    if optional:
                        log(msg + " (없어도 계속)")
                        continue
                    raise RuntimeError(msg)
                el = locator.first
                await el.scroll_into_view_if_needed()
                await _human_move_near(page, el)
                await _rand_delay(200, 500)
                await el.click(timeout=15000)
                clicked = True
                log(f"[{label_prefix} {j}] CLICK OK 선택자={selector}")

            if not clicked:
                msg = f"[{label_prefix} {j}] 실패: '{label}' 을(를) 찾지 못함"
                if optional:
                    log(msg + " (없어도 계속)")
                    continue
                raise RuntimeError(msg)

            ok_count += 1
            await _human_delay_async(wait_after)
            after = page.url
            if after != before:
                log(f"[{label_prefix} {j}] 이동: {before}  →  {after}")
            else:
                log(f"[{label_prefix} {j}] 클릭 후 동일 URL (페이지 내 동작)  url={after}")
        except Exception as exc:
            if optional:
                log(f"[{label_prefix} {j}] 실패(건너뜀): {exc}")
                continue
            raise
    return ok_count


async def run_targets(page: Page, targets: List[Dict[str, Any]], log=print) -> None:
    for i, target in enumerate(targets, start=1):
        url = str(target.get("url", "")).strip()
        if not url:
            continue
        wait_until = target.get("wait_until") or "domcontentloaded"
        wait_ms = int(target.get("wait_ms") or 1500)
        log(f"[사이트 {i}] 페이지 열기: {url}")
        await page.goto(url, wait_until=wait_until, timeout=90000)
        await _human_delay_async(wait_ms)

        clicks = target.get("clicks") or []
        await _perform_clicks(page, clicks, log=log, label_prefix=f"사이트 {i} 클릭")

        host = urlparse(url).netloc
        log(f"[사이트 {i}] 완료: {host}  최종주소={page.url}")


# ---------------------------------------------------------------------------
# Google search → find own site → human browse → banners → revisit
# ---------------------------------------------------------------------------

GOOGLE_CONSENT_TEXTS = [
    "모두 수락",
    "모두 동의",
    "Accept all",
    "I agree",
    "동의",
    "Accept",
]


async def _dismiss_google_consent(page: Page, log) -> None:
    clicked = await _click_button_by_texts(page, GOOGLE_CONSENT_TEXTS, timeout_ms=1500)
    if clicked:
        log(f"[검색] 동의 화면: {clicked}")
        await _rand_delay(600, 1200)


async def _wait_serp_ready(page: Page, log) -> None:
    """Wait until Google results container is present."""
    for sel in ("#search", "#rso", "#center_col", "div#main"):
        try:
            await page.locator(sel).first.wait_for(state="attached", timeout=12000)
            log(f"[검색] 결과 영역 감지: {sel}")
            return
        except Exception:
            continue
    log("[검색] 결과 영역 대기 타임아웃 — 계속 진행")


def _search_url_for_keyword(keyword: str, search_url: str = "") -> str:
    """Build a reliable Google search URL (Korean-friendly)."""
    base = (search_url or "https://www.google.com/").strip()
    q = quote_plus(keyword)
    if "{q}" in base:
        return base.replace("{q}", q)
    # Always use explicit /search?q= so Korean keywords don't get lost in IME typing
    if "google." in base:
        # keep host if custom google domain
        try:
            host = urlparse(base if "://" in base else "https://" + base).netloc or "www.google.com"
        except Exception:
            host = "www.google.com"
        return f"https://{host}/search?q={q}&hl=ko&gl=kr&pws=0"
    return f"https://www.google.com/search?q={q}&hl=ko&gl=kr&pws=0"


async def _type_search_query(page: Page, keyword: str, log) -> None:
    """Type into Google search box. Uses insert_text for Korean/Unicode reliability."""
    selectors = [
        'textarea[name="q"]',
        'input[name="q"]',
        'textarea[aria-label*="검색"]',
        'input[aria-label*="Search"]',
        'textarea[title="검색"]',
        "#APjFqb",
    ]
    box = None
    for sel in selectors:
        el = await _first_visible(page.locator(sel), timeout_ms=2500)
        if el is not None:
            box = el
            break
    if box is None:
        raise RuntimeError("Google 검색창을 찾지 못했습니다.")

    await box.scroll_into_view_if_needed()
    await _human_move_near(page, box)
    await box.click(timeout=10000)
    await _rand_delay(200, 450)
    try:
        await box.fill("")
    except Exception:
        await box.click(click_count=3)
        await page.keyboard.press("Backspace")

    log(f"[검색] 검색창에 입력 시작 keyword='{keyword}' (len={len(keyword)})")
    # insert_text handles Hangul/Unicode reliably (keyboard.type often breaks IME)
    try:
        await page.keyboard.insert_text(keyword)
    except Exception:
        try:
            await box.fill(keyword)
        except Exception:
            for ch in keyword:
                await page.keyboard.type(ch, delay=random.randint(40, 90))
    await _rand_delay(350, 700)
    # verify field value
    try:
        val = await box.input_value()
        log(f"[검색] 검색창 값 확인: '{val}'")
        if keyword not in (val or "") and (val or "").strip() != keyword.strip():
            log("[검색] 검색창 값 불일치 → fill 재시도")
            await box.fill(keyword)
            val = await box.input_value()
            log(f"[검색] 재입력 후: '{val}'")
    except Exception as exc:
        log(f"[검색] 검색창 값 확인 실패: {exc}")

    await page.keyboard.press("Enter")
    await page.wait_for_load_state("domcontentloaded", timeout=60000)
    await _rand_delay(1000, 1800)
    await _wait_serp_ready(page, log)
    log(f"[검색] Enter 후 주소: {page.url}")


async def _safe_goto(page: Page, url: str, log, *, label: str = "이동") -> bool:
    """goto with detailed logs; return False if failed."""
    try:
        log(f"[검색] {label}: {url}")
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=90000)
        status = resp.status if resp else "-"
        log(f"[검색] {label} 응답 status={status} 현재url={page.url}")
        return True
    except Exception as exc:
        log(f"[검색] {label} 실패: {type(exc).__name__}: {exc}")
        return False


async def _google_search(
    page: Page,
    keyword: str,
    *,
    search_url: str,
    log,
    browser: Optional[Browser] = None,
    prefer_direct: bool = True,
) -> Page:
    """
    검색어를 Google에 확실히 넣고 결과 페이지를 연다.
    탭이 닫혀 있으면 복구한 뒤 재시도. 반환: 사용 중인 live page.
    """
    keyword = (keyword or "").strip()
    if not keyword:
        raise RuntimeError("검색어가 비어 있습니다.")

    log("[검색] ========== 검색 실행 시작 ==========")
    log(f"[검색] ★★★ 검색어(원본): '{keyword}' (글자수={len(keyword)})")
    direct = _search_url_for_keyword(keyword, search_url)
    log(f"[검색] ★★★ 검색 URL: {direct}")

    last_err: Optional[Exception] = None
    for attempt in range(1, 4):
        log(f"[검색] —— 시도 {attempt}/3 ——")
        try:
            page = await ensure_live_page(page, browser, log)
            log(f"[검색] 현재 탭 상태 ok · url={page.url}")

            ok = False
            if prefer_direct:
                ok = await _safe_goto(page, direct, log, label=f"직접검색(시도{attempt})")
                if ok:
                    await _rand_delay(700, 1400)
                    await _dismiss_google_consent(page, log)
                    await _wait_serp_ready(page, log)

            if not ok:
                log("[검색] 직접 URL 실패 → 홈 열고 검색창 입력")
                page = await ensure_live_page(page, browser, log)
                home = "https://www.google.com/webhp?hl=ko"
                if not await _safe_goto(page, home, log, label="Google 홈"):
                    # last resort: new tab + direct
                    page = await ensure_live_page(None, browser, log)
                    if not await _safe_goto(page, direct, log, label="새탭 직접검색"):
                        raise RuntimeError("Google 검색 페이지를 열 수 없습니다")
                else:
                    await _rand_delay(600, 1200)
                    await _dismiss_google_consent(page, log)
                    await _type_search_query(page, keyword, log)
                    await _dismiss_google_consent(page, log)

            # Verify q=
            final = page.url or ""
            log(f"[검색] 결과 페이지 URL: {final}")
            qs = parse_qs(urlparse(final).query)
            q_in_url = unquote((qs.get("q") or [""])[0])
            log(f"[검색] URL q= 값: '{q_in_url}'")
            if not q_in_url:
                log("[검색] ⚠ URL에 q= 없음 → 직접검색 URL 강제")
                page = await ensure_live_page(page, browser, log)
                await _safe_goto(page, direct, log, label="q없음 강제검색")
                await _dismiss_google_consent(page, log)
                await _wait_serp_ready(page, log)
                q_in_url = unquote(
                    (parse_qs(urlparse(page.url).query).get("q") or [""])[0]
                )
                log(f"[검색] 강제 후 q=: '{q_in_url}'")

            # normalize compare
            def _norm(s: str) -> str:
                return re.sub(r"\s+", "", (s or "")).lower()

            if q_in_url and _norm(keyword) not in _norm(q_in_url) and _norm(q_in_url) not in _norm(
                keyword
            ):
                log(
                    f"[검색] ⚠ 검색어 불일치 keyword='{keyword}' vs q='{q_in_url}' → 재검색"
                )
                page = await ensure_live_page(page, browser, log)
                await _safe_goto(page, direct, log, label="불일치 재검색")
                await _dismiss_google_consent(page, log)
                await _wait_serp_ready(page, log)
                q_in_url = unquote(
                    (parse_qs(urlparse(page.url).query).get("q") or [""])[0]
                )
                log(f"[검색] 재검색 후 q=: '{q_in_url}'")

            log(f"[검색] ========== 검색 성공 · 검색어='{keyword}' · q='{q_in_url}' ==========")
            log(f"[검색] 최종 URL: {page.url}")
            return page
        except Exception as exc:
            last_err = exc
            log(f"[검색] 시도 {attempt} 실패: {type(exc).__name__}: {exc}")
            await _rand_delay(500, 1000)
            page = await ensure_live_page(page, browser, log)

    raise RuntimeError(f"Google 검색 실패 (검색어='{keyword}'): {last_err}")


async def _looks_like_google_ad(locator: Locator) -> bool:
    """
    Skip Google Ads / sponsored blocks.
    Own-site SEO QA must use organic results only — never ad clicks.
    """
    try:
        # data-text-ad / commercial unit containers
        for sel in (
            "[data-text-ad]",
            "[data-dtld]",
            ".commercial-unit-desktop-top",
            ".commercial-unit-desktop-rhs",
            ".ads-ad",
            "#tads",
            "#tadsb",
            "#rhs",
            "[aria-label*='Ads']",
            "[aria-label*='광고']",
        ):
            try:
                if await locator.evaluate(
                    """(el, sel) => {
                        const n = el.closest(sel);
                        return !!(n);
                    }""",
                    sel,
                ):
                    return True
            except Exception:
                continue
        # ancestor text markers
        marker = await locator.evaluate(
            """(el) => {
                let n = el;
                for (let i = 0; i < 8 && n; i++) {
                    const t = (n.innerText || n.textContent || '').slice(0, 400);
                    if (/^\\s*스폰서/.test(t) || /\\bSponsored\\b/i.test(t)
                        || /광고\\s*$/m.test(t) || /\\bAd\\b/.test(t.split('\\n')[0]||'')) {
                        // weak heuristic — also check class/id
                    }
                    const id = (n.id || '') + ' ' + (n.className || '');
                    if (/\\b(tads|ads-ad|commercial-unit|pla-unit|cu-container)\\b/i.test(id))
                        return true;
                    const label = n.getAttribute && (n.getAttribute('aria-label') || '');
                    if (/광고|Ads|Sponsored/i.test(label)) return true;
                    const dta = n.getAttribute && n.getAttribute('data-text-ad');
                    if (dta !== null && dta !== undefined) return true;
                    n = n.parentElement;
                }
                return false;
            }"""
        )
        return bool(marker)
    except Exception:
        return False


def _score_serp_candidate(
    *,
    real_url: str,
    link_text: str,
    target_domain: str,
    allowed_domains: List[str],
    url_contains: List[str],
    url_regex: Optional[re.Pattern],
    title_contains: List[str],
    title_regex: Optional[re.Pattern],
    require_domain: bool,
    display_path: str = "",
    path_targets: Optional[List[str]] = None,
    path_exclude: Optional[List[str]] = None,
    path_regex: Optional[re.Pattern] = None,
    path_regexes: Optional[List[re.Pattern]] = None,
    require_regex: bool = False,
    domain_set: Optional[set] = None,
    paths_exact_set: Optional[set] = None,
    full_url_set: Optional[set] = None,
    require_path_or_regex: bool = False,
) -> int:
    """
    Score SERP link for OWN site.
    require_domain / allowed_domains prevent third-party clicks.
    path_targets / path_regex / url_regex / bulk URL lists filter clicks.
    domain_set / paths_exact_set: fast path for 1k~100k own sites.
    """
    score = 0
    domain = _normalize_domain(target_domain)
    allowed = [_normalize_domain(d) for d in allowed_domains if _normalize_domain(d)]
    if domain and domain not in allowed:
        allowed.insert(0, domain)
    if domain_set is None and allowed:
        domain_set = set(allowed)

    parsed = urlparse(real_url)
    host = parsed.netloc or ""
    low_url = real_url.lower()
    low_text = (link_text or "").lower()

    # Skip pure Google chrome
    if host and "google." in host.lower():
        if domain_set:
            from .bulk_targets import host_in_bulk

            if not host_in_bulk(host, domain_set):
                return 0
        elif not any(_host_matches_domain(host, d) for d in allowed if d):
            return 0

    if domain_set is not None:
        from .bulk_targets import host_in_bulk

        domain_ok = host_in_bulk(host, domain_set)
    else:
        domain_ok = any(_host_matches_domain(host, d) for d in allowed if d)

    # Hard guard: never score third-party when own-site mode requires domain
    if require_domain:
        if not allowed and not domain_set:
            return 0
        if not domain_ok:
            return 0

    if domain_ok:
        score += 100
    elif require_domain:
        return 0

    # Path / regex filters (exclude first, then whitelist)
    ok_path, reason = _path_filter_allows(
        real_url,
        display_path,
        path_targets=list(path_targets or []),
        path_exclude=list(path_exclude or []),
        path_regex=path_regex,
        path_regexes=path_regexes,
        url_regex=url_regex,
        require_regex=require_regex,
        paths_exact_set=paths_exact_set,
        full_url_set=full_url_set,
        require_path_or_regex=require_path_or_regex,
    )
    if not ok_path:
        return 0
    if reason in ("bulk_full_url", "path_exact", "regex_or_exact"):
        score += 90

    url_path = _url_path_only(real_url)
    if path_targets and _path_pattern_hits(url_path, display_path, list(path_targets or [])):
        score += 50  # strong boost for intentional path target
    if display_path and path_targets:
        score += 10

    for token in url_contains:
        t = token.strip().lower()
        if t and t in low_url:
            score += 25

    rxs = list(path_regexes or [])
    if path_regex is not None:
        rxs.append(path_regex)
    if rxs and _path_regex_hits(url_path, display_path, real_url, rxs):
        score += 80  # user-specified path regex is primary intent
    if url_regex and url_regex.search(real_url):
        score += 70

    for token in title_contains:
        t = token.strip().lower()
        if t and t in low_text:
            score += 15

    if title_regex and title_regex.search(link_text or ""):
        score += 20

    if domain_ok:
        segs = [s for s in (parsed.path or "/").split("/") if s]
        if len(segs) == 0:
            score += 8
        elif len(segs) == 1:
            score += 4

    if score <= 0:
        return 0
    return score


async def _collect_serp_matches(
    page: Page,
    *,
    target_domain: str,
    allowed_domains: List[str],
    url_contains: List[str],
    url_regex: Optional[re.Pattern],
    title_contains: List[str],
    title_regex: Optional[re.Pattern],
    require_domain: bool,
    skip_ads: bool,
    log,
    path_targets: Optional[List[str]] = None,
    path_exclude: Optional[List[str]] = None,
    path_regex: Optional[re.Pattern] = None,
    path_regexes: Optional[List[re.Pattern]] = None,
    require_regex: bool = False,
    domain_set: Optional[set] = None,
    paths_exact_set: Optional[set] = None,
    full_url_set: Optional[set] = None,
    require_path_or_regex: bool = False,
) -> List[Tuple[int, Locator, str, str]]:
    """Return sorted list of (score, locator, url, link_text). Ads excluded when skip_ads."""
    found: List[Tuple[int, Locator, str, str]] = []
    seen: set[str] = set()
    skipped_ads = 0
    skipped_foreign = 0
    skipped_path = 0
    path_targets = list(path_targets or [])
    path_exclude = list(path_exclude or [])
    path_regexes = list(path_regexes or [])
    need_display = bool(
        path_targets or path_exclude or path_regex or path_regexes or url_regex
    )
    if domain_set is None and allowed_domains:
        domain_set = {
            _normalize_domain(d) for d in allowed_domains if _normalize_domain(d)
        }
    anchors = page.locator("a[href]")
    try:
        count = await anchors.count()
    except Exception:
        count = 0

    for i in range(min(count, 120)):
        a = anchors.nth(i)
        try:
            if not await a.is_visible(timeout=250):
                continue
            href = await a.get_attribute("href") or ""
            real = _extract_real_url(href)
            if not real or real.startswith("javascript:") or real.startswith("#"):
                continue

            if skip_ads and await _looks_like_google_ad(a):
                skipped_ads += 1
                continue

            host = urlparse(real).netloc or ""
            allowed = [_normalize_domain(d) for d in allowed_domains if _normalize_domain(d)]
            td = _normalize_domain(target_domain)
            if td and td not in allowed:
                allowed.insert(0, td)
            if require_domain and (domain_set or allowed):
                if domain_set is not None:
                    from .bulk_targets import host_in_bulk

                    if not host_in_bulk(host, domain_set):
                        skipped_foreign += 1
                        continue
                elif not any(_host_matches_domain(host, d) for d in allowed):
                    skipped_foreign += 1
                    continue

            display_path = ""
            if need_display:
                display_path = await _extract_display_path(a)

            ok_path, _reason = _path_filter_allows(
                real,
                display_path,
                path_targets=path_targets,
                path_exclude=path_exclude,
                path_regex=path_regex,
                path_regexes=path_regexes,
                url_regex=url_regex,
                require_regex=require_regex,
                paths_exact_set=paths_exact_set,
                full_url_set=full_url_set,
                require_path_or_regex=require_path_or_regex,
            )
            if not ok_path:
                skipped_path += 1
                continue

            try:
                text = (await a.inner_text(timeout=800) or "").strip()
            except Exception:
                text = ""
            text = re.sub(r"\s+", " ", text)[:200]
            score = _score_serp_candidate(
                real_url=real,
                link_text=text,
                target_domain=target_domain,
                allowed_domains=allowed,
                url_contains=url_contains,
                url_regex=url_regex,
                title_contains=title_contains,
                title_regex=title_regex,
                require_domain=require_domain,
                display_path=display_path,
                path_targets=path_targets,
                path_exclude=path_exclude,
                path_regex=path_regex,
                path_regexes=path_regexes,
                require_regex=require_regex,
                domain_set=domain_set,
                paths_exact_set=paths_exact_set,
                full_url_set=full_url_set,
                require_path_or_regex=require_path_or_regex,
            )
            if score <= 0:
                continue
            key = real.split("#")[0]
            if key in seen:
                continue
            seen.add(key)
            found.append((score, a, real, text))
        except Exception:
            continue

    if skipped_ads:
        log(f"[검색] 광고/스폰서 블록 스킵 {skipped_ads}건 (광고 클릭 안 함)")
    if skipped_foreign and require_domain:
        log(f"[검색] 자사 도메인 외 링크 제외 {skipped_foreign}건")
    if skipped_path:
        log(
            f"[검색] path/정규식 필터 제외 {skipped_path}건 "
            f"(path타겟={path_targets or '-'} path정규식={len(path_regexes) + (1 if path_regex else 0)}개 "
            f"url정규식={'ON' if url_regex else 'OFF'} 제외={path_exclude or '-'})"
        )
    found.sort(key=lambda x: x[0], reverse=True)
    return found


async def _goto_next_serp_page(page: Page, log) -> bool:
    next_texts = ["다음", "Next", "다음 페이지"]
    clicked = await _click_button_by_texts(page, next_texts, timeout_ms=1500)
    if clicked:
        log(f"[검색] 다음 결과 페이지: {clicked}")
        await page.wait_for_load_state("domcontentloaded", timeout=60000)
        await _rand_delay(1000, 2000)
        return True
    if await _click_if_visible(
        page, "a#pnnext, a[aria-label='Next page'], a[aria-label='다음 페이지']"
    ):
        log("[검색] 다음 결과 페이지 (링크)")
        await page.wait_for_load_state("domcontentloaded", timeout=60000)
        await _rand_delay(1000, 2000)
        return True
    return False


def _allowed_list(target_domain: str, allowed_domains: List[str]) -> List[str]:
    allowed = [_normalize_domain(d) for d in allowed_domains if _normalize_domain(d)]
    td = _normalize_domain(target_domain)
    if td and td not in allowed:
        allowed.insert(0, td)
    return allowed


def _is_allowed_host(host: str, allowed: List[str]) -> bool:
    return any(_host_matches_domain(host, d) for d in allowed if d)


async def _click_serp_result(
    page: Page,
    el: Locator,
    real: str,
    *,
    allowed: List[str],
    require_domain: bool,
    log,
    is_ad: bool = False,
    keyword: str = "",
) -> Dict[str, Any]:
    """
    Click one SERP result and verify landing.
    Returns evidence dict (clicked True/False + profile/IP/URLs).
    """
    from datetime import datetime

    evidence: Dict[str, Any] = {
        "clicked": False,
        "ok": False,
        "target_url": real,
        "matched_url": real,
        "final_url": "",
        "serp_url": page.url or "",
        "host_ok": False,
        "is_ad": bool(is_ad),
        "method": "",
        "keyword": keyword or getattr(log, "keyword", "") or "",
        "profile": getattr(log, "profile", "") or "",
        "email": getattr(log, "email", "") or "",
        "proxy": getattr(log, "proxy", "") or "",
        "ip": getattr(log, "proxy_ip", "") or "미확인",
        "job": getattr(log, "job_index", 0) or 0,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "error": "",
    }
    host = urlparse(real).netloc or ""
    if require_domain and allowed and not _is_allowed_host(host, allowed):
        evidence["error"] = f"가드: 대상 호스트 불일치 ({host})"
        log(f"[검색] 가드: 자사 도메인 아님 → 스킵 {real}")
        if hasattr(log, "click_evidence"):
            log.click_evidence(evidence)  # type: ignore[attr-defined]
        return evidence

    method = "element_click"
    try:
        await el.scroll_into_view_if_needed()
        await _rand_delay(300, 800)
        await _human_move_near(page, el)
        await _rand_delay(150, 450)
        try:
            await el.hover(timeout=3000)
            await _rand_delay(150, 500)
        except Exception:
            pass
        kind = "광고 링크" if is_ad else "검색 결과 링크"
        log(
            f"[사람] 지금 {kind}를 누르려 합니다. "
            f"주소는 {real[:80]}{'…' if len(real)>80 else ''} 입니다."
        )
        await el.click(timeout=15000)
    except Exception as click_exc:
        method = "goto_fallback"
        evidence["error"] = f"링크 클릭이 안 되어 주소로 바로 이동: {click_exc}"
        log("[사람] 링크가 잘 안 눌려서, 같은 주소로 직접 이동합니다.")
        try:
            await page.goto(real, wait_until="domcontentloaded", timeout=90000)
        except Exception as go_exc:
            evidence["error"] = f"직접 이동도 실패: {go_exc}"
            evidence["method"] = method
            log("[사람] 결국 그 주소로 들어가지 못했습니다.")
            if hasattr(log, "click_evidence"):
                log.click_evidence(evidence)  # type: ignore[attr-defined]
            return evidence

    evidence["method"] = method
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=90000)
    except Exception:
        pass
    # 사람처럼 도착 직후 잠깐 멈춤 (고정 시간 아님)
    await _rand_delay(700, 2200)
    if random.random() < 0.4:
        await _mouse_wander(page, moves=random.randint(1, 2))
    final_url = page.url or ""
    final_host = urlparse(final_url).netloc or ""
    evidence["final_url"] = final_url
    evidence["landed_url"] = final_url

    if require_domain and allowed and not _is_allowed_host(final_host, allowed):
        evidence["host_ok"] = False
        evidence["error"] = f"다른 사이트({final_host})로 가서 무효 처리"
        log(
            f"[사람] 눌러 보니 예상과 다른 사이트({final_host})라서 "
            "이번 클릭은 세지 않습니다."
        )
        if hasattr(log, "click_evidence"):
            log.click_evidence(evidence)  # type: ignore[attr-defined]
        return evidence

    evidence["host_ok"] = True
    evidence["clicked"] = True
    evidence["ok"] = True
    evidence["error"] = ""
    log(
        f"[사람] 사이트에 들어왔습니다. "
        f"화면 주소: {final_url[:90]}{'…' if len(final_url)>90 else ''}"
    )
    if hasattr(log, "click_evidence"):
        log.click_evidence(evidence)  # type: ignore[attr-defined]
    elif hasattr(log, "set_matched_url"):
        try:
            log.set_matched_url(final_url)  # type: ignore[attr-defined]
        except Exception:
            pass
    return evidence


async def _find_and_click_many_own_sites(
    page: Page,
    *,
    keyword: str,
    search_url: str,
    target_domain: str,
    allowed_domains: List[str],
    url_contains: List[str],
    url_regex: Optional[re.Pattern],
    title_contains: List[str],
    title_regex: Optional[re.Pattern],
    require_domain: bool,
    skip_ads: bool,
    max_pages: int,
    max_clicks: int,
    banner_clicks: List[Dict[str, Any]],
    human: Dict[str, Any],
    log,
    browser: Optional[Browser] = None,
    path_targets: Optional[List[str]] = None,
    path_exclude: Optional[List[str]] = None,
    path_regex: Optional[re.Pattern] = None,
    path_regexes: Optional[List[re.Pattern]] = None,
    require_regex: bool = False,
    domain_set: Optional[set] = None,
    paths_exact_set: Optional[set] = None,
    full_url_set: Optional[set] = None,
    require_path_or_regex: bool = False,
) -> Dict[str, Any]:
    """
    Scan multiple SERP pages and click UP TO max_clicks distinct own-site results.
    After each visit: browse + CTA, then return to SERP for next match.
    path_targets / path_regex / url_regex: only click matching own-site links.
    path_exclude: never click these paths.
    """
    allowed = _allowed_list(target_domain, allowed_domains)
    path_targets = list(path_targets or [])
    path_exclude = list(path_exclude or [])
    path_regexes = list(path_regexes or [])
    if domain_set is None:
        domain_set = set(allowed)
    max_clicks = max(1, int(max_clicks or 1))
    max_pages = max(1, int(max_pages or 3))
    clicked_keys: set[str] = set()
    visited: List[str] = []
    banner_total = 0
    click_evidence: List[Dict[str, Any]] = []
    verified_clicks = 0

    log(
        f"[사람] 구글에서「{keyword}」검색 결과를 살핍니다. "
        f"목표 사이트 {len(domain_set or allowed)}개, "
        f"이 검색에서 클릭은 최대 {max_clicks}번, "
        f"결과 페이지는 최대 {max_pages}장까지 봅니다."
        + (" (광고 결과는 건너뜀)" if skip_ads else " (광고 결과도 포함)")
    )
    if path_targets:
        log(f"[검색] ★ path 타겟(이 path만 클릭): {path_targets}")
    if path_regex or path_regexes:
        log(
            f"[검색] ★ path/링크 정규식 "
            f"{len(path_regexes) + (1 if path_regex else 0)}개 · require={require_regex}"
        )
    if url_regex:
        log(f"[검색] ★ URL 정규식: {url_regex.pattern[:120]}")
    if path_exclude:
        log(f"[검색] ★ path 제외(발견 시 클릭 안 함): {path_exclude}")

    for page_no in range(1, max_pages + 1):
        if len(clicked_keys) >= max_clicks:
            break

        page = await ensure_live_page(page, browser, log)
        log(
            f"[사람] 검색 결과 {page_no}페이지를 봅니다. "
            f"(이미 누른 링크 {len(clicked_keys)}/{max_clicks})"
        )
        if human.get("scroll", True):
            await _human_scroll(
                page,
                steps=random.randint(
                    int(human.get("serp_scroll_min") or 2),
                    int(human.get("serp_scroll_max") or 6),
                ),
                allow_up=True,
                up_chance=float(human.get("scroll_up_chance") or 0.2),
            )
        if human.get("mouse_wander", True):
            await _mouse_wander(page, moves=random.randint(1, 3))
        await _rand_delay(350, 800)

        matches = await _collect_serp_matches(
            page,
            target_domain=target_domain,
            allowed_domains=allowed,
            url_contains=url_contains,
            url_regex=url_regex,
            title_contains=title_contains,
            title_regex=title_regex,
            require_domain=require_domain,
            skip_ads=skip_ads,
            log=log,
            path_targets=path_targets,
            path_exclude=path_exclude,
            path_regex=path_regex,
            path_regexes=path_regexes,
            require_regex=require_regex,
            domain_set=domain_set,
            paths_exact_set=paths_exact_set,
            full_url_set=full_url_set,
            require_path_or_regex=require_path_or_regex,
        )
        # drop already clicked
        fresh = []
        for sc, el, u, t in matches:
            key = u.split("#")[0].rstrip("/")
            if key in clicked_keys:
                continue
            fresh.append((sc, el, u, t, key))

        log(
            f"[사람] 이 페이지에서 목표와 맞는 링크를 "
            f"{len(fresh)}개 찾았습니다."
            + (f" (전체 후보 {len(matches)}개 중)" if len(matches) != len(fresh) else "")
        )
        for rank, (sc, _el, u, t, _k) in enumerate(fresh[:5], start=1):
            log(
                f"[사람] 후보 {rank}: {(t or '(제목 없음)')[:40]} "
                f"— {u[:55]}{'…' if len(u)>55 else ''}"
            )

        if not fresh:
            if page_no < max_pages:
                if not await _goto_next_serp_page(page, log):
                    log("[사람] 다음 검색 결과 페이지가 없어 여기서 멈춥니다.")
                    break
                continue
            break

        # Click several results from this SERP page before going to next page
        for sc, el, real, text, key in fresh:
            if len(clicked_keys) >= max_clicks:
                break
            preview = (text or "")[:50]
            is_ad = False
            try:
                is_ad = await _looks_like_google_ad(el)
            except Exception:
                is_ad = False
            log(
                f"[사람] 이제 이 링크를 누릅니다"
                f"{' (광고)' if is_ad else ''}: "
                f"{preview or real[:40]}"
            )
            ev = await _click_serp_result(
                page,
                el,
                real,
                allowed=allowed,
                require_domain=require_domain,
                log=log,
                is_ad=is_ad,
                keyword=keyword,
            )
            if isinstance(ev, dict):
                click_evidence.append(ev)
                ok = bool(ev.get("clicked") or ev.get("ok"))
            else:
                ok = bool(ev)
            if not ok:
                u = (page.url or "").lower()
                if "google." not in u or ("search" not in u and "q=" not in u):
                    page = await _return_to_serp(
                        page,
                        keyword=keyword,
                        search_url=search_url,
                        log=log,
                        browser=browser,
                    )
                continue

            clicked_keys.add(key)
            verified_clicks += 1
            visited.append(page.url or (ev.get("final_url") if isinstance(ev, dict) else "") or real)
            traffic = human.get("_traffic") if isinstance(human, dict) else None
            if traffic is not None:
                try:
                    traffic.mark_click(
                        f"serp_own_site#{len(clicked_keys)}",
                        url=page.url,
                    )
                except Exception:
                    pass
            # own-site cookie inject after SERP landing (QA session cookies)
            ck_cfg = human.get("_cookies_cfg") if isinstance(human, dict) else None
            if ck_cfg:
                try:
                    await inject_cookies(page, ck_cfg, log=log, phase="on_site")
                    # reload once so app JS picks up injected cookies
                    if (ck_cfg or {}).get("reload_on_site", True):
                        try:
                            await page.reload(wait_until="domcontentloaded", timeout=45000)
                            log("[쿠키] 사이트 랜딩 후 리로드 (세션 반영)")
                        except Exception as rel_exc:
                            log(f"[쿠키] 리로드 경고: {rel_exc}")
                except Exception as ck_exc:
                    log(f"[쿠키] on_site 경고: {ck_exc}")
            n = await _browse_own_site(
                page, banner_clicks=banner_clicks, human=human, log=log
            )
            banner_total += n
            # aggressive OPS pressure (own domain only)
            ops_preset = human.get("_ops_preset") if isinstance(human, dict) else None
            if ops_preset and str(ops_preset.get("name") or "normal") != "normal":
                try:
                    from .own_site_ops import aggressive_on_site

                    ops_out = await aggressive_on_site(
                        page,
                        allowed_domains=allowed,
                        preset=ops_preset,
                        log=log,
                        traffic=traffic,
                    )
                    log(
                        f"[OPS] on-site pressure links={ops_out.get('links_clicked')} "
                        f"forms={ops_out.get('forms_seen')} assets={ops_out.get('assets')}"
                    )
                except Exception as ops_exc:
                    log(f"[OPS] on-site 경고: {ops_exc}")
            if traffic is not None:
                try:
                    slice_info = traffic.end_click()
                    if slice_info:
                        log(
                            f"[TRAFFIC] ★ 이 클릭으로 들어간 실제 요청 "
                            f"{slice_info.get('requests')}개 "
                            f"(자사={slice_info.get('target_host_hits')} "
                            f"Google={slice_info.get('google_hits')} "
                            f"≈{slice_info.get('bytes_in')}B "
                            f"{slice_info.get('duration_ms')}ms)"
                        )
                except Exception:
                    pass
            log(
                f"[사람] 사이트 안 둘러보기를 마쳤습니다. "
                f"배너·버튼 클릭 {n}회, 검색 클릭 누적 {len(clicked_keys)}/{max_clicks}."
            )

            if len(clicked_keys) < max_clicks:
                page = await _return_to_serp(
                    page,
                    keyword=keyword,
                    search_url=search_url,
                    log=log,
                    browser=browser,
                )
                await _human_scroll(page, steps=random.randint(1, 3), allow_up=True)
                await _rand_delay(500, 1000)
                break

        if len(clicked_keys) >= max_clicks:
            break
        if page_no < max_pages:
            page = await ensure_live_page(page, browser, log)
            u = (page.url or "").lower()
            if "google." not in u or ("search" not in u and "q=" not in u):
                page = await _return_to_serp(
                    page,
                    keyword=keyword,
                    search_url=search_url,
                    log=log,
                    browser=browser,
                )
            if not await _goto_next_serp_page(page, log):
                log("[검색] 다음 페이지 없음 — 이 검색어 종료")
                break

    log(
        f"[사람] 이번 검색 정리: 실제로 들어간 횟수 {verified_clicks}번, "
        f"사이트 안 배너·버튼 {banner_total}번. "
        "(한꺼번에 서버를 때리는 방식이 아닙니다)"
    )
    return {
        "clicks": len(clicked_keys),
        "verified_clicks": verified_clicks,
        "visited": visited,
        "banner_clicks": banner_total,
        "ok": len(clicked_keys) > 0,
        "click_evidence": click_evidence,
        "click_verified": verified_clicks > 0,
    }


async def _find_and_click_own_site(
    page: Page,
    *,
    target_domain: str,
    allowed_domains: List[str],
    url_contains: List[str],
    url_regex: Optional[re.Pattern],
    title_contains: List[str],
    title_regex: Optional[re.Pattern],
    require_domain: bool,
    skip_ads: bool,
    max_pages: int,
    human: Dict[str, Any],
    log,
    path_targets: Optional[List[str]] = None,
    path_exclude: Optional[List[str]] = None,
) -> bool:
    """Legacy single-click helper (first match only)."""
    out = await _find_and_click_many_own_sites(
        page,
        keyword="",
        search_url="https://www.google.com/",
        target_domain=target_domain,
        allowed_domains=allowed_domains,
        url_contains=url_contains,
        url_regex=url_regex,
        title_contains=title_contains,
        title_regex=title_regex,
        require_domain=require_domain,
        skip_ads=skip_ads,
        max_pages=max_pages,
        max_clicks=1,
        banner_clicks=[],
        human=human,
        log=log,
        path_targets=path_targets,
        path_exclude=path_exclude,
    )
    return bool(out.get("ok"))


async def _browse_own_site(
    page: Page,
    *,
    banner_clicks: List[Dict[str, Any]],
    human: Dict[str, Any],
    log,
) -> int:
    """
    사이트 안 행동: 사람처럼 둘러보기.
    - 스크롤·멈춤·마우스 이동 시간이 매번 다름 (기계 패턴 방지)
    - 배너/버튼 클릭 순서 섞기
    - 디도스처럼 연속 요청을 퍼붓지 않음
    """
    host = _normalize_domain(urlparse(page.url).netloc)
    log(
        f"[사람] 사이트에 들어와 잠시 둘러봅니다. "
        f"주소는 {page.url[:80]}{'…' if len(page.url or '')>80 else ''} 입니다."
    )

    # 체류 시간을 넓게 랜덤 (고정 구간 고정 반복 금지)
    dwell_min = int(human.get("dwell_ms_min") or 4500)
    dwell_max = int(human.get("dwell_ms_max") or 14000)
    # 세션마다 약간 흔들기
    dwell_min = max(2000, dwell_min + random.randint(-800, 1200))
    dwell_max = max(dwell_min + 1500, dwell_max + random.randint(-1500, 2500))
    do_scroll = bool(human.get("scroll", True))
    mouse_wander = bool(human.get("mouse_wander", True))
    read_pauses = bool(human.get("read_pauses", True))
    scroll_min = int(human.get("scroll_steps_min") or 2)
    scroll_max = int(human.get("scroll_steps_max") or 9)
    up_chance = float(human.get("scroll_up_chance") or 0.28)

    # 도착 직후: 항상 같은 대기가 아니게
    await _rand_delay(400, 2100)
    if mouse_wander and random.random() < 0.85:
        log("[사람] 화면을 눈으로 훑듯 마우스를 조금 움직입니다.")
        await _mouse_wander(page, moves=random.randint(1, 5))

    # 행동 순서를 섞음: 스크롤 먼저 / 읽기 먼저 / 배너 먼저
    phases = ["scroll_a", "read", "scroll_b", "banner", "scroll_c", "internal"]
    random.shuffle(phases)
    # 너무 짧지 않게 핵심 단계는 유지
    if "read" not in phases[:4]:
        phases.insert(random.randint(0, 2), "read")
    if "banner" not in phases and banner_clicks:
        phases.insert(random.randint(1, min(3, len(phases))), "banner")

    clicked_n = 0
    did_scroll = False

    for phase in phases:
        if phase == "scroll_a" or phase == "scroll_b" or phase == "scroll_c":
            if not do_scroll or random.random() < 0.12:
                continue
            steps = random.randint(scroll_min, max(scroll_min, scroll_max))
            log(
                f"[사람] 페이지를 위아래로 천천히 살펴봅니다. "
                f"(스크롤 약 {steps}번, 간격은 제각각)"
            )
            await _human_scroll(
                page,
                steps=steps,
                allow_up=True,
                up_chance=up_chance + random.uniform(-0.08, 0.12),
            )
            did_scroll = True
            if mouse_wander and random.random() < 0.5:
                await _mouse_wander(page, moves=random.randint(1, 3))

        elif phase == "read":
            if read_pauses:
                sec = random.randint(dwell_min, dwell_max) / 1000.0
                log(
                    f"[사람] 글을 읽는 것처럼 약 {sec:.1f}초 머뭅니다. "
                    "(서버를 두드리는 행동이 아닙니다)"
                )
                await _human_idle(page, dwell_min, dwell_max)
            else:
                await _rand_delay(dwell_min, dwell_max)

        elif phase == "banner":
            if not banner_clicks:
                # 설정 배너 없어도 화면에 보이는 버튼/링크를 가끔 눌러 봄
                if random.random() < 0.55:
                    n_auto = await _soft_click_visible_cta(page, log=log)
                    clicked_n += n_auto
                continue
            # 배너 목록 순서 섞기
            banners = list(banner_clicks)
            random.shuffle(banners)
            # 전부 안 누르고 일부만 (사람처럼)
            take = max(1, min(len(banners), random.randint(1, len(banners))))
            subset = banners[:take]
            log(
                f"[사람] 화면 안 배너·버튼 글자를 찾아 "
                f"{take}개 정도 눌러 봅니다. (한꺼번에 퍼붓지 않음)"
            )
            for bi, bcfg in enumerate(subset):
                if bi > 0:
                    await _rand_delay(900, 2800)
                one = await _perform_clicks(
                    page, [bcfg], log=log, label_prefix="배너·버튼"
                )
                clicked_n += int(one or 0)
                if do_scroll and random.random() < 0.45:
                    await _human_scroll(
                        page, steps=random.randint(1, 2), allow_up=True, up_chance=0.3
                    )
            if clicked_n:
                log(f"[사람] 배너·버튼을 {clicked_n}번 눌렀습니다.")
            else:
                log("[사람] 이번에는 맞는 배너·버튼을 못 찾아 넘어갑니다.")

        elif phase == "internal":
            # 기본 ON에 가깝게: human 플래그 없거나 True면 가끔 내부 링크
            allow_internal = human.get("random_internal_click", True)
            if not allow_internal or random.random() < 0.35:
                continue
            try:
                domain = host
                links = page.locator("a[href]")
                n = min(await links.count(), 40)
                candidates = []
                for i in range(n):
                    a = links.nth(i)
                    try:
                        if not await a.is_visible(timeout=180):
                            continue
                        href = await a.get_attribute("href") or ""
                        real = _extract_real_url(href)
                        if real and _host_matches_domain(urlparse(real).netloc, domain):
                            low = real.lower()
                            if any(
                                x in low
                                for x in (
                                    "logout",
                                    "signout",
                                    "login",
                                    "javascript:",
                                    "#",
                                )
                            ):
                                continue
                            candidates.append((a, real))
                    except Exception:
                        continue
                if candidates:
                    a, real = random.choice(candidates[:12])
                    log(
                        f"[사람] 같은 사이트 안 다른 글(메뉴)로 한 번 더 들어가 봅니다. "
                        f"{real[:70]}{'…' if len(real)>70 else ''}"
                    )
                    await _human_move_near(page, a)
                    await _rand_delay(200, 900)
                    await a.click(timeout=10000)
                    await page.wait_for_load_state("domcontentloaded", timeout=60000)
                    await _rand_delay(1000, 3200)
                    if do_scroll:
                        await _human_scroll(
                            page, steps=random.randint(1, 4), allow_up=True
                        )
                    clicked_n += 1
            except Exception as exc:
                log(f"[사람] 안쪽 링크는 건너뜁니다. ({exc})")

    if not did_scroll and do_scroll:
        await _human_scroll(page, steps=random.randint(2, 5), allow_up=True)

    # 나갈 때 한 번 더 짧게 머무름
    await _rand_delay(600, 2000)
    log(
        f"[사람] 이 사이트 둘러보기를 마칩니다. "
        f"마지막 화면: {(page.url or '')[:80]}"
    )
    return clicked_n


async def _soft_click_visible_cta(page: Page, *, log) -> int:
    """설정 없이도 보이는 가입/시작/메뉴 비슷한 글자 버튼을 가끔 누름."""
    words = [
        "가입",
        "시작",
        "입금",
        "메뉴",
        "더보기",
        "자세히",
        "이벤트",
        "쿠폰",
        "play",
        "start",
        "join",
        "bonus",
    ]
    random.shuffle(words)
    for w in words[: random.randint(2, 5)]:
        try:
            loc = page.get_by_text(w, exact=False).first
            if await loc.is_visible(timeout=400):
                log(f"[사람] 화면에 보이는「{w}」글자를 눌러 봅니다.")
                await _human_move_near(page, loc)
                await _rand_delay(150, 600)
                await loc.click(timeout=5000)
                await _rand_delay(800, 2200)
                return 1
        except Exception:
            continue
    return 0


async def _return_to_serp(
    page: Page,
    *,
    keyword: str,
    search_url: str,
    log,
    browser: Optional[Browser] = None,
) -> Page:
    """Go back to search results (prefer history back, else re-search)."""
    page = await ensure_live_page(page, browser, log)
    try:
        await page.go_back(wait_until="domcontentloaded", timeout=30000)
        await _rand_delay(900, 1600)
        u = (page.url or "").lower()
        if "google." in u and ("search" in u or "q=" in u):
            log("[검색] 뒤로가기 → 검색 결과로 복귀")
            await _mouse_wander(page, moves=1)
            return page
    except Exception as exc:
        log(f"[검색] 뒤로가기 실패: {exc}")
    log("[검색] 다시 검색으로 SERP 복귀")
    return await _google_search(
        page, keyword, search_url=search_url, log=log, browser=browser
    )


# Placeholder samples that must not override real user keywords
_PLACEHOLDER_KEYWORDS = {
    "example-mybrand",
    "my brand",
    "mybrand",
    "test",
    "내 브랜드 키워드",
    "keyword",
}


def split_search_keywords(value: Any) -> List[str]:
    """
    검색어 여러 개 파싱.
    지원 구분자: 줄바꿈, / , | , 쉼표
    예: 카지노사이트/카지노사이트순위/카지노사이트추천
        카지노사이트 | 카지노사이트순위
        (한 줄에 하나)
    URL(http…) 은 쪼개지 않음.
    """
    if value is None:
        return []
    if isinstance(value, list):
        parts: List[str] = []
        for x in value:
            parts.extend(split_search_keywords(x))
        # de-dupe preserve order
        out: List[str] = []
        seen: set[str] = set()
        for p in parts:
            k = p.lower()
            if k not in seen:
                seen.add(k)
                out.append(p)
        return out

    raw = str(value).replace("\r\n", "\n").replace("\r", "\n")
    chunks: List[str] = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # URL keep whole
        if re.search(r"https?://", line, re.I):
            chunks.append(line)
            continue
        # primary separators for multi-keyword one-liners
        # prefer / and | first (user request), then comma
        if re.search(r"[/|]", line):
            for piece in re.split(r"[/|]+", line):
                p = piece.strip()
                if p:
                    chunks.append(p)
            continue
        if "," in line:
            for piece in line.split(","):
                p = piece.strip()
                if p:
                    chunks.append(p)
            continue
        chunks.append(line)

    out2: List[str] = []
    seen2: set[str] = set()
    for p in chunks:
        s = str(p).strip()
        if not s or s.startswith("#"):
            continue
        key = s.lower()
        if key in seen2:
            continue
        seen2.add(key)
        out2.append(s)
    return out2


def _lines_list(value: Any) -> List[str]:
    """Normalize list or multi-line/comma string into clean unique list."""
    items: List[str] = []
    if value is None:
        return items
    if isinstance(value, list):
        raw_parts = [str(x) for x in value]
    else:
        # keep newlines as separators; also support comma for simple lists
        raw = str(value).replace("\r\n", "\n").replace("\r", "\n")
        raw_parts = []
        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            if "," in line and "http" not in line.lower():
                raw_parts.extend(line.split(","))
            else:
                raw_parts.append(line)
    seen: set[str] = set()
    for p in raw_parts:
        s = str(p).strip()
        if not s or s.startswith("#"):
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        items.append(s)
    return items


def resolve_search_keywords(cfg: Dict[str, Any], log=None) -> List[str]:
    """
    Resolve the exact keyword list the user intended.
    Priority:
      1) keywords_text (GUI multi-line or slash-separated)
      2) keywords[] array
      3) keyword single field (supports 카지노/순위/추천 style)
    Drops empty/placeholder samples when real keywords exist.
    Fallback order is preserved: first keyword first, then next on miss.
    """
    from_text = split_search_keywords(cfg.get("keywords_text"))
    from_arr = split_search_keywords(cfg.get("keywords"))
    single = str(cfg.get("keyword") or "").strip()
    from_single = split_search_keywords(single) if single else []

    ordered: List[str] = []
    for src in (from_text, from_arr, from_single):
        for k in src:
            if k and k not in ordered:
                ordered.append(k)

    real = [k for k in ordered if k.strip().lower() not in _PLACEHOLDER_KEYWORDS]
    if real:
        # keep only real user keywords (drop example-mybrand etc.)
        ordered = real
    elif ordered:
        if log:
            log(f"[검색] 경고: 플레이스홀더 검색어만 있음 → {ordered}")

    if log:
        log(f"[검색] 검색어 해석 결과 ({len(ordered)}개): {ordered}")
        log(
            f"[검색] 소스 keywords_text={len(from_text)} keywords[]={len(from_arr)} "
            f"keyword='{single}' → {len(from_single)}개"
        )
        if len(ordered) > 1:
            log(
                "[검색] ★ 폴백 모드: 앞 검색어에서 타겟(광고/자사) 없으면 "
                "다음 검색어로 자동 전환"
            )
    return ordered


def _compile_regex_any(patterns: List[str]) -> Optional[re.Pattern]:
    """Combine multiple regex patterns with OR."""
    compiled: List[str] = []
    for p in patterns:
        p = (p or "").strip()
        if not p:
            continue
        try:
            re.compile(p)
            compiled.append(f"(?:{p})")
        except re.error:
            continue
    if not compiled:
        return None
    try:
        return re.compile("|".join(compiled), re.I)
    except re.error:
        return None


async def run_search_flow(
    page: Page,
    cfg: Dict[str, Any],
    log=print,
    *,
    browser: Optional[Browser] = None,
) -> Dict[str, Any]:
    """
    자사 다중 사이트(수천~수만) · 다중 검색어 · URL/path/정규식 매칭:
      검색어마다 SERP를 여러 페이지 돌며 자사 도메인+지정 path/URL 결과만 클릭.

    keys:
      keywords[] / keyword / keywords_text
      allowed_domains[] / domains_text / target_domain / bulk_* 
      url_regex / path_regex / path_targets / bulk_full_urls
      max_result_clicks, max_serp_pages, skip_ads, require_domain
    """
    out: Dict[str, Any] = {
        "search_ok": False,
        "site_clicks": 0,
        "visits": 0,
        "keyword": "",
        "keywords_done": [],
        "matched_url": "",
        "matched_urls": [],
        "cookies_set": 0,
        "error": "",
        "click_evidence": [],
        "click_verified": False,
        "final_url": "",
        "is_ad": False,
        "click_method": "",
    }
    if not cfg or not cfg.get("enabled", True):
        out["error"] = "search_flow disabled"
        return out

    keywords = resolve_search_keywords(cfg, log=log)
    if not keywords:
        raise RuntimeError(
            "search_flow: 검색어가 비어 있습니다. "
            "GUI '기본 검색어' 또는 '추가 검색어 목록'에 입력하세요."
        )

    # ── 프로필(작업)별 검색어 다양화 ─────────────────────────────
    # 오토 게임처럼: 창마다 다른 검색어로 조회 → 타겟 1회 클릭
    job_index = int(cfg.get("_job_index") or cfg.get("job_index") or 0)
    if bool(cfg.get("keyword_rotate", True)) and len(keywords) > 1 and job_index > 0:
        off = (max(0, job_index) - 1) % len(keywords)
        keywords = keywords[off:] + keywords[:off]
        if log:
            log(
                f"[SEARCH] 검색어 로테이트 job={job_index} offset={off} "
                f"→ 시작='{keywords[0]}'"
            )
    if bool(cfg.get("keyword_shuffle", True)) and len(keywords) > 1:
        # 분 단위 시드: 같은 분에는 재현 가능, 프로필마다 다른 순서
        seed = (job_index or 1) * 9973 + int(time.time()) // 120
        rng = random.Random(seed)
        head = keywords[0]
        rest = keywords[1:]
        rng.shuffle(rest)
        keywords = [head] + rest
        if log:
            log(
                f"[SEARCH] 검색어 셔플 job={job_index} seed={seed} "
                f"order={keywords[:8]}{'…' if len(keywords)>8 else ''}"
            )
    # one_keyword_per_job: 주 검색어 1개만 쓰고 폴백 허용 시 나머지 유지
    if bool(cfg.get("one_keyword_per_job")) and keywords:
        primary = keywords[0]
        if bool(cfg.get("keyword_fallback", True)) and len(keywords) > 1:
            keywords = [primary] + keywords[1:]
        else:
            keywords = [primary]
        if log:
            log(f"[SEARCH] 1검색어/프로필 모드 primary='{primary}' fallback={len(keywords)-1}")

    target_domain = str(
        cfg.get("target_domain") or cfg.get("own_domain") or cfg.get("site_domain") or ""
    ).strip()
    allowed_domains = _lines_list(cfg.get("allowed_domains") or cfg.get("own_domains"))
    allowed_domains.extend(_lines_list(cfg.get("domains_text")))
    # normalize domains (strip scheme/path)
    allowed_domains = list(
        dict.fromkeys(
            d
            for d in (_normalize_domain(x) or x for x in allowed_domains)
            if d
        )
    )
    if target_domain:
        td = _normalize_domain(target_domain) or target_domain
        if td not in allowed_domains:
            allowed_domains.insert(0, td)
        target_domain = td

    url_contains = _lines_list(
        cfg.get("target_url_contains") or cfg.get("url_contains") or cfg.get("url_contains_text")
    )
    regex_patterns = _lines_list(cfg.get("url_regexes") or cfg.get("url_regex_text"))
    one_rx = str(cfg.get("url_regex") or cfg.get("target_url_regex") or "").strip()
    if one_rx:
        regex_patterns.insert(0, one_rx)
    url_regex = _compile_regex_any(regex_patterns)

    # path-only regex (primary user intent: keyword → path regex click)
    path_rx_patterns = _lines_list(
        cfg.get("path_regexes")
        or cfg.get("path_regex_text")
        or cfg.get("link_path_regex")
        or cfg.get("link_path_regex_text")
    )
    one_path_rx = str(
        cfg.get("path_regex") or cfg.get("target_path_regex") or ""
    ).strip()
    if one_path_rx:
        path_rx_patterns.insert(0, one_path_rx)
    path_regexes = _compile_path_regexes(path_rx_patterns)
    path_regex: Optional[re.Pattern] = None  # always use list for multi-match
    # require match when user set any regex (default ON)
    require_regex = bool(
        cfg.get(
            "require_regex",
            cfg.get("require_url_regex", bool(url_regex or path_regexes)),
        )
    )

    title_contains = _lines_list(
        cfg.get("title_contains") or cfg.get("result_title_contains") or cfg.get("title_contains_text")
    )
    title_regex = _compile_regex(str(cfg.get("title_regex") or ""))

    # Google Ads 제목 옆 작은 path + 실제 URL path
    path_targets = _lines_list(
        cfg.get("path_targets")
        or cfg.get("target_paths")
        or cfg.get("path_include")
        or cfg.get("path_targets_text")
    )
    path_exclude = _lines_list(
        cfg.get("path_exclude")
        or cfg.get("exclude_paths")
        or cfg.get("path_block")
        or cfg.get("path_exclude_text")
    )

    # bulk scale sets (1k~100k own sites)
    paths_exact_set = set()
    for p in list(cfg.get("bulk_paths_exact") or []) + path_targets:
        pn = _normalize_path_pattern(str(p))
        if pn:
            paths_exact_set.add(pn)
    full_url_set = set()
    for u in list(cfg.get("bulk_full_urls") or []):
        full_url_set.add(str(u).lower().split("#")[0].rstrip("/"))
        bare = str(u).lower().split("://", 1)[-1].split("#")[0].rstrip("/")
        full_url_set.add(bare)
    domain_set = {
        _normalize_domain(d)
        for d in (
            list(cfg.get("_bulk_domain_set") or [])
            + list(cfg.get("allowed_domains") or [])
            + allowed_domains
        )
        if _normalize_domain(d)
    }
    require_path_or_regex = bool(
        cfg.get(
            "require_path_or_regex",
            bool(path_regexes or url_regex or paths_exact_set or full_url_set),
        )
    )
    if path_regexes or url_regex:
        require_regex = bool(cfg.get("require_regex", True))

    purpose = str(cfg.get("purpose") or cfg.get("mode") or "own_site_qa").strip().lower()
    own_site_qa = purpose in ("own_site_qa", "seo_qa", "cta_qa", "authorized", "multi_site", "")
    require_domain = bool(
        cfg.get("require_domain", True if allowed_domains else False)
    )
    if own_site_qa:
        require_domain = True
    skip_ads = bool(cfg.get("skip_ads", True))
    # path 타겟이 있으면 광고 결과도 필요할 수 있음 → 사용자가 명시 안 했으면 광고 허용 힌트만 로그
    if path_targets and skip_ads:
        # keep skip_ads as user set; just note it
        pass

    if not allowed_domains:
        raise RuntimeError(
            "search_flow: 자사 도메인 목록이 필요합니다. "
            "domains_text / allowed_domains 에 최대 수백 개까지 넣을 수 있습니다."
        )
    if not target_domain:
        target_domain = allowed_domains[0]

    search_url = str(cfg.get("search_url") or "https://www.google.com/").strip()
    max_pages = int(cfg.get("max_serp_pages") or cfg.get("max_pages") or 5)
    max_clicks = int(
        cfg.get("max_result_clicks")
        or cfg.get("clicks_per_keyword")
        or cfg.get("max_clicks_per_search")
        or 1  # 기본 1클릭 (프로필당 타겟 한 번)
    )
    # hard cap for "single click" mission mode
    if bool(cfg.get("single_click", True)):
        max_clicks = min(max_clicks, 1)
    max_keywords = int(cfg.get("max_keywords_per_job") or 0)  # 0 = all
    if max_keywords > 0:
        keywords = keywords[:max_keywords]

    human = dict(cfg.get("human") or {})
    human.setdefault("scroll", True)
    human.setdefault("mouse_wander", True)
    human.setdefault("read_pauses", True)
    human.setdefault("dwell_ms_min", int(cfg.get("dwell_ms_min") or 3500))
    human.setdefault("dwell_ms_max", int(cfg.get("dwell_ms_max") or 10000))
    human.setdefault("scroll_steps_min", 3)
    human.setdefault("scroll_steps_max", 7)
    human.setdefault("scroll_up_chance", 0.25)
    human.setdefault("random_internal_click", bool(cfg.get("random_internal_click", False)))
    human.setdefault("serp_scroll_min", 2)
    human.setdefault("serp_scroll_max", 6)
    # cookie inject on site landing (from run_browser_job side-channel)
    if cfg.get("_cookies_cfg"):
        human["_cookies_cfg"] = cfg.get("_cookies_cfg")
    # traffic tracker side-channel
    if cfg.get("_traffic") is not None:
        human["_traffic"] = cfg.get("_traffic")
    # OPS browser preset (swarm/hammer) — scales human timings
    if cfg.get("_ops_preset"):
        human["_ops_preset"] = cfg.get("_ops_preset")
        try:
            from .own_site_ops import resolve_ops_preset

            preset = resolve_ops_preset({"preset": (cfg.get("_ops_preset") or {}).get("name")})
            preset = dict(cfg.get("_ops_preset") or preset)
            scale = float(preset.get("dwell_scale") or 1.0)
            human["dwell_ms_min"] = max(400, int(human["dwell_ms_min"] * scale))
            human["dwell_ms_max"] = max(human["dwell_ms_min"] + 200, int(human["dwell_ms_max"] * scale))
            human["scroll_steps_min"] = int(human["scroll_steps_min"]) + int(
                preset.get("scroll_boost") or 0
            )
            human["scroll_steps_max"] = int(human["scroll_steps_max"]) + int(
                preset.get("scroll_boost") or 0
            )
            if preset.get("internal_click"):
                human["random_internal_click"] = True
            max_clicks = max_clicks + int(preset.get("max_clicks_boost") or 0)
            revisit = int(cfg.get("revisit_count") or 0) + int(
                preset.get("revisit_boost") or 0
            )
            cfg = dict(cfg)
            cfg["revisit_count"] = revisit
            log(
                f"[OPS] browser preset={preset.get('name')} "
                f"dwell_scale={scale} clicks={max_clicks} revisit={revisit}"
            )
        except Exception as exc:
            log(f"[OPS] preset 적용 경고: {exc}")
    out["cookies_set"] = 0

    banner_clicks = list(
        cfg.get("banner_clicks")
        or (cfg.get("on_site") or {}).get("clicks")
        or (cfg.get("on_site") or {}).get("banner_clicks")
        or cfg.get("clicks")
        or []
    )

    # 타겟 광고 클릭: path 타겟이 있고 사용자가 skip_ads 명시 안 했으면 광고 허용
    if path_targets and cfg.get("skip_ads") is None:
        skip_ads = False
        log("[SEARCH] path 타겟 있음 → 광고 결과 허용 (skip_ads 자동 OFF)")
    # prefer_ads / click_ads 옵션
    if cfg.get("prefer_ads") or cfg.get("click_ads") or cfg.get("target_ads"):
        skip_ads = False
        log("[SEARCH] 타겟 광고 모드 ON → 광고·스폰서 결과 포함")

    # 검색어 폴백: 앞 검색어에서 타겟 없으면 다음 검색어 (기본 ON)
    # keyword_fallback=False 이면 모든 검색어를 순회(기존 다중 점검)
    keyword_fallback = bool(cfg.get("keyword_fallback", True))
    # 폴백 성공 시 즉시 중단 (기본 True). False면 성공해도 다음 검색어 계속
    stop_on_first_hit = bool(cfg.get("stop_on_first_keyword_hit", keyword_fallback))

    out["keyword"] = keywords[0]
    log(
        f"[SEARCH] 자사 다중점검 시작 keywords={len(keywords)} domains={len(allowed_domains)} "
        f"클릭/검색어={max_clicks} SERP페이지={max_pages} skip_ads={skip_ads} "
        f"fallback={keyword_fallback}"
    )
    log(
        f"[SEARCH] 도메인 샘플: {', '.join(allowed_domains[:8])}"
        + (f" …(+{len(allowed_domains)-8})" if len(allowed_domains) > 8 else "")
    )
    log(
        f"[SEARCH] ★ 검색어 목록({len(keywords)}개): "
        + " / ".join(f"'{k}'" for k in keywords[:15])
        + (f" …(+{len(keywords)-15})" if len(keywords) > 15 else "")
    )
    if len(keywords) > 1 and keyword_fallback:
        log(
            "[SEARCH] ★ 폴백: 앞 검색어에 타겟(광고/자사) 없으면 → 다음 검색어 자동 전환"
        )
    if url_regex:
        log(f"[SEARCH] URL 정규식: {url_regex.pattern[:120]}")
    if path_regexes:
        log(
            f"[SEARCH] ★ path/링크 정규식 {len(path_regexes)}개 "
            f"(require_regex={require_regex}): "
            + " | ".join(rx.pattern for rx in path_regexes[:6])
        )
    if domain_set:
        log(f"[SEARCH] ★ 자사 도메인 set {len(domain_set)}개 (대량 매칭 O(1))")
    if paths_exact_set:
        log(f"[SEARCH] ★ path 정확목록 {len(paths_exact_set)}개")
    if full_url_set:
        log(f"[SEARCH] ★ 전체 URL 목록 ~{len(full_url_set)} entries")
    if url_contains:
        log(f"[SEARCH] URL 특징: {url_contains[:12]}")
    if path_targets:
        log(f"[SEARCH] path 타겟(이 path만): {path_targets}")
        if skip_ads:
            log(
                "[SEARCH] 안내: path 타겟은 광고에도 쓰입니다. "
                "광고를 치려면 '광고 스킵' 을 OFF 하세요."
            )
    if path_exclude:
        log(f"[SEARCH] path 제외(클릭 안 함): {path_exclude}")

    if browser is None:
        try:
            browser = page.context.browser  # type: ignore[assignment]
        except Exception:
            browser = None

    if cfg.get("warmup", True):
        page = await _warmup_via_proxy(page, browser, log)
    else:
        page = await ensure_live_page(page, browser, log)

    all_visited: List[str] = []
    total_banners = 0
    any_ok = False
    errors: List[str] = []
    winning_keyword = ""
    all_evidence: List[Dict[str, Any]] = []

    for ki, keyword in enumerate(keywords, start=1):
        if hasattr(log, "set_keyword"):
            try:
                log.set_keyword(keyword)  # type: ignore[attr-defined]
            except Exception:
                pass
        log("")
        log(f"[SEARCH] ######## 검색어 {ki}/{len(keywords)} ########")
        log(f"[SEARCH] ★★★ 지금 검색할 단어: '{keyword}' ★★★")
        if ki > 1 and keyword_fallback:
            log(
                f"[SEARCH] 폴백 {ki}/{len(keywords)} — 이전 검색어에 타겟 없음 → '{keyword}' 재검색"
            )
        try:
            page = await ensure_live_page(page, browser, log)
            page = await _google_search(
                page, keyword, search_url=search_url, log=log, browser=browser
            )
            log(f"[SEARCH] SERP 도착 url={page.url}")
            batch = await _find_and_click_many_own_sites(
                page,
                keyword=keyword,
                search_url=search_url,
                target_domain=target_domain,
                allowed_domains=allowed_domains,
                url_contains=url_contains,
                url_regex=url_regex,
                title_contains=title_contains,
                title_regex=title_regex,
                require_domain=require_domain,
                skip_ads=skip_ads,
                max_pages=max_pages,
                max_clicks=max_clicks,
                banner_clicks=banner_clicks,
                human=human,
                log=log,
                browser=browser,
                path_targets=path_targets,
                path_exclude=path_exclude,
                path_regex=path_regex,
                path_regexes=path_regexes,
                require_regex=require_regex,
                domain_set=domain_set,
                paths_exact_set=paths_exact_set or None,
                full_url_set=full_url_set or None,
                require_path_or_regex=require_path_or_regex,
            )
            clicks = int(batch.get("clicks") or 0)
            visited = list(batch.get("visited") or [])
            total_banners += int(batch.get("banner_clicks") or 0)
            all_visited.extend(visited)
            for ev in list(batch.get("click_evidence") or []):
                if isinstance(ev, dict):
                    all_evidence.append(ev)
            out["keywords_done"].append(
                {"keyword": keyword, "clicks": clicks, "visited": visited}
            )
            if clicks > 0:
                any_ok = True
                out["visits"] += clicks
                winning_keyword = keyword
                out["keyword"] = keyword
                log(
                    f"[SEARCH] 검색어 '{keyword}' 완료 — 타겟 클릭 {clicks}건"
                    + (" (폴백 성공)" if ki > 1 else "")
                )
                if stop_on_first_hit:
                    if ki < len(keywords):
                        log(
                            f"[SEARCH] ★ 타겟 발견 → 남은 검색어 "
                            f"{len(keywords) - ki}개 생략 (폴백 종료)"
                        )
                    break
            else:
                msg = (
                    f"검색어 '{keyword}' — 타겟(광고/자사) 결과 없음 "
                    f"(도메인 {len(allowed_domains)}개 · skip_ads={skip_ads})"
                )
                log(f"[SEARCH] {msg}")
                if keyword_fallback and ki < len(keywords):
                    log(
                        f"[SEARCH] → 다음 검색어로 폴백: '{keywords[ki]}'"
                    )
                errors.append(msg)
        except Exception as exc:
            log(f"[SEARCH] 검색어 '{keyword}' 오류: {exc}")
            errors.append(f"{keyword}: {exc}")
            # 탭 닫힘 등 치명 오류면 복구 후 다음 검색어 시도
            try:
                page = await ensure_live_page(page, browser, log)
            except Exception as rec_exc:
                log(f"[SEARCH] 탭 복구 실패: {rec_exc}")
                break

    out["search_ok"] = any_ok
    out["site_clicks"] = total_banners
    out["matched_urls"] = all_visited
    out["matched_url"] = all_visited[0] if all_visited else ""
    out["click_evidence"] = all_evidence
    verified_any = any(bool(e.get("clicked") or e.get("ok")) for e in all_evidence)
    out["click_verified"] = verified_any or any_ok
    if all_evidence:
        last_ok = next(
            (e for e in reversed(all_evidence) if e.get("clicked") or e.get("ok")),
            all_evidence[-1],
        )
        out["final_url"] = str(last_ok.get("final_url") or out["matched_url"] or "")
        out["is_ad"] = bool(last_ok.get("is_ad"))
        out["click_method"] = str(last_ok.get("method") or "")
        if out["final_url"]:
            out["matched_url"] = out["final_url"]
    if winning_keyword:
        out["keyword"] = winning_keyword
    if hasattr(log, "set_matched_url") and out["matched_url"]:
        try:
            log.set_matched_url(out["matched_url"])  # type: ignore[attr-defined]
        except Exception:
            pass
    if hasattr(log, "audit"):
        try:
            log.audit(  # type: ignore[attr-defined]
                f"증거합계 evidence={len(all_evidence)} verified={out['click_verified']} "
                f"keyword='{out.get('keyword')}' final={out.get('final_url') or '-'}"
            )
        except Exception:
            pass

    if not any_ok:
        out["error"] = (
            errors[0]
            if errors
            else f"타겟 검색 결과 0건 (시도 검색어 {len(keywords)}개)"
        )
        log(f"[SEARCH] 실패: {out['error']}")
        if len(keywords) > 1:
            log(
                "[SEARCH] 모든 폴백 검색어에서 타겟을 찾지 못했습니다. "
                "도메인·광고스킵·path타겟 설정을 확인하세요."
            )
        raise RuntimeError(out["error"])

    log(
        f"[SEARCH] DONE ok={out['search_ok']} visits={out['visits']} "
        f"cta={out['site_clicks']} keywords_tried={len(out['keywords_done'])} "
        f"click_verified={out['click_verified']} evidence={len(all_evidence)} "
        f"hit='{winning_keyword}' urls={len(all_visited)}"
    )
    for u in all_visited[:20]:
        log(f"[SEARCH]   방문: {u}")
    if len(all_visited) > 20:
        log(f"[SEARCH]   … 외 {len(all_visited)-20}건")
    return out


async def run_browser_job(
    ws_endpoint: str,
    debug_port: Optional[str],
    *,
    google_cfg: Dict[str, Any],
    email: str,
    password: str,
    targets: List[Dict[str, Any]],
    search_flow: Optional[Dict[str, Any]] = None,
    cookies_cfg: Optional[Dict[str, Any]] = None,
    log=print,
    ask_2fa: Optional[Ask2FAFn] = None,
    job_meta: Optional[Dict[str, Any]] = None,
    engine: str = "octo",
    proxy: Optional[Dict[str, Any]] = None,
    headless: bool = True,
) -> Dict[str, Any]:
    session = BrowserSession(
        ws_endpoint or "",
        debug_port,
        engine=engine,
        proxy=proxy,
        headless=headless,
    )
    meta = dict(job_meta or {})
    ck_cfg = dict(cookies_cfg or {})
    result: Dict[str, Any] = {
        "google_ok": False,
        "targets_ok": False,
        "search_ok": False,
        "search_visits": 0,
        "site_clicks": 0,
        "keyword": "",
        "matched_url": "",
        "detected_ip": meta.get("proxy_ip") or "",
        "cookies_set": 0,
        "error": "",
        "job_meta": meta,
        "traffic": {},
        "engine": engine,
    }
    traffic = None
    try:
        page = await session.connect()
        eng = session.engine
        if eng in ("playwright", "pw", "chromium", "server"):
            log(
                f"[브라우저] Playwright 서버 엔진 시작 OK · "
                f"proxy={meta.get('proxy') or '-'} headless={headless} 현재={page.url}"
            )
            log(
                f"[흐름] Ubuntu/VPS 자동화 · 프록시 경유 Chromium · "
                f"proxy_host={meta.get('proxy_host') or '-'} "
                f"(Octo Local 불가 시 서버 엔진)"
            )
        else:
            log(
                f"[브라우저] CDP 연결 OK  profile={meta.get('profile') or '-'} "
                f"proxy={meta.get('proxy') or '-'}  현재={page.url}"
            )
            log(
                f"[흐름] Octo 자동화 시작 · uuid={str(meta.get('uuid') or '')[:8] or '-'}… "
                f"proxy_host={meta.get('proxy_host') or '-'} known_ip={meta.get('proxy_ip') or 'n/a'} "
                f"os={meta.get('profile_os') or '-'} mobile_fp={meta.get('mobile_fp')}"
            )

        # Real traffic metrics (request/response counts per phase & click)
        try:
            from .traffic_metrics import TrafficTracker

            allowed = list(meta.get("allowed_hosts") or [])
            traffic = TrafficTracker(
                allowed_hosts=allowed,
                log=log if callable(log) else print,
                enabled=bool(meta.get("traffic_metrics", True)),
            )
            await traffic.attach(page)
        except Exception as tr_exc:
            log(f"[TRAFFIC] 초기화 스킵: {tr_exc}")
            traffic = None

        # Cookie inject right after Octo CDP connect
        if traffic:
            traffic.begin_phase("after_connect")
        n_ck = await inject_cookies(page, ck_cfg, log=log, phase="after_connect")
        if n_ck:
            result["cookies_set"] = int(result.get("cookies_set") or 0) + n_ck
            page = await session.live_page(log)

        # Confirm exit IP in a side tab (does not kill main tab)
        if traffic:
            traffic.begin_phase("exit_ip_check")
        if not result["detected_ip"] or str(result["detected_ip"]) in (
            "",
            "pending",
            "n/a",
            "미확인",
        ):
            try:
                page = await session.live_page(log)
                ip = await _detect_exit_ip(page, log)
                if ip:
                    result["detected_ip"] = ip
                    if hasattr(log, "set_proxy_ip"):
                        try:
                            log.set_proxy_ip(ip)  # type: ignore[attr-defined]
                        except Exception:
                            pass
                page = await session.live_page(log)
            except Exception as exc:
                log(f"[프록시] 출구 IP 확인 스킵: {exc}")
                page = await session.live_page(log)

        # Session-cookie mode: skip Google login (own-site session test)
        cookie_when = _normalize_cookie_when(str(ck_cfg.get("when") or ""))
        skip_google_for_cookies = bool(
            ck_cfg.get("enabled") and cookie_when == "replace_login"
        )
        gcfg = dict(google_cfg or {})
        if skip_google_for_cookies:
            gcfg["enabled"] = False
            log("[쿠키] replace_login — Google 로그인 스킵, 주입 세션으로 진행")

        if gcfg.get("enabled", True):
            log(f"[흐름] Google 로그인 mode={gcfg.get('mode')} email={email or '-'}")
            if not (email or "").strip() and str(gcfg.get("mode") or "auto").lower() in (
                "auto",
                "autofill",
            ):
                log(
                    "[흐름] ⚠ 이메일 비어 있음 — 자동 로그인 불가. "
                    "홈 ② 계정(email|비밀번호|2FA) 확인 후 다시 시작하세요."
                )
            otp_cfg = dict(gcfg.get("otp_fetch") or gcfg.get("otp") or {})
            if not otp_cfg.get("url") and meta.get("otp_url"):
                otp_cfg["url"] = meta.get("otp_url")
            if not otp_cfg.get("secret") and meta.get("otp_secret"):
                otp_cfg["secret"] = meta.get("otp_secret")
            page = await session.live_page(log)
            if traffic:
                traffic.begin_phase("google_login")
                traffic.mark_click("google_login", url=str(gcfg.get("login_url") or ""))
            result["google_ok"] = await google_login(
                page,
                mode=str(gcfg.get("mode") or "auto"),
                email=email,
                password=password,
                login_url=str(gcfg.get("login_url") or "https://accounts.google.com/"),
                success_url_contains=list(gcfg.get("success_url_contains") or []),
                manual_wait_sec=int(gcfg.get("manual_wait_sec") or 300),
                autofill_pause_ms=int(gcfg.get("autofill_pause_ms") or 350),
                ask_2fa=ask_2fa,
                otp=otp_cfg,
                log=log,
            )
            if traffic:
                traffic.end_click()
                traffic.log_phase_summary()
            log(f"[흐름] Google 로그인 결과 ok={result['google_ok']}")
            # 로그인 후 탭이 닫히거나 전환된 경우 복구 (원클릭 검색 단계 실패 방지)
            try:
                page = await session.live_page(log)
            except Exception as rec_exc:
                log(f"[흐름] 로그인 후 탭 복구: {rec_exc}")
                page = await session.live_page(log)
        else:
            result["google_ok"] = True
            log("[흐름] Google 로그인 단계 끔 (skip)")

        sf = search_flow or {}
        if sf.get("enabled"):
            if traffic:
                traffic.begin_phase("search_flow")
                # refresh allowed hosts from search_flow
                hosts = list(meta.get("allowed_hosts") or [])
                td = str(sf.get("target_domain") or "").strip()
                if td and td not in hosts:
                    hosts.insert(0, td)
                for d in sf.get("allowed_domains") or []:
                    if d and d not in hosts:
                        hosts.append(str(d))
                traffic.set_allowed_hosts(hosts)
            n_ck2 = await inject_cookies(page, ck_cfg, log=log, phase="before_search")
            if n_ck2:
                result["cookies_set"] = int(result.get("cookies_set") or 0) + n_ck2
            log(
                f"[SEARCH] 시작 keyword='{sf.get('keyword') or '-'}' "
                f"keywords={sf.get('keywords') or sf.get('keywords_text') or '-'} "
                f"domain={sf.get('target_domain') or '-'} "
                f"url_contains={sf.get('target_url_contains') or '-'} "
                f"regex={sf.get('url_regex') or '-'} "
                f"revisit={sf.get('revisit_count', 0)}"
            )
            page = await session.live_page(log)
            # pass cookies + OPS preset + traffic + job index into search flow
            sf_run = dict(sf)
            sf_run["_cookies_cfg"] = ck_cfg
            sf_run["_job_index"] = int(meta.get("job_index") or 0)
            if meta.get("ops_preset"):
                sf_run["_ops_preset"] = meta.get("ops_preset")
            elif ck_cfg.get("_ops_preset"):
                sf_run["_ops_preset"] = ck_cfg.get("_ops_preset")
            if traffic is not None:
                sf_run["_traffic"] = traffic
            # security / single-click mission defaults (override only if unset)
            sf_run.setdefault("single_click", True)
            sf_run.setdefault("keyword_rotate", True)
            sf_run.setdefault("keyword_shuffle", True)
            sf_run.setdefault("stop_on_first_keyword_hit", True)
            sf_run.setdefault("max_result_clicks", 1)
            search_result = await run_search_flow(
                page, sf_run, log=log, browser=session.browser
            )
            if search_result.get("cookies_set"):
                result["cookies_set"] = int(result.get("cookies_set") or 0) + int(
                    search_result.get("cookies_set") or 0
                )
            result["search_ok"] = bool(search_result.get("search_ok"))
            result["search_visits"] = int(search_result.get("visits") or 0)
            result["site_clicks"] = int(search_result.get("site_clicks") or 0)
            result["keyword"] = str(search_result.get("keyword") or sf.get("keyword") or "")
            result["matched_url"] = str(search_result.get("matched_url") or "")
            result["final_url"] = str(search_result.get("final_url") or result["matched_url"] or "")
            result["click_verified"] = bool(search_result.get("click_verified"))
            result["click_evidence"] = list(search_result.get("click_evidence") or [])
            result["is_ad"] = bool(search_result.get("is_ad"))
            result["click_method"] = str(search_result.get("click_method") or "")
            if search_result.get("error") and not result["search_ok"]:
                result["error"] = str(search_result["error"])
            result["targets_ok"] = result["search_ok"]
            if traffic:
                traffic.log_phase_summary()
            log(
                f"[SEARCH] 종료 ok={result['search_ok']} verified={result['click_verified']} "
                f"visits={result['search_visits']} banner={result['site_clicks']} "
                f"matched={result['matched_url'] or '-'} method={result['click_method'] or '-'} "
                f"ad={result['is_ad']} evidence={len(result['click_evidence'])}"
            )
            if hasattr(log, "audit"):
                try:
                    log.audit(  # type: ignore[attr-defined]
                        f"잡종료 실제클릭={result['click_verified']} "
                        f"IP={result.get('detected_ip') or '-'} "
                        f"URL={result.get('final_url') or result.get('matched_url') or '-'}"
                    )
                except Exception:
                    pass
        elif targets:
            if traffic:
                traffic.begin_phase("direct_targets")
            log(f"[흐름] 직접 URL 모드 targets={len(targets)}")
            await run_targets(page, targets, log=log)
            result["targets_ok"] = True
            result["matched_url"] = page.url
        else:
            # 로그인만 테스트하는 베타 단계 허용
            if google_cfg.get("enabled", True) and result.get("google_ok"):
                log("[흐름] 검색/URL 없음 — 로그인 단계만 완료 (베타 테스트 모드)")
                result["targets_ok"] = True
            else:
                raise RuntimeError(
                    "실행할 작업이 없습니다. 검색 흐름(search_flow) 또는 직접 URL(targets)을 설정하세요."
                )

        if sf.get("enabled") and targets and sf.get("also_run_targets"):
            log("[흐름] also_run_targets — 추가 URL 실행")
            await run_targets(page, targets, log=log)

        if traffic:
            result["traffic"] = traffic.log_full_summary()
        log(
            f"[흐름] 브라우저 잡 완료 matched={result.get('matched_url') or '-'} "
            f"ip={result.get('detected_ip') or '-'} "
            f"traffic_req={(result.get('traffic') or {}).get('total_requests', '-')}"
        )
        return result
    except Exception as exc:
        result["error"] = str(exc)
        log(f"[브라우저] 오류: {exc}")
        if traffic:
            try:
                result["traffic"] = traffic.summary()
            except Exception:
                pass
        return result
    finally:
        await session.close()
        if session.engine in ("playwright", "pw", "chromium", "server"):
            log("[브라우저] Playwright 엔진 종료")
        else:
            log("[브라우저] CDP 연결 해제 (Octo 창은 Local Stop 에서 종료)")


def run_browser_job_sync(*args, **kwargs) -> Dict[str, Any]:
    return asyncio.run(run_browser_job(*args, **kwargs))
