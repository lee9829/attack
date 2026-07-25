from __future__ import annotations

import csv
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .automation import Ask2FAFn, run_browser_job_sync
from .logutil import JobLog
from .octo_client import OctoClient, OctoError
from .proxy_manager import Proxy, ProxyRotator, load_proxies, parse_proxy_text


LogFn = Callable[[str], None]
CancelFn = Callable[[], bool]


@dataclass
class AccountJob:
    email: str
    password: str
    profile_title: str
    notes: str = ""
    otp_url: str = ""
    otp_selector: str = ""
    otp_secret: str = ""


def parse_account_pipe_line(line: str, *, index: int = 1) -> Optional[Dict[str, str]]:
    """
    One-line Google account paste formats (Tobi / shop style):

      email|password|2fa_secret
      email|password|2fa_secret|notes
      email:password:2fa_secret
      email password 2fa_secret   (whitespace, 3+ tokens)

    2FA secret = Google Authenticator / 2fa-auth.com base32 key.
    Returns dict keys: email, password, profile_title, otp_secret, otp_url, notes
    """
    raw = (line or "").strip()
    if not raw or raw.startswith("#"):
        return None
    # strip common chat prefixes
    for prefix in ("tobi:", "account:", "acc:", "gmail:"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix) :].strip()

    parts: List[str] = []
    if "|" in raw:
        parts = [p.strip() for p in raw.split("|")]
    elif raw.count(":") >= 2 and "@" in raw.split(":")[0]:
        # email:pass:secret  (careful: password may contain :)
        # split only first two colons from left for email, rest join then last = secret
        first = raw.find(":")
        second = raw.find(":", first + 1)
        if first > 0 and second > first:
            parts = [
                raw[:first].strip(),
                raw[first + 1 : second].strip(),
                raw[second + 1 :].strip(),
            ]
    else:
        # whitespace split if looks like email + 2 more tokens
        toks = raw.split()
        if len(toks) >= 3 and "@" in toks[0]:
            parts = [toks[0], toks[1], " ".join(toks[2:])]

    parts = [p for p in parts if p is not None]
    if len(parts) < 2:
        return None

    email = parts[0].strip()
    password = parts[1].strip() if len(parts) > 1 else ""
    secret = ""
    notes = "pipe-paste"

    if len(parts) >= 3:
        # secret is usually 3rd field; if more fields, pick longest base32-looking
        candidates = [p.strip() for p in parts[2:]]
        best = ""
        for c in candidates:
            # skip cookie-looking blobs
            if "c_user=" in c.lower() or c.lower().startswith("xs="):
                continue
            if "@" in c and "." in c:
                continue
            # base32-ish
            cleaned = re.sub(r"[\s\-]", "", c).upper()
            cleaned = re.sub(r"[^A-Z2-7=]", "", cleaned)
            if len(cleaned) >= 16 and len(cleaned) >= len(best):
                best = cleaned
        if best:
            secret = best.rstrip("=")
        else:
            # fallback: 3rd raw field as secret
            secret = re.sub(r"[\s\-]", "", parts[2]).upper()
            secret = re.sub(r"[^A-Z2-7=]", "", secret).rstrip("=")
        if len(parts) >= 4 and not any("c_user" in p.lower() for p in parts[3:]):
            notes = parts[-1][:80] if parts[-1] != parts[2] else notes

    # email sanity: allow missing @ only if profile-only — skip empty
    if not email:
        return None
    # if first field is not email but secret-like only, reject
    if "@" not in email and len(parts) < 3:
        return None

    local = email.split("@")[0] if "@" in email else email
    local = re.sub(r"[^a-zA-Z0-9._-]", "", local)[:24] or f"user{index}"
    title = f"g-{local}-{index}"

    return {
        "email": email,
        "password": password,
        "profile_title": title,
        "otp_secret": secret,
        "otp_url": "https://2fa-auth.com/" if secret else "",
        "notes": notes,
    }


def parse_account_bulk_text(text: str) -> List[Dict[str, str]]:
    """Parse multi-line email|password|secret paste into account rows."""
    rows: List[Dict[str, str]] = []
    seen_email: set[str] = set()
    idx = 1
    for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        row = parse_account_pipe_line(line, index=idx)
        if not row:
            continue
        key = row["email"].lower()
        if key in seen_email:
            # keep latest password/secret
            for i, old in enumerate(rows):
                if old["email"].lower() == key:
                    row["profile_title"] = old.get("profile_title") or row["profile_title"]
                    rows[i] = row
                    break
            continue
        seen_email.add(key)
        rows.append(row)
        idx += 1
    return rows


