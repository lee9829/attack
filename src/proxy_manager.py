from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urlparse


@dataclass
class Proxy:
    host: str
    port: int
    login: str = ""
    password: str = ""
    type: str = "http"
    change_ip_url: str = ""
    raw_line: str = ""

    @property
    def alias(self) -> str:
        auth = "auth" if self.login else "noauth"
        return f"{self.type}://{self.host}:{self.port}({auth})"

    @property
    def display(self) -> str:
        user = f"{self.login}@" if self.login else ""
        return f"{self.type}://{user}{self.host}:{self.port}"

    def to_line(self) -> str:
        if self.raw_line:
            return self.raw_line
        base = f"{self.host}:{self.port}"
        if self.login or self.password:
            base = f"{base}:{self.login}:{self.password}"
        if self.type and self.type != "http":
            # keep simple host:port form; type stored separately
            pass
        if self.change_ip_url:
            base = f"{base}|{self.change_ip_url}"
        return base

    def to_octo_inline(self) -> dict:
        data = {
            "type": self.type,
            "host": self.host,
            "port": int(self.port),
            "login": self.login or "",
            "password": self.password or "",
        }
        if self.change_ip_url:
            data["change_ip_url"] = self.change_ip_url
        return data


def _split_host_port_user_pass(line: str) -> Optional[Proxy]:
    parts = line.split(":")
    if len(parts) < 2:
        return None
    host = parts[0].strip()
    try:
        port = int(parts[1].strip())
    except ValueError:
        return None
    if len(parts) == 2:
        return Proxy(host=host, port=port)
    if len(parts) == 3:
        return Proxy(host=host, port=port, login=parts[2], password="")
    login = parts[2]
    password = ":".join(parts[3:])
    return Proxy(host=host, port=port, login=login, password=password)


def parse_proxy_line(line: str, default_type: str = "http") -> Optional[Proxy]:
    original = line.rstrip("\n")
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None

    change_ip = ""
    if "|" in raw:
        raw, change_ip = raw.split("|", 1)
        raw = raw.strip()
        change_ip = change_ip.strip()

    proxy_type = default_type
    if "://" in raw:
        parsed = urlparse(raw)
        scheme = (parsed.scheme or default_type).lower()
        if scheme in ("http", "https", "socks", "socks5", "ssh"):
            proxy_type = scheme
        host = parsed.hostname or ""
        port = parsed.port
        login = parsed.username or ""
        password = parsed.password or ""
        if not host or not port:
            return None
        return Proxy(
            host=host,
            port=int(port),
            login=login or "",
            password=password or "",
            type=proxy_type,
            change_ip_url=change_ip,
            raw_line=original.strip(),
        )

    p = _split_host_port_user_pass(raw)
    if p is None:
        return None
    p.type = default_type
    p.change_ip_url = change_ip
    p.raw_line = original.strip()
    return p


def parse_proxy_text(
    text: str, default_type: str = "http"
) -> Tuple[List[Proxy], List[str]]:
    """Parse multi-line proxy paste. Returns (valid_proxies, error_lines)."""
    proxies: List[Proxy] = []
    errors: List[str] = []
    seen = set()
    for idx, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        proxy = parse_proxy_line(line, default_type=default_type)
        if proxy is None:
            errors.append(f"{idx}행: 형식 오류 → {stripped[:80]}")
            continue
        key = (proxy.type, proxy.host, proxy.port, proxy.login, proxy.password)
        if key in seen:
            continue
        seen.add(key)
        proxies.append(proxy)
    return proxies, errors


def load_proxies(path: str | Path, default_type: str = "http") -> List[Proxy]:
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"프록시 파일이 없습니다: {file_path}")
    proxies, errors = parse_proxy_text(
        file_path.read_text(encoding="utf-8"), default_type=default_type
    )
    if not proxies:
        detail = "; ".join(errors[:3]) if errors else "내용 없음"
        raise ValueError(f"유효한 프록시가 없습니다. ({detail})")
    return proxies


class ProxyRotator:
    """
    modes:
      - round_robin: 순서대로 돌아가며 사용
      - fixed: start_index 프록시만 계속 사용
      - from_selected: start_index 부터 순서대로 (끝에서 처음으로)
    """

    def __init__(
        self,
        proxies: List[Proxy],
        start_index: int = 0,
        mode: str = "round_robin",
    ):
        if not proxies:
            raise ValueError("프록시 목록이 비어 있습니다.")
        self.proxies = proxies
        self.mode = (mode or "round_robin").strip().lower()
        self.index = max(0, int(start_index)) % len(proxies)
        self._fixed_index = self.index
        self._lock = threading.Lock()

    def next(self) -> Proxy:
        with self._lock:
            if self.mode == "fixed":
                return self.proxies[self._fixed_index]
            proxy = self.proxies[self.index]
            self.index = (self.index + 1) % len(self.proxies)
            return proxy

    def peek(self) -> Proxy:
        with self._lock:
            if self.mode == "fixed":
                return self.proxies[self._fixed_index]
            return self.proxies[self.index]

    @property
    def current_index(self) -> int:
        with self._lock:
            if self.mode == "fixed":
                return self._fixed_index
            return self.index
