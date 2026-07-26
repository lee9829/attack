# -*- coding: utf-8 -*-
"""자연어 한글 로그 — 비개발자도 바로 이해되게 (코드식 키=값 최소화)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, Optional


LogFn = Callable[[str], None]

# 짧은 한글 배지 (색 구분용 태그 유지)
TAG_KO = {
    "INFO": "안내",
    "OK": "성공",
    "WARN": "주의",
    "ERR": "문제",
    "STEP": "진행",
    "CLICK": "클릭",
    "SEARCH": "검색",
    "SITE": "사이트",
    "PROXY": "접속",
    "PROFILE": "프로필",
    "SUM": "요약",
    "TRAFFIC": "통신",
    "MATCH": "매칭",
    "EVD": "확인",
    "AUDIT": "기록",
    "STORY": "이야기",
}


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _short_url(url: str, n: int = 72) -> str:
    u = (url or "").strip()
    if len(u) <= n:
        return u or "(주소 없음)"
    return u[: n - 1] + "…"


class JobLog:
    """
    자연어 로그.
    예: [17:06:49] [클릭] 프로필 g-xxx · IP 1.2.3.4 — 검색 결과 링크를 눌렀습니다.
    """

    def __init__(
        self,
        base_log: Optional[LogFn] = None,
        *,
        job_index: int = 0,
        profile: str = "",
        proxy: str = "",
        proxy_ip: str = "",
        email: str = "",
    ):
        self._base = base_log or (lambda m: print(m, flush=True))
        self.job_index = job_index
        self.profile = profile or ""
        self.proxy = proxy or ""
        self.proxy_ip = proxy_ip or ""
        self.email = email or ""
        self.keyword = ""
        self.matched_url = ""
        self.step = 0
        self.natural = True  # 자연어 모드

    def set_proxy_ip(self, ip: str) -> None:
        if ip:
            self.proxy_ip = str(ip)

    def set_keyword(self, kw: str) -> None:
        self.keyword = kw or ""

    def set_matched_url(self, url: str) -> None:
        self.matched_url = url or ""

    def who(self) -> str:
        """사람 읽기용 주어(누가)."""
        bits = []
        if self.job_index:
            bits.append(f"{self.job_index}번 작업")
        if self.profile:
            bits.append(f"프로필「{self.profile}」")
        if self.email:
            bits.append(f"계정 {self.email}")
        if self.proxy_ip and self.proxy_ip not in ("미확인", "-", ""):
            bits.append(f"접속 IP {self.proxy_ip}")
        elif self.proxy and self.proxy != "-":
            p = self.proxy
            if len(p) > 36:
                p = p[:34] + "…"
            bits.append(f"프록시 {p}")
        return " · ".join(bits) if bits else "작업"

    def ctx(self) -> str:
        return self.who()

    def _emit(self, tag: str, msg: str) -> None:
        ko = TAG_KO.get(tag, tag)
        # 자연어: [시각] [한글배지] 누가 — 무엇을
        line = f"[{ts()}] [{ko}] {msg}"
        try:
            self._base(line)
        except Exception:
            print(line, flush=True)

    def story(self, msg: str) -> None:
        """이야기체 한 줄 (비개발자용 본문)."""
        who = self.who()
        text = (msg or "").strip()
        if who and who != "작업":
            self._emit("STORY", f"{who} — {text}")
        else:
            self._emit("STORY", text)

    def info(self, msg: str) -> None:
        self._emit("INFO", msg if not self.natural else self._soft(msg))

    def ok(self, msg: str) -> None:
        self._emit("OK", msg if not self.natural else self._soft(msg))

    def warn(self, msg: str) -> None:
        self._emit("WARN", msg if not self.natural else self._soft(msg))

    def err(self, msg: str) -> None:
        self._emit("ERR", msg if not self.natural else self._soft(msg))

    def _soft(self, msg: str) -> str:
        """기술 접두어를 부드럽게."""
        m = (msg or "").strip()
        for pref in (
            "[검색] ",
            "[SEARCH] ",
            "[사이트] ",
            "[SITE] ",
            "[CLICK] ",
            "[프록시] ",
            "[PROXY] ",
            "[PROFILE] ",
            "[프로필] ",
            "[Google] ",
            "[TRAFFIC] ",
            "[OPS] ",
            "[흐름] ",
            "[2FA] ",
            "[쿠키] ",
            "[브라우저] ",
            "[Agent] ",
        ):
            if m.startswith(pref):
                m = m[len(pref) :]
                break
        return m

    def step_log(self, title: str, msg: str = "") -> None:
        self.step += 1
        if msg:
            self.story(f"{self.step}단계. {title}. {msg}")
        else:
            self.story(f"{self.step}단계. {title}.")

    def click(self, msg: str) -> None:
        self._emit("CLICK", self._soft(msg))

    def evidence(self, msg: str) -> None:
        self._emit("EVD", msg)

    def audit(self, msg: str) -> None:
        self._emit("AUDIT", self._soft(msg))

    def click_evidence(self, data: Dict[str, Any]) -> None:
        """클릭 결과를 문장으로 설명."""
        clicked = bool(data.get("clicked") or data.get("ok"))
        prof = data.get("profile") or self.profile or "알 수 없는 프로필"
        ip = data.get("ip") or self.proxy_ip or "아직 모름"
        email = data.get("email") or self.email or "-"
        kw = data.get("keyword") or self.keyword or "(검색어 없음)"
        target = data.get("matched_url") or data.get("target_url") or ""
        final = data.get("final_url") or data.get("landed_url") or target
        is_ad = data.get("is_ad")
        ad_txt = "구글 광고(스폰서)" if is_ad else "일반 검색 결과"
        method = str(data.get("method") or "")
        how = (
            "링크를 직접 눌러"
            if method == "element_click"
            else "주소로 이동해"
            if method == "goto_fallback"
            else "방식으로"
        )
        job = data.get("job") or self.job_index or "-"

        if clicked:
            self.story(
                f"실제로 들어갔습니다. "
                f"{job}번 · 프로필「{prof}」· 계정 {email} · 접속 IP {ip} 이(가) "
                f"검색어「{kw}」로 나온 {ad_txt}를 {how} 열었습니다."
            )
            self.story(f"누른 주소: {_short_url(str(target), 90)}")
            self.story(f"도착한 화면 주소: {_short_url(str(final), 90)}")
            self.ok(
                f"클릭 성공 확인 — 프로필「{prof}」, IP {ip}, 도착 {_short_url(str(final), 60)}"
            )
            self.set_matched_url(str(final or target))
        else:
            err = data.get("error") or "페이지에 제대로 도착하지 못했습니다"
            self.story(
                f"클릭이 확인되지 않았습니다. "
                f"프로필「{prof}」· IP {ip} · 검색어「{kw}」. 이유: {err}"
            )
            self.warn(f"클릭 미확인 — 프로필「{prof}」, IP {ip}")

    def search(self, msg: str) -> None:
        self._emit("SEARCH", self._soft(msg))

    def site(self, msg: str) -> None:
        self._emit("SITE", self._soft(msg))

    def proxy_log(self, msg: str) -> None:
        self._emit("PROXY", self._soft(msg))

    def profile_log(self, msg: str) -> None:
        self._emit("PROFILE", self._soft(msg))

    def sep(self, title: str = "") -> None:
        if title:
            self._base(f"[{ts()}] ── {title} ──")
        else:
            self._base(f"[{ts()}] ────────")

    def traffic(self, msg: str) -> None:
        self._emit("INFO", self._soft(msg))

    def summary(self, data: Dict[str, Any]) -> None:
        self.sep("한 줄 요약 (누구나 읽기)")
        prof = data.get("profile") or self.profile or "-"
        ip = data.get("proxy_ip") or self.proxy_ip or "미확인"
        email = data.get("email") or self.email or "-"
        kw = data.get("keyword") or self.keyword or "-"
        final = data.get("final_url") or data.get("matched_url") or "-"
        clicked = data.get("click_verified")
        g_ok = data.get("google_ok")
        banners = data.get("banner_clicks") or 0
        ad = data.get("is_ad")

        self.story(f"이번 작업 주인공: 프로필「{prof}」, 계정 {email}, 접속 IP {ip}")
        if g_ok is True:
            self.story("구글 로그인은 되었습니다.")
        elif g_ok is False:
            self.story("구글 로그인은 되지 않았거나 확인되지 않았습니다.")
        self.story(f"검색에 쓴 말: 「{kw}」")
        if clicked:
            kind = "광고 결과" if ad else "검색 결과"
            self.story(
                f"그 뒤 {kind}를 눌러 사이트로 들어갔고, 도착 주소는 {_short_url(str(final), 80)} 입니다."
            )
        else:
            self.story("검색 결과 클릭·도착은 이번에 확인되지 않았습니다.")
        try:
            bn = int(banners or 0)
        except Exception:
            bn = 0
        if bn > 0:
            self.story(f"사이트 안에서 배너·버튼 같은 것을 {bn}번 눌러 보았습니다.")
        self.story(
            "참고: 이 작업은 한꺼번에 서버를 두드리는 방식이 아니라, "
            "사람처럼 검색하고 링크를 누르고 스크롤하며 둘러보는 방식입니다."
        )
        if data.get("error") and data.get("error") not in ("-", "", None):
            self.warn(f"남긴 문제 메시지: {data.get('error')}")
        self.sep()

    def __call__(self, msg: str) -> None:
        """Drop-in for plain log(str) — 태그 보고 분류 후 자연어화."""
        m = msg or ""
        soft = self._soft(m)
        upper = m.upper()
        # story-style paths from automation
        if m.startswith("[사람]") or m.startswith("[이야기]"):
            self.story(self._soft(m.replace("[사람]", "").replace("[이야기]", "").strip()))
        elif m.startswith("[검색]") or m.startswith("[SEARCH]") or "SEARCH" in upper[:24]:
            self.search(soft)
        elif m.startswith("[사이트]") or m.startswith("[SITE]"):
            self.site(soft)
        elif "클릭" in m or m.startswith("[CLICK]") or "[CLICK]" in upper:
            self.click(soft)
        elif m.startswith("[Profile]") or m.startswith("[PROFILE]") or m.startswith("[프로필]"):
            self.profile_log(soft)
        elif (
            m.startswith("[Proxy]")
            or m.startswith("[PROXY]")
            or m.startswith("[Local]")
            or m.startswith("[프록시]")
        ):
            self.proxy_log(soft)
        elif "오류" in m or "실패" in m or m.startswith("[ERR]"):
            self.err(soft)
        elif "성공" in m or m.startswith("[OK]"):
            self.ok(soft)
        else:
            self.info(soft)