def validate_account_secrets(rows: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Check that otp_secret fields produce valid TOTP codes.
    Returns {ok, total, with_secret, valid_secret, invalid:[{email,reason}], preview:[{email,code}]}
    """
    from .automation import generate_totp, extract_totp_secret

    total = len(rows)
    with_secret = 0
    valid = 0
    invalid: List[Dict[str, str]] = []
    preview: List[Dict[str, str]] = []
    for r in rows:
        email = str(r.get("email") or "")
        secret = str(r.get("otp_secret") or r.get("secret") or "").strip()
        if not secret:
            continue
        with_secret += 1
        cleaned = extract_totp_secret(secret) or secret
        code = generate_totp(cleaned)
        if code:
            valid += 1
            r["otp_secret"] = cleaned  # normalize in-place
            preview.append({"email": email, "code": code})
        else:
            invalid.append(
                {
                    "email": email,
                    "reason": "시크릿이 TOTP로 해석되지 않음 (base32 키 확인)",
                }
            )
    return {
        "ok": with_secret == 0 or (valid == with_secret and not invalid),
        "total": total,
        "with_secret": with_secret,
        "valid_secret": valid,
        "invalid": invalid,
        "preview": preview[:5],
    }


def load_config(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"설정 파일이 없습니다: {p}")
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def save_config(path: str | Path, config: Dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def load_accounts(path: str | Path) -> List[AccountJob]:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"계정 파일이 없습니다: {p}")

    jobs: List[AccountJob] = []
    with p.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row:
                continue
            email = (row.get("email") or "").strip()
            password = (row.get("password") or "").strip()
            title = (row.get("profile_title") or "").strip()
            notes = (row.get("notes") or "").strip()
            otp_url = (row.get("otp_url") or row.get("otp_site") or "").strip()
            otp_selector = (row.get("otp_selector") or "").strip()
            otp_secret = (
                row.get("otp_secret")
                or row.get("totp_secret")
                or row.get("secret")
                or row.get("twofa_secret")
                or ""
            ).strip()
            if not email and not title:
                continue
            if not title:
                title = f"auto-google-{email.split('@')[0] if email else 'user'}"
            jobs.append(
                AccountJob(
                    email=email,
                    password=password,
                    profile_title=title,
                    notes=notes,
                    otp_url=otp_url,
                    otp_selector=otp_selector,
                    otp_secret=otp_secret,
                )
            )
    if not jobs:
        raise ValueError("accounts.csv 에 유효한 행이 없습니다.")
    return jobs


def accounts_from_rows(rows: List[Dict[str, str]]) -> List[AccountJob]:
    jobs: List[AccountJob] = []
    for row in rows:
        email = (row.get("email") or "").strip()
        password = (row.get("password") or "").strip()
        title = (row.get("profile_title") or "").strip()
        notes = (row.get("notes") or "").strip()
        otp_url = (row.get("otp_url") or row.get("otp_site") or "").strip()
        otp_selector = (row.get("otp_selector") or "").strip()
        otp_secret = (
            row.get("otp_secret")
            or row.get("totp_secret")
            or row.get("secret")
            or row.get("twofa_secret")
            or ""
        ).strip()
        if not email and not title:
            continue
        # 빈 샘플 행(이메일 없고 기본 프로필만) 스킵 — 자동로그인 방해 방지
        if (
            not email
            and not password
            and not otp_secret
            and title in ("auto-google-1", "auto-google-user", "sample")
        ):
            continue
        if not title:
            title = f"auto-google-{email.split('@')[0] if email else 'user'}"
        jobs.append(
            AccountJob(
                email=email,
                password=password,
                profile_title=title,
                notes=notes,
                otp_url=otp_url,
                otp_selector=otp_selector,
                otp_secret=otp_secret,
            )
        )
    if not jobs:
        raise ValueError(
            "계정이 없습니다. 홈 ② 에 email|비밀번호|2FA시크릿 을 붙여넣으세요."
        )
    return jobs


def default_log(msg: str) -> None:
    print(msg, flush=True)


class JobRunner:
    def __init__(
        self,
        config: Dict[str, Any],
        base_dir: Path,
        *,
        proxies: Optional[List[Proxy]] = None,
        accounts: Optional[List[AccountJob]] = None,
        log: Optional[LogFn] = None,
        should_cancel: Optional[CancelFn] = None,
        proxy_start_index: int = 0,
        ask_2fa: Optional[Ask2FAFn] = None,
        on_job_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.config = config
        self.base_dir = base_dir
        self.should_cancel = should_cancel or (lambda: False)
        self.ask_2fa = ask_2fa
        self.on_job_progress = on_job_progress
        self.started_uuids: List[str] = []
        self._uuid_lock = threading.Lock()
        self._log_lock = threading.Lock()
        self._active_jobs: Dict[int, Dict[str, Any]] = {}
        self._active_lock = threading.Lock()

        # dual log: GUI/console + rotating session file under logs/
        user_log = log or default_log
        logs_dir = base_dir / "logs"
        try:
            logs_dir.mkdir(parents=True, exist_ok=True)
            from datetime import datetime as _dt

            self._log_path = logs_dir / f"session_{_dt.now().strftime('%Y%m%d_%H%M%S')}.log"
            self._log_fp = self._log_path.open("a", encoding="utf-8")
        except Exception:
            self._log_path = None
            self._log_fp = None

        def _dual(msg: str) -> None:
            with self._log_lock:
                try:
                    user_log(msg)
                except Exception:
                    print(msg, flush=True)
                if self._log_fp:
                    try:
                        self._log_fp.write(msg + "\n")
                        self._log_fp.flush()
                    except Exception:
                        pass

        self.log = _dual
        if self._log_path:
            self.log(f"[INFO] 세션 로그 파일: {self._log_path}")

        token = str(config.get("octo_api_token") or "").strip()
        if not token or "여기에" in token or token.lower() in ("your_token", "changeme"):
            raise ValueError("Octo API 토큰을 설정하세요.")

        self.client = OctoClient(
            api_token=token,
            cloud_base=str(
                config.get("cloud_base")
                or "https://app.octobrowser.net/api/v2/automation"
            ),
            local_base=str(config.get("local_base") or "http://127.0.0.1:58888/api"),
        )

        proxy_type = str(config.get("proxy_type") or "http")
        if proxies is not None:
            self.proxies = list(proxies)
        elif config.get("proxies_text"):
            self.proxies, errs = parse_proxy_text(
                str(config["proxies_text"]), default_type=proxy_type
            )
            if errs:
                self.log(f"[Proxy] 형식 오류 {len(errs)}건 (무시된 줄 있음)")
        else:
            proxies_path = base_dir / str(config.get("proxies_file") or "proxies.txt")
            self.proxies = load_proxies(proxies_path, default_type=proxy_type)

        if not self.proxies:
            raise ValueError("유효한 프록시가 없습니다. 프록시를 붙여넣고 검증하세요.")

        mode = str(config.get("proxy_mode") or "round_robin")
        start_idx = int(config.get("proxy_start_index", proxy_start_index) or 0)
        self.rotator = ProxyRotator(self.proxies, start_index=start_idx, mode=mode)

        if accounts is not None:
            self.accounts = list(accounts)
        elif config.get("accounts_rows"):
            self.accounts = accounts_from_rows(list(config["accounts_rows"]))
        else:
            accounts_path = base_dir / str(config.get("accounts_file") or "accounts.csv")
            self.accounts = load_accounts(accounts_path)

        self.targets = list(config.get("targets") or [])
        self.search_flow = dict(config.get("search_flow") or {})
        # Merge domains.txt / keywords.txt if present (for 100~300+ own sites)
        self._merge_list_files()
        sf_on = bool(self.search_flow.get("enabled"))
        has_kw = bool(
            str(self.search_flow.get("keyword") or "").strip()
            or any(str(k).strip() for k in (self.search_flow.get("keywords") or []))
            or str(self.search_flow.get("keywords_text") or "").strip()
        )
        has_match = bool(
            str(
                self.search_flow.get("target_domain")
                or self.search_flow.get("own_domain")
                or ""
            ).strip()
            or (self.search_flow.get("allowed_domains") or [])
            or str(self.search_flow.get("domains_text") or "").strip()
            or (self.search_flow.get("target_url_contains") or [])
            or str(self.search_flow.get("url_regex") or "").strip()
            or str(self.search_flow.get("url_regex_text") or "").strip()
            or str(self.search_flow.get("path_regex") or "").strip()
            or bool(self.search_flow.get("path_regexes"))
            or bool(self.search_flow.get("bulk_full_urls"))
            or bool(self.search_flow.get("path_targets"))
        )
        if not self.targets and not (sf_on and has_kw and has_match):
            raise ValueError(
                "검색어 + 자사 도메인/URL 목록(domains.txt · urls.txt · 웹 대량붙여넣기)을 설정하세요."
            )

    def _merge_list_files(self) -> None:
        """Load bulk own-site lists from text files into search_flow (1k~100k+)."""
        sf = self.search_flow

        def read_lines(path: Path) -> List[str]:
            if not path.is_file():
                return []
            raw = None
            for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
                try:
                    raw = path.read_text(encoding=enc)
                    break
                except UnicodeDecodeError:
                    continue
            if raw is None:
                raw = path.read_text(encoding="utf-8", errors="replace")
            out: List[str] = []
            for line in raw.splitlines():
                s = line.strip()
                if s and not s.startswith("#"):
                    out.append(s)
            return out

        # Mass URL/domain/path files (defense-scale own sites)
        try:
            from .bulk_targets import (
                apply_bulk_to_search_flow,
                load_bulk_files,
                parse_bulk_text,
            )

            bulk = load_bulk_files(self.base_dir)
            # also merge UI paste fields
            paste = str(
                sf.get("bulk_urls_text")
                or self.config.get("bulk_urls_text")
                or ""
            ).strip()
            if paste:
                from .bulk_targets import parse_bulk_text as _pbt

                pasted = _pbt(paste)
                # merge paste into bulk
                for d in pasted.domains:
                    if d not in bulk.domain_set:
                        bulk.domains.append(d)
                        bulk.domain_set.add(d)
                bulk.paths_exact |= pasted.paths_exact
                for r in pasted.path_regexes:
                    if r not in bulk.path_regexes:
                        bulk.path_regexes.append(r)
                for u in pasted.full_urls:
                    ul = u.lower().rstrip("/")
                    if ul not in bulk.full_url_set:
                        bulk.full_url_set.add(ul)
                        bulk.full_urls.append(u)
                bulk.raw_count += pasted.raw_count

            if bulk.domains or bulk.full_urls or bulk.paths_exact or bulk.path_regexes:
                sf = apply_bulk_to_search_flow(sf, bulk, merge=True)
                self.search_flow = sf
                st = bulk.stats()
                self.log(
                    f"[Init] ★ 대량 타겟 로드 domains={st['domains']} "
                    f"urls={st['full_urls']} paths={st['paths_exact']} "
                    f"regex={st['path_regexes']} (자사 방어 테스트용)"
                )
        except Exception as exc:
            self.log(f"[Init] bulk 타겟 로드 경고: {exc}")

        dom_file = self.base_dir / str(
            sf.get("domains_file") or self.config.get("domains_file") or "domains.txt"
        )
        kw_file = self.base_dir / str(
            sf.get("keywords_file") or self.config.get("keywords_file") or "keywords.txt"
        )
        file_domains = read_lines(dom_file)
        file_kws = read_lines(kw_file)

        # Domains: merge file into config
        if file_domains:
            existing = list(sf.get("allowed_domains") or [])
            text = str(sf.get("domains_text") or "")
            # parse domain-only or URL lines
            cleaned = []
            for x in file_domains:
                if "://" in x or "/" in x:
                    try:
                        from .bulk_targets import parse_bulk_line

                        it = parse_bulk_line(x)
                        if it and it.get("host"):
                            cleaned.append(it["host"])
                        continue
                    except Exception:
                        pass
                cleaned.append(x)
            merged = existing + [x for x in cleaned if x not in existing]
            # unique preserve order
            seen = set()
            uniq = []
            for d in merged:
                dd = str(d).strip().lower()
                if dd.startswith("www."):
                    dd = dd[4:]
                if dd and dd not in seen:
                    seen.add(dd)
                    uniq.append(dd)
            sf["allowed_domains"] = uniq
            if not text.strip():
                sf["domains_text"] = "\n".join(uniq[:100])
            self.log(f"[Init] domains.txt 로드: {len(uniq)}개")

        # Keywords: GUI/config wins. Only use keywords.txt if config has no real keywords.
        cfg_kws = []
        for src in (
            sf.get("keywords_text"),
            sf.get("keywords"),
            sf.get("keyword"),
        ):
            if not src:
                continue
            if isinstance(src, list):
                cfg_kws.extend(str(x).strip() for x in src if str(x).strip())
            else:
                for ln in str(src).replace("\r", "\n").split("\n"):
                    ln = ln.strip()
                    if ln and not ln.startswith("#"):
                        cfg_kws.append(ln)
        # unique
        seen = set()
        cfg_unique = []
        for k in cfg_kws:
            if k not in seen:
                seen.add(k)
                cfg_unique.append(k)

        placeholders = {
            "example-mybrand",
            "my brand",
            "mybrand",
            "test",
            "내 브랜드 키워드",
        }
        real_cfg = [k for k in cfg_unique if k.lower() not in placeholders]

        if real_cfg:
            sf["keywords"] = real_cfg
            sf["keyword"] = real_cfg[0]
            sf["keywords_text"] = "\n".join(real_cfg)
            self.log(
                f"[Init] 검색어(설정/GUI 우선) {len(real_cfg)}개: {real_cfg[:8]}"
                + (f" …(+{len(real_cfg)-8})" if len(real_cfg) > 8 else "")
            )
            # rewrite keywords.txt so file matches UI (UTF-8)
            try:
                kw_file.write_text("\n".join(real_cfg) + "\n", encoding="utf-8")
            except OSError:
                pass
        elif file_kws:
            clean_file = [k for k in file_kws if k.lower() not in placeholders] or file_kws
            sf["keywords"] = clean_file
            sf["keyword"] = clean_file[0]
            sf["keywords_text"] = "\n".join(clean_file)
            self.log(f"[Init] keywords.txt 로드: {len(clean_file)}개 → {clean_file[:8]}")
        else:
            self.log("[Init] 검색어 없음 — GUI에서 입력 필요")

        self.search_flow = sf

    def _check_cancel(self) -> None:
        if self.should_cancel():
            raise InterruptedError("사용자가 작업을 중지했습니다.")

    def _profile_options(self) -> Dict[str, Any]:
        """Octo profile OS / mobile / tags from config.octo or top-level."""
        oc = dict(self.config.get("octo") or {})
        os_name = str(
            oc.get("profile_os")
            or self.config.get("profile_os")
            or "win"
        )
        mobile = bool(
            oc.get("mobile_fingerprint")
            or self.config.get("mobile_fingerprint")
            or str(os_name).lower() in ("android", "mobile")
        )
        if mobile:
            os_name = "android"
        return {
            "os_name": os_name,
            "mobile": mobile,
            "os_version": str(oc.get("os_version") or self.config.get("os_version") or ""),
            "device": str(oc.get("device") or self.config.get("device") or ""),
            "tags": list(oc.get("tags") or self.config.get("profile_tags") or []),
            "match_ip": bool(
                oc.get("match_mobile_ip", self.config.get("match_mobile_ip", True))
            ),
            "use_one_time": bool(
                oc.get("one_time_profiles") or self.config.get("one_time_profiles")
            ),
            "traffic_metrics": bool(
                oc.get("traffic_metrics", self.config.get("traffic_metrics", True))
            ),
        }

    def prepare_profile(self, title: str, proxy: Proxy, jlog: JobLog) -> str:
        jlog.step_log("프로필 준비", f"title={title}")
        opts = self._profile_options()
        existing = None
        if self.config.get("reuse_existing_profiles", True):
            jlog.profile_log(f"Cloud 검색 중… title='{title}'")
            existing = self.client.find_profile_by_title(title)

        if existing:
            uuid = str(existing["uuid"])
            jlog.profile_log(
                f"기존 프로필 사용 title='{title}' uuid={uuid[:8]}… "
                f"status={existing.get('status') or '-'}"
            )
            # Best-effort OS / mobile info
            try:
                info = self.client.profile_os_info(uuid)
                jlog.profile_log(
                    f"핑거프린트 OS={info.get('os')} mobile={info.get('mobile')} "
                    f"device={info.get('device') or '-'} tags={info.get('tags') or []}"
                )
            except Exception as exc:
                jlog.warn(f"프로필 상세 조회 스킵: {exc}")
            jlog.step_log("프록시 주입 (Cloud PATCH)", proxy.display)
            jlog.proxy_log(
                f"적용 중 → host={proxy.host} port={proxy.port} type={proxy.type} "
                f"auth={'yes' if proxy.login else 'no'}"
            )
            self.client.update_profile_proxy(uuid, proxy)
            # tag for mobile matching audit
            tags = list(opts.get("tags") or [])
            if opts.get("mobile") and "mobile" not in [str(t).lower() for t in tags]:
                tags.append("mobile")
            if tags:
                try:
                    self.client.update_profile_tags(uuid, tags)
                except Exception:
                    pass
            jlog.ok(f"프록시 주입 완료 → {proxy.display}")
            return uuid

        if not self.config.get("create_profile_if_missing", True):
            raise OctoError(f"프로필이 없고 생성도 비활성화됨: {title}")

        os_name = opts["os_name"]
        jlog.step_log(
            "새 프로필 생성 (Cloud POST)",
            f"title={title} os={os_name} mobile={opts['mobile']} + proxy",
        )
        tags = list(opts.get("tags") or [])
        if opts.get("mobile") and "mobile" not in [str(t).lower() for t in tags]:
            tags.extend(["mobile", "auto"])
        try:
            for tname in tags:
                self.client.ensure_tag(str(tname))
        except Exception:
            pass

        if opts.get("mobile"):
            uuid = self.client.create_mobile_profile(
                title=title,
                proxy=proxy,
                os_version=str(opts.get("os_version") or "14"),
                device=str(opts.get("device") or ""),
                tags=tags,
            )
        else:
            fp = self.client.build_fingerprint(
                os_name,
                os_version=str(opts.get("os_version") or ""),
            )
            uuid = self.client.create_profile(
                title=title,
                proxy=proxy,
                os_name=os_name,
                fingerprint=fp,
                tags=tags,
            )
        jlog.ok(
            f"프로필 생성 uuid={uuid[:8]}… os={os_name} proxy={proxy.display}"
        )
        return uuid

    def run_one(self, account: AccountJob, job_index: int) -> Dict[str, Any]:
        self._check_cancel()
        proxy = self.rotator.next()
        jlog = JobLog(
            self.log,
            job_index=job_index,
            profile=account.profile_title,
            proxy=proxy.display,
            email=account.email,
        )
        jlog.sep(f"JOB {job_index} START")
        jlog.info(
            f"배정 profile='{account.profile_title}' email={account.email or '-'} "
            f"notes={account.notes or '-'}"
        )
        jlog.proxy_log(
            f"배정 proxy={proxy.display} host={proxy.host}:{proxy.port} "
            f"type={proxy.type} mode={self.rotator.mode if hasattr(self.rotator,'mode') else self.config.get('proxy_mode')}"
        )

        sf = self.search_flow or {}
        if self.config.get("dry_run"):
            jlog.step_log("DRY RUN — 브라우저 실행 없음")
            if sf.get("enabled"):
                kw = str(sf.get("keyword") or "").strip()
                if not kw:
                    kws = [str(x).strip() for x in (sf.get("keywords") or []) if str(x).strip()]
                    kw = kws[0] if kws else "-"
                jlog.search(f"검색어='{kw}'")
                jlog.search(f"도메인={sf.get('target_domain') or '-'}")
                jlog.search(f"URL특징={sf.get('target_url_contains') or '-'}")
                jlog.search(f"정규식={sf.get('url_regex') or '-'}")
                jlog.search(f"재방문={sf.get('revisit_count', 0)} 배너={len(sf.get('banner_clicks') or [])}")
                jlog.info(
                    "파이프라인: Cloud 프록시주입 → Local Start → Google(프록시) → "
                    "검색 → 매칭클릭 → 스크롤/체류/배너 → 재방문 → Stop"
                )
            else:
                jlog.info("직접 URL targets 모드")
            jlog.ok("DRY RUN 배정 확인 완료")
            jlog.summary(
                {
                    "job": job_index,
                    "profile": account.profile_title,
                    "proxy": proxy.display,
                    "dry_run": True,
                }
            )
            return {"ok": True, "dry_run": True, "proxy": proxy.alias}

        uuid = self.prepare_profile(account.profile_title, proxy, jlog)
        self._check_cancel()

        for active in self.client.list_active_profiles():
            if str(active.get("uuid")) == uuid:
                jlog.warn("이미 실행 중인 프로필 → force stop 후 재시작")
                self.client.stop_profile(uuid, force=True)
                time.sleep(2)

        prof_opts = self._profile_options()
        jlog.step_log(
            "Local API 프로필 Start",
            f"uuid={uuid[:8]}… headless={self.config.get('headless')} "
            f"os={prof_opts.get('os_name')}",
        )
        start = self.client.start_profile(
            uuid,
            headless=bool(self.config.get("headless", False)),
            timeout_sec=int(self.config.get("start_timeout_sec") or 120),
        )
        with self._uuid_lock:
            self.started_uuids.append(uuid)
        ws = str(start.get("ws_endpoint") or "")
        debug_port = start.get("debug_port")
        geo = self.client.extract_connection_ip(start)
        ip = geo.get("ip") or ""
        if geo.get("country") or geo.get("city"):
            jlog.proxy_log(
                f"connection_data country={geo.get('country') or '-'} "
                f"city={geo.get('city') or '-'}"
            )
        jlog.set_proxy_ip(ip or "미확인")
        jlog.ok(
            f"프로필 시작 완료 · 디버그포트={debug_port} · "
            f"API보고IP={ip or '없음'} · CDP={'연결가능' if (ws or debug_port) else '불가'}"
        )
        if ip:
            jlog.proxy_log(
                f"★ 이 작업에서 클릭·접속에 사용될 출구 IP = {ip} "
                f"(프로필={account.profile_title}, 프록시={proxy.display})"
            )
        else:
            jlog.warn(
                "Local API가 출구 IP를 반환하지 않음 — 브라우저에서 재확인 후 로그에 기록됩니다"
            )

        # Profile fingerprint detail for mobile/IP matching
        profile_info: Dict[str, Any] = {"uuid": uuid, "os": prof_opts.get("os_name"), "mobile": prof_opts.get("mobile")}
        try:
            profile_info = self.client.profile_os_info(uuid)
            jlog.profile_log(
                f"매칭 기준 FP os={profile_info.get('os')} mobile={profile_info.get('mobile')} "
                f"device={profile_info.get('device') or '-'}"
            )
        except Exception as exc:
            jlog.warn(f"profile_os_info: {exc}")

        if not ws and debug_port:
            ws = f"http://127.0.0.1:{debug_port}"

        try:
            self._check_cancel()
            ask_2fa = self.ask_2fa
            if ask_2fa is None:

                def ask_2fa(prompt: str) -> Optional[str]:
                    jlog.warn(prompt)
                    try:
                        return input("2차 인증 코드 붙여넣기 후 Enter: ").strip() or None
                    except EOFError:
                        return None

            jlog.step_log(
                "브라우저 자동화 시작",
                f"검색ON={bool(sf.get('enabled'))} google={self.config.get('google_login',{}).get('mode')}",
            )
            google_cfg = dict(self.config.get("google_login") or {})
            # per-account credentials win; multi-account = each job has own secret
            otp = dict(google_cfg.get("otp_fetch") or google_cfg.get("otp") or {})
            # never inherit previous job's secret from a shared mutable dict
            otp.pop("secret", None)
            otp.pop("otp_secret", None)
            if account.otp_selector:
                otp["selector"] = account.otp_selector
            if account.otp_secret:
                otp["secret"] = account.otp_secret
                otp["enabled"] = True
                # local TOTP only — no browser tab needed for 2fa-auth.com
                otp["url"] = ""
                google_cfg["enabled"] = True
                google_cfg["mode"] = "auto"
            elif account.otp_url:
                otp["url"] = account.otp_url
                otp["enabled"] = True
            elif not otp.get("secret"):
                # clear global shared secret when this account has none
                # (prevents wrong account using previous secret)
                pass
            google_cfg["otp_fetch"] = otp

            # secret 있으면 팝업 완전 차단 (풀 오토)
            job_ask_2fa = None if account.otp_secret else ask_2fa

            if otp.get("secret"):
                from .automation import extract_totp_secret, generate_totp

                sec = extract_totp_secret(str(otp["secret"]))
                pre = generate_totp(sec or str(otp["secret"]))
                jlog.info(
                    f"2FA 시크릿 OK (len={len(sec or '')}) — 자동 인증 모드"
                    + (f" · 지금코드={pre}" if pre else " · (시크릿 검증 실패 가능)")
                )
                if pre:
                    jlog.info(
                        f"[2FA] TOTP 미리보기: {pre}  (Google Authenticator 동일 알고리즘)"
                    )
                # normalize secret stored for browser job
                if sec:
                    otp["secret"] = sec
                    google_cfg["otp_fetch"] = otp
            elif otp.get("url"):
                jlog.info(f"2FA 타사 코드 URL: {otp.get('url')}")
            else:
                jlog.warn("이 계정에 2FA 시크릿 없음 — 챌린지 시 수동/실패 가능")

            if self.on_job_progress:
                try:
                    self.on_job_progress(
                        {
                            "phase": "browser",
                            "job": job_index,
                            "email": account.email,
                            "profile": account.profile_title,
                            "has_2fa": bool(account.otp_secret),
                        }
                    )
                except Exception:
                    pass

            ops_cfg = dict(self.config.get("ops") or {})
            ops_preset = {}
            try:
                from .own_site_ops import resolve_ops_preset

                ops_preset = resolve_ops_preset(ops_cfg)
            except Exception:
                ops_preset = {"name": "normal"}

            # allowed hosts for traffic target counting
            allowed_hosts = list(sf.get("allowed_domains") or [])
            td = str(sf.get("target_domain") or "").strip()
            if td:
                allowed_hosts.insert(0, td)

            result = run_browser_job_sync(
                ws,
                str(debug_port) if debug_port else None,
                google_cfg=google_cfg,
                email=account.email,
                password=account.password,
                targets=self.targets,
                search_flow=self.search_flow,
                cookies_cfg=dict(self.config.get("cookies") or {}),
                log=jlog,
                ask_2fa=job_ask_2fa,
                job_meta={
                    "job_index": job_index,
                    "profile": account.profile_title,
                    "proxy": proxy.display,
                    "proxy_host": f"{proxy.host}:{proxy.port}",
                    "proxy_ip": ip,
                    "email": account.email,
                    "uuid": uuid,
                    "otp_url": otp.get("url") or "",
                    "otp_secret": otp.get("secret") or "",
                    "ops_preset": ops_preset,
                    "ops_mode": str(ops_cfg.get("mode") or "browser"),
                    "profile_os": profile_info.get("os"),
                    "mobile_fp": bool(profile_info.get("mobile")),
                    "traffic_metrics": prof_opts.get("traffic_metrics", True),
                    "allowed_hosts": allowed_hosts,
                },
            )
            browser_ip = str(result.get("detected_ip") or "")
            if browser_ip:
                jlog.set_proxy_ip(browser_ip)
                jlog.proxy_log(
                    f"★ 브라우저로 확인한 출구 IP = {browser_ip} "
                    f"→ 이후 검색·클릭은 이 IP로 나간 것입니다"
                )

            # Mobile fingerprint ↔ exit IP match report
            ip_match: Dict[str, Any] = {}
            if prof_opts.get("match_ip", True):
                try:
                    ip_match = self.client.match_profile_mobile_ip(
                        profile_info=profile_info,
                        exit_ip=browser_ip or jlog.proxy_ip,
                        api_ip=ip,
                        proxy=proxy,
                        prefer_mobile=bool(prof_opts.get("mobile")),
                    )
                    jlog.step_log("프로필·모바일·IP 매칭", ip_match.get("summary", ""))
                    if ip_match.get("status") == "ok":
                        jlog.ok(ip_match.get("summary", "match ok"))
                    elif ip_match.get("status") == "partial":
                        jlog.warn(ip_match.get("summary", "match partial"))
                    else:
                        jlog.warn(ip_match.get("summary", "match weak"))
                except Exception as exc:
                    jlog.warn(f"IP 매칭 스킵: {exc}")

            traffic = dict(result.get("traffic") or {})
            ok = bool(result.get("targets_ok") or result.get("search_ok"))
            g_phase = (traffic.get("by_phase") or {}).get("google_login") or {}
            jlog.summary(
                {
                    "job": job_index,
                    "profile": account.profile_title,
                    "uuid": uuid[:8] + "…",
                    "profile_os": profile_info.get("os") or prof_opts.get("os_name"),
                    "mobile_fp": bool(profile_info.get("mobile")),
                    "proxy": proxy.display,
                    "proxy_ip": jlog.proxy_ip,
                    "api_ip": ip or "-",
                    "ip_match": (ip_match or {}).get("status") or "-",
                    "ip_match_score": (ip_match or {}).get("score"),
                    "email": account.email or "-",
                    "keyword": result.get("keyword") or sf.get("keyword") or "-",
                    "matched_url": result.get("matched_url") or "-",
                    "search_ok": result.get("search_ok"),
                    "visits": result.get("search_visits"),
                    "banner_clicks": result.get("site_clicks") or result.get("banner_clicks"),
                    "google_ok": result.get("google_ok"),
                    "google_login_requests": g_phase.get("requests"),
                    "traffic_total_requests": traffic.get("total_requests"),
                    "traffic_target_requests": traffic.get("target_site_requests"),
                    "traffic_google_requests": traffic.get("google_requests"),
                    "traffic_bytes": traffic.get("total_bytes_human"),
                    "traffic_clicks": traffic.get("click_count"),
                    "traffic_avg_req_per_click": traffic.get("avg_requests_per_click"),
                    "ok": ok,
                    "error": result.get("error") or "-",
                }
            )
            return {
                "ok": ok,
                "uuid": uuid,
                "proxy": proxy.alias,
                "proxy_ip": jlog.proxy_ip,
                "ip_match": ip_match,
                "traffic": traffic,
                "result": result,
            }
        finally:
            if self.config.get("stop_profile_after_job", True):
                try:
                    jlog.step_log("프로필 Stop", uuid[:8] + "…")
                    self.client.stop_profile(uuid)
                    jlog.ok("프로필 중지 완료")
                except Exception as exc:
                    jlog.warn(f"프로필 중지 경고: {exc}")

    def stop_started(self, force: bool = True) -> None:
        with self._uuid_lock:
            uuids = list(self.started_uuids)
        for uuid in uuids:
            try:
                self.client.stop_profile(uuid, force=force)
                self.log(f"[Local] 중지: {uuid[:8]}…")
            except Exception as exc:
                self.log(f"[Local] 중지 실패 {uuid[:8]}…: {exc}")

    def _parallel_workers(self, n_jobs: int) -> int:
        raw = self.config.get("parallel_jobs")
        if raw is None:
            raw = self.config.get("concurrency")
        try:
            workers = int(raw or 1)
        except (TypeError, ValueError):
            workers = 1
        workers = max(1, workers)
        # hard cap: Octo Local + machine safety
        cap = int(self.config.get("parallel_jobs_max") or 20)
        cap = max(1, min(cap, 30))
        return min(workers, n_jobs, cap)

    def _set_active(self, job_index: int, info: Optional[Dict[str, Any]]) -> None:
        with self._active_lock:
            if info is None:
                self._active_jobs.pop(job_index, None)
            else:
                self._active_jobs[job_index] = info

    def active_jobs_snapshot(self) -> List[Dict[str, Any]]:
        with self._active_lock:
            return list(self._active_jobs.values())

    def _emit_progress(self, payload: Dict[str, Any]) -> None:
        if not self.on_job_progress:
            return
        try:
            data = dict(payload)
            data["active_jobs"] = self.active_jobs_snapshot()
            data["parallel"] = int(self.config.get("parallel_jobs") or 1)
            self.on_job_progress(data)
        except Exception:
            pass

    def _run_one_tracked(self, account: AccountJob, job_index: int) -> Dict[str, Any]:
        self._set_active(
            job_index,
            {
                "job": job_index,
                "email": account.email,
                "profile": account.profile_title,
                "phase": "running",
                "has_2fa": bool(account.otp_secret),
            },
        )
        self._emit_progress(
            {
                "phase": "start",
                "job": job_index,
                "email": account.email,
                "profile": account.profile_title,
                "has_2fa": bool(account.otp_secret),
            }
        )
        try:
            out = self.run_one(account, job_index)
            return out
        finally:
            self._set_active(job_index, None)

    def run_all(self) -> Dict[str, int]:
        boot = JobLog(self.log, job_index=0, profile="-", proxy="-")
        boot.sep("SESSION START")
        boot.step_log("Cloud API 연결 테스트")
        n = self.client.test_connection()
        boot.ok(f"Cloud OK (프로필 샘플 {n})")

        try:
            user = self.client.local_username()
            boot.ok(f"Local API OK user={user or 'unknown'}")
        except OctoError as exc:
            raise RuntimeError(str(exc)) from exc

        max_jobs = int(self.config.get("max_jobs") or 0)
        jobs = self.accounts
        if max_jobs > 0:
            jobs = jobs[:max_jobs]

        # OPS presets can force higher parallelism / multi-wave
        ops_cfg = dict(self.config.get("ops") or {})
        ops_mode = str(ops_cfg.get("mode") or "browser").lower()
        try:
            cur_par = int(self.config.get("parallel_jobs") or 1)
        except (TypeError, ValueError):
            cur_par = 1
        want_par = int(ops_cfg.get("swarm_parallel") or 0)
        if ops_cfg.get("force_parallel") and want_par > 0:
            self.config["parallel_jobs"] = want_par
        elif ops_mode in ("swarm", "hammer", "blitz", "full") and want_par > cur_par:
            self.config["parallel_jobs"] = want_par
        if ops_mode in ("swarm", "blitz", "full") and float(
            self.config.get("stagger_start_sec") or 1
        ) > 0.8:
            # tighten stagger for swarm feel unless user set very low already
            if ops_cfg.get("tight_stagger", True):
                self.config["stagger_start_sec"] = min(
                    float(self.config.get("stagger_start_sec") or 1.5), 0.35
                )
        waves = max(1, min(int(ops_cfg.get("waves") or 1), 20))
        if waves > 1:
            base_jobs = list(jobs)
            jobs = []
            for w in range(waves):
                jobs.extend(base_jobs)
            boot.info(f"OPS multi-wave ×{waves} → 큐 {len(jobs)} jobs")

        workers = self._parallel_workers(len(jobs))
        stagger = float(self.config.get("stagger_start_sec") or 0)
        if stagger < 0:
            stagger = 0.0

        boot.info(
            f"큐: 프로필 {len(jobs)}개 · 프록시 {len(self.proxies)}개 · "
            f"mode={self.config.get('proxy_mode')} · dry_run={bool(self.config.get('dry_run'))} · "
            f"동시실행={workers} · OPS={ops_mode} waves={waves}"
        )

        # Optional pre-browser HTTP OPS (recon/hammer) — own domain only
        if ops_cfg.get("enabled") and ops_mode in (
            "recon",
            "hammer",
            "full",
            "swarm",
            "blitz",
        ):
            if ops_cfg.get("run_http_ops", True):
                try:
                    from .own_site_ops import run_ops_suite, save_report

                    boot.sep("OPS HTTP SUITE (자사 도메인 전용)")
                    rep = run_ops_suite(self.config, log=self.log)
                    path = save_report(rep, self.base_dir)
                    boot.ok(f"OPS 리포트 저장: {path}")
                    boot.info(f"OPS summary: {rep.get('summary')}")
                    self._last_ops_report = rep
                except Exception as exc:
                    boot.err(f"OPS suite 실패: {exc}")
                    self._last_ops_report = {"error": str(exc)}

        if ops_mode == "recon" and ops_cfg.get("browser_after_recon") is False:
            boot.info("OPS recon-only — 브라우저 작업 스킵")
            return {
                "success": 1,
                "fail": 0,
                "cancelled": 0,
                "total": 0,
                "parallel": 0,
                "ops_report": getattr(self, "_last_ops_report", None),
                "history": [],
            }
        sf = self.search_flow or {}
        if sf.get("enabled"):
            boot.search(
                f"검색ON keyword='{sf.get('keyword') or '-'}' "
                f"keywords={len(sf.get('keywords') or [])} "
                f"domain={sf.get('target_domain') or '-'} "
                f"contains={sf.get('target_url_contains') or '-'} "
                f"regex={sf.get('url_regex') or '-'}"
            )
        ck = self.config.get("cookies") or {}
        if ck.get("enabled"):
            boot.info(
                f"쿠키 주입 ON · when={ck.get('when') or 'after_connect'} "
                f"domain={ck.get('domain') or ck.get('url') or '-'}"
            )

        delay = int(self.config.get("delay_between_jobs_sec") or 15)
        success = 0
        fail = 0
        cancelled = 0
        history: List[Dict[str, Any]] = []
        hist_lock = threading.Lock()
        stats_lock = threading.Lock()
        n_jobs = len(jobs)
        n_2fa = sum(1 for a in jobs if a.otp_secret)
        boot.info(f"2FA 시크릿 있는 계정: {n_2fa}/{n_jobs} (있으면 팝업 없이 자동 인증)")

        def _record(i: int, account: AccountJob, out: Optional[Dict[str, Any]], exc: Optional[BaseException]) -> None:
            nonlocal success, fail, cancelled
            if isinstance(exc, InterruptedError):
                with stats_lock:
                    cancelled += 1
                boot.warn(str(exc))
                with hist_lock:
                    history.append(
                        {
                            "job": i,
                            "email": account.email,
                            "profile": account.profile_title,
                            "ok": False,
                            "error": "cancelled",
                            "has_2fa": bool(account.otp_secret),
                        }
                    )
                return
            if exc is not None:
                with stats_lock:
                    fail += 1
                boot.err(f"[{i}/{n_jobs}] 실패: {account.email or account.profile_title} · {exc}")
                with hist_lock:
                    history.append(
                        {
                            "job": i,
                            "email": account.email,
                            "profile": account.profile_title,
                            "ok": False,
                            "error": str(exc),
                            "has_2fa": bool(account.otp_secret),
                        }
                    )
                return

            assert out is not None
            google_ok = bool((out.get("result") or {}).get("google_ok"))
            with hist_lock:
                history.append(
                    {
                        "job": i,
                        "email": account.email,
                        "profile": account.profile_title,
                        "proxy": out.get("proxy"),
                        "ip": out.get("proxy_ip"),
                        "ok": out.get("ok"),
                        "google_ok": google_ok,
                        "matched": (out.get("result") or {}).get("matched_url"),
                        "keyword": (out.get("result") or {}).get("keyword"),
                        "cookies_set": (out.get("result") or {}).get("cookies_set"),
                        "has_2fa": bool(account.otp_secret),
                    }
                )
            if out.get("ok"):
                with stats_lock:
                    success += 1
                boot.ok(f"[{i}/{n_jobs}] 성공 · {account.email or account.profile_title}")
            else:
                with stats_lock:
                    fail += 1
                boot.warn(
                    f"[{i}/{n_jobs}] 미완료 · {account.email or account.profile_title} "
                    f"google_ok={google_ok}"
                )
            with stats_lock:
                s, f = success, fail
            self._emit_progress(
                {
                    "phase": "done_one",
                    "job": i,
                    "total": n_jobs,
                    "email": account.email,
                    "profile": account.profile_title,
                    "success": s,
                    "fail": f,
                    "ok": bool(out.get("ok")),
                }
            )

        # ── parallel path ──────────────────────────────────
        if workers > 1 and not self.config.get("dry_run"):
            boot.info(
                f"★ 동시 실행 모드: {workers}개 Octo 프로필이 병렬로 클릭합니다 "
                f"(stagger={stagger}s)"
            )
            self._emit_progress(
                {
                    "phase": "session_start",
                    "total": n_jobs,
                    "parallel": workers,
                    "success": 0,
                    "fail": 0,
                }
            )

            def _worker(item: tuple) -> tuple:
                i, account = item
                if self.should_cancel():
                    raise InterruptedError("사용자가 작업을 중지했습니다.")
                # staggered start reduces Local API burst
                if stagger > 0 and i > 1:
                    offset = stagger * ((i - 1) % workers)
                    end = time.time() + offset
                    while time.time() < end:
                        if self.should_cancel():
                            raise InterruptedError("사용자가 작업을 중지했습니다.")
                        time.sleep(0.1)
                boot.sep(f"PARALLEL START {i}/{n_jobs} · {account.email or account.profile_title}")
                out = self._run_one_tracked(account, i)
                return i, account, out

            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="octo-job") as pool:
                futures = {
                    pool.submit(_worker, (i, acc)): (i, acc)
                    for i, acc in enumerate(jobs, start=1)
                }
                for fut in as_completed(futures):
                    i, acc = futures[fut]
                    try:
                        _i, account, out = fut.result()
                        _record(_i, account, out, None)
                    except InterruptedError as exc:
                        _record(i, acc, None, exc)
                    except Exception as exc:
                        _record(i, acc, None, exc)

            if self.should_cancel():
                done = success + fail + cancelled
                if done < n_jobs:
                    cancelled += n_jobs - done

        else:
            # ── sequential (or dry-run) ─────────────────────
            for i, account in enumerate(jobs, start=1):
                if self.should_cancel():
                    cancelled = n_jobs - i + 1
                    boot.warn(f"남은 작업 취소 ({cancelled}건)")
                    break

                boot.sep(f"QUEUE {i}/{n_jobs} · {account.email or account.profile_title}")
                try:
                    out = self._run_one_tracked(account, i)
                    _record(i, account, out, None)
                except InterruptedError as exc:
                    cancelled = n_jobs - i + 1
                    boot.warn(str(exc))
                    break
                except Exception as exc:
                    _record(i, account, None, exc)
                    boot.info("다음 계정으로 계속 진행합니다…")

                if i < n_jobs and delay > 0 and not self.config.get("dry_run"):
                    if self.should_cancel():
                        break
                    boot.info(f"다음 계정까지 {delay}초 대기… ({i}/{n_jobs})")
                    end = time.time() + delay
                    while time.time() < end:
                        if self.should_cancel():
                            break
                        time.sleep(0.25)

        boot.sep("SESSION DONE")
        boot.info(
            f"성공={success} 실패={fail} 취소={cancelled} 총={n_jobs} 동시={workers}"
        )
        # stable order in history for logs
        history.sort(key=lambda h: int(h.get("job") or 0))
        for h in history:
            boot.info(
                f"  · J{h.get('job')} {h.get('email') or h.get('profile')} "
                f"2FA={'Y' if h.get('has_2fa') else 'N'} "
                f"IP={h.get('ip') or '-'} ok={h.get('ok')} "
                f"g={h.get('google_ok', '-')} "
                f"ck={h.get('cookies_set', '-')} "
                f"site={h.get('matched') or h.get('error') or '-'}"
            )
        self._emit_progress(
            {
                "phase": "session_done",
                "success": success,
                "fail": fail,
                "cancelled": cancelled,
                "total": n_jobs,
                "parallel": workers,
            }
        )
        return {
            "success": success,
            "fail": fail,
            "cancelled": cancelled,
            "total": len(jobs),
            "parallel": workers,
            "ops_mode": ops_mode,
            "ops_report": getattr(self, "_last_ops_report", None),
            "history": history,
        }
