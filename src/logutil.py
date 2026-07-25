# -*- coding: utf-8 -*-
"""한글 상세 로그 — 프로필·프록시·출구IP·검색어·클릭 대상을 관리자가 읽기 쉽게."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, Optional


LogFn = Callable[[str], None]

# English tags kept for color coding in GUI, messages are Korean
TAG_KO = {
    "INFO": "안내",
    "OK": "성공",
    "WARN": "경고",
    "ERR": "오류",
    "STEP": "단계",
    "CLICK": "클릭",
    "SEARCH": "검색",
    "SITE": "사이트",
    "PROXY": "프록시",
    "PROFILE": "프로필",
    "SUM": "요약",
    "TRAFFIC": "트래픽",
    "MATCH": "매칭",
}


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


class JobLog:
    """
    모든 로그 줄에 시각 + 작업 맥락(작업번호/프로필/프록시/출구IP)을 붙입니다.
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
        self.profile = profile or "-"
        self.proxy = proxy or "-"
        self.proxy_ip = proxy_ip or "미확인"
        self.email = email or "-"
        self.keyword = ""
        self.matched_url = ""
        self.step = 0

    def set_proxy_ip(self, ip: str) -> None:
        if ip:
            self.proxy_ip = str(ip)

    def set_keyword(self, kw: str) -> None:
        self.keyword = kw or ""

    def set_matched_url(self, url: str) -> None:
        self.matched_url = url or ""

    def ctx(self) -> str:
        """관리자용 한글 맥락 한 줄."""
        parts = [f"작업{self.job_index or '-'}"]
        if self.profile and self.profile != "-":
            parts.append(f"프로필={self.profile}")
        if self.email and self.email != "-":
            parts.append(f"계정={self.email}")
        if self.proxy and self.proxy != "-":
            p = self.proxy
            if len(p) > 48:
                p = p[:46] + "…"
            parts.append(f"프록시={p}")
        ip = self.proxy_ip or "미확인"
        parts.append(f"출구IP={ip}")
        if self.keyword:
            parts.append(f"검색어='{self.keyword}'")
        return " · ".join(parts)

    def _emit(self, tag: str, msg: str) -> None:
        ko = TAG_KO.get(tag, tag)
        # Keep [TAG] for GUI color + Korean label for admins
        line = f"[{ts()}] [{tag}/{ko}] [{self.ctx()}] {msg}"
        try:
            self._base(line)
        except Exception:
            print(line, flush=True)

    def info(self, msg: str) -> None:
        self._emit("INFO", msg)

    def ok(self, msg: str) -> None:
        self._emit("OK", msg)

    def warn(self, msg: str) -> None:
        self._emit("WARN", msg)

    def err(self, msg: str) -> None:
        self._emit("ERR", msg)

    def step_log(self, title: str, msg: str = "") -> None:
        self.step += 1
        body = f"단계 {self.step} · {title}"
        if msg:
            body += f" — {msg}"
        self._emit("STEP", body)

    def click(self, msg: str) -> None:
        self._emit("CLICK", msg)

    def search(self, msg: str) -> None:
        self._emit("SEARCH", msg)

    def site(self, msg: str) -> None:
        self._emit("SITE", msg)

    def proxy_log(self, msg: str) -> None:
        self._emit("PROXY", msg)

    def profile_log(self, msg: str) -> None:
        self._emit("PROFILE", msg)

    def sep(self, title: str = "") -> None:
        bar = "─" * 40
        if title:
            self._base(f"[{ts()}] {bar} {title} {bar}")
        else:
            self._base(f"[{ts()}] {bar}")

    def traffic(self, msg: str) -> None:
        self._emit("INFO", f"[TRAFFIC] {msg}")

    def summary(self, data: Dict[str, Any]) -> None:
        self.sep("작업 요약 (관리자용)")
        labels = {
            "job": "작업번호",
            "profile": "옥토 프로필",
            "uuid": "프로필 UUID",
            "profile_os": "프로필 OS/핑거프린트",
            "mobile_fp": "모바일 핑거프린트",
            "proxy": "프록시",
            "proxy_ip": "출구 IP (클릭에 사용된 IP)",
            "api_ip": "Local API 보고 IP",
            "ip_match": "프로필·IP 매칭",
            "ip_match_score": "매칭 점수",
            "email": "Google 계정",
            "keyword": "검색어",
            "matched_url": "클릭한 사이트 URL",
            "search_ok": "검색·클릭 성공",
            "visits": "사이트 방문 횟수",
            "banner_clicks": "CTA/배너 클릭 수",
            "google_ok": "Google 로그인 성공",
            "traffic_total_requests": "실제 네트워크 요청 총수",
            "traffic_target_requests": "자사 도메인 요청 수",
            "traffic_google_requests": "Google 계열 요청 수",
            "traffic_bytes": "수신 바이트(대략)",
            "traffic_clicks": "클릭 이벤트 수",
            "traffic_avg_req_per_click": "클릭당 평균 요청 수",
            "google_login_requests": "Google 로그인 구간 요청 수",
            "ok": "전체 성공 여부",
            "error": "오류",
            "dry_run": "DRY RUN",
        }
        for k, v in data.items():
            if k in ("traffic_detail", "click_slices", "ip_match_detail"):
                continue
            label = labels.get(k, k)
            self._emit("SUM", f"{label} = {v}")
        # always restate IP clearly
        self._emit(
            "SUM",
            f"※ 이 작업에서 클릭/접속에 쓰인 출구 IP = {data.get('proxy_ip') or self.proxy_ip}",
        )
        # traffic one-liner for operators
        tr = data.get("traffic_total_requests")
        if tr is not None:
            self._emit(
                "SUM",
                f"※ 실제 트래픽: 총요청={tr} · 자사={data.get('traffic_target_requests', '-')} · "
                f"Google={data.get('traffic_google_requests', '-')} · "
                f"클릭당평균={data.get('traffic_avg_req_per_click', '-')} · "
                f"수신={data.get('traffic_bytes', '-')}",
            )
        im = data.get("ip_match")
        if im:
            self._emit(
                "SUM",
                f"※ 프로필↔모바일/IP 매칭 = {im} (score={data.get('ip_match_score', '-')})",
            )
        self.sep()

    def __call__(self, msg: str) -> None:
        """Drop-in replacement for plain log(str)."""
        m = msg or ""
        upper = m.upper()
        if m.startswith("[검색]") or m.startswith("[SEARCH]") or "SEARCH" in upper[:24]:
            self.search(m)
        elif m.startswith("[사이트]") or m.startswith("[사이트 ") or m.startswith("[SITE]"):
            self.site(m)
        elif "클릭" in m or m.startswith("[CLICK]") or "[CLICK]" in upper:
            self.click(m)
        elif m.startswith("[Profile]") or m.startswith("[PROFILE]") or m.startswith("[프로필]"):
            self.profile_log(m)
        elif (
            m.startswith("[Proxy]")
            or m.startswith("[PROXY]")
            or m.startswith("[Local]")
            or m.startswith("[프록시]")
        ):
            self.proxy_log(m)
        elif "오류" in m or "실패" in m or m.startswith("[ERR]"):
            self.err(m)
        elif "성공" in m or m.startswith("[OK]"):
            self.ok(m)
        else:
            self.info(m)
