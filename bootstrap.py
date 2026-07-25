# -*- coding: utf-8 -*-
"""First-run dependency check. Silent when already installed."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
MARKER = BASE_DIR / ".deps_ok"
REQUIRED = ("requests", "playwright", "fastapi", "uvicorn", "jinja2")
PIP_PACKAGES = [
    "requests>=2.31.0",
    "playwright>=1.40.0",
    "fastapi>=0.110.0",
    "uvicorn[standard]>=0.27.0",
    "jinja2>=3.1.0",
    "python-multipart>=0.0.9",
]


def _has(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None


def _pip_install(*packages: str) -> None:
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--quiet",
        *packages,
    ]
    subprocess.check_call(cmd)


def _ensure_playwright_driver() -> None:
    subprocess.check_call(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        cwd=str(BASE_DIR),
    )


def ensure_dependencies(*, force: bool = False) -> None:
    if MARKER.is_file() and not force:
        if all(_has(m) for m in REQUIRED):
            return

    missing = [m for m in REQUIRED if not _has(m)]
    if missing or force:
        print(f"[Setup] 패키지 설치 중: {', '.join(missing or REQUIRED)} …")
        _pip_install(*PIP_PACKAGES)

    if not _has("playwright"):
        raise RuntimeError("playwright 설치에 실패했습니다.")
    if not _has("fastapi"):
        raise RuntimeError("fastapi 설치에 실패했습니다.")

    print("[Setup] Playwright 드라이버 준비 중 (최초 1회, 잠시 걸릴 수 있음)…")
    try:
        _ensure_playwright_driver()
    except Exception as exc:
        # CDP 연결은 Octo 브라우저를 쓰므로 드라이버 실패해도 계속 시도
        print(f"[Setup] Playwright 브라우저 설치 경고: {exc}")

    try:
        MARKER.write_text("ok\n", encoding="utf-8")
    except OSError:
        pass
    print("[Setup] 준비 완료")


if __name__ == "__main__":
    ensure_dependencies(force="--force" in sys.argv)
