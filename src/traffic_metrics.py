# -*- coding: utf-8 -*-
"""
Real browser traffic metrics (Playwright request/response listeners).

Tracks how many network requests a Google login / SERP click / site visit
actually generates — for ops QA visibility, not third-party abuse.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse


def _host(url: str) -> str:
    try:
        h = (urlparse(url).netloc or "").lower()
        if h.startswith("www."):
            h = h[4:]
        return h
    except Exception:
        return ""


def _is_google(host: str) -> bool:
    h = (host or "").lower()
    return (
        "google." in h
        or h.endswith("gstatic.com")
        or h.endswith("googleapis.com")
        or h.endswith("ggpht.com")
        or "youtube.com" in h
    )


@dataclass
class ClickSlice:
    label: str
    started_at: float
    ended_at: float = 0.0
    requests: int = 0
    responses: int = 0
    bytes_in: int = 0
    failed: int = 0
    hosts: Dict[str, int] = field(default_factory=dict)
    target_host_hits: int = 0
    google_hits: int = 0
    url: str = ""

    def finalize(self) -> None:
        if not self.ended_at:
            self.ended_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        self.finalize()
        dur = max(0.0, self.ended_at - self.started_at)
        top_hosts = sorted(self.hosts.items(), key=lambda x: -x[1])[:8]
        return {
            "label": self.label,
            "url": self.url,
            "duration_ms": int(dur * 1000),
            "requests": self.requests,
            "responses": self.responses,
            "bytes_in": self.bytes_in,
            "failed": self.failed,
            "target_host_hits": self.target_host_hits,
            "google_hits": self.google_hits,
            "top_hosts": top_hosts,
            "req_per_sec": round(self.requests / dur, 2) if dur > 0 else 0,
        }


class TrafficTracker:
    """
    Attach to a Playwright Page/Context to count real network traffic.

    Usage:
        tr = TrafficTracker(allowed_hosts=["mysite.com"])
        await tr.attach(page)
        tr.begin_phase("google_login")
        ...
        tr.mark_click("serp_own_site", url=...)
        ...
        tr.end_click()
        summary = tr.summary()
    """

    def __init__(
        self,
        *,
        allowed_hosts: Optional[List[str]] = None,
        log: Optional[Callable[[str], None]] = None,
        enabled: bool = True,
    ):
        self.enabled = enabled
        self._log = log or (lambda m: None)
        self.allowed_hosts = [
            h.lower().lstrip("www.") for h in (allowed_hosts or []) if h
        ]
        self._lock = threading.Lock()
        self.total_requests = 0
        self.total_responses = 0
        self.total_failed = 0
        self.total_bytes = 0
        self.by_host: Dict[str, int] = defaultdict(int)
        self.by_resource: Dict[str, int] = defaultdict(int)
        self.by_phase: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"requests": 0, "responses": 0, "bytes": 0, "failed": 0}
        )
        self.phase = "init"
        self.phase_started = time.time()
        self.started_at = time.time()
        self.clicks: List[ClickSlice] = []
        self._open_click: Optional[ClickSlice] = None
        self._attached = False
        self._handlers: List[Any] = []

    def set_allowed_hosts(self, hosts: List[str]) -> None:
        self.allowed_hosts = [h.lower().lstrip("www.") for h in hosts if h]

    def _is_target(self, host: str) -> bool:
        h = (host or "").lower().lstrip("www.")
        if not h or not self.allowed_hosts:
            return False
        for d in self.allowed_hosts:
            if h == d or h.endswith("." + d):
                return True
        return False

    def begin_phase(self, name: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            self.phase = name or "phase"
            self.phase_started = time.time()
        self._log(f"[TRAFFIC] phase → {self.phase}")

    def mark_click(self, label: str, url: str = "") -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._open_click:
                self._open_click.finalize()
                self.clicks.append(self._open_click)
            self._open_click = ClickSlice(
                label=label, started_at=time.time(), url=url or ""
            )
        self._log(f"[TRAFFIC] click 시작 label={label} url={url or '-'}")

    def end_click(self) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        with self._lock:
            if not self._open_click:
                return None
            self._open_click.finalize()
            snap = self._open_click.to_dict()
            self.clicks.append(self._open_click)
            self._open_click = None
        self._log(
            f"[TRAFFIC] ★ 클릭당 트래픽 label={snap['label']} "
            f"요청={snap['requests']}개 응답={snap['responses']} "
            f"자사호스트={snap['target_host_hits']} "
            f"Google={snap['google_hits']} "
            f"수신≈{self._fmt_bytes(snap['bytes_in'])} "
            f"실패={snap['failed']} "
            f"{snap['duration_ms']}ms"
        )
        return snap

    def _on_request(self, request) -> None:
        if not self.enabled:
            return
        try:
            url = request.url
            host = _host(url)
            rtype = str(getattr(request, "resource_type", None) or "other")
            with self._lock:
                self.total_requests += 1
                self.by_host[host] += 1
                self.by_resource[rtype] += 1
                ph = self.by_phase[self.phase]
                ph["requests"] += 1
                if self._open_click:
                    c = self._open_click
                    c.requests += 1
                    c.hosts[host] = c.hosts.get(host, 0) + 1
                    if self._is_target(host):
                        c.target_host_hits += 1
                    if _is_google(host):
                        c.google_hits += 1
        except Exception:
            pass

    def _on_response(self, response) -> None:
        if not self.enabled:
            return
        try:
            url = response.url
            host = _host(url)
            size = 0
            try:
                headers = response.headers or {}
                cl = headers.get("content-length") or headers.get("Content-Length")
                if cl:
                    size = int(cl)
            except Exception:
                size = 0
            failed = False
            try:
                st = int(response.status)
                failed = st >= 400
            except Exception:
                pass
            with self._lock:
                self.total_responses += 1
                self.total_bytes += size
                if failed:
                    self.total_failed += 1
                ph = self.by_phase[self.phase]
                ph["responses"] += 1
                ph["bytes"] += size
                if failed:
                    ph["failed"] += 1
                if self._open_click:
                    c = self._open_click
                    c.responses += 1
                    c.bytes_in += size
                    if failed:
                        c.failed += 1
        except Exception:
            pass

    def _on_request_failed(self, request) -> None:
        if not self.enabled:
            return
        with self._lock:
            self.total_failed += 1
            self.by_phase[self.phase]["failed"] += 1
            if self._open_click:
                self._open_click.failed += 1

    async def attach(self, page) -> None:
        """Attach once at browser context level (avoids double-count with page)."""
        if not self.enabled or self._attached:
            return
        try:
            target = None
            try:
                target = page.context
            except Exception:
                target = page
            target.on("request", self._on_request)
            target.on("response", self._on_response)
            target.on("requestfailed", self._on_request_failed)
            self._handlers.append(target)
            self._attached = True
            self._log("[TRAFFIC] 네트워크 리스너 부착 — 실제 요청/응답 집계 시작")
        except Exception as exc:
            self._log(f"[TRAFFIC] 부착 실패: {exc}")

    def snapshot_phase(self) -> Dict[str, Any]:
        with self._lock:
            ph = dict(self.by_phase.get(self.phase) or {})
            elapsed = max(0.001, time.time() - self.phase_started)
        return {
            "phase": self.phase,
            "requests": int(ph.get("requests") or 0),
            "responses": int(ph.get("responses") or 0),
            "bytes": int(ph.get("bytes") or 0),
            "failed": int(ph.get("failed") or 0),
            "elapsed_ms": int(elapsed * 1000),
            "req_per_sec": round(int(ph.get("requests") or 0) / elapsed, 2),
        }

    def log_phase_summary(self) -> None:
        if not self.enabled:
            return
        s = self.snapshot_phase()
        self._log(
            f"[TRAFFIC] phase={s['phase']} 실제요청={s['requests']} "
            f"응답={s['responses']} 수신≈{self._fmt_bytes(s['bytes'])} "
            f"실패={s['failed']} {s['elapsed_ms']}ms "
            f"({s['req_per_sec']} req/s)"
        )

    @staticmethod
    def _fmt_bytes(n: int) -> str:
        n = int(n or 0)
        if n < 1024:
            return f"{n}B"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f}KB"
        return f"{n / (1024 * 1024):.2f}MB"

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            if self._open_click:
                self._open_click.finalize()
                self.clicks.append(self._open_click)
                self._open_click = None
            clicks = [c.to_dict() for c in self.clicks]
            top_hosts = sorted(self.by_host.items(), key=lambda x: -x[1])[:12]
            by_res = dict(self.by_resource)
            by_phase = {k: dict(v) for k, v in self.by_phase.items()}
            total_req = self.total_requests
            total_resp = self.total_responses
            total_bytes = self.total_bytes
            total_failed = self.total_failed
            elapsed = max(0.001, time.time() - self.started_at)

        target_req = 0
        google_req = 0
        for h, n in self.by_host.items():
            if self._is_target(h):
                target_req += n
            if _is_google(h):
                google_req += n

        per_click_avg = 0.0
        if clicks:
            per_click_avg = sum(c["requests"] for c in clicks) / len(clicks)

        return {
            "enabled": self.enabled,
            "elapsed_ms": int(elapsed * 1000),
            "total_requests": total_req,
            "total_responses": total_resp,
            "total_bytes": total_bytes,
            "total_bytes_human": self._fmt_bytes(total_bytes),
            "total_failed": total_failed,
            "req_per_sec": round(total_req / elapsed, 2),
            "target_site_requests": target_req,
            "google_requests": google_req,
            "clicks": clicks,
            "click_count": len(clicks),
            "avg_requests_per_click": round(per_click_avg, 1),
            "top_hosts": top_hosts,
            "by_resource": by_res,
            "by_phase": by_phase,
        }

    def log_full_summary(self) -> Dict[str, Any]:
        s = self.summary()
        if not self.enabled:
            return s
        self._log("─" * 40 + " 실제 트래픽 요약 " + "─" * 40)
        self._log(
            f"[TRAFFIC] 전체 요청={s['total_requests']} 응답={s['total_responses']} "
            f"수신≈{s['total_bytes_human']} 실패={s['total_failed']} "
            f"({s['req_per_sec']} req/s · {s['elapsed_ms']}ms)"
        )
        self._log(
            f"[TRAFFIC] 자사 도메인 요청={s['target_site_requests']} · "
            f"Google 계열={s['google_requests']}"
        )
        self._log(
            f"[TRAFFIC] 클릭 이벤트={s['click_count']} · "
            f"클릭당 평균 요청={s['avg_requests_per_click']}"
        )
        for i, c in enumerate(s.get("clicks") or [], 1):
            self._log(
                f"[TRAFFIC]  클릭#{i} [{c['label']}] 요청={c['requests']} "
                f"자사={c['target_host_hits']} G={c['google_hits']} "
                f"≈{self._fmt_bytes(c['bytes_in'])} {c['duration_ms']}ms "
                f"url={c.get('url') or '-'}"
            )
        for phase, ph in (s.get("by_phase") or {}).items():
            self._log(
                f"[TRAFFIC]  phase[{phase}] req={ph.get('requests', 0)} "
                f"resp={ph.get('responses', 0)} fail={ph.get('failed', 0)} "
                f"bytes={self._fmt_bytes(int(ph.get('bytes') or 0))}"
            )
        tops = s.get("top_hosts") or []
        if tops:
            self._log(
                "[TRAFFIC] top hosts: "
                + ", ".join(f"{h}={n}" for h, n in tops[:8])
            )
        self._log("─" * 80)
        return s
