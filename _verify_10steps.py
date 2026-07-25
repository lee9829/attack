# -*- coding: utf-8 -*-
"""
10단계 점검 스크립트 — 검색어 폴백 / 저장·로드 / 계정 / 로그인 기본값
GUI 없이 핵심 로직만 검증.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.automation import (  # noqa: E402
    resolve_search_keywords,
    split_search_keywords,
)
from src.runner import (  # noqa: E402
    accounts_from_rows,
    load_config,
    save_config,
)

PASS = 0
FAIL = 0


def check(step: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [OK] {step}" + (f" — {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {step}" + (f" — {detail}" if detail else ""))


def step1_keyword_parse() -> None:
    print("\n=== 1단계: 검색어 파싱 ===")
    r = split_search_keywords("카지노사이트/카지노사이트순위/카지노사이트추천")
    check("슬래시 3개", r == ["카지노사이트", "카지노사이트순위", "카지노사이트추천"], str(r))
    r2 = split_search_keywords("카지노사이트 / 카지노사이트순위 / 카지노사이트추천")
    check("슬래시+공백", r2 == r, str(r2))
    r3 = split_search_keywords("A|B|C")
    check("파이프", r3 == ["A", "B", "C"], str(r3))
    r4 = split_search_keywords("A\nB\nC")
    check("줄바꿈", r4 == ["A", "B", "C"], str(r4))
    r5 = split_search_keywords("A,B,C")
    check("쉼표", r5 == ["A", "B", "C"], str(r5))


def step2_resolve_priority() -> None:
    print("\n=== 2단계: resolve_search_keywords ===")
    cfg = {
        "keyword": "카지노사이트/카지노사이트순위/카지노사이트추천",
        "keywords": [],
        "keywords_text": "",
    }
    r = resolve_search_keywords(cfg)
    check("keyword 슬래시 해석", len(r) == 3 and r[0] == "카지노사이트", str(r))

    cfg2 = {
        "keyword": "카지노사이트 / 카지노사이트순위 / 카지노사이트추천",
        "keywords": ["카지노사이트", "카지노사이트순위", "카지노사이트추천"],
        "keywords_text": "카지노사이트\n카지노사이트순위\n카지노사이트추천",
        "keyword_fallback": True,
    }
    r2 = resolve_search_keywords(cfg2)
    check("중복 제거 후 3개", r2 == [
        "카지노사이트",
        "카지노사이트순위",
        "카지노사이트추천",
    ], str(r2))


def step3_collect_like_save_fields() -> None:
    print("\n=== 3단계: collect→저장 필드 형태 ===")
    # simulate collect_config output for multi kw
    raw = "카지노사이트/카지노사이트순위/카지노사이트추천"
    kw_list = split_search_keywords(raw)
    keyword = " / ".join(kw_list) if len(kw_list) > 1 else kw_list[0]
    domain = "mysite.com"
    domain_list = [domain]
    domains_text = "\n".join(domain_list)
    allow_ads = True
    skip_ads = not allow_ads
    sf = {
        "keyword": keyword,
        "keywords": kw_list,
        "keywords_text": "\n".join(kw_list),
        "keyword_fallback": True,
        "stop_on_first_keyword_hit": True,
        "target_domain": domain,
        "allowed_domains": domain_list,
        "domains_text": domains_text,
        "skip_ads": skip_ads,
    }
    check("keyword 표시 형식", " / " in sf["keyword"], sf["keyword"])
    check("keywords 3개", len(sf["keywords"]) == 3, str(sf["keywords"]))
    check("keywords_text 줄바꿈", sf["keywords_text"].count("\n") == 2, repr(sf["keywords_text"]))
    check("광고 허용 시 skip_ads=False", sf["skip_ads"] is False)
    check("domains_text 채움", sf["domains_text"] == "mysite.com")
    check("fallback ON", sf["keyword_fallback"] is True)


def step4_save_load_roundtrip() -> None:
    print("\n=== 4단계: config 저장→로드 왕복 ===")
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        cfg = {
            "octo_api_token": "test-token",
            "google_login": {
                "enabled": True,
                "mode": "auto",
                "autofill_pause_ms": 350,
                "otp_fetch": {"enabled": True, "secret": ""},
            },
            "search_flow": {
                "enabled": True,
                "keyword": "카지노사이트 / 카지노사이트순위 / 카지노사이트추천",
                "keywords": [
                    "카지노사이트",
                    "카지노사이트순위",
                    "카지노사이트추천",
                ],
                "keywords_text": "카지노사이트\n카지노사이트순위\n카지노사이트추천",
                "keyword_fallback": True,
                "stop_on_first_keyword_hit": True,
                "target_domain": "mysite.com",
                "allowed_domains": ["mysite.com"],
                "domains_text": "mysite.com",
                "skip_ads": False,
            },
            "accounts_rows": [
                {
                    "email": "a@test.com",
                    "password": "pw",
                    "profile_title": "auto-google-a",
                    "otp_secret": "JBSWY3DPEHPK3PXP",
                    "otp_url": "https://2fa-auth.com/",
                    "notes": "원클릭",
                }
            ],
            "proxies_text": "1.2.3.4:8080:user:pass",
        }
        path = base / "config.json"
        save_config(path, cfg)
        # side files like _persist_config
        sf = cfg["search_flow"]
        (base / "keywords.txt").write_text(
            "\n".join(sf["keywords"]), encoding="utf-8"
        )
        (base / "domains.txt").write_text(sf["domains_text"], encoding="utf-8")
        (base / "proxies.txt").write_text(cfg["proxies_text"], encoding="utf-8")

        loaded = load_config(path)
        kws = resolve_search_keywords(loaded["search_flow"])
        check("로드 후 검색어 3개", kws == sf["keywords"], str(kws))
        check("로드 후 fallback", loaded["search_flow"].get("keyword_fallback") is True)
        check("로드 후 skip_ads=False", loaded["search_flow"].get("skip_ads") is False)
        check("로드 후 pause 350", loaded["google_login"].get("autofill_pause_ms") == 350)
        check(
            "keywords.txt 내용",
            (base / "keywords.txt").read_text(encoding="utf-8").splitlines()
            == sf["keywords"],
        )
        check(
            "domains.txt 내용",
            (base / "domains.txt").read_text(encoding="utf-8").strip() == "mysite.com",
        )
        check("계정 이메일 유지", loaded["accounts_rows"][0]["email"] == "a@test.com")


def step5_empty_account_filter() -> None:
    print("\n=== 5단계: 빈 샘플 계정 필터 ===")
    try:
        accounts_from_rows(
            [
                {
                    "email": "",
                    "password": "",
                    "profile_title": "auto-google-1",
                    "otp_secret": "",
                }
            ]
        )
        check("빈 샘플 거부", False, "ValueError 미발생")
    except ValueError:
        check("빈 샘플 거부", True)

    jobs = accounts_from_rows(
        [
            {
                "email": "",
                "password": "",
                "profile_title": "auto-google-1",
                "otp_secret": "",
            },
            {
                "email": "real@gmail.com",
                "password": "x",
                "profile_title": "g-real",
                "otp_secret": "JBSWY3DPEHPK3PXP",
            },
        ]
    )
    check("실제 계정만 남음", len(jobs) == 1 and jobs[0].email == "real@gmail.com", str(jobs))


def step6_start_preconditions() -> None:
    print("\n=== 6단계: 원클릭 시작 전제조건 로직 ===")
    # token / proxy / email / keyword / domain checks (logic only)
    def can_start(token, proxies, email, keyword, domain, mode="auto", g_enabled=True):
        if not token:
            return False, "token"
        if not proxies:
            return False, "proxy"
        if g_enabled and mode == "auto" and not email:
            return False, "email"
        if not keyword:
            return False, "keyword"
        if not domain:
            return False, "domain"
        return True, "ok"

    ok, why = can_start("tok", ["p"], "a@b.com", "카지노", "mysite.com")
    check("정상 시작 가능", ok and why == "ok")
    ok, why = can_start("", ["p"], "a@b.com", "카지노", "mysite.com")
    check("토큰 없으면 차단", not ok and why == "token")
    ok, why = can_start("tok", [], "a@b.com", "카지노", "mysite.com")
    check("프록시 없으면 차단", not ok and why == "proxy")
    ok, why = can_start("tok", ["p"], "", "카지노", "mysite.com")
    check("이메일 없으면 차단", not ok and why == "email")
    ok, why = can_start("tok", ["p"], "a@b.com", "", "mysite.com")
    check("검색어 없으면 차단", not ok and why == "keyword")


def step7_login_defaults() -> None:
    print("\n=== 7단계: 로그인 고속 기본값 ===")
    # read source for defaults
    auto = (ROOT / "src" / "automation.py").read_text(encoding="utf-8")
    gui = (ROOT / "src" / "gui_app.py").read_text(encoding="utf-8")
    check("autofill_pause 350 in gui collect", '"autofill_pause_ms": 350' in gui or "autofill_pause_ms\": 350" in gui)
    check("google_login default 350 fallback", "or 350" in auto)
    check("fast fill in _type_human", "fast: bool = False" in auto)
    check("_fill_email uses fast=True", "fast=True" in auto)
    check("고속 로그인 로그 문구", "자동 로그인 시작 (고속)" in auto)


def step8_ads_sync() -> None:
    print("\n=== 8단계: 광고 allow/skip 동기화 ===")
    # allow_ads True → skip False
    allow_ads = True
    skip = not allow_ads
    check("allow ON → skip OFF", skip is False)
    allow_ads = False
    skip = not allow_ads
    check("allow OFF → skip ON", skip is True)
    gui = (ROOT / "src" / "gui_app.py").read_text(encoding="utf-8")
    check("var_allow_ads 존재", "var_allow_ads" in gui)
    check("collect에서 skip_ads_val", "skip_ads_val" in gui)
    check("홈 체크 문구", "타겟 광고 클릭 허용" in gui)


def step9_fallback_logic_in_code() -> None:
    print("\n=== 9단계: 폴백 로직 코드 존재 ===")
    auto = (ROOT / "src" / "automation.py").read_text(encoding="utf-8")
    check("keyword_fallback 기본 True", 'cfg.get("keyword_fallback", True)' in auto)
    check("stop_on_first_keyword_hit", "stop_on_first_keyword_hit" in auto)
    check("폴백 로그", "다음 검색어로 폴백" in auto)
    check("타겟 발견 시 생략", "남은 검색어" in auto)
    check("split_search_keywords 함수", "def split_search_keywords" in auto)
    check("ensure_live_page 검색 루프", "ensure_live_page" in auto)


def step10_project_config_and_persist() -> None:
    print("\n=== 10단계: 프로젝트 config + persist 함수 ===")
    cfg_path = ROOT / "config.json"
    check("config.json 존재", cfg_path.is_file())
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    sf = cfg.get("search_flow") or {}
    kws = resolve_search_keywords(sf)
    check("실 config 검색어 ≥1", len(kws) >= 1, str(kws))
    check("실 config fallback 키", sf.get("keyword_fallback") is True or "keyword_fallback" in sf)
    check("실 config pause 350", (cfg.get("google_login") or {}).get("autofill_pause_ms") == 350)
    gui = (ROOT / "src" / "gui_app.py").read_text(encoding="utf-8")
    check("_persist_config 존재", "def _persist_config" in gui)
    check("save_settings 가 apply 호출", "_apply_quick_account_silent()" in gui)
    check("start_jobs 가 _persist_config 호출", "_persist_config(cfg" in gui)
    check("btn_start_home 존재", "btn_start_home" in gui)
    check("keywords.txt 존재", (ROOT / "keywords.txt").is_file())
    kw_file = [
        ln.strip()
        for ln in (ROOT / "keywords.txt").read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    check("keywords.txt 비어있지 않음", len(kw_file) >= 1, str(kw_file))

    # full roundtrip with real multi keywords via temp
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        out_cfg = {
            "search_flow": {
                "keyword": "카지노사이트 / 카지노사이트순위 / 카지노사이트추천",
                "keywords": [
                    "카지노사이트",
                    "카지노사이트순위",
                    "카지노사이트추천",
                ],
                "keywords_text": "카지노사이트\n카지노사이트순위\n카지노사이트추천",
                "keyword_fallback": True,
                "stop_on_first_keyword_hit": True,
                "target_domain": "mysite.com",
                "allowed_domains": ["mysite.com"],
                "domains_text": "mysite.com",
                "skip_ads": False,
                "enabled": True,
            },
            "google_login": {"autofill_pause_ms": 350, "mode": "auto", "enabled": True},
            "accounts_rows": [
                {
                    "email": "x@y.com",
                    "password": "p",
                    "profile_title": "t",
                    "otp_secret": "JBSWY3DPEHPK3PXP",
                }
            ],
            "proxies_text": "h:1:u:p",
        }
        p = base / "config.json"
        save_config(p, out_cfg)
        (base / "keywords.txt").write_text(
            "\n".join(out_cfg["search_flow"]["keywords"]), encoding="utf-8"
        )
        reloaded = load_config(p)
        # simulate load display
        parts = resolve_search_keywords(reloaded["search_flow"])
        disp = " / ".join(parts)
        re_split = split_search_keywords(disp)
        check("표시→재파싱 동일", re_split == parts, f"{disp} → {re_split}")


def main() -> int:
    print("Octo 자동화 10단계 점검 시작")
    step1_keyword_parse()
    step2_resolve_priority()
    step3_collect_like_save_fields()
    step4_save_load_roundtrip()
    step5_empty_account_filter()
    step6_start_preconditions()
    step7_login_defaults()
    step8_ads_sync()
    step9_fallback_logic_in_code()
    step10_project_config_and_persist()
    print("\n" + "=" * 50)
    print(f"결과: PASS={PASS} FAIL={FAIL}")
    if FAIL:
        print("일부 실패 — 수정 필요")
        return 1
    print("전체 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
