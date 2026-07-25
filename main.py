# -*- coding: utf-8 -*-
"""
Octo Browser API — 프로필·프록시 로테이션 + 검색/클릭 자동화
Windows 10/11

기본: 로컬 웹 UI (브라우저)
  python main.py
  python main.py --web

예전 Tkinter GUI:
  python main.py --gui

CLI:
  python main.py --cli
"""
from __future__ import annotations

import argparse
import shutil
import sys
import traceback
from pathlib import Path


def _app_dir() -> Path:
    # PyInstaller onefile: keep config next to the .exe
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = _app_dir()
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
_src_root = Path(__file__).resolve().parent if not getattr(sys, "frozen", False) else BASE_DIR
if str(_src_root) not in sys.path:
    sys.path.insert(0, str(_src_root))


def ensure_files() -> None:
    if getattr(sys, "frozen", False):
        resource_dir = Path(getattr(sys, "_MEIPASS", BASE_DIR))
    else:
        resource_dir = Path(__file__).resolve().parent

    pairs = [
        ("config.example.json", "config.json"),
        ("proxies.example.txt", "proxies.txt"),
        ("accounts.example.csv", "accounts.csv"),
        ("domains.example.txt", "domains.txt"),
        ("keywords.example.txt", "keywords.txt"),
    ]
    for src_name, dst_name in pairs:
        src = resource_dir / src_name
        if not src.exists():
            src = BASE_DIR / src_name
        dst = BASE_DIR / dst_name
        if not dst.exists() and src.exists():
            shutil.copy(src, dst)
            print(f"[Setup] {dst_name} 생성됨")


def _show_error(title: str, msg: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, msg)
        root.destroy()
    except Exception:
        print(f"{title}: {msg}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Octo Browser로 프록시·프로필 자동화 후 검색/클릭"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--web",
        action="store_true",
        help="로컬 웹 UI (기본값)",
    )
    mode.add_argument(
        "--gui",
        action="store_true",
        help="예전 Tkinter GUI",
    )
    mode.add_argument(
        "--cli",
        action="store_true",
        help="콘솔 모드",
    )
    parser.add_argument("--config", default="config.json", help="설정 파일 경로")
    parser.add_argument("--dry-run", action="store_true", help="배정만 확인 (CLI)")
    parser.add_argument("--host", default="127.0.0.1", help="웹 서버 호스트")
    parser.add_argument("--port", type=int, default=8787, help="웹 서버 포트")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="웹 시작 시 브라우저 자동 열기 안 함",
    )
    parser.add_argument(
        "--skip-deps",
        action="store_true",
        help="의존성 자동 설치 건너뛰기",
    )
    args = parser.parse_args()

    # default mode = web (unless gui/cli chosen)
    use_web = not args.gui and not args.cli
    if args.web:
        use_web = True

    if not args.skip_deps and not getattr(sys, "frozen", False):
        try:
            from bootstrap import ensure_dependencies

            ensure_dependencies()
        except Exception as exc:
            _show_error(
                "준비 실패",
                "필요한 패키지를 자동 설치하지 못했습니다.\n\n"
                f"{exc}\n\n"
                "인터넷이 되는 PC에서 한 번 실행하거나,\n"
                "python -m pip install -r requirements.txt\n"
                "를 실행해 주세요.",
            )
            return 1

    ensure_files()

    if use_web:
        try:
            from web.app import run_web

            run_web(
                BASE_DIR,
                host=args.host,
                port=args.port,
                open_browser=not args.no_browser,
            )
            return 0
        except Exception as exc:
            _show_error("웹 UI 실행 오류", f"{exc}\n\n{traceback.format_exc()[-800:]}")
            return 1

    if args.gui:
        try:
            from src.gui_app import run_gui

            run_gui(BASE_DIR)
            return 0
        except Exception as exc:
            _show_error("GUI 실행 오류", f"{exc}\n\n{traceback.format_exc()[-800:]}")
            return 1

    # CLI
    from src.runner import JobRunner, load_config

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = BASE_DIR / config_path
    if not config_path.is_file():
        print(f"설정 파일이 없습니다: {config_path}")
        return 1
    try:
        config = load_config(config_path)
    except Exception as exc:
        print(f"설정 로드 실패: {exc}")
        return 1
    if args.dry_run:
        config["dry_run"] = True
    try:
        JobRunner(config, BASE_DIR).run_all()
        return 0
    except KeyboardInterrupt:
        print("\n사용자 중단")
        return 130
    except Exception as exc:
        print(f"\n실행 실패: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
