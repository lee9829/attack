# -*- coding: utf-8 -*-
"""Load / normalize / persist config for the web UI."""
from __future__ import annotations

import csv
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.proxy_manager import Proxy, parse_proxy_text
from src.runner import (
    accounts_from_rows,
    load_config,
    parse_account_bulk_text,
    save_config,
    validate_account_secrets,
)


PLACEHOLDER_KEYWORDS = {
    "example-mybrand",
    "my brand",
    "mybrand",
    "test",
    "내 브랜드 키워드",
}


def default_config() -> Dict[str, Any]:
    return {
        "octo_api_token": "",
        "cloud_base": "https://app.octobrowser.net/api/v2/automation",
        "local_base": "http://127.0.0.1:58888/api",
        "octo_email": "",
        "octo_password": "",
        "octo_auto_login": True,
        "allow_cloud_only": False,
        # agent | auto | octo | playwright
        # agent = Windows PC local Octo via agent/start_agent.bat
        "browser_engine": "agent",
        "proxy_type": "http",
        "proxy_mode": "round_robin",
        "proxy_start_index": 0,
        "proxies_file": "proxies.txt",
        "accounts_file": "accounts.csv",
        "proxies_text": "",
        "accounts_rows": [],
        "reuse_existing_profiles": True,
        "create_profile_if_missing": True,
        "headless": False,
        "start_timeout_sec": 120,
        "delay_between_jobs_sec": 15,
        "stop_profile_after_job": True,
        "max_jobs": 0,
        "parallel_jobs": 3,
        "stagger_start_sec": 1.5,
        "parallel_jobs_max": 20,
        "octo": {
            "profile_os": "android",
            "mobile_fingerprint": True,
            "os_version": "14",
            "device": "",
            "tags": ["mobile", "auto"],
            "match_mobile_ip": True,
            "traffic_metrics": True,
            "one_time_profiles": False,
        },
        "profile_os": "android",
        "mobile_fingerprint": True,
        "match_mobile_ip": True,
        "traffic_metrics": True,
        "macro_loops": 1,
        "delay_between_loops_sec": 30,
        "ops": {
            "enabled": False,
            "mode": "browser",
            "browser_preset": "normal",
            "run_http_ops": False,
            "browser_after_recon": True,
            "skip_hammer": False,
            "skip_recon": False,
            "path_workers": 16,
            "hammer_requests": 100,
            "hammer_workers": 24,
            "hammer_url": "",
            "multi_hammer": True,
            "swarm_parallel": 5,
            "force_parallel": False,
            "tight_stagger": True,
            "waves": 1,
            "intensity": 5,
            "extra_paths_text": "",
            "extra_paths": [],
        },
        "cookies": {
            "enabled": False,
            "when": "on_site",
            "domain": "",
            "url": "",
            "warm_url": "",
            "warm_navigate": True,
            "clear_first": False,
            "reload_on_site": True,
            "text": "",
            "json": "",
            "cookies": [],
        },
        "google_login": {
            "enabled": True,
            "mode": "auto",
            "login_url": "https://accounts.google.com/",
            "success_url_contains": [
                "myaccount.google.com",
                "mail.google.com",
                "accounts.google.com/b/",
                "drive.google.com",
            ],
            "manual_wait_sec": 300,
            "autofill_pause_ms": 350,
            "otp_fetch": {
                "enabled": True,
                "secret": "",
                "url": "",
                "selector": "",
                "regex": r"\b(\d{6})\b",
                "wait_ms": 2500,
            },
        },
        "search_flow": {
            "enabled": True,
            "purpose": "own_site_qa",
            "keyword": "",
            "keywords": [],
            "keywords_text": "",
            "keyword_fallback": True,
            "stop_on_first_keyword_hit": True,
            "target_domain": "",
            "allowed_domains": [],
            "domains_text": "",
            "domains_file": "domains.txt",
            "keywords_file": "keywords.txt",
            "target_url_contains": [],
            "url_regex": "",
            "url_regex_text": "",
            "path_regex": "",
            "path_regex_text": "",
            "path_regexes": [],
            "require_regex": True,
            "title_contains": [],
            "title_regex": "",
            "path_targets": [],
            "path_targets_text": "",
            "path_exclude": [],
            "path_exclude_text": "",
            "bulk_urls_text": "",
            "bulk_full_urls": [],
            "bulk_paths_exact": [],
            "require_path_or_regex": False,
            "require_domain": True,
            "skip_ads": True,
            "search_url": "https://www.google.com/",
            "max_serp_pages": 3,
            "max_result_clicks": 1,
            "revisit_count": 1,
            "warmup": True,
            "human": {
                "dwell_ms_min": 4000,
                "dwell_ms_max": 12000,
                "scroll": True,
                "mouse_wander": True,
                "read_pauses": True,
                "scroll_steps_min": 3,
                "scroll_steps_max": 8,
                "scroll_up_chance": 0.25,
                "random_internal_click": False,
                "serp_scroll_min": 2,
                "serp_scroll_max": 5,
            },
            "banner_clicks": [
                {
                    "selector": "",
                    "text_contains": "메뉴",
                    "wait_after_ms": 2000,
                    "optional": True,
                },
                {
                    "selector": "",
                    "text_contains": "예약",
                    "wait_after_ms": 2000,
                    "optional": True,
                },
            ],
            "also_run_targets": False,
        },
        "targets": [],
        "dry_run": False,
    }


def deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(base)
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_or_default(base_dir: Path) -> Dict[str, Any]:
    base = default_config()
    cfg_path = base_dir / "config.json"
    if cfg_path.is_file():
        try:
            loaded = load_config(cfg_path)
            base = deep_merge(base, loaded)
        except Exception:
            pass

    # side files
    proxies_path = base_dir / "proxies.txt"
    if proxies_path.is_file() and not (base.get("proxies_text") or "").strip():
        try:
            base["proxies_text"] = proxies_path.read_text(encoding="utf-8")
        except OSError:
            pass

    domains_path = base_dir / "domains.txt"
    keywords_path = base_dir / "keywords.txt"
    sf = base.setdefault("search_flow", {})
    if domains_path.is_file() and not (sf.get("domains_text") or "").strip():
        try:
            text = domains_path.read_text(encoding="utf-8").strip()
            if text:
                sf["domains_text"] = text
                sf["allowed_domains"] = [
                    ln.strip()
                    for ln in text.splitlines()
                    if ln.strip() and not ln.strip().startswith("#")
                ]
                if not sf.get("target_domain") and sf["allowed_domains"]:
                    sf["target_domain"] = sf["allowed_domains"][0]
        except OSError:
            pass
    if keywords_path.is_file() and not (sf.get("keywords") or []):
        try:
            text = keywords_path.read_text(encoding="utf-8").strip()
            if text:
                from src.automation import split_search_keywords

                kws = split_search_keywords(text)
                if kws:
                    sf["keywords"] = kws
                    sf["keywords_text"] = "\n".join(kws)
                    sf["keyword"] = " / ".join(kws) if len(kws) > 1 else kws[0]
        except Exception:
            pass

    # accounts.csv if rows empty
    if not base.get("accounts_rows"):
        acc_path = base_dir / "accounts.csv"
        if acc_path.is_file():
            try:
                rows: List[Dict[str, str]] = []
                with acc_path.open(encoding="utf-8-sig", newline="") as f:
                    for row in csv.DictReader(f):
                        if not row:
                            continue
                        email = (row.get("email") or "").strip()
                        title = (row.get("profile_title") or "").strip()
                        if not email and not title:
                            continue
                        rows.append(
                            {
                                "email": email,
                                "password": (row.get("password") or "").strip(),
                                "profile_title": title,
                                "otp_secret": (
                                    row.get("otp_secret")
                                    or row.get("totp_secret")
                                    or row.get("secret")
                                    or ""
                                ).strip(),
                                "otp_url": (row.get("otp_url") or "").strip(),
                                "notes": (row.get("notes") or "").strip(),
                            }
                        )
                if rows:
                    base["accounts_rows"] = rows
            except Exception:
                pass

    return base


