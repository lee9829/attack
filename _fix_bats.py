# -*- coding: utf-8 -*-
"""Rewrite .bat files with Windows CRLF + pure ASCII (CMD-safe)."""
from pathlib import Path

BASE = Path(__file__).resolve().parent

INSTALL = """@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo  Octo Google Site Automation - Install
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found in PATH.
  echo Install Python 3.10+ from https://www.python.org/downloads/
  echo Check "Add python.exe to PATH" then run this again.
  pause
  exit /b 1
)

python --version
echo.
echo [1/3] Upgrade pip...
python -m pip install --upgrade pip
echo.
echo [2/3] Install packages...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] pip install failed
  pause
  exit /b 1
)
echo.
echo [3/3] Install Playwright Chromium...
python -m playwright install chromium
if errorlevel 1 (
  echo [WARN] playwright install failed. Check network.
)

if not exist config.json (
  copy /Y config.example.json config.json >nul
  echo [OK] created config.json
)
if not exist proxies.txt (
  copy /Y proxies.example.txt proxies.txt >nul
  echo [OK] created proxies.txt
)
if not exist accounts.csv (
  copy /Y accounts.example.csv accounts.csv >nul
  echo [OK] created accounts.csv
)

echo.
echo Install done.
echo 1^) Set Octo API token in GUI or config.json
echo 2^) Paste proxies and add Google accounts
echo 3^) Start Octo Browser and login
echo 4^) Double-click run.bat
echo.
pause
"""

RUN = """@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Octo Google Site Automation

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found.
  echo Run install.bat first.
  pause
  exit /b 1
)

if not exist config.json (
  if exist config.example.json copy /Y config.example.json config.json >nul
)

echo Starting GUI...
python main.py
if errorlevel 1 (
  echo.
  echo An error occurred.
  pause
)
"""


def to_bytes(text: str) -> bytes:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    return normalized.encode("ascii")


def write_bat(path: Path, text: str) -> None:
    data = to_bytes(text)
    path.write_bytes(data)
    if not data.startswith(b"@echo off\r\n"):
        raise SystemExit(f"bad header in {path}: {list(data[:20])}")
    print(f"OK {path.name} size={len(data)}")


def main() -> None:
    write_bat(BASE / "install.bat", INSTALL)
    write_bat(BASE / "run.bat", RUN)

    for p in BASE.glob("*.bat"):
        if p.name.lower() in ("install.bat", "run.bat"):
            continue
        if p.name.startswith("_"):
            continue
        raw = p.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        text = raw.decode("utf-8", errors="replace")
        if "requirements.txt" in text or "playwright" in text or "pip install" in text:
            write_bat(p, INSTALL)
        else:
            write_bat(p, RUN)


if __name__ == "__main__":
    main()