def normalize_config(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize client payload into JobRunner-ready config."""
    cfg = deep_merge(default_config(), raw or {})

    # accounts paste
    bulk = str(raw.get("accounts_bulk") or "").strip()
    if bulk:
        parsed = parse_account_bulk_text(bulk)
        if parsed:
            cfg["accounts_rows"] = parsed

    rows = list(cfg.get("accounts_rows") or [])
    cleaned: List[Dict[str, str]] = []
    for r in rows:
        email = (r.get("email") or "").strip()
        title = (r.get("profile_title") or "").strip()
        secret = (r.get("otp_secret") or "").strip()
        password = (r.get("password") or "").strip()
        if (
            not email
            and not password
            and not secret
            and title in ("auto-google-1", "auto-google-user", "sample", "")
        ):
            continue
        cleaned.append(
            {
                "email": email,
                "password": password,
                "profile_title": title,
                "otp_secret": secret,
                "otp_url": (r.get("otp_url") or "").strip(),
                "notes": (r.get("notes") or "").strip(),
            }
        )
    cfg["accounts_rows"] = cleaned

    # search keywords / domains
    sf = cfg.setdefault("search_flow", {})
    from src.automation import split_search_keywords

    kw_src = "\n".join(
        [
            str(sf.get("keyword") or ""),
            str(sf.get("keywords_text") or ""),
            "\n".join(str(x) for x in (sf.get("keywords") or [])),
        ]
    )
    kws = split_search_keywords(kw_src)
    real = [k for k in kws if k.lower() not in PLACEHOLDER_KEYWORDS]
    if real:
        kws = real
    sf["keywords"] = kws
    sf["keywords_text"] = "\n".join(kws)
    if kws:
        sf["keyword"] = " / ".join(kws) if len(kws) > 1 else kws[0]

    domains_text = str(sf.get("domains_text") or "").strip()
    domain_list = [
        ln.strip()
        for ln in domains_text.replace(",", "\n").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    td = str(sf.get("target_domain") or "").strip()
    if td and td not in domain_list:
        domain_list.insert(0, td)
    for d in list(sf.get("allowed_domains") or []):
        d = str(d).strip()
        if d and d not in domain_list:
            domain_list.append(d)
    sf["allowed_domains"] = domain_list
    sf["domains_text"] = "\n".join(domain_list)
    if domain_list and not td:
        sf["target_domain"] = domain_list[0]

    # path / URL regex for SERP click filter
    path_rx_text = str(
        sf.get("path_regex_text")
        or sf.get("path_regex")
        or raw.get("path_regex_text")
        or raw.get("path_regex")
        or ""
    ).strip()
    path_rx_list = [
        ln.strip()
        for ln in path_rx_text.replace(",", "\n").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    for extra in list(sf.get("path_regexes") or []):
        e = str(extra).strip()
        if e and e not in path_rx_list:
            path_rx_list.append(e)
    sf["path_regexes"] = path_rx_list
    sf["path_regex_text"] = "\n".join(path_rx_list)
    sf["path_regex"] = path_rx_list[0] if path_rx_list else ""

    url_rx_text = str(
        sf.get("url_regex_text")
        or sf.get("url_regex")
        or raw.get("url_regex_text")
        or ""
    ).strip()
    url_rx_list = [
        ln.strip()
        for ln in url_rx_text.replace(",", "\n").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    sf["url_regex_text"] = "\n".join(url_rx_list)
    sf["url_regex"] = url_rx_list[0] if url_rx_list else str(sf.get("url_regex") or "")

    path_targets_text = str(
        sf.get("path_targets_text") or raw.get("path_targets_text") or ""
    ).strip()
    if path_targets_text:
        sf["path_targets_text"] = path_targets_text
        sf["path_targets"] = [
            ln.strip()
            for ln in path_targets_text.replace(",", "\n").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
    path_exclude_text = str(
        sf.get("path_exclude_text") or raw.get("path_exclude_text") or ""
    ).strip()
    if path_exclude_text:
        sf["path_exclude_text"] = path_exclude_text
        sf["path_exclude"] = [
            ln.strip()
            for ln in path_exclude_text.replace(",", "\n").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]

    # when any regex is set, force require_regex (keyword → only matching links)
    if path_rx_list or url_rx_list:
        sf["require_regex"] = bool(sf.get("require_regex", True))
    else:
        sf["require_regex"] = bool(sf.get("require_regex", False))

    # bulk URLs paste (thousands of own-site addresses)
    bulk_text = str(
        raw.get("bulk_urls_text")
        or sf.get("bulk_urls_text")
        or ""
    ).strip()
    sf["bulk_urls_text"] = bulk_text
    if bulk_text:
        try:
            from src.bulk_targets import apply_bulk_to_search_flow, parse_bulk_text

            bulk = parse_bulk_text(bulk_text)
            sf = apply_bulk_to_search_flow(sf, bulk, merge=True)
            sf["bulk_urls_text"] = bulk_text
        except Exception:
            pass

    # banner clicks from text lines "메뉴,예약"
    banner_text = str(raw.get("banner_text") or "").strip()
    if banner_text:
        clicks = []
        for part in banner_text.replace("\n", ",").split(","):
            t = part.strip()
            if t:
                clicks.append(
                    {
                        "selector": "",
                        "text_contains": t,
                        "wait_after_ms": 2000,
                        "optional": True,
                    }
                )
        if clicks:
            sf["banner_clicks"] = clicks

    # google login: any per-account secret → full auto
    g = cfg.setdefault("google_login", {})
    otp = g.setdefault("otp_fetch", {})
    any_secret = any((r.get("otp_secret") or "").strip() for r in cleaned)
    if any_secret:
        g["enabled"] = True
        g["mode"] = "auto"
        otp["enabled"] = True
        otp["secret"] = ""
        otp["url"] = ""
    cfg["google_login"] = g
    cfg["search_flow"] = sf

    # parallel + macro loops (0 = infinite until stop)
    try:
        cfg["parallel_jobs"] = max(1, min(int(cfg.get("parallel_jobs") or 1), 30))
    except (TypeError, ValueError):
        cfg["parallel_jobs"] = 1
    try:
        cfg["stagger_start_sec"] = max(0.0, float(cfg.get("stagger_start_sec") or 0))
    except (TypeError, ValueError):
        cfg["stagger_start_sec"] = 0.0
    try:
        cfg["macro_loops"] = max(0, min(int(cfg.get("macro_loops") or 1), 9999))
    except (TypeError, ValueError):
        cfg["macro_loops"] = 1
    try:
        cfg["delay_between_loops_sec"] = max(
            0, int(cfg.get("delay_between_loops_sec") or 30)
        )
    except (TypeError, ValueError):
        cfg["delay_between_loops_sec"] = 30
    try:
        cfg["delay_between_jobs_sec"] = max(
            0, int(cfg.get("delay_between_jobs_sec") or 15)
        )
    except (TypeError, ValueError):
        cfg["delay_between_jobs_sec"] = 15

    # cookies (own-site QA inject into Octo profile)
    ck = dict(cfg.get("cookies") or {})
    ck_text = str(
        raw.get("cookies_text")
        or ck.get("text")
        or ck.get("json")
        or ""
    ).strip()
    if not ck_text and isinstance(ck.get("cookies"), list) and ck.get("cookies"):
        try:
            ck_text = json.dumps(ck["cookies"], ensure_ascii=False, indent=2)
        except Exception:
            ck_text = ""
    domain = str(ck.get("domain") or sf.get("target_domain") or "").strip()
    url = str(ck.get("url") or ck.get("warm_url") or "").strip()
    if not url and domain:
        url = f"https://{domain}/"
    parsed_list: List[Dict[str, Any]] = []
    if ck_text:
        try:
            from src.automation import parse_cookie_payload

            parsed_list = parse_cookie_payload(
                ck_text, default_domain=domain, default_url=url
            )
        except Exception:
            parsed_list = []
    ck_out = {
        "enabled": bool(ck.get("enabled")),
        "when": str(ck.get("when") or "on_site"),
        "domain": domain,
        "url": url,
        "warm_url": str(ck.get("warm_url") or url or ""),
        "warm_navigate": bool(ck.get("warm_navigate", True)),
        "clear_first": bool(ck.get("clear_first", False)),
        "reload_on_site": bool(ck.get("reload_on_site", True)),
        "text": ck_text,
        "json": ck_text,
        "cookies": parsed_list if parsed_list else list(ck.get("cookies") or []),
    }
    cfg["cookies"] = ck_out

    # Octo profile fingerprint / mobile IP match / traffic metrics
    octo_in = dict(raw.get("octo") or cfg.get("octo") or {})
    profile_os = str(
        octo_in.get("profile_os")
        or raw.get("profile_os")
        or cfg.get("profile_os")
        or "win"
    ).lower()
    if profile_os in ("mobile", "phone", "and"):
        profile_os = "android"
    if profile_os not in ("win", "mac", "android"):
        profile_os = "win"
    mobile_fp = bool(
        octo_in.get(
            "mobile_fingerprint",
            raw.get("mobile_fingerprint", profile_os == "android"),
        )
    )
    if mobile_fp:
        profile_os = "android"
    tags_raw = octo_in.get("tags") or raw.get("profile_tags") or ["mobile", "auto"]
    if isinstance(tags_raw, str):
        tags_list = [t.strip() for t in tags_raw.replace(",", "\n").splitlines() if t.strip()]
    else:
        tags_list = [str(t).strip() for t in list(tags_raw) if str(t).strip()]
    cfg["octo"] = {
        "profile_os": profile_os,
        "mobile_fingerprint": mobile_fp,
        "os_version": str(octo_in.get("os_version") or raw.get("os_version") or "14"),
        "device": str(octo_in.get("device") or raw.get("device") or ""),
        "tags": tags_list,
        "match_mobile_ip": bool(
            octo_in.get("match_mobile_ip", raw.get("match_mobile_ip", True))
        ),
        "traffic_metrics": bool(
            octo_in.get("traffic_metrics", raw.get("traffic_metrics", True))
        ),
        "one_time_profiles": bool(
            octo_in.get("one_time_profiles", raw.get("one_time_profiles", False))
        ),
    }
    cfg["profile_os"] = profile_os
    cfg["mobile_fingerprint"] = mobile_fp
    cfg["match_mobile_ip"] = cfg["octo"]["match_mobile_ip"]
    cfg["traffic_metrics"] = cfg["octo"]["traffic_metrics"]

    # OPS / authorized red-team style suite (own domain only)
    ops_in = dict(raw.get("ops") or cfg.get("ops") or {})
    mode = str(ops_in.get("mode") or "swarm").lower()
    if mode not in (
        "browser",
        "recon",
        "hammer",
        "swarm",
        "full",
        "blitz",
        "stealth_probe",
    ):
        mode = "swarm"
    preset = str(
        ops_in.get("browser_preset") or ops_in.get("preset") or mode
    ).lower()
    if preset == "full":
        preset = "blitz"
    if preset not in ("normal", "swarm", "hammer", "blitz", "stealth_probe"):
        preset = "blitz" if mode in ("swarm", "full", "blitz", "hammer") else "normal"
    try:
        waves = max(1, min(int(ops_in.get("waves") or 1), 20))
    except (TypeError, ValueError):
        waves = 1
    try:
        intensity = max(1, min(int(ops_in.get("intensity") or 5), 5))
    except (TypeError, ValueError):
        intensity = 5
    extra_text = str(ops_in.get("extra_paths_text") or "").strip()
    extra_paths = list(ops_in.get("extra_paths") or [])
    if extra_text:
        for ln in extra_text.replace(",", "\n").splitlines():
            ln = ln.strip()
            if ln and ln not in extra_paths:
                extra_paths.append(ln)
    cfg["ops"] = {
        "enabled": bool(ops_in.get("enabled", True)),
        "mode": mode,
        "browser_preset": preset,
        "run_http_ops": bool(ops_in.get("run_http_ops", True)),
        "browser_after_recon": bool(ops_in.get("browser_after_recon", True)),
        "skip_hammer": bool(ops_in.get("skip_hammer", False)),
        "skip_recon": bool(ops_in.get("skip_recon", False)),
        "path_workers": int(ops_in.get("path_workers") or 16),
        "hammer_requests": int(ops_in.get("hammer_requests") or 100),
        "hammer_workers": int(ops_in.get("hammer_workers") or 24),
        "hammer_url": str(ops_in.get("hammer_url") or ""),
        "multi_hammer": bool(ops_in.get("multi_hammer", True)),
        "swarm_parallel": int(ops_in.get("swarm_parallel") or cfg.get("parallel_jobs") or 5),
        "force_parallel": bool(ops_in.get("force_parallel", False)),
        "tight_stagger": bool(ops_in.get("tight_stagger", True)),
        "waves": waves,
        "intensity": intensity,
        "extra_paths_text": extra_text,
        "extra_paths": extra_paths,
    }
    return cfg


def persist_config(base_dir: Path, cfg: Dict[str, Any]) -> None:
    base_dir = Path(base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)

    (base_dir / "proxies.txt").write_text(
        str(cfg.get("proxies_text") or ""), encoding="utf-8"
    )

    with (base_dir / "accounts.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "email",
                "password",
                "profile_title",
                "otp_secret",
                "otp_url",
                "notes",
            ],
        )
        w.writeheader()
        for r in cfg.get("accounts_rows") or []:
            w.writerow(
                {
                    "email": r.get("email", ""),
                    "password": r.get("password", ""),
                    "profile_title": r.get("profile_title", ""),
                    "otp_secret": r.get("otp_secret", ""),
                    "otp_url": r.get("otp_url", ""),
                    "notes": r.get("notes", ""),
                }
            )

    sf = cfg.get("search_flow") or {}
    (base_dir / "domains.txt").write_text(
        str(sf.get("domains_text") or ""), encoding="utf-8"
    )
    kws = list(sf.get("keywords") or [])
    (base_dir / "keywords.txt").write_text(
        "\n".join(str(k) for k in kws), encoding="utf-8"
    )

    # never write secrets markers into example — full config is local-only
    save_config(base_dir / "config.json", cfg)


def parse_proxies(
    text: str, proxy_type: str = "http"
) -> Tuple[List[Proxy], List[str]]:
    return parse_proxy_text(text or "", default_type=proxy_type or "http")


def public_config_view(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Mask secrets slightly for display (still local-only)."""
    view = deepcopy(cfg)
    # keep full data for local web (localhost only) — user needs to edit passwords
    # but strip nothing critical; document that web binds to 127.0.0.1
    rows = view.get("accounts_rows") or []
    view["accounts_count"] = len(rows)
    proxies, _ = parse_proxies(
        str(view.get("proxies_text") or ""),
        str(view.get("proxy_type") or "http"),
    )
    view["proxies_count"] = len(proxies)
    sf = view.get("search_flow") or {}
    view["keywords_count"] = len(sf.get("keywords") or [])
    view["domains_count"] = len(sf.get("allowed_domains") or [])
    return view


def build_accounts(cfg: Dict[str, Any]):
    rows = list(cfg.get("accounts_rows") or [])
    all_acc = accounts_from_rows(rows) if rows else []
    with_email = [a for a in all_acc if (a.email or "").strip()]
    accounts = with_email or all_acc
    # normalize secrets
    try:
        from src.automation import extract_totp_secret

        for a in accounts:
            if a.otp_secret:
                a.otp_secret = extract_totp_secret(a.otp_secret) or a.otp_secret
    except Exception:
        pass
    return accounts


def secret_validation(accounts) -> Dict[str, Any]:
    return validate_account_secrets(
        [
            {
                "email": a.email,
                "otp_secret": a.otp_secret,
                "password": a.password,
            }
            for a in accounts
        ]
    )
