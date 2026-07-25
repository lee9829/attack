# -*- coding: utf-8 -*-
"""Browser-like GUI for Octo Automation (profiles, proxies, search & human click)."""
from __future__ import annotations

import csv
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional

from .octo_client import OctoClient, OctoError
from .proxy_manager import Proxy, parse_proxy_text
from .runner import (
    JobRunner,
    accounts_from_rows,
    load_config,
    parse_account_bulk_text,
    parse_account_pipe_line,
    save_config,
    validate_account_secrets,
)
from .manual_ko import MANUAL_BODY, MANUAL_TITLE
from .step_tests import STEP_CATALOG, run_step, save_feedback


APP_TITLE = "자사 검색·CTA 점검"
APP_VERSION = "1.14.0"

# Browser-chrome palette
C = {
    "bg": "#0b0e14",
    "chrome": "#12161f",
    "tabbar": "#161b26",
    "sidebar": "#0e1219",
    "card": "#171c27",
    "border": "#2a3142",
    "text": "#eef2f8",
    "muted": "#8b95a8",
    "accent": "#3dd68c",
    "accent_dim": "#1f8f5a",
    "blue": "#5b9dff",
    "warn": "#f0b429",
    "danger": "#ff6b6b",
    "input": "#0a0d13",
    "hover": "#1c2433",
    "ok": "#3dd68c",
}


USAGE_KO = """
══════════════════════════════════════
  30초 매크로 사용법 (이것만 보면 됨)
══════════════════════════════════════

① Octo API 토큰

② 구글 계정 한 줄 붙여넣기 (가장 중요)
   user@gmail.com|비밀번호|2FA시크릿

   여러 계정이면 줄마다:
   a@gmail.com|pass1|SECRET1
   b@gmail.com|pass2|SECRET2

   → [붙여넣기 반영] → 계정마다 프로필 자동 생성

③ 검색어 · 자사 도메인 · 프록시

④ [▶ 원클릭 시작]
   계정마다: 프로필 → 프록시 → 로그인 → 2FA 자동
   → 검색 → 클릭 → 다음 계정

2FA = 2fa-auth.com / Authenticator 시크릿
(코드 생성·복사·Google 입력 전부 자동)

실시간 상태에 2FA 코드·로그인 성공 표시


■ 용도 (허가된 자사 점검만)
  자사 사이트 검색 노출 확인 + 주요 CTA(메뉴/예약 등) 동작 점검.
  · 대상: 설정한 자사 도메인만
  · path 타겟/제외 · 광고 스킵 옵션 지원

■ 허용 행위
  1) Google 검색 후 자사 도메인 유기(일반) 결과만 클릭
  2) 사이트에서 스크롤·체류
  3) 메뉴 / 예약 등 CTA 버튼 클릭
  4) (선택) 재방문으로 동일 점검 반복

■ 금지
  · 구글 광고·스폰서 클릭 조작
  · 경쟁사·타인 사이트 대상
  · 무단 트래픽·부정 클릭

■ 준비
  1) Octo Browser 실행 + 로그인
  2) API 토큰 입력 (설정 → Additional → API Token)
  3) start.vbs 더블클릭

■ 설정 순서
  ① 홈 …… 토큰 → 연결 테스트
  ② 프록시 … 붙여넣기 → 검증
  ③ 프로필 … Octo 프로필 이름
  ④ 검색·점검 … 검색어 + 자사 도메인 + CTA 글자
  ⑤ ▶ 시작

■ 검색·점검 탭 입력 예 (자사)
  검색어: example-mybrand 공식
  자사 도메인: example-mybrand.com
  CTA 글자: 메뉴 / 예약
  · 「광고 결과 클릭 안 함」 체크 유지
  · 「자사 도메인만」 체크 유지

■ 로그
  프로필 · 프록시 · 출구 IP · 검색어 · 클릭한 자사 URL · CTA 가
  하단 콘솔과 logs/ 폴더에 기록됩니다.
""".strip()


class GuiApp:
    def __init__(self, root: tk.Tk, base_dir: Path):
        self.root = root
        self.base_dir = base_dir
        self.config_path = base_dir / "config.json"
        self.log_queue: queue.Queue = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        self.cancel_flag = threading.Event()
        self.validated_proxies: List[Proxy] = []
        self.is_running = False
        self._otp_result: Optional[str] = None
        self._otp_event = threading.Event()
        self._nav_btns: Dict[str, tk.Button] = {}
        self._pages: Dict[str, tk.Frame] = {}
        self._current_page = "home"

        self.root.title(f"{APP_TITLE}  v{APP_VERSION}")
        self.root.geometry("1320x860")
        self.root.minsize(1080, 720)
        self.root.configure(bg=C["bg"])
        try:
            self.root.state("zoomed")
        except tk.TclError:
            pass

        self._init_vars()
        self._build_style()
        self._build_shell()
        self._load_initial()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._drain_log_queue)

    # ---------- vars ----------
    def _init_vars(self) -> None:
        self.var_token = tk.StringVar()
        self.var_show_token = tk.BooleanVar(value=False)
        self.var_conn_status = tk.StringVar(value="● Offline — 연결 테스트 필요")
        self.var_summary = tk.StringVar(value="홈에서 계정·검색어 입력 후 ▶ 원클릭 시작")
        self.var_addr = tk.StringVar(value="octo://automation/ready")
        self.var_status = tk.StringVar(value="대기 중")
        # 실시간 매크로 상태 (2FA 코드 등 프로그램에 표시)
        self.var_live_step = tk.StringVar(value="단계: 대기")
        self.var_live_google = tk.StringVar(value="Google: —")
        self.var_live_2fa = tk.StringVar(value="2FA 코드: —")
        self.var_live_search = tk.StringVar(value="검색·클릭: —")
        self.var_live_hint = tk.StringVar(
            value="홈에서 구글 아이디 + 시크릿 키를 넣고 시작하면 2FA까지 자동입니다."
        )
        # 원클릭 입력 (홈 한 화면)
        self.var_quick_email = tk.StringVar()
        self.var_quick_password = tk.StringVar()
        self.var_quick_secret = tk.StringVar()
        self.var_quick_keyword = tk.StringVar()
        self.var_quick_domain = tk.StringVar()
        self.var_show_pw = tk.BooleanVar(value=False)
        self.var_show_secret = tk.BooleanVar(value=False)

        self.var_proxy_type = tk.StringVar(value="http")
        self.var_proxy_mode = tk.StringVar(value="round_robin")
        self.var_proxy_count = tk.StringVar(value="Proxies: 0")
        self.var_proxy_selected = tk.StringVar(value="start index: 0")

        self.var_email = tk.StringVar()
        self.var_password = tk.StringVar()
        self.var_profile = tk.StringVar(value="auto-google-1")
        self.var_notes = tk.StringVar()
        self.var_acc_otp = tk.StringVar()
        self.var_acc_secret = tk.StringVar()
        self.var_g_enabled = tk.BooleanVar(value=True)
        self.var_g_mode = tk.StringVar(value="auto")
        self.var_g_wait = tk.IntVar(value=300)
        self.var_otp_url = tk.StringVar(value="https://2fa-auth.com/")
        self.var_otp_selector = tk.StringVar()
        self.var_otp_secret = tk.StringVar()
        self.var_otp_enabled = tk.BooleanVar(value=True)
        self.current_runner: Optional[Any] = None

        self.var_search_enabled = tk.BooleanVar(value=True)
        self.var_keyword = tk.StringVar(value="")
        self.var_own_domain = tk.StringVar(value="")
        self.var_url_contains = tk.StringVar()
        self.var_url_regex = tk.StringVar()
        self.var_title_contains = tk.StringVar()
        self.var_title_regex = tk.StringVar()
        self.var_path_targets = tk.StringVar()
        self.var_path_exclude = tk.StringVar()
        self.var_require_domain = tk.BooleanVar(value=True)
        self.var_skip_ads = tk.BooleanVar(value=True)
        # 홈 UI: 체크 = 광고 클릭 허용 (skip_ads 의 반대)
        self.var_allow_ads = tk.BooleanVar(value=False)
        self.var_max_serp = tk.IntVar(value=5)
        self.var_max_clicks = tk.IntVar(value=8)
        self.var_revisit = tk.IntVar(value=0)
        self.var_dwell_min = tk.IntVar(value=4000)
        self.var_dwell_max = tk.IntVar(value=12000)
        self.var_scroll_min = tk.IntVar(value=3)
        self.var_scroll_max = tk.IntVar(value=8)
        self.var_human_scroll = tk.BooleanVar(value=True)
        self.var_mouse_wander = tk.BooleanVar(value=True)
        self.var_read_pauses = tk.BooleanVar(value=True)
        self.var_internal_click = tk.BooleanVar(value=False)
        self.var_warmup = tk.BooleanVar(value=True)

        self.var_url = tk.StringVar()
        self.var_wait_ms = tk.IntVar(value=2000)
        self.var_sel = tk.StringVar()
        self.var_sel_text = tk.StringVar()
        self.var_sel_wait = tk.IntVar(value=1500)
        self.var_sel_opt = tk.BooleanVar(value=True)

        self.var_delay = tk.IntVar(value=15)
        self.var_timeout = tk.IntVar(value=120)
        self.var_max_jobs = tk.IntVar(value=0)
        self.var_headless = tk.BooleanVar(value=False)
        self.var_stop_after = tk.BooleanVar(value=True)
        self.var_reuse = tk.BooleanVar(value=True)
        self.var_create = tk.BooleanVar(value=True)
        self.var_cloud = tk.StringVar(value="https://app.octobrowser.net/api/v2/automation")
        self.var_local = tk.StringVar(value="http://127.0.0.1:58888/api")

    # ---------- style ----------
    def _build_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        bg, card, text, muted = C["bg"], C["card"], C["text"], C["muted"]
        accent, border, inp = C["accent"], C["border"], C["input"]

        style.configure(".", background=bg, foreground=text, fieldbackground=inp)
        style.configure("TFrame", background=bg)
        style.configure("Card.TFrame", background=card)
        style.configure("TLabel", background=bg, foreground=text, font=("Segoe UI", 9))
        style.configure("Card.TLabel", background=card, foreground=text, font=("Segoe UI", 9))
        style.configure("Muted.TLabel", background=bg, foreground=muted, font=("Segoe UI", 9))
        style.configure("CardMuted.TLabel", background=card, foreground=muted, font=("Segoe UI", 9))
        style.configure("H1.TLabel", background=bg, foreground=text, font=("Segoe UI", 16, "bold"))
        style.configure("H2.TLabel", background=card, foreground=accent, font=("Segoe UI", 11, "bold"))
        style.configure("Accent.TLabel", background=bg, foreground=accent, font=("Segoe UI", 9, "bold"))
        style.configure(
            "TLabelframe",
            background=card,
            foreground=text,
            bordercolor=border,
            relief="solid",
            borderwidth=1,
        )
        style.configure(
            "TLabelframe.Label",
            background=card,
            foreground=accent,
            font=("Segoe UI", 10, "bold"),
        )
        style.configure(
            "TButton",
            background=C["hover"],
            foreground=text,
            bordercolor=border,
            padding=(10, 6),
            font=("Segoe UI", 9),
        )
        style.map("TButton", background=[("active", "#273247"), ("disabled", "#151a24")])
        style.configure(
            "Start.TButton",
            background=C["accent_dim"],
            foreground="#04140c",
            font=("Segoe UI", 11, "bold"),
            padding=(18, 10),
        )
        style.map("Start.TButton", background=[("active", accent), ("disabled", "#163526")])
        style.configure(
            "Stop.TButton",
            background="#5a2228",
            foreground="#ffd6d6",
            font=("Segoe UI", 10, "bold"),
            padding=(12, 8),
        )
        style.map("Stop.TButton", background=[("active", C["danger"])])
        style.configure(
            "TEntry",
            fieldbackground=inp,
            foreground=text,
            insertcolor=text,
            bordercolor=border,
            padding=5,
        )
        style.configure(
            "TSpinbox",
            fieldbackground=inp,
            foreground=text,
            insertcolor=text,
            bordercolor=border,
            arrowsize=12,
        )
        style.configure(
            "TCombobox",
            fieldbackground=inp,
            foreground=text,
            arrowcolor=text,
            bordercolor=border,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", inp)],
            foreground=[("readonly", text)],
        )
        style.configure(
            "TCheckbutton",
            background=card,
            foreground=text,
            font=("Segoe UI", 9),
            focuscolor=card,
        )
        style.map("TCheckbutton", background=[("active", card)])
        style.configure(
            "Treeview",
            background=inp,
            foreground=text,
            fieldbackground=inp,
            bordercolor=border,
            rowheight=28,
            font=("Segoe UI", 9),
        )
        style.configure(
            "Treeview.Heading",
            background=C["hover"],
            foreground=text,
            font=("Segoe UI", 9, "bold"),
            relief="flat",
        )
        style.map(
            "Treeview",
            background=[("selected", "#1a3d30")],
            foreground=[("selected", accent)],
        )
        style.configure(
            "Vertical.TScrollbar",
            background=C["chrome"],
            troughcolor=bg,
            bordercolor=border,
            arrowcolor=muted,
        )

    def _text(self, parent, **kw) -> tk.Text:
        opts = dict(
            bg=C["input"],
            fg=C["text"],
            insertbackground=C["text"],
            selectbackground="#1a3d30",
            selectforeground=C["accent"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=C["border"],
            highlightcolor=C["blue"],
            font=("Consolas", 10),
        )
        opts.update(kw)
        return tk.Text(parent, **opts)

    def _listbox(self, parent, **kw) -> tk.Listbox:
        opts = dict(
            bg=C["input"],
            fg=C["text"],
            selectbackground="#1a3d30",
            selectforeground=C["accent"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=C["border"],
            font=("Consolas", 10),
            activestyle="none",
        )
        opts.update(kw)
        return tk.Listbox(parent, **opts)

    def _card(self, parent, title: str = "") -> ttk.LabelFrame:
        return ttk.LabelFrame(parent, text=f"  {title}  " if title else "", padding=12)

    # ---------- shell (browser chrome) ----------
    def _build_shell(self) -> None:
        # Top chrome: traffic lights + title + fake address bar
        chrome = tk.Frame(self.root, bg=C["chrome"], height=52)
        chrome.pack(fill=tk.X)
        chrome.pack_propagate(False)

        dots = tk.Frame(chrome, bg=C["chrome"])
        dots.pack(side=tk.LEFT, padx=14, pady=16)
        for color in ("#ff5f57", "#febc2e", "#28c840"):
            c = tk.Canvas(dots, width=13, height=13, bg=C["chrome"], highlightthickness=0)
            c.create_oval(1, 1, 12, 12, fill=color, outline=color)
            c.pack(side=tk.LEFT, padx=3)

        tk.Label(
            chrome,
            text=f"  {APP_TITLE}",
            bg=C["chrome"],
            fg=C["text"],
            font=("Segoe UI", 11, "bold"),
        ).pack(side=tk.LEFT, padx=(4, 10))

        # Address bar
        addr_wrap = tk.Frame(chrome, bg=C["input"], highlightbackground=C["border"], highlightthickness=1)
        addr_wrap.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8, pady=12)
        tk.Label(addr_wrap, text="  🔒", bg=C["input"], fg=C["accent"], font=("Segoe UI", 9)).pack(
            side=tk.LEFT
        )
        tk.Entry(
            addr_wrap,
            textvariable=self.var_addr,
            bg=C["input"],
            fg=C["blue"],
            insertbackground=C["text"],
            relief=tk.FLAT,
            font=("Consolas", 10),
            readonlybackground=C["input"],
            state="readonly",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6, ipady=4)

        tk.Label(
            chrome,
            text=f"v{APP_VERSION}  ",
            bg=C["chrome"],
            fg=C["muted"],
            font=("Segoe UI", 9),
        ).pack(side=tk.RIGHT, padx=12)

        # Body: sidebar + content
        body = tk.Frame(self.root, bg=C["bg"])
        body.pack(fill=tk.BOTH, expand=True)

        side = tk.Frame(body, bg=C["sidebar"], width=180)
        side.pack(side=tk.LEFT, fill=tk.Y)
        side.pack_propagate(False)

        tk.Label(
            side,
            text="WORKSPACE",
            bg=C["sidebar"],
            fg=C["muted"],
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", padx=16, pady=(16, 8))

        nav_items = [
            ("home", "⌂  원클릭 시작"),
            ("proxies", "◈  프록시"),
            ("profiles", "◎  프로필·2FA"),
            ("search", "⌕  검색·path"),
            ("beta", "⚗  베타 테스트"),
            ("settings", "⚙  설정"),
            ("guide", "?  쉬운 사용법"),
        ]
        for key, label in nav_items:
            btn = tk.Button(
                side,
                text=label,
                anchor="w",
                bd=0,
                padx=16,
                pady=11,
                font=("Segoe UI", 10),
                bg=C["sidebar"],
                fg=C["muted"],
                activebackground=C["hover"],
                activeforeground=C["text"],
                cursor="hand2",
                command=lambda k=key: self._show_page(k),
            )
            btn.pack(fill=tk.X, padx=8, pady=2)
            self._nav_btns[key] = btn

        # content stack
        self.content = tk.Frame(body, bg=C["bg"])
        self.content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        for key in ("home", "proxies", "profiles", "search", "beta", "settings", "guide"):
            page = tk.Frame(self.content, bg=C["bg"])
            self._pages[key] = page

        self._build_page_home()
        self._build_page_proxies()
        self._build_page_profiles()
        self._build_page_search()
        self._build_page_beta()
        self._build_page_settings()
        self._build_page_guide()

        # Bottom action bar + log
        bottom = tk.Frame(self.root, bg=C["chrome"])
        bottom.pack(fill=tk.BOTH, expand=False)

        bar = tk.Frame(bottom, bg=C["chrome"])
        bar.pack(fill=tk.X, padx=10, pady=(8, 4))

        self.btn_save = ttk.Button(bar, text="저장", command=self.save_settings)
        self.btn_save.pack(side=tk.LEFT, padx=3)
        self.btn_test = ttk.Button(bar, text="연결 테스트", command=self.test_connection)
        self.btn_test.pack(side=tk.LEFT, padx=3)
        self.btn_manual = ttk.Button(bar, text="📖 메뉴얼·의견", command=self.show_admin_manual)
        self.btn_manual.pack(side=tk.LEFT, padx=6)
        self.btn_dry = ttk.Button(bar, text="미리보기", command=lambda: self.start_jobs(True))
        self.btn_dry.pack(side=tk.LEFT, padx=8)
        self.btn_start = ttk.Button(
            bar,
            text="▶  원클릭 시작",
            style="Start.TButton",
            command=lambda: self.start_jobs(False),
        )
        self.btn_start.pack(side=tk.LEFT, padx=3)
        self.btn_stop = ttk.Button(
            bar, text="■ 중지", style="Stop.TButton", command=self.stop_jobs, state=tk.DISABLED
        )
        self.btn_stop.pack(side=tk.LEFT, padx=3)

        ttk.Label(bar, textvariable=self.var_status, style="Accent.TLabel").pack(
            side=tk.LEFT, padx=16
        )

        # 실시간 상태 한 줄 (2FA 코드 포함)
        live = ttk.LabelFrame(bottom, text="  실시간 상태 · 2FA · Google · 검색  ", padding=6)
        live.pack(fill=tk.X, padx=10, pady=(0, 4))
        live_row = ttk.Frame(live)
        live_row.pack(fill=tk.X)
        for var in (
            self.var_live_step,
            self.var_live_google,
            self.var_live_2fa,
            self.var_live_search,
        ):
            ttk.Label(live_row, textvariable=var, style="Accent.TLabel").pack(
                side=tk.LEFT, padx=(0, 18)
            )
        ttk.Label(live, textvariable=self.var_live_hint, style="Muted.TLabel").pack(
            anchor="w", pady=(4, 0)
        )

        log_box = ttk.LabelFrame(
            bottom,
            text="  실행 로그 (자동 진행 내용이 여기에 전부 표시됩니다)  ",
            padding=6,
        )
        log_box.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        log_tools = ttk.Frame(log_box)
        log_tools.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(log_tools, text="로그 지우기", command=self._clear_log).pack(side=tk.LEFT, padx=2)
        ttk.Button(log_tools, text="로그 저장…", command=self._save_log).pack(side=tk.LEFT, padx=2)
        ttk.Label(
            log_tools,
            text="2FA 성공 시 「2FA 코드: 123456」 형태로 위에 표시됩니다",
            style="Muted.TLabel",
        ).pack(side=tk.LEFT, padx=10)

        log_inner = ttk.Frame(log_box)
        log_inner.pack(fill=tk.BOTH, expand=True)
        self.txt_log = self._text(log_inner, height=10, wrap=tk.WORD, font=("Consolas", 9))
        sb = ttk.Scrollbar(log_inner, command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=sb.set)
        self.txt_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        # color tags for commercial console look
        self.txt_log.tag_configure("OK", foreground="#3dd68c")
        self.txt_log.tag_configure("ERR", foreground="#ff6b6b")
        self.txt_log.tag_configure("WARN", foreground="#f0b429")
        self.txt_log.tag_configure("STEP", foreground="#c4a5ff")
        self.txt_log.tag_configure("PROXY", foreground="#5b9dff")
        self.txt_log.tag_configure("PROFILE", foreground="#6edcc0")
        self.txt_log.tag_configure("SEARCH", foreground="#7ec8ff")
        self.txt_log.tag_configure("CLICK", foreground="#ffb86c")
        self.txt_log.tag_configure("SITE", foreground="#9ae6b4")
        self.txt_log.tag_configure("SUM", foreground="#e2e8f0")
        self.txt_log.tag_configure("INFO", foreground="#c5cdd8")
        self.txt_log.tag_configure("2FA", foreground="#ffd166")

        self._show_page("home")

    def _show_page(self, key: str) -> None:
        self._current_page = key
        for k, fr in self._pages.items():
            fr.pack_forget()
        self._pages[key].pack(fill=tk.BOTH, expand=True, padx=14, pady=12)
        for k, btn in self._nav_btns.items():
            if k == key:
                btn.configure(bg=C["hover"], fg=C["accent"], font=("Segoe UI", 10, "bold"))
            else:
                btn.configure(bg=C["sidebar"], fg=C["muted"], font=("Segoe UI", 10))
        labels = {
            "home": "octo://홈",
            "proxies": "octo://프록시",
            "profiles": "octo://프로필",
            "search": "octo://자사-검색-CTA점검",
            "beta": "octo://베타-단계테스트",
            "settings": "octo://설정",
            "guide": "octo://사용법",
        }
        self.var_addr.set(labels.get(key, "octo://"))
        self.refresh_summary()

    # ---------- pages ----------
    def _build_page_home(self) -> None:
        f = self._pages["home"]
        # scrollable home for dense form
        canvas = tk.Canvas(f, bg=C["bg"], highlightthickness=0)
        scroll = ttk.Scrollbar(f, orient=tk.VERTICAL, command=canvas.yview)
        inner = tk.Frame(canvas, bg=C["bg"])
        inner.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        ttk.Label(inner, text="원클릭 매크로 · 계정 여러 개도 한 번에", style="H1.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            inner,
            text="이메일|비번|2FA시크릿 한 줄 붙여넣기 → 계정마다 Octo 프로필 + 로그인 + 2FA 자동 + 검색 클릭",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(2, 10))

        # ① Octo
        conn = self._card(inner, "① Octo API 토큰 (한 번만)")
        conn.pack(fill=tk.X, pady=4)
        row = ttk.Frame(conn)
        row.pack(fill=tk.X)
        ttk.Label(row, text="API Token", style="Card.TLabel").pack(side=tk.LEFT)
        self.ent_token = ttk.Entry(row, textvariable=self.var_token, width=52, show="•")
        self.ent_token.pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True)
        ttk.Checkbutton(
            row,
            text="보이기",
            variable=self.var_show_token,
            command=lambda: self.ent_token.configure(
                show="" if self.var_show_token.get() else "•"
            ),
        ).pack(side=tk.LEFT)
        ttk.Button(row, text="연결 테스트", command=self.test_connection).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Label(conn, textvariable=self.var_conn_status, style="H2.TLabel").pack(
            anchor="w", pady=(8, 0)
        )
        ttk.Label(
            conn,
            text="Octo Browser 실행·로그인 → Settings → Additional → API Token 복사",
            style="CardMuted.TLabel",
        ).pack(anchor="w", pady=2)

        # ② Bulk pipe paste — main UX
        bulk = self._card(
            inner,
            "② 구글 계정 한 줄 붙여넣기  (이메일|비밀번호|2FA시크릿)",
        )
        bulk.pack(fill=tk.BOTH, expand=True, pady=8)
        ttk.Label(
            bulk,
            text=(
                "예시 한 줄:\n"
                "  user@gmail.com|MyPass123|7DWQUCVH4ZBILCWK4AGGTUTPYDDS3ZCG\n"
                "여러 계정이면 줄바꿈으로 계속 붙여넣기. | 로 아이디·비번·시크릿 자동 분리.\n"
                "2FA 시크릿 = 2fa-auth.com / Authenticator 키 (계정마다 달라도 OK · 시작 시 각각 자동 인증)"
            ),
            style="CardMuted.TLabel",
            justify=tk.LEFT,
        ).pack(anchor="w")
        self.txt_bulk_accounts = self._text(
            bulk, height=6, wrap=tk.NONE, font=("Consolas", 10)
        )
        self.txt_bulk_accounts.pack(fill=tk.BOTH, expand=True, pady=6)
        # Ctrl+V 후 자동 인식 힌트
        self.txt_bulk_accounts.bind("<<Paste>>", self._on_bulk_paste_event)
        self.txt_bulk_accounts.bind("<FocusOut>", lambda _e: self._maybe_auto_parse_bulk())
        brow = ttk.Frame(bulk)
        brow.pack(fill=tk.X)
        ttk.Button(
            brow,
            text="▶ 붙여넣기 반영 + 2FA 검증",
            command=self._apply_bulk_pipe_accounts,
        ).pack(side=tk.LEFT)
        ttk.Button(brow, text="목록 비우기", command=self._clear_bulk_accounts).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(brow, text="2FA 미리보기", command=self._preview_bulk_first_2fa).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(brow, text="시작 전 점검", command=self._preflight_check).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Label(
            bulk,
            text="붙여넣기만 해도 자동 인식됩니다. 계정 수=작업 수 · 각각 프로필+프록시+로그인+2FA+검색.",
            style="CardMuted.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        # ②-B single fields (optional)
        gbox = self._card(inner, "②-B 한 계정만 직접 입력 (선택)")
        gbox.pack(fill=tk.X, pady=4)
        grid = ttk.Frame(gbox)
        grid.pack(fill=tk.X)
        labels_vars = [
            (0, "구글 아이디", self.var_quick_email, "", 36),
            (1, "비밀번호", self.var_quick_password, "•", 36),
            (2, "2FA 시크릿", self.var_quick_secret, "•", 36),
        ]
        for r, lab, var, show, w in labels_vars:
            ttk.Label(grid, text=lab, style="Card.TLabel").grid(
                row=r, column=0, sticky="w", pady=3
            )
            ent = ttk.Entry(grid, textvariable=var, width=w, show=show)
            ent.grid(row=r, column=1, sticky="we", padx=8, pady=3)
            if r == 1:
                self.ent_quick_pw = ent
            if r == 2:
                self.ent_quick_secret = ent
        grid.columnconfigure(1, weight=1)
        show_row = ttk.Frame(gbox)
        show_row.pack(fill=tk.X, pady=2)
        ttk.Checkbutton(
            show_row,
            text="비번 보이기",
            variable=self.var_show_pw,
            command=lambda: self.ent_quick_pw.configure(
                show="" if self.var_show_pw.get() else "•"
            ),
        ).pack(side=tk.LEFT)
        ttk.Checkbutton(
            show_row,
            text="시크릿 보이기",
            variable=self.var_show_secret,
            command=lambda: self.ent_quick_secret.configure(
                show="" if self.var_show_secret.get() else "•"
            ),
        ).pack(side=tk.LEFT, padx=10)
        ttk.Button(show_row, text="이 계정 추가", command=self._apply_quick_account).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(show_row, text="2FA 미리보기", command=self._preview_quick_2fa).pack(
            side=tk.LEFT
        )

        # ③ Search
        sbox = self._card(inner, "③ 검색어 · 자사 도메인")
        sbox.pack(fill=tk.X, pady=4)
        sgrid = ttk.Frame(sbox)
        sgrid.pack(fill=tk.X)
        ttk.Label(sgrid, text="검색어", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(sgrid, textvariable=self.var_quick_keyword, width=48).grid(
            row=0, column=1, sticky="we", padx=8, pady=4
        )
        ttk.Label(sgrid, text="자사 도메인", style="Card.TLabel").grid(
            row=1, column=0, sticky="w"
        )
        ttk.Entry(sgrid, textvariable=self.var_quick_domain, width=48).grid(
            row=1, column=1, sticky="we", padx=8, pady=4
        )
        sgrid.columnconfigure(1, weight=1)
        ttk.Label(
            sbox,
            text=(
                "여러 검색어: 카지노사이트/카지노사이트순위/카지노사이트추천\n"
                "→ 앞 검색어에 타겟(광고/자사) 없으면 다음 검색어로 자동 폴백\n"
                "도메인 예: mysite.com"
            ),
            style="CardMuted.TLabel",
            justify=tk.LEFT,
        ).pack(anchor="w", pady=4)
        def _sync_ads_from_home() -> None:
            # 홈 체크 ON = 광고 허용 = skip_ads False
            self.var_skip_ads.set(not bool(self.var_allow_ads.get()))

        def _sync_ads_to_home(*_a) -> None:
            try:
                self.var_allow_ads.set(not bool(self.var_skip_ads.get()))
            except Exception:
                pass

        ttk.Checkbutton(
            sbox,
            text="타겟 광고 클릭 허용 (광고·스폰서 포함 · 광고 찾을 때 ON)",
            variable=self.var_allow_ads,
            command=_sync_ads_from_home,
        ).pack(anchor="w", pady=2)
        try:
            self.var_skip_ads.trace_add("write", lambda *_: _sync_ads_to_home())
        except Exception:
            pass

        # ④ Proxy mini paste
        pbox = self._card(inner, "④ 프록시 (선택 · 한 줄에 host:port:user:pass)")
        pbox.pack(fill=tk.BOTH, expand=True, pady=8)
        self.txt_quick_proxies = self._text(pbox, height=4, wrap=tk.NONE, font=("Consolas", 9))
        self.txt_quick_proxies.pack(fill=tk.BOTH, expand=True)
        prow = ttk.Frame(pbox)
        prow.pack(fill=tk.X, pady=4)
        ttk.Button(prow, text="프록시에 반영·검증", command=self._apply_quick_proxies).pack(
            side=tk.LEFT
        )
        ttk.Label(
            prow,
            text="비워 두면 프록시 탭에 이미 있는 목록을 사용합니다.",
            style="CardMuted.TLabel",
        ).pack(side=tk.LEFT, padx=10)

        # pipeline + start
        pipe = self._card(inner, "자동으로 하는 일 (시작 버튼 한 번)")
        pipe.pack(fill=tk.X, pady=6)
        ttk.Label(
            pipe,
            justify=tk.LEFT,
            style="Card.TLabel",
            text=(
                "계정마다 반복:\n"
                "① Octo 프로필(계정별)  →  ② 프록시 로테이션  →  ③ 브라우저 실행\n"
                "④ 그 계정 아이디·비번 입력  →  ⑤ 그 계정 2FA 시크릿으로 코드 자동 입력\n"
                "⑥ 검색 → 자사 클릭·체류  →  ⑦ 다음 계정으로…"
            ),
        ).pack(anchor="w")
        act = ttk.Frame(pipe)
        act.pack(fill=tk.X, pady=(10, 0))
        self.btn_start_home = ttk.Button(
            act,
            text="▶  원클릭 시작 (전부 자동)",
            style="Start.TButton",
            command=lambda: self.start_jobs(False),
        )
        self.btn_start_home.pack(side=tk.LEFT)
        ttk.Button(
            act, text="설정만 저장", command=self._quick_save_all
        ).pack(side=tk.LEFT, padx=8)
        ttk.Button(act, text="쉬운 사용법", command=lambda: self._show_page("guide")).pack(
            side=tk.LEFT
        )

        sumf = self._card(inner, "현재 설정 요약")
        sumf.pack(fill=tk.X, pady=8)
        ttk.Label(sumf, textvariable=self.var_summary, style="Card.TLabel", justify=tk.LEFT).pack(
            anchor="w"
        )

    def _build_page_proxies(self) -> None:
        f = self._pages["proxies"]
        ttk.Label(f, text="프록시", style="H1.TLabel").pack(anchor="w")
        ttk.Label(
            f,
            text="프로필마다 다른 프록시를 Octo API 로 자동 주입합니다. 한 줄 형식: host:port:user:pass",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(0, 8))

        top = ttk.Frame(f)
        top.pack(fill=tk.X, pady=4)
        ttk.Label(top, text="Type").pack(side=tk.LEFT)
        ttk.Combobox(
            top,
            textvariable=self.var_proxy_type,
            values=["http", "https", "socks5", "socks"],
            width=10,
            state="readonly",
        ).pack(side=tk.LEFT, padx=6)
        ttk.Label(top, text="Mode").pack(side=tk.LEFT, padx=(12, 0))
        ttk.Combobox(
            top,
            textvariable=self.var_proxy_mode,
            values=["round_robin", "from_selected", "fixed"],
            width=14,
            state="readonly",
        ).pack(side=tk.LEFT, padx=6)
        ttk.Label(
            top,
            text="round_robin=순차  ·  from_selected=선택부터  ·  fixed=고정",
            style="Muted.TLabel",
        ).pack(side=tk.LEFT, padx=8)

        paste = self._card(f, "Paste proxies")
        paste.pack(fill=tk.BOTH, expand=True, pady=6)
        self.txt_proxies = self._text(paste, height=12, wrap=tk.NONE)
        self.txt_proxies.pack(fill=tk.BOTH, expand=True)

        act = ttk.Frame(f)
        act.pack(fill=tk.X, pady=4)
        ttk.Button(act, text="✓ 검증 · 반영", command=self.validate_proxies).pack(
            side=tk.LEFT, padx=3
        )
        ttk.Button(act, text="파일 불러오기", command=self.load_proxy_file).pack(
            side=tk.LEFT, padx=3
        )
        ttk.Button(act, text="비우기", command=self.clear_proxies).pack(side=tk.LEFT, padx=3)
        ttk.Label(act, textvariable=self.var_proxy_count, style="Accent.TLabel").pack(
            side=tk.LEFT, padx=12
        )

        lst = self._card(f, "Validated list (click = start index)")
        lst.pack(fill=tk.BOTH, expand=True, pady=4)
        self.lst_proxies = self._listbox(lst, height=7, selectmode=tk.SINGLE)
        self.lst_proxies.pack(fill=tk.BOTH, expand=True)
        self.lst_proxies.bind("<<ListboxSelect>>", self._on_proxy_select)
        ttk.Label(f, textvariable=self.var_proxy_selected, style="Muted.TLabel").pack(anchor="w")

    def _build_page_profiles(self) -> None:
        f = self._pages["profiles"]
        ttk.Label(f, text="프로필", style="H1.TLabel").pack(anchor="w")
        ttk.Label(
            f,
            text="Octo 프로필 단위로 작업합니다. 이름만 있어도 생성·프록시 주입·실행이 됩니다.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(0, 8))

        acc = self._card(f, "Profile rows")
        acc.pack(fill=tk.BOTH, expand=True)
        cols = ("email", "password", "profile_title", "otp_secret", "otp_url", "notes")
        self.tree_acc = ttk.Treeview(acc, columns=cols, show="headings", height=9)
        for c, t, w in (
            ("email", "Google 이메일", 160),
            ("password", "비밀번호", 90),
            ("profile_title", "프로필명", 120),
            ("otp_secret", "2FA시크릿", 140),
            ("otp_url", "2FA URL", 160),
            ("notes", "메모", 80),
        ):
            self.tree_acc.heading(c, text=t)
            self.tree_acc.column(c, width=w)
        sb_acc = ttk.Scrollbar(acc, command=self.tree_acc.yview)
        self.tree_acc.configure(yscrollcommand=sb_acc.set)
        self.tree_acc.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        sb_acc.pack(side=tk.RIGHT, fill=tk.Y)

        form = ttk.Frame(f)
        form.pack(fill=tk.X, pady=8)
        for label, var, w, show in (
            ("email", self.var_email, 16, ""),
            ("password", self.var_password, 10, "•"),
            ("profile", self.var_profile, 12, ""),
            ("2FA시크릿", self.var_acc_secret, 16, "•"),
            ("2FA URL", self.var_acc_otp, 18, ""),
            ("notes", self.var_notes, 8, ""),
        ):
            ttk.Label(form, text=label).pack(side=tk.LEFT, padx=2)
            ttk.Entry(form, textvariable=var, width=w, show=show).pack(side=tk.LEFT, padx=2)
        ttk.Button(form, text="추가", command=self.add_account_row).pack(side=tk.LEFT, padx=6)
        ttk.Button(form, text="삭제", command=self.del_account_row).pack(side=tk.LEFT)
        ttk.Button(form, text="CSV", command=self.load_accounts_csv).pack(side=tk.LEFT, padx=6)
        ttk.Button(form, text="샘플", command=self.add_sample_account).pack(side=tk.LEFT)

        g = self._card(f, "Google 로그인 + 2FA · https://2fa-auth.com/")
        g.pack(fill=tk.X, pady=6)
        row1 = ttk.Frame(g)
        row1.pack(fill=tk.X)
        ttk.Checkbutton(
            row1, text="Google 로그인 사용", variable=self.var_g_enabled
        ).pack(side=tk.LEFT)
        ttk.Label(row1, text="모드", style="Card.TLabel").pack(side=tk.LEFT, padx=(12, 4))
        ttk.Combobox(
            row1,
            textvariable=self.var_g_mode,
            values=["auto", "manual", "skip"],
            width=10,
            state="readonly",
        ).pack(side=tk.LEFT)
        ttk.Label(row1, text="대기초", style="Card.TLabel").pack(side=tk.LEFT, padx=(12, 4))
        ttk.Spinbox(row1, from_=30, to=1800, textvariable=self.var_g_wait, width=8).pack(
            side=tk.LEFT
        )
        ttk.Label(
            row1,
            text="  auto → 아이디·비번 → 2FA 자동(2fa-auth.com)",
            style="CardMuted.TLabel",
        ).pack(side=tk.LEFT, padx=8)

        row2 = ttk.Frame(g)
        row2.pack(fill=tk.X, pady=(8, 0))
        ttk.Checkbutton(
            row2, text="2FA 자동 (2fa-auth.com)", variable=self.var_otp_enabled
        ).pack(side=tk.LEFT)
        ttk.Label(row2, text="2FA 시크릿 키", style="Card.TLabel").pack(
            side=tk.LEFT, padx=(10, 4)
        )
        ttk.Entry(row2, textvariable=self.var_otp_secret, width=28, show="•").pack(
            side=tk.LEFT, padx=2
        )
        ttk.Button(row2, text="코드 테스트", command=self._test_totp_secret).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(row2, text="사이트 열기", command=self._open_2fa_auth_site).pack(
            side=tk.LEFT
        )

        row3 = ttk.Frame(g)
        row3.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(row3, text="백업 URL", style="Card.TLabel").pack(side=tk.LEFT)
        ttk.Entry(row3, textvariable=self.var_otp_url, width=40).pack(
            side=tk.LEFT, padx=6, fill=tk.X, expand=True
        )
        ttk.Label(row3, text="CSS", style="Card.TLabel").pack(side=tk.LEFT, padx=(6, 2))
        ttk.Entry(row3, textvariable=self.var_otp_selector, width=12).pack(side=tk.LEFT)
        ttk.Label(
            g,
            text="사용법: 2fa-auth.com / Google Authenticator 의 시크릿 키를 붙여넣기 → "
            "[코드 테스트]로 6자리 확인 → 계정 행에도 시크릿 입력 후 저장. "
            "로그인 시 같은 알고리즘으로 자동 입력합니다. "
            "(파이프 형식 note|id|SECRET|… 도 가능 · 실패 시 팝업)",
            style="CardMuted.TLabel",
        ).pack(anchor="w", pady=(6, 0))

    def _build_page_search(self) -> None:
        f = self._pages["search"]
        # scrollable canvas for dense form
        canvas = tk.Canvas(f, bg=C["bg"], highlightthickness=0)
        scroll = ttk.Scrollbar(f, orient=tk.VERTICAL, command=canvas.yview)
        inner = tk.Frame(canvas, bg=C["bg"])
        inner.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        ttk.Label(inner, text="검색 · 자사 노출 · CTA 점검", style="H1.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            inner,
            text="검색어 → 자사 도메인 유기 결과만 → 스크롤·체류 → 메뉴/예약 CTA · 광고 클릭 안 함",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(0, 8))

        s = self._card(inner, "① 검색 + 자사 사이트 매칭")
        s.pack(fill=tk.X, pady=4)
        ttk.Checkbutton(
            s, text="검색 점검 흐름 ON (권장)", variable=self.var_search_enabled
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=2)

        def row(r, label, var, width=48, hint=""):
            ttk.Label(s, text=label, style="Card.TLabel").grid(
                row=r, column=0, sticky="w", pady=4
            )
            ttk.Entry(s, textvariable=var, width=width).grid(
                row=r, column=1, sticky="we", padx=8, pady=4
            )
            if hint:
                ttk.Label(s, text=hint, style="CardMuted.TLabel").grid(
                    row=r, column=2, sticky="w"
                )

        row(
            1,
            "기본 검색어",
            self.var_keyword,
            hint="여러 개: 카지노사이트/카지노사이트순위/카지노사이트추천 (폴백)",
        )
        row(2, "대표 도메인", self.var_own_domain, hint="목록 없을 때 사용")
        row(3, "URL 특징", self.var_url_contains, hint="쉼표/여러줄: menu,/kr")
        row(4, "URL 정규식", self.var_url_regex, hint=r"여러 줄 OR 가능")
        row(5, "결과 제목", self.var_title_contains, hint="쉼표: 공식")
        row(
            6,
            "path 타겟",
            self.var_path_targets,
            hint="광고 제목옆 path · 이 path만 클릭  예: promo, /landing",
        )
        row(
            7,
            "path 제외",
            self.var_path_exclude,
            hint="이 path 발견 시 클릭 안 함  예: blog, /old",
        )
        ttk.Checkbutton(
            s,
            text="자사 도메인만 클릭 (300개 목록 중 일치만)",
            variable=self.var_require_domain,
        ).grid(row=8, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Checkbutton(
            s,
            text="광고·스폰서 결과 클릭 안 함 (path 타겟 광고 치려면 OFF)",
            variable=self.var_skip_ads,
        ).grid(row=9, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Label(
            s,
            text="path = Google 광고/결과에 제목 옆 작은 경로 (example.com › promo › sale). "
            "타겟에 넣으면 그 path만, 제외에 넣으면 그 path는 스킵.",
            style="CardMuted.TLabel",
        ).grid(row=10, column=0, columnspan=3, sticky="w", pady=(2, 4))
        s.columnconfigure(1, weight=1)

        bulk = self._card(inner, "①-B 자사 사이트 목록 · 추가 검색어 (최대 수백 개)")
        bulk.pack(fill=tk.BOTH, expand=True, pady=8)
        ttk.Label(
            bulk,
            text=(
                "도메인 한 줄에 하나 (300개 OK) · domains.txt 자동 로드 · "
                "검색어: 한 줄에 하나 또는 A/B/C 폴백 · keywords.txt"
            ),
            style="CardMuted.TLabel",
        ).pack(anchor="w")
        bulk_row = ttk.Frame(bulk)
        bulk_row.pack(fill=tk.BOTH, expand=True, pady=6)
        left = ttk.Frame(bulk_row)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))
        right = ttk.Frame(bulk_row)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ttk.Label(left, text="자사 도메인 목록", style="Card.TLabel").pack(anchor="w")
        self.txt_domains = self._text(left, height=8, wrap=tk.NONE, font=("Consolas", 9))
        self.txt_domains.pack(fill=tk.BOTH, expand=True)
        ttk.Label(right, text="추가 검색어 목록", style="Card.TLabel").pack(anchor="w")
        self.txt_keywords = self._text(right, height=8, wrap=tk.NONE, font=("Consolas", 9))
        self.txt_keywords.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            bulk,
            text="예 도메인: brand1.com / shop.brand2.co.kr · 예 검색어: brand1 공식 / brand2 예약",
            style="CardMuted.TLabel",
        ).pack(anchor="w", pady=4)

        h = self._card(inner, "② 사람처럼 행동")
        h.pack(fill=tk.X, pady=8)
        opt = ttk.Frame(h)
        opt.pack(fill=tk.X)
        for text, var in (
            ("스크롤", self.var_human_scroll),
            ("마우스 이동", self.var_mouse_wander),
            ("읽는 시간", self.var_read_pauses),
            ("Google 워밍업", self.var_warmup),
            ("내부 링크 탐색", self.var_internal_click),
        ):
            ttk.Checkbutton(opt, text=text, variable=var).pack(side=tk.LEFT, padx=6)

        nums = ttk.Frame(h)
        nums.pack(fill=tk.X, pady=8)
        pairs = [
            ("SERP 페이지", self.var_max_serp, 1, 15),
            ("검색어당 클릭수", self.var_max_clicks, 1, 50),
            ("체류 min ms", self.var_dwell_min, 500, 120000),
            ("체류 max ms", self.var_dwell_max, 500, 120000),
            ("스크롤 min", self.var_scroll_min, 1, 20),
            ("스크롤 max", self.var_scroll_max, 1, 30),
        ]
        for i, (lab, var, lo, hi) in enumerate(pairs):
            ttk.Label(nums, text=lab, style="Card.TLabel").grid(
                row=i // 3, column=(i % 3) * 2, sticky="w", padx=4, pady=3
            )
            ttk.Spinbox(nums, from_=lo, to=hi, textvariable=var, width=8).grid(
                row=i // 3, column=(i % 3) * 2 + 1, sticky="w", padx=4
            )

        c = self._card(inner, "③ 자사 CTA 버튼 (메뉴 / 예약 등 보이는 글자)")
        c.pack(fill=tk.BOTH, expand=True, pady=4)
        cols = ("text", "selector", "wait", "optional")
        self.tree_clicks = ttk.Treeview(c, columns=cols, show="headings", height=5)
        self.tree_clicks.heading("text", text="배너/버튼 글자")
        self.tree_clicks.heading("selector", text="CSS (선택)")
        self.tree_clicks.heading("wait", text="대기ms")
        self.tree_clicks.heading("optional", text="없어도 계속")
        self.tree_clicks.column("text", width=260)
        self.tree_clicks.column("selector", width=140)
        self.tree_clicks.column("wait", width=80)
        self.tree_clicks.column("optional", width=90)
        self.tree_clicks.pack(fill=tk.BOTH, expand=True)

        form = ttk.Frame(inner)
        form.pack(fill=tk.X, pady=6)
        ttk.Label(form, text="글자").pack(side=tk.LEFT)
        ttk.Entry(form, textvariable=self.var_sel_text, width=22).pack(side=tk.LEFT, padx=4)
        ttk.Label(form, text="CSS").pack(side=tk.LEFT)
        ttk.Entry(form, textvariable=self.var_sel, width=16).pack(side=tk.LEFT, padx=4)
        ttk.Label(form, text="ms").pack(side=tk.LEFT)
        ttk.Spinbox(form, from_=0, to=60000, textvariable=self.var_sel_wait, width=7).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Checkbutton(form, text="optional", variable=self.var_sel_opt).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(form, text="추가", command=self.add_click_row).pack(side=tk.LEFT, padx=4)
        ttk.Button(form, text="삭제", command=self.del_click_row).pack(side=tk.LEFT)

        d = self._card(inner, "④ (선택) 검색 OFF 시 직접 URL")
        d.pack(fill=tk.X, pady=8)
        ttk.Label(d, text="URL", style="Card.TLabel").pack(side=tk.LEFT)
        ttk.Entry(d, textvariable=self.var_url, width=60).pack(side=tk.LEFT, padx=8)
        ttk.Label(d, text="wait ms", style="Card.TLabel").pack(side=tk.LEFT)
        ttk.Spinbox(d, from_=0, to=60000, textvariable=self.var_wait_ms, width=8).pack(
            side=tk.LEFT, padx=4
        )

        ttk.Label(
            inner,
            text="예: 검색어=우리카페 강남 · 도메인=ourcafe.co.kr · URL특징=menu · 배너=예약하기 · 재방문=1",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=6)

    def _build_page_settings(self) -> None:
        f = self._pages["settings"]
        ttk.Label(f, text="설정", style="H1.TLabel").pack(anchor="w", pady=(0, 8))
        box = self._card(f, "Job options")
        box.pack(fill=tk.X)
        grid = ttk.Frame(box)
        grid.pack(fill=tk.X)
        for r, label, var, lo, hi in (
            (0, "작업 간 대기(초)", self.var_delay, 0, 600),
            (1, "프로필 시작 타임아웃", self.var_timeout, 30, 600),
            (2, "최대 작업 수 (0=전체)", self.var_max_jobs, 0, 999),
        ):
            ttk.Label(grid, text=label, style="Card.TLabel").grid(
                row=r, column=0, sticky="w", pady=4
            )
            ttk.Spinbox(grid, from_=lo, to=hi, textvariable=var, width=10).grid(
                row=r, column=1, sticky="w", padx=8
            )
        ttk.Checkbutton(box, text="헤드리스 (보통 OFF)", variable=self.var_headless).pack(
            anchor="w", pady=2
        )
        ttk.Checkbutton(box, text="작업 후 프로필 중지", variable=self.var_stop_after).pack(
            anchor="w"
        )
        ttk.Checkbutton(box, text="같은 이름 프로필 재사용", variable=self.var_reuse).pack(
            anchor="w"
        )
        ttk.Checkbutton(box, text="없으면 프로필 생성", variable=self.var_create).pack(
            anchor="w"
        )

        api = self._card(f, "API endpoints")
        api.pack(fill=tk.X, pady=10)
        ttk.Label(api, text="Cloud", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(api, textvariable=self.var_cloud, width=70).grid(
            row=0, column=1, sticky="we", padx=6, pady=4
        )
        ttk.Label(api, text="Local", style="Card.TLabel").grid(row=1, column=0, sticky="w")
        ttk.Entry(api, textvariable=self.var_local, width=70).grid(
            row=1, column=1, sticky="we", padx=6, pady=4
        )
        api.columnconfigure(1, weight=1)

    def _build_page_beta(self) -> None:
        """Beta lab: one-by-one tests + user feedback form."""
        f = self._pages["beta"]
        # clear if double-defined — rewrite cleanly once
        for w in f.winfo_children():
            w.destroy()

        ttk.Label(f, text="베타 테스트 · 단계별 검증", style="H1.TLabel").pack(anchor="w")
        ttk.Label(
            f,
            text=(
                "권장 순서: T1→T2→T3→T4→T5 후 T6 로그인, T7 검색, T8 CTA. "
                "각 단계 후 아래 의견(좋음/보통/문제)을 남겨 주세요."
            ),
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(2, 8))

        self.var_beta_last = tk.StringVar(value="마지막 결과: 아직 없음")
        self.var_beta_rating = tk.StringVar(value="보통")
        self.var_beta_comment = tk.StringVar()
        self._beta_last_result = {}
        self._beta_last_step = ""

        steps_box = self._card(f, "단계 테스트 (하나만 골라 실행)")
        steps_box.pack(fill=tk.BOTH, expand=True, pady=4)

        # grid of steps 3 columns
        grid = ttk.Frame(steps_box)
        grid.pack(fill=tk.BOTH, expand=True)
        for i, step in enumerate(STEP_CATALOG):
            r, c = divmod(i, 2)
            cell = ttk.Frame(grid, padding=4)
            cell.grid(row=r, column=c, sticky="nsew", padx=4, pady=4)
            grid.columnconfigure(c, weight=1)
            ttk.Label(cell, text=step["title"], style="H2.TLabel").pack(anchor="w")
            ttk.Label(cell, text=step["desc"], style="CardMuted.TLabel", wraplength=360).pack(
                anchor="w"
            )
            ttk.Button(
                cell,
                text=f"▶ {step['id']} 만 실행",
                command=lambda s=step: self.run_beta_step(s),
            ).pack(anchor="w", pady=6)

        extra = ttk.Frame(steps_box)
        extra.pack(fill=tk.X, pady=4)
        ttk.Button(
            extra,
            text="▶ T7+ 검색 3클릭 테스트",
            command=lambda: self.run_beta_step(
                {"id": "T7+", "title": "검색 3클릭", "fn": "search3"}
            ),
        ).pack(side=tk.LEFT, padx=4)

        fb = self._card(f, "결과 · 사용자 의견")
        fb.pack(fill=tk.X, pady=8)
        ttk.Label(fb, textvariable=self.var_beta_last, style="Accent.TLabel").pack(
            anchor="w"
        )
        row = ttk.Frame(fb)
        row.pack(fill=tk.X, pady=6)
        ttk.Label(row, text="평가", style="Card.TLabel").pack(side=tk.LEFT)
        ttk.Combobox(
            row,
            textvariable=self.var_beta_rating,
            values=["좋음", "보통", "문제있음", "재현불가"],
            width=12,
            state="readonly",
        ).pack(side=tk.LEFT, padx=8)
        ttk.Label(row, text="의견", style="Card.TLabel").pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.var_beta_comment, width=50).pack(
            side=tk.LEFT, padx=6, fill=tk.X, expand=True
        )
        ttk.Button(row, text="의견 저장", command=self.save_beta_feedback).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Label(
            fb,
            text="저장 위치: feedback/feedback_단계_시간.txt  ·  콘솔에도 실시간 로그",
            style="CardMuted.TLabel",
        ).pack(anchor="w")

    def run_beta_step(self, step: Dict[str, Any]) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showwarning("실행 중", "다른 작업이 끝날 때까지 기다리거나 STOP 하세요.")
            return
        try:
            cfg = self.collect_config()
        except Exception as exc:
            messagebox.showerror("설정 오류", str(exc))
            return

        step_id = str(step.get("id") or "?")
        fn = str(step.get("fn") or "")
        self.cancel_flag.clear()
        self._set_running(True)
        self.log("=" * 50)
        self.log(f"[BETA] {step.get('title')} 시작")

        def work() -> None:
            result: Dict[str, Any] = {}
            try:
                result = run_step(
                    fn,
                    cfg,
                    self.base_dir,
                    self.log,
                    ask_2fa=self.ask_2fa_code,
                )
                ok = bool(result.get("ok"))
                self._beta_last_result = result
                self._beta_last_step = step_id
                msg = f"마지막 결과: {step_id} → {'성공' if ok else '실패/미완료'}  {result}"
                self.root.after(0, lambda: self.var_beta_last.set(msg[:220]))
                if ok:
                    self.log(f"[BETA][OK] {step_id} 통과")
                else:
                    self.log(f"[BETA][WARN] {step_id} 미완료 — 로그 확인")
            except Exception as exc:
                self._beta_last_result = {"ok": False, "error": str(exc)}
                self._beta_last_step = step_id
                self.log(f"[BETA][ERR] {step_id}: {exc}")
                self.root.after(
                    0,
                    lambda: self.var_beta_last.set(f"마지막 결과: {step_id} → 오류 {exc}")
                    if True
                    else None,
                )
                self.root.after(
                    0, lambda: messagebox.showerror(f"{step_id} 실패", str(exc))
                )
            finally:
                self.root.after(0, self._unlock_ui_after_job)

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def save_beta_feedback(self) -> None:
        step = self._beta_last_step or "NONE"
        try:
            path = save_feedback(
                self.base_dir,
                step_id=step,
                rating=self.var_beta_rating.get(),
                comment=self.var_beta_comment.get(),
                result=self._beta_last_result,
            )
            self.log(f"[안내] 베타 의견 저장: {path}")
            messagebox.showinfo("저장됨", f"피드백 저장:\n{path}")
            self.var_beta_comment.set("")
        except Exception as exc:
            messagebox.showerror("저장 실패", str(exc))

    def show_admin_manual(self) -> None:
        """관리자 메뉴얼 팝업 + 의견 입력."""
        win = tk.Toplevel(self.root)
        win.title(MANUAL_TITLE)
        win.configure(bg=C["chrome"])
        win.geometry("820x640")
        win.transient(self.root)
        try:
            win.grab_set()
        except tk.TclError:
            pass

        top = tk.Frame(win, bg=C["chrome"], padx=12, pady=10)
        top.pack(fill=tk.X)
        tk.Label(
            top,
            text="관리자 메뉴얼 · Octo API · 기능 · 로그 읽는 법",
            bg=C["chrome"],
            fg=C["accent"],
            font=("Segoe UI", 13, "bold"),
        ).pack(anchor="w")
        tk.Label(
            top,
            text="아래 내용을 확인한 뒤 평가·의견을 남겨 주세요. (feedback 폴더 저장)",
            bg=C["chrome"],
            fg=C["muted"],
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(4, 0))

        body = tk.Frame(win, bg=C["bg"], padx=10, pady=6)
        body.pack(fill=tk.BOTH, expand=True)
        txt = tk.Text(
            body,
            wrap=tk.WORD,
            font=("Segoe UI", 10),
            bg=C["input"],
            fg=C["text"],
            insertbackground=C["text"],
            relief=tk.FLAT,
            padx=10,
            pady=10,
        )
        sb = ttk.Scrollbar(body, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        txt.insert("1.0", MANUAL_BODY)
        txt.configure(state=tk.DISABLED)

        foot = tk.Frame(win, bg=C["chrome"], padx=12, pady=10)
        foot.pack(fill=tk.X)
        tk.Label(
            foot, text="관리자 평가", bg=C["chrome"], fg=C["text"], font=("Segoe UI", 9, "bold")
        ).pack(side=tk.LEFT)
        var_rate = tk.StringVar(value="보통")
        ttk.Combobox(
            foot,
            textvariable=var_rate,
            values=["좋음", "보통", "문제있음", "개선필요", "재현불가"],
            width=12,
            state="readonly",
        ).pack(side=tk.LEFT, padx=8)
        tk.Label(foot, text="의견", bg=C["chrome"], fg=C["text"]).pack(side=tk.LEFT)
        var_cmt = tk.StringVar()
        ttk.Entry(foot, textvariable=var_cmt, width=40).pack(side=tk.LEFT, padx=6, fill=tk.X, expand=True)

        def save_and_close(save: bool) -> None:
            if save:
                try:
                    path = save_feedback(
                        self.base_dir,
                        step_id="MANUAL",
                        rating=var_rate.get(),
                        comment=var_cmt.get(),
                        result={"source": "admin_manual_popup", "version": APP_VERSION},
                    )
                    self.log(f"[안내] 메뉴얼 의견 저장: {path}")
                    messagebox.showinfo("저장됨", f"의견이 저장되었습니다.\n{path}", parent=win)
                except Exception as exc:
                    messagebox.showerror("저장 실패", str(exc), parent=win)
                    return
            try:
                win.grab_release()
            except tk.TclError:
                pass
            win.destroy()

        ttk.Button(foot, text="의견 저장 후 닫기", command=lambda: save_and_close(True)).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(foot, text="닫기", command=lambda: save_and_close(False)).pack(side=tk.LEFT)
        win.protocol("WM_DELETE_WINDOW", lambda: save_and_close(False))

    def _build_page_guide(self) -> None:
        f = self._pages["guide"]
        ttk.Label(f, text="사용법", style="H1.TLabel").pack(anchor="w")
        ttk.Label(
            f,
            text="처음 쓰는 분은 이 페이지만 읽고 START 하면 됩니다.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(0, 8))
        box = self._card(f, "Guide")
        box.pack(fill=tk.BOTH, expand=True)
        txt = self._text(box, wrap=tk.WORD, font=("Segoe UI", 10), height=28)
        txt.pack(fill=tk.BOTH, expand=True)
        txt.insert("1.0", USAGE_KO)
        txt.configure(state=tk.DISABLED)

    # ---------- helpers ----------
    def log(self, msg: str) -> None:
        self.log_queue.put(msg)

    def _update_live_status(self, msg: str) -> None:
        """Parse log lines into the big live status strip (2FA code visible)."""
        m = msg or ""
        low = m.lower()
        # 2FA code display
        import re as _re

        mcode = _re.search(
            r"(?:2FA|2fa|TOTP|코드)\D{0,20}(?:생성|수신|자동|입력|미리보기)?[^\d]{0,12}(\d{6})",
            m,
        )
        if not mcode:
            mcode = _re.search(r"2FA 코드[:\s=]+(\d{6})", m)
        if mcode:
            code = mcode.group(1)
            self.var_live_2fa.set(f"2FA 코드: {code}")
            self.var_live_hint.set(f"2FA 자동 코드 {code} 를 Google에 입력 중…")
        if "2차 인증 통과" in m or ("2fa" in low and "성공" in m) or "인증 성공" in m:
            self.var_live_google.set("Google: 2FA 통과 · 로그인 성공")
            cur = self.var_live_2fa.get()
            if "코드:" in cur and any(ch.isdigit() for ch in cur):
                self.var_live_2fa.set(cur.split("·")[0].strip() + " · 인증 완료")
            self.var_live_hint.set("Google 2FA 인증 완료 — 검색 단계로 진행합니다.")
            self.var_live_step.set("단계: Google 로그인 완료")
        elif "google" in low and "로그인" in m and ("성공" in m or "ok=true" in low):
            self.var_live_google.set("Google: 로그인 성공")
            self.var_live_step.set("단계: 로그인 완료")
        elif "2단계 인증" in m or ("2fa" in low and "감지" in m) or "자동 코드 생성" in m:
            self.var_live_step.set("단계: 2FA 자동 처리 중")
            self.var_live_google.set("Google: 2FA 화면")
        elif "아이디" in m or ("이메일" in m and "입력" in m):
            self.var_live_google.set("Google: 아이디 입력 중")
            self.var_live_step.set("단계: Google 로그인")
        elif "비밀번호" in m and "입력" in m:
            self.var_live_google.set("Google: 비밀번호 입력 중")
        if "[SEARCH]" in m or "[검색]" in m:
            self.var_live_step.set("단계: 검색·클릭")
            if "검색어" in m or "keyword" in low:
                self.var_live_search.set(f"검색·클릭: {m[-80:]}")
            if "클릭" in m and ("확정" in m or "도착" in m or "CLICK" in m):
                self.var_live_search.set(f"클릭 완료: {m[-70:]}")
                self.var_live_hint.set("자사 사이트 클릭 완료 — 체류·CTA 진행 중")
        if "[PROXY]" in m or "출구 IP" in m or "출구IP" in m:
            self.var_live_step.set("단계: 프록시·프로필")
        if "ERR" in m.upper() or "실패" in m:
            if "2fa" in low or "2차" in m:
                self.var_live_2fa.set("2FA 코드: 실패 — 로그 확인")
                self.var_live_hint.set("2FA 실패: 시크릿 키를 확인하거나 로그를 보세요.")

    def _log_tag(self, msg: str) -> str:
        u = msg.upper()
        if "2FA" in u or "2차 인증" in msg or "TOTP" in u:
            return "2FA"
        for tag in (
            "ERR",
            "WARN",
            "OK",
            "STEP",
            "PROXY",
            "PROFILE",
            "SEARCH",
            "CLICK",
            "SITE",
            "SUM",
            "INFO",
        ):
            if f"[{tag}]" in u:
                return tag
        if "오류" in msg or "실패" in msg:
            return "ERR"
        if "CLICK" in u or "클릭" in msg:
            return "CLICK"
        if "PROXY" in u or "프록시" in msg or "IP =" in msg or "IP=" in msg:
            return "PROXY"
        return "INFO"

    def _clear_log(self) -> None:
        self.txt_log.delete("1.0", tk.END)

    def _save_log(self) -> None:
        path = filedialog.asksaveasfilename(
            title="로그 저장",
            defaultextension=".log",
            filetypes=[("Log", "*.log"), ("Text", "*.txt"), ("All", "*.*")],
            initialdir=str(self.base_dir),
            initialfile="octo-automation.log",
        )
        if not path:
            return
        try:
            Path(path).write_text(self.txt_log.get("1.0", tk.END), encoding="utf-8")
            self.log(f"[OK] 로그 저장: {path}")
        except Exception as exc:
            messagebox.showerror("저장 실패", str(exc))

    def _drain_log_queue(self) -> None:
        try:
            while True:
                msg = self.log_queue.get_nowait()
                tag = self._log_tag(msg)
                self.txt_log.insert(tk.END, msg + "\n", tag)
                self.txt_log.see(tk.END)
                try:
                    self._update_live_status(msg)
                except Exception:
                    pass
        except queue.Empty:
            pass
        self.root.after(80, self._drain_log_queue)

    def _set_running(self, running: bool) -> None:
        self.is_running = running
        idle = tk.NORMAL if not running else tk.DISABLED
        run = tk.DISABLED if not running else tk.NORMAL
        buttons = [self.btn_start, self.btn_dry, self.btn_test, self.btn_save]
        if hasattr(self, "btn_start_home"):
            buttons.append(self.btn_start_home)
        for w in buttons:
            try:
                w.configure(state=idle)
            except Exception:
                pass
        # 메뉴얼은 항상 열 수 있음
        try:
            self.btn_manual.configure(state=tk.NORMAL)
        except Exception:
            pass
        try:
            self.btn_stop.configure(state=run)
        except Exception:
            pass
        self.var_status.set("실행 중…" if running else "대기")
        if running:
            self.var_addr.set("octo://실행중")
        else:
            self.var_addr.set("octo://대기")

    def _on_proxy_select(self, _e=None) -> None:
        sel = self.lst_proxies.curselection()
        if sel:
            self.var_proxy_selected.set(
                f"start index: {sel[0]}  ({self.lst_proxies.get(sel[0])})"
            )

    def _selected_proxy_index(self) -> int:
        sel = self.lst_proxies.curselection()
        return int(sel[0]) if sel else 0

    def _split_csv(self, raw: str) -> List[str]:
        return [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]

    # ---------- proxy ----------
    def validate_proxies(self) -> None:
        text = self.txt_proxies.get("1.0", tk.END)
        proxies, errors = parse_proxy_text(text, default_type=self.var_proxy_type.get())
        self.validated_proxies = proxies
        self.lst_proxies.delete(0, tk.END)
        for i, p in enumerate(proxies):
            self.lst_proxies.insert(tk.END, f"[{i}] {p.display}")
        self.var_proxy_count.set(
            f"Proxies: {len(proxies)}"
            + (f"  ·  errors {len(errors)}" if errors else "")
        )
        if proxies:
            self.lst_proxies.selection_set(0)
            self._on_proxy_select()
        if errors:
            messagebox.showwarning(
                "Proxy format", "\n".join(errors[:8]) + ("\n…" if len(errors) > 8 else "")
            )
        else:
            self.log(f"[Proxy] validated {len(proxies)}")
        self.refresh_summary()

    def load_proxy_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Proxy TXT",
            filetypes=[("Text", "*.txt"), ("All", "*.*")],
            initialdir=str(self.base_dir),
        )
        if not path:
            return
        self.txt_proxies.delete("1.0", tk.END)
        self.txt_proxies.insert("1.0", Path(path).read_text(encoding="utf-8"))
        self.validate_proxies()

    def clear_proxies(self) -> None:
        self.txt_proxies.delete("1.0", tk.END)
        self.lst_proxies.delete(0, tk.END)
        self.validated_proxies = []
        self.var_proxy_count.set("Proxies: 0")
        self.refresh_summary()

    # ---------- accounts ----------
    def _on_bulk_paste_event(self, _event=None):
        # after paste, schedule parse (clipboard already in widget after event)
        self.root.after(50, self._maybe_auto_parse_bulk)
        return None

    def _maybe_auto_parse_bulk(self) -> None:
        if not hasattr(self, "txt_bulk_accounts"):
            return
        if getattr(self, "_bulk_applying", False):
            return
        text = self.txt_bulk_accounts.get("1.0", tk.END).strip()
        if not text or "|" not in text:
            return
        rows = parse_account_bulk_text(text)
        if len(rows) >= 1:
            self._apply_bulk_pipe_accounts(silent=True)

    def _apply_bulk_pipe_accounts(self, silent: bool = False) -> None:
        """Parse email|password|secret lines → profile table + validate 2FA."""
        if not hasattr(self, "txt_bulk_accounts"):
            return
        if getattr(self, "_bulk_applying", False):
            return
        self._bulk_applying = True
        try:
            self._apply_bulk_pipe_accounts_inner(silent=silent)
        finally:
            self._bulk_applying = False

    def _apply_bulk_pipe_accounts_inner(self, silent: bool = False) -> None:
        text = self.txt_bulk_accounts.get("1.0", tk.END)
        rows = parse_account_bulk_text(text)
        if not rows:
            joined = (
                f"{self.var_quick_email.get()}|{self.var_quick_password.get()}|{self.var_quick_secret.get()}"
            )
            if "|" in joined:
                rows = parse_account_bulk_text(joined)
        if not rows:
            if not silent:
                messagebox.showinfo(
                    "붙여넣기",
                    "형식을 인식하지 못했습니다.\n\n"
                    "한 줄 예:\n"
                    "user@gmail.com|비밀번호|2FA시크릿키\n\n"
                    "여러 계정이면 줄마다 하나씩.",
                )
            return

        # validate / normalize secrets
        v = validate_account_secrets(rows)
        for item in list(self.tree_acc.get_children()):
            self.tree_acc.delete(item)
        for r in rows:
            self.tree_acc.insert(
                "",
                tk.END,
                values=(
                    r.get("email", ""),
                    r.get("password", ""),
                    r.get("profile_title", ""),
                    r.get("otp_secret", ""),
                    r.get("otp_url", "") or "https://2fa-auth.com/",
                    r.get("notes", "pipe"),
                ),
            )

        r0 = rows[0]
        self.var_quick_email.set(r0.get("email", ""))
        self.var_quick_password.set(r0.get("password", ""))
        self.var_quick_secret.set(r0.get("otp_secret", ""))
        # global secret empty — each job uses own secret (critical for multi-account)
        self.var_otp_secret.set("")
        self.var_otp_enabled.set(True)
        self.var_otp_url.set("https://2fa-auth.com/")
        self.var_g_enabled.set(True)
        self.var_g_mode.set("auto")

        n_sec = int(v.get("valid_secret") or 0)
        self.var_live_google.set(f"Google: {len(rows)}계정")
        self.var_live_2fa.set(f"2FA: {n_sec}/{len(rows)} 시크릿 유효")
        if v.get("preview"):
            p0 = v["preview"][0]
            self.var_live_2fa.set(f"2FA 코드: {p0.get('code')} (첫계정 미리보기)")
        self.var_live_hint.set(
            f"{len(rows)}계정 준비 · 시작 시 각각 프로필·로그인·2FA·검색 자동"
        )
        self.log(
            f"[안내] pipe 반영: 계정 {len(rows)}개 · 2FA유효 {n_sec}개 · "
            f"프로필 자동 (계정마다 시크릿 분리)"
        )
        for i, r in enumerate(rows, 1):
            em = r.get("email", "")
            has = "2FA✓" if r.get("otp_secret") else "2FA✗"
            self.log(f"[안내]   #{i}/{len(rows)} {em} · {r.get('profile_title')} · {has}")
        for inv in v.get("invalid") or []:
            self.log(f"[WARN] 2FA 시크릿 오류: {inv.get('email')} — {inv.get('reason')}")
        for p in v.get("preview") or []:
            self.log(f"[2FA] 미리보기 {p.get('email')}: {p.get('code')}")

        self.refresh_summary()
        # auto-save accounts
        try:
            cfg = self.collect_config()
            save_config(self.config_path, cfg)
            with (self.base_dir / "accounts.csv").open(
                "w", encoding="utf-8", newline=""
            ) as fh:
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
                for r in rows:
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
            self.log("[Save] accounts.csv + config 자동 저장")
        except Exception as exc:
            self.log(f"[WARN] 자동 저장 실패: {exc}")

        if silent:
            return
        msg = (
            f"{len(rows)}개 계정 등록\n"
            f"2FA 시크릿 유효: {n_sec}개\n"
        )
        if v.get("invalid"):
            msg += f"\n⚠ 시크릿 오류 {len(v['invalid'])}개 — 로그 확인\n"
        if v.get("preview"):
            msg += f"\n첫 계정 지금 코드: {v['preview'][0].get('code')}\n"
        msg += "\n▶ 원클릭 시작 → 계정마다 자동 로그인·2FA·검색"
        messagebox.showinfo("반영 완료", msg)

    def _preflight_check(self) -> None:
        """Human-readable checklist before START."""
        self._apply_quick_account_silent()
        issues: List[str] = []
        ok_lines: List[str] = []
        token = self.var_token.get().strip()
        if token:
            ok_lines.append("✓ Octo API 토큰")
        else:
            issues.append("✗ Octo API 토큰 없음")
        if not self.validated_proxies:
            self.validate_proxies()
        if self.validated_proxies:
            ok_lines.append(f"✓ 프록시 {len(self.validated_proxies)}개")
        else:
            issues.append("✗ 프록시 없음 (홈 ④ 또는 프록시 탭)")
        rows = self._account_rows()
        if rows:
            v = validate_account_secrets(rows)
            ok_lines.append(f"✓ 계정 {len(rows)}개")
            ok_lines.append(
                f"✓ 2FA 시크릿 유효 {v.get('valid_secret')}/{v.get('with_secret')}"
            )
            for inv in v.get("invalid") or []:
                issues.append(f"✗ 2FA 오류: {inv.get('email')}")
            no_pw = [r for r in rows if r.get("email") and not r.get("password")]
            if no_pw:
                issues.append(f"✗ 비밀번호 없는 계정 {len(no_pw)}개")
        else:
            issues.append("✗ 구글 계정 없음 (email|pass|secret 붙여넣기)")
        kw = self.var_quick_keyword.get().strip() or self.var_keyword.get().strip()
        dom = self.var_quick_domain.get().strip() or self.var_own_domain.get().strip()
        if kw:
            ok_lines.append(f"✓ 검색어: {kw}")
        else:
            issues.append("✗ 검색어 없음")
        if dom:
            ok_lines.append(f"✓ 도메인: {dom}")
        else:
            issues.append("✗ 자사 도메인 없음")
        body = "\n".join(ok_lines + ([""] + issues if issues else []))
        if issues:
            messagebox.showwarning("시작 전 점검", body + "\n\n빨간 항목을 고친 뒤 시작하세요.")
        else:
            messagebox.showinfo("시작 전 점검", body + "\n\n준비 완료 — ▶ 원클릭 시작 가능")
        self.log("[안내] 시작 전 점검\n" + body)

    def _clear_bulk_accounts(self) -> None:
        if hasattr(self, "txt_bulk_accounts"):
            self.txt_bulk_accounts.delete("1.0", tk.END)
        for item in list(self.tree_acc.get_children()):
            self.tree_acc.delete(item)
        self.var_quick_email.set("")
        self.var_quick_password.set("")
        self.var_quick_secret.set("")
        self.refresh_summary()
        self.log("[안내] 계정 목록 비움")

    def _preview_bulk_first_2fa(self) -> None:
        from .automation import generate_totp

        secret = ""
        if hasattr(self, "txt_bulk_accounts"):
            rows = parse_account_bulk_text(self.txt_bulk_accounts.get("1.0", tk.END))
            if rows:
                secret = rows[0].get("otp_secret") or ""
        if not secret:
            secret = self.var_quick_secret.get().strip()
        if not secret:
            for item in self.tree_acc.get_children():
                v = self.tree_acc.item(item, "values")
                if len(v) >= 4 and str(v[3]).strip():
                    secret = str(v[3]).strip()
                    break
        if not secret:
            messagebox.showinfo("2FA", "시크릿이 있는 계정을 먼저 붙여넣으세요.")
            return
        code = generate_totp(secret)
        if not code:
            messagebox.showerror("2FA", "시크릿 해석 실패")
            return
        self.var_live_2fa.set(f"2FA 코드: {code}")
        self.log(f"[2FA] 미리보기 코드 = {code}")
        messagebox.showinfo(
            "2FA 미리보기",
            f"지금 코드: {code}\n\n"
            "시작 시 이 값이 Google에 자동 입력됩니다.\n"
            "(2fa-auth.com 과 같은 알고리즘)",
        )

    def _preview_quick_2fa(self) -> None:
        """Show live TOTP from home quick form secret."""
        from .automation import extract_totp_secret, generate_totp

        raw = self.var_quick_secret.get().strip() or self.var_otp_secret.get().strip()
        # also accept full pipe line in secret or email field
        for cand in (
            raw,
            self.var_quick_email.get().strip(),
            f"{self.var_quick_email.get()}|{self.var_quick_password.get()}|{self.var_quick_secret.get()}",
        ):
            if "|" in cand:
                parsed = parse_account_pipe_line(cand, index=1)
                if parsed and parsed.get("otp_secret"):
                    raw = parsed["otp_secret"]
                    if parsed.get("email"):
                        self.var_quick_email.set(parsed["email"])
                    if parsed.get("password"):
                        self.var_quick_password.set(parsed["password"])
                    self.var_quick_secret.set(raw)
                    break
        if not raw:
            messagebox.showinfo(
                "2FA",
                "② 의 「2FA 시크릿 키」에 키를 붙여넣으세요.\n"
                "2fa-auth.com 과 같은 6자리가 생성됩니다.",
            )
            return
        code = generate_totp(raw)
        if not code:
            messagebox.showerror("2FA", "시크릿을 읽지 못했습니다. 키를 다시 확인하세요.")
            self.var_live_2fa.set("2FA 코드: 오류")
            return
        self.var_live_2fa.set(f"2FA 코드: {code}")
        self.var_live_hint.set(
            f"지금 유효한 코드 {code} (약 30초) · 로그인 시 자동 입력됩니다"
        )
        self.log(f"[2FA] 미리보기 코드 = {code}  (2fa-auth.com 동일 알고리즘)")
        messagebox.showinfo(
            "2FA 코드 미리보기",
            f"지금 코드:  {code}\n\n"
            f"정규화 시크릿: {extract_totp_secret(raw)[:10]}…\n"
            "원클릭 시작 시 이 값이 Google 2FA 칸에 자동으로 들어갑니다.",
        )

    def _apply_quick_account(self) -> None:
        """Push home quick Google+2FA into profiles table + global 2FA."""
        email = self.var_quick_email.get().strip()
        password = self.var_quick_password.get().strip()
        secret = self.var_quick_secret.get().strip()
        # full pipe in any field
        blob = "|".join(x for x in (email, password, secret) if x)
        if "|" in email or (email.count("|") >= 2):
            blob = email
        parsed = parse_account_pipe_line(blob, index=1) if "|" in blob else None
        if parsed:
            email = parsed.get("email") or email
            password = parsed.get("password") or password
            secret = parsed.get("otp_secret") or secret
            self.var_quick_email.set(email)
            self.var_quick_password.set(password)
            self.var_quick_secret.set(secret)
        if not email and not secret:
            messagebox.showinfo(
                "입력",
                "구글 아이디를 입력하거나\n"
                "email|password|secret 한 줄을 붙여넣으세요.",
            )
            return
        profile = f"g-{(email.split('@')[0] if email else 'user')}"[:40]
        # clear empty sample rows
        for item in list(self.tree_acc.get_children()):
            v = self.tree_acc.item(item, "values")
            if len(v) >= 3 and not str(v[0]).strip() and str(v[2]) in (
                "auto-google-1",
                "sample",
            ):
                self.tree_acc.delete(item)
        # update first row or insert
        kids = self.tree_acc.get_children()
        values = (
            email,
            password,
            profile,
            secret,
            "https://2fa-auth.com/",
            "원클릭 입력",
        )
        if kids:
            self.tree_acc.item(kids[0], values=values)
        else:
            self.tree_acc.insert("", tk.END, values=values)
        self.var_email.set(email)
        self.var_password.set(password)
        self.var_profile.set(profile)
        self.var_acc_secret.set(secret)
        self.var_otp_secret.set(secret)
        self.var_otp_url.set("https://2fa-auth.com/")
        self.var_otp_enabled.set(True)
        self.var_g_enabled.set(True)
        self.var_g_mode.set("auto")
        # keyword/domain
        if self.var_quick_keyword.get().strip():
            self.var_keyword.set(self.var_quick_keyword.get().strip())
        if self.var_quick_domain.get().strip():
            self.var_own_domain.set(self.var_quick_domain.get().strip())
            if hasattr(self, "txt_domains"):
                d = self.var_quick_domain.get().strip()
                cur = self.txt_domains.get("1.0", tk.END).strip()
                if d and d not in cur:
                    self.txt_domains.delete("1.0", tk.END)
                    self.txt_domains.insert("1.0", d + ("\n" + cur if cur else ""))
        self.log(
            f"[안내] 계정 반영: {email or '-'} · 2FA시크릿={'있음' if secret else '없음'} · 프로필={profile}"
        )
        self.var_live_google.set(f"Google: {email or '설정됨'}")
        if secret:
            self.var_live_2fa.set("2FA 코드: 시크릿 준비됨 (실행 시 자동)")
        self.refresh_summary()
        messagebox.showinfo(
            "반영됨",
            "프로필·2FA에 저장 준비 완료.\n"
            "아래 ▶ 원클릭 시작 하면 로그인·2FA·검색이 자동 진행됩니다.",
        )

    def _apply_quick_proxies(self) -> None:
        if not hasattr(self, "txt_quick_proxies"):
            return
        text = self.txt_quick_proxies.get("1.0", tk.END).strip()
        if not text:
            messagebox.showinfo("프록시", "프록시를 붙여넣거나, 프록시 탭을 사용하세요.")
            return
        if hasattr(self, "txt_proxies"):
            self.txt_proxies.delete("1.0", tk.END)
            self.txt_proxies.insert("1.0", text)
        self.validate_proxies()
        self.log(f"[Proxy] 원클릭 프록시 반영 {len(self.validated_proxies)}개")

    def _quick_save_all(self) -> None:
        self._apply_quick_account_silent()
        if hasattr(self, "txt_quick_proxies"):
            q = self.txt_quick_proxies.get("1.0", tk.END).strip()
            if q and hasattr(self, "txt_proxies"):
                self.txt_proxies.delete("1.0", tk.END)
                self.txt_proxies.insert("1.0", q)
                self.validate_proxies()
        self.save_settings()

    def _apply_quick_account_silent(self) -> None:
        """Same as apply without dialogs — used before START."""
        # bulk box has priority if filled
        if hasattr(self, "txt_bulk_accounts"):
            bulk = self.txt_bulk_accounts.get("1.0", tk.END).strip()
            if bulk:
                rows = parse_account_bulk_text(bulk)
                if rows:
                    for item in list(self.tree_acc.get_children()):
                        self.tree_acc.delete(item)
                    for r in rows:
                        self.tree_acc.insert(
                            "",
                            tk.END,
                            values=(
                                r.get("email", ""),
                                r.get("password", ""),
                                r.get("profile_title", ""),
                                r.get("otp_secret", ""),
                                r.get("otp_url", "") or "https://2fa-auth.com/",
                                r.get("notes", "pipe"),
                            ),
                        )
                    r0 = rows[0]
                    self.var_quick_email.set(r0.get("email", ""))
                    self.var_quick_password.set(r0.get("password", ""))
                    self.var_quick_secret.set(r0.get("otp_secret", ""))
                    self.var_otp_secret.set("")  # per-account secret only
                    self.var_otp_enabled.set(True)
                    self.var_g_enabled.set(True)
                    self.var_g_mode.set("auto")
                    self.log(f"[안내] 시작 전 bulk 계정 {len(rows)}개 자동 반영")

        email = self.var_quick_email.get().strip()
        password = self.var_quick_password.get().strip()
        secret = self.var_quick_secret.get().strip()
        # parse pipe if user only filled one line in email field
        if "|" in email:
            p = parse_account_pipe_line(email, index=1)
            if p:
                email, password, secret = (
                    p.get("email", ""),
                    p.get("password", ""),
                    p.get("otp_secret", ""),
                )
                self.var_quick_email.set(email)
                self.var_quick_password.set(password)
                self.var_quick_secret.set(secret)
        if email or secret or password:
            profile = f"auto-google-{(email.split('@')[0] if email else 'user')}"[:40]
            values = (
                email,
                password,
                profile,
                secret,
                "https://2fa-auth.com/" if secret else (self.var_otp_url.get() or ""),
                "원클릭",
            )
            kids = self.tree_acc.get_children()
            if kids:
                # overwrite first row if empty email or same
                v0 = self.tree_acc.item(kids[0], "values")
                if not str(v0[0] if v0 else "").strip() or str(v0[0]) == email or email:
                    self.tree_acc.item(kids[0], values=values)
                else:
                    self.tree_acc.insert("", 0, values=values)
            else:
                self.tree_acc.insert("", tk.END, values=values)
            if secret:
                self.var_otp_secret.set(secret)
                self.var_otp_enabled.set(True)
                self.var_otp_url.set(self.var_otp_url.get().strip() or "https://2fa-auth.com/")
            if email or password:
                self.var_g_enabled.set(True)
                self.var_g_mode.set("auto")
        if self.var_quick_keyword.get().strip():
            raw_kw = self.var_quick_keyword.get().strip()
            self.var_keyword.set(raw_kw)
            if hasattr(self, "txt_keywords"):
                from .automation import split_search_keywords

                parts = split_search_keywords(raw_kw)
                # 추가 검색어 목록에 줄 단위로 동기화 (폴백 목록 표시)
                if parts:
                    self.txt_keywords.delete("1.0", tk.END)
                    self.txt_keywords.insert("1.0", "\n".join(parts))
        if self.var_quick_domain.get().strip():
            d = self.var_quick_domain.get().strip()
            self.var_own_domain.set(d)
            if hasattr(self, "txt_domains"):
                cur = self.txt_domains.get("1.0", tk.END).strip()
                if d and d not in cur:
                    self.txt_domains.delete("1.0", tk.END)
                    self.txt_domains.insert("1.0", d + ("\n" + cur if cur else ""))
        if hasattr(self, "txt_quick_proxies"):
            q = self.txt_quick_proxies.get("1.0", tk.END).strip()
            if q and hasattr(self, "txt_proxies"):
                self.txt_proxies.delete("1.0", tk.END)
                self.txt_proxies.insert("1.0", q)

    def _test_totp_secret(self) -> None:
        """Generate a live 6-digit code from secret (2fa-auth.com compatible)."""
        from .automation import extract_totp_secret, generate_totp

        raw = (
            self.var_otp_secret.get().strip()
            or self.var_acc_secret.get().strip()
            or self.var_quick_secret.get().strip()
        )
        if not raw:
            # try first account secret
            for item in self.tree_acc.get_children():
                v = self.tree_acc.item(item, "values")
                if len(v) >= 4 and str(v[3]).strip():
                    raw = str(v[3]).strip()
                    break
        if not raw:
            messagebox.showinfo(
                "2FA 테스트",
                "시크릿 키를 입력하세요.\n\n"
                "예: 2fa-auth.com 에 넣는 base32 키\n"
                "또는 note|id|SECRET|… 형식 붙여넣기",
            )
            return
        code = generate_totp(raw)
        secret_show = extract_totp_secret(raw)
        if not code:
            messagebox.showerror(
                "2FA 테스트 실패",
                "시크릿을 해석하지 못했습니다.\n"
                f"추출 결과: {secret_show or '(비어 있음)'}\n\n"
                "https://2fa-auth.com/ 에서 같은 키로 코드가 나오는지 확인해 주세요.",
            )
            return
        messagebox.showinfo(
            "2FA 코드 (2fa-auth.com 동일)",
            f"지금 코드:  {code}\n\n"
            f"시크릿(정규화): {secret_show[:8]}…{secret_show[-4:] if len(secret_show)>12 else secret_show}\n"
            f"유효: 약 30초\n\n"
            "Google 로그인 2FA 화면에 이 값이 자동 입력됩니다.",
        )
        self.log(f"[2FA] 테스트 코드 생성 OK: {code} (2fa-auth.com 알고리즘)")

    def _open_2fa_auth_site(self) -> None:
        import webbrowser

        url = self.var_otp_url.get().strip() or "https://2fa-auth.com/"
        try:
            webbrowser.open(url)
            self.log(f"[2FA] 사이트 열기: {url}")
        except Exception as exc:
            messagebox.showerror("열기 실패", str(exc))

    def add_account_row(self) -> None:
        email = self.var_email.get().strip()
        password = self.var_password.get().strip()
        profile = self.var_profile.get().strip() or (
            f"auto-google-{email.split('@')[0]}" if email else ""
        )
        secret = self.var_acc_secret.get().strip()
        otp = self.var_acc_otp.get().strip()
        notes = self.var_notes.get().strip()
        if not email and not profile:
            messagebox.showinfo("입력", "이메일 또는 프로필 이름을 입력하세요.")
            return
        self.tree_acc.insert(
            "", tk.END, values=(email, password, profile, secret, otp, notes)
        )
        self.var_email.set("")
        self.var_password.set("")
        self.var_acc_secret.set("")
        self.var_acc_otp.set("")
        self.refresh_summary()

    def del_account_row(self) -> None:
        for item in self.tree_acc.selection():
            self.tree_acc.delete(item)
        self.refresh_summary()

    def add_sample_account(self) -> None:
        self.tree_acc.insert(
            "",
            tk.END,
            values=("", "", "auto-google-1", "", "https://2fa-auth.com/", "sample"),
        )
        self.refresh_summary()

    def load_accounts_csv(self) -> None:
        path = filedialog.askopenfilename(
            title="accounts CSV",
            filetypes=[("CSV", "*.csv"), ("All", "*.*")],
            initialdir=str(self.base_dir),
        )
        if not path:
            return
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("email") or row.get("profile_title"):
                    self.tree_acc.insert(
                        "",
                        tk.END,
                        values=(
                            row.get("email", ""),
                            row.get("password", ""),
                            row.get("profile_title", ""),
                            row.get("otp_secret", "")
                            or row.get("totp_secret", "")
                            or row.get("secret", ""),
                            row.get("otp_url", "") or row.get("otp_site", ""),
                            row.get("notes", ""),
                        ),
                    )
        self.refresh_summary()

    def _account_rows(self) -> List[Dict[str, str]]:
        rows = []
        for item in self.tree_acc.get_children():
            v = self.tree_acc.item(item, "values")
            # 6-col: email, password, profile, otp_secret, otp_url, notes
            # 5-col legacy: email, password, profile, otp_url, notes
            # 4-col legacy: email, password, profile, notes
            if len(v) >= 6:
                email, password, profile, otp_secret, otp_url, notes = (
                    v[0],
                    v[1],
                    v[2],
                    v[3],
                    v[4],
                    v[5],
                )
            elif len(v) >= 5:
                email, password, profile = v[0], v[1], v[2]
                otp_secret = ""
                otp_url, notes = v[3], v[4]
            else:
                email = v[0] if len(v) > 0 else ""
                password = v[1] if len(v) > 1 else ""
                profile = v[2] if len(v) > 2 else ""
                notes = v[3] if len(v) > 3 else ""
                otp_url = ""
                otp_secret = ""
            rows.append(
                {
                    "email": str(email),
                    "password": str(password),
                    "profile_title": str(profile),
                    "otp_secret": str(otp_secret),
                    "otp_url": str(otp_url),
                    "notes": str(notes),
                }
            )
        return rows

    # ---------- clicks ----------
    def add_click_row(self) -> None:
        text = self.var_sel_text.get().strip()
        sel = self.var_sel.get().strip()
        if not text and not sel:
            messagebox.showinfo("입력", "배너/버튼에 보이는 글자를 입력하세요.")
            return
        self.tree_clicks.insert(
            "",
            tk.END,
            values=(
                text,
                sel,
                int(self.var_sel_wait.get()),
                "예" if self.var_sel_opt.get() else "아니오",
            ),
        )
        self.var_sel_text.set("")
        self.refresh_summary()

    def del_click_row(self) -> None:
        for item in self.tree_clicks.selection():
            self.tree_clicks.delete(item)
        self.refresh_summary()

    def _click_rows(self) -> List[Dict[str, Any]]:
        clicks = []
        for item in self.tree_clicks.get_children():
            v = self.tree_clicks.item(item, "values")
            text = str(v[0]) if v else ""
            sel = str(v[1]) if len(v) > 1 else ""
            wait = v[2] if len(v) > 2 else 1000
            opt = str(v[3]) if len(v) > 3 else "예"
            clicks.append(
                {
                    "selector": sel,
                    "text_contains": text,
                    "button_text": text,
                    "wait_after_ms": int(wait or 1000),
                    "optional": str(opt).startswith("예"),
                }
            )
        return clicks

    # ---------- 2FA ----------
    def ask_2fa_code(self, prompt: str) -> Optional[str]:
        self._otp_result = None
        self._otp_event.clear()
        self.root.after(0, lambda: self._show_2fa_dialog(prompt))
        while not self._otp_event.wait(timeout=0.4):
            if self.cancel_flag.is_set():
                return None
        return self._otp_result

    def _show_2fa_dialog(self, prompt: str) -> None:
        # 시크릿이 있으면 팝업 전에 한 번 더 자동 생성 시도 안 함 — 이미 엔진에서 처리
        self.var_live_step.set("단계: 2FA 수동 입력 대기")
        self.var_live_hint.set("시크릿 자동 입력이 안 되어 팝업이 떴습니다. 6자리를 넣어 주세요.")
        win = tk.Toplevel(self.root)
        win.title("2FA 수동 입력 (자동 실패 시)")
        win.configure(bg=C["chrome"])
        win.transient(self.root)
        win.grab_set()
        win.geometry("520x320")
        frm = tk.Frame(win, bg=C["chrome"], padx=16, pady=16)
        frm.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            frm,
            text="2차 인증 코드 (보통은 자동입니다)",
            bg=C["chrome"],
            fg=C["accent"],
            font=("Segoe UI", 13, "bold"),
        ).pack(anchor="w")
        tk.Label(
            frm, text=prompt, bg=C["chrome"], fg=C["text"], justify=tk.LEFT, wraplength=480
        ).pack(anchor="w", pady=8)
        var_code = tk.StringVar()
        ent = tk.Entry(
            frm,
            textvariable=var_code,
            font=("Consolas", 18),
            bg=C["input"],
            fg=C["text"],
            insertbackground=C["text"],
            relief=tk.FLAT,
        )
        ent.pack(anchor="w", fill=tk.X, ipady=6)
        ent.focus_set()
        try:
            clip = self.root.clipboard_get()
            clean = "".join(ch for ch in str(clip) if ch.isdigit())
            if 4 <= len(clean) <= 8:
                var_code.set(clean)
        except tk.TclError:
            pass

        def finish(ok: bool) -> None:
            if ok:
                code = "".join(
                    ch for ch in var_code.get().strip() if ch.isalnum()
                )
                if not code:
                    return
                self._otp_result = code
                self.log(f"[2FA] 수동 입력 코드: {code}")
                self.var_live_2fa.set(f"2FA 코드: {code}")
            else:
                self._otp_result = None
            try:
                win.grab_release()
            except tk.TclError:
                pass
            win.destroy()
            self._otp_event.set()

        btns = tk.Frame(frm, bg=C["chrome"])
        btns.pack(fill=tk.X, pady=12)
        ttk.Button(btns, text="확인", command=lambda: finish(True)).pack(side=tk.LEFT, padx=4)
        ttk.Button(btns, text="취소", command=lambda: finish(False)).pack(side=tk.LEFT)
        win.bind("<Return>", lambda _e: finish(True))
        win.bind("<Escape>", lambda _e: finish(False))
        win.protocol("WM_DELETE_WINDOW", lambda: finish(False))

    # ---------- config ----------
    def collect_config(self) -> Dict[str, Any]:
        if not self.validated_proxies:
            proxies, _ = parse_proxy_text(
                self.txt_proxies.get("1.0", tk.END),
                default_type=self.var_proxy_type.get(),
            )
            self.validated_proxies = proxies

        clicks = self._click_rows()
        url = self.var_url.get().strip()
        targets: List[Dict[str, Any]] = []
        if url:
            targets.append(
                {
                    "url": url,
                    "wait_until": "domcontentloaded",
                    "wait_ms": int(self.var_wait_ms.get()),
                    "clicks": clicks if not self.var_search_enabled.get() else [],
                }
            )

        keyword = self.var_keyword.get().strip()
        # 홈 빠른 검색어 우선 (원클릭 입력)
        quick_kw = ""
        if hasattr(self, "var_quick_keyword"):
            quick_kw = self.var_quick_keyword.get().strip()
        if quick_kw:
            keyword = quick_kw
        dmin, dmax = int(self.var_dwell_min.get()), int(self.var_dwell_max.get())
        if dmax < dmin:
            dmax = dmin
        smin, smax = int(self.var_scroll_min.get()), int(self.var_scroll_max.get())
        if smax < smin:
            smax = smin

        domain = self.var_own_domain.get().strip()
        if hasattr(self, "var_quick_domain"):
            qd = self.var_quick_domain.get().strip()
            if qd:
                domain = qd
        domains_text = ""
        keywords_text = ""
        if hasattr(self, "txt_domains"):
            domains_text = self.txt_domains.get("1.0", tk.END).strip()
        if hasattr(self, "txt_keywords"):
            keywords_text = self.txt_keywords.get("1.0", tk.END).strip()
        domain_list = [
            ln.strip()
            for ln in domains_text.replace(",", "\n").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        # 검색어: / | 줄바꿈 지원 (카지노사이트/순위/추천 → 폴백 목록)
        from .automation import split_search_keywords

        kw_list: List[str] = []
        seen_kw: set[str] = set()
        for src in (keyword, keywords_text):
            for s in split_search_keywords(src):
                if s not in seen_kw:
                    seen_kw.add(s)
                    kw_list.append(s)
        placeholders = {
            "example-mybrand",
            "my brand",
            "mybrand",
            "test",
            "내 브랜드 키워드",
        }
        real_kw = [k for k in kw_list if k.lower() not in placeholders]
        if real_kw:
            kw_list = real_kw
        # 저장/표시용: 여러 검색어면 " / " 로 이어 쓰기 (재로드 시 폴백 목록 유지)
        if kw_list:
            keyword = " / ".join(kw_list) if len(kw_list) > 1 else kw_list[0]
        if domain and domain not in domain_list:
            domain_list.insert(0, domain)
        # domains_text 를 목록과 항상 동기화 (domains.txt 빈 파일 방지)
        if domain_list:
            domains_text = "\n".join(domain_list)
        # 광고 허용 동기화: 홈 allow_ads ON → skip_ads OFF
        if hasattr(self, "var_allow_ads"):
            skip_ads_val = not bool(self.var_allow_ads.get())
            try:
                self.var_skip_ads.set(skip_ads_val)
            except Exception:
                pass
        else:
            skip_ads_val = bool(self.var_skip_ads.get())
        regex_text = self.var_url_regex.get().strip()
        path_targets = self._split_csv(self.var_path_targets.get())
        search_flow = {
            "enabled": bool(self.var_search_enabled.get()),
            "purpose": "own_site_qa",
            "keyword": keyword,
            "keywords": kw_list,
            "keywords_text": "\n".join(kw_list),
            "keyword_fallback": True,
            "stop_on_first_keyword_hit": True,
            "target_domain": domain or (domain_list[0] if domain_list else ""),
            "allowed_domains": domain_list,
            "domains_text": domains_text,
            "domains_file": "domains.txt",
            "keywords_file": "keywords.txt",
            "target_url_contains": self._split_csv(self.var_url_contains.get()),
            "url_regex": regex_text.splitlines()[0].strip() if regex_text else "",
            "url_regex_text": regex_text,
            "title_contains": self._split_csv(self.var_title_contains.get()),
            "title_regex": self.var_title_regex.get().strip(),
            "path_targets": path_targets,
            "path_exclude": self._split_csv(self.var_path_exclude.get()),
            "path_targets_text": self.var_path_targets.get().strip(),
            "path_exclude_text": self.var_path_exclude.get().strip(),
            "require_domain": bool(self.var_require_domain.get()),
            "skip_ads": skip_ads_val,
            "search_url": "https://www.google.com/",
            "max_serp_pages": int(self.var_max_serp.get()),
            "max_result_clicks": int(self.var_max_clicks.get()),
            "revisit_count": int(self.var_revisit.get()),
            "warmup": bool(self.var_warmup.get()),
            "human": {
                "dwell_ms_min": dmin,
                "dwell_ms_max": dmax,
                "scroll": bool(self.var_human_scroll.get()),
                "mouse_wander": bool(self.var_mouse_wander.get()),
                "read_pauses": bool(self.var_read_pauses.get()),
                "scroll_steps_min": smin,
                "scroll_steps_max": smax,
                "scroll_up_chance": 0.25,
                "random_internal_click": bool(self.var_internal_click.get()),
                "serp_scroll_min": 2,
                "serp_scroll_max": 6,
            },
            "banner_clicks": clicks,
            "also_run_targets": False,
        }

        return {
            "octo_api_token": self.var_token.get().strip(),
            "cloud_base": self.var_cloud.get().strip(),
            "local_base": self.var_local.get().strip(),
            "proxy_type": self.var_proxy_type.get(),
            "proxy_mode": self.var_proxy_mode.get(),
            "proxy_start_index": self._selected_proxy_index(),
            "proxies_file": "proxies.txt",
            "accounts_file": "accounts.csv",
            "proxies_text": self.txt_proxies.get("1.0", tk.END).strip(),
            "accounts_rows": self._account_rows(),
            "reuse_existing_profiles": bool(self.var_reuse.get()),
            "create_profile_if_missing": bool(self.var_create.get()),
            "headless": bool(self.var_headless.get()),
            "start_timeout_sec": int(self.var_timeout.get()),
            "delay_between_jobs_sec": int(self.var_delay.get()),
            "stop_profile_after_job": bool(self.var_stop_after.get()),
            "max_jobs": int(self.var_max_jobs.get()),
            "google_login": {
                "enabled": bool(self.var_g_enabled.get()),
                "mode": self.var_g_mode.get() or "auto",
                "login_url": "https://accounts.google.com/",
                "success_url_contains": [
                    "myaccount.google.com",
                    "mail.google.com",
                    "accounts.google.com/b/",
                    "drive.google.com",
                ],
                "manual_wait_sec": int(self.var_g_wait.get()),
                "autofill_pause_ms": 350,
                "otp_fetch": {
                    "enabled": bool(self.var_otp_enabled.get()),
                    # 글로벌 시크릿은 계정별 시크릿이 없을 때만 (다중 계정 교차 오염 방지)
                    "secret": (
                        self.var_otp_secret.get().strip()
                        if self.var_otp_enabled.get()
                        and not any(
                            (r.get("otp_secret") or "").strip()
                            for r in self._account_rows()
                        )
                        else ""
                    ),
                    "url": self.var_otp_url.get().strip()
                    if self.var_otp_enabled.get()
                    else "",
                    "selector": self.var_otp_selector.get().strip(),
                    "regex": r"\b(\d{6})\b",
                    "wait_ms": 2500,
                },
            },
            "search_flow": search_flow,
            "targets": targets,
            "dry_run": False,
        }

    def refresh_summary(self) -> None:
        n_proxy = len(self.validated_proxies)
        if not n_proxy:
            proxies, _ = parse_proxy_text(
                self.txt_proxies.get("1.0", tk.END), self.var_proxy_type.get()
            )
            n_proxy = len(proxies)
        n_acc = len(self.tree_acc.get_children()) if hasattr(self, "tree_acc") else 0
        n_click = len(self.tree_clicks.get_children()) if hasattr(self, "tree_clicks") else 0
        if self.var_search_enabled.get():
            nd = 0
            nk = 0
            if hasattr(self, "txt_domains"):
                nd = len(
                    [
                        ln
                        for ln in self.txt_domains.get("1.0", tk.END).splitlines()
                        if ln.strip() and not ln.strip().startswith("#")
                    ]
                )
            if hasattr(self, "txt_keywords"):
                nk = len(
                    [
                        ln
                        for ln in self.txt_keywords.get("1.0", tk.END).splitlines()
                        if ln.strip() and not ln.strip().startswith("#")
                    ]
                )
            try:
                from .automation import split_search_keywords

                qk = ""
                if hasattr(self, "var_quick_keyword"):
                    qk = self.var_quick_keyword.get().strip()
                base_kw = qk or self.var_keyword.get().strip()
                if base_kw:
                    nk = max(nk, len(split_search_keywords(base_kw)))
            except Exception:
                if self.var_keyword.get().strip():
                    nk = max(nk, 1)
            pt = self.var_path_targets.get().strip()
            pe = self.var_path_exclude.get().strip()
            path_bits = []
            if pt:
                path_bits.append(f"path타겟ON")
            if pe:
                path_bits.append(f"path제외ON")
            path_s = (" · " + " · ".join(path_bits)) if path_bits else ""
            line = (
                f"검색 ON · 검색어 {nk}개 · 자사도메인 {nd}개 · "
                f"클릭/검색어={self.var_max_clicks.get()} · SERP {self.var_max_serp.get()}p · "
                f"광고스킵={self.var_skip_ads.get()}{path_s} · CTA {n_click}"
            )
        else:
            line = f"검색 OFF · URL {self.var_url.get().strip() or '-'} · 클릭 {n_click}"
        self.var_summary.set(
            f"프록시 {n_proxy} · 모드 {self.var_proxy_mode.get()} · 시작 #{self._selected_proxy_index()}\n"
            f"프로필 {n_acc} · Google={self.var_g_mode.get()} (on={self.var_g_enabled.get()})\n"
            f"{line}\n"
            f"행동: scroll={self.var_human_scroll.get()} mouse={self.var_mouse_wander.get()} "
            f"read={self.var_read_pauses.get()} warmup={self.var_warmup.get()}"
        )

    def _persist_config(self, cfg: Dict[str, Any], *, quiet: bool = False) -> None:
        """
        config.json + proxies.txt + accounts.csv + domains.txt + keywords.txt
        전부 원자적으로 저장. 시작 시·수동 저장 공통.
        """
        (self.base_dir / "proxies.txt").write_text(
            cfg.get("proxies_text") or "", encoding="utf-8"
        )
        with (self.base_dir / "accounts.csv").open(
            "w", encoding="utf-8", newline=""
        ) as fh:
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
                # 완전 빈 샘플 행은 저장 생략
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
        # accounts_rows 도 정리본으로 맞춰 저장
        cleaned_rows = []
        for r in cfg.get("accounts_rows") or []:
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
            cleaned_rows.append(r)
        if cleaned_rows:
            cfg["accounts_rows"] = cleaned_rows

        sf = cfg.get("search_flow") or {}
        # domains / keywords 파일 — list 우선, text 폴백
        dom_list = list(sf.get("allowed_domains") or [])
        dom_text = str(sf.get("domains_text") or "").strip()
        if not dom_text and dom_list:
            dom_text = "\n".join(str(d) for d in dom_list)
        if not dom_text and sf.get("target_domain"):
            dom_text = str(sf.get("target_domain"))
        # sync back into cfg so config.json always has domains_text
        if dom_text:
            sf["domains_text"] = dom_text
            if not sf.get("allowed_domains"):
                sf["allowed_domains"] = [
                    ln.strip()
                    for ln in dom_text.splitlines()
                    if ln.strip() and not ln.strip().startswith("#")
                ]
            cfg["search_flow"] = sf

        kws = list(sf.get("keywords") or [])
        if not kws:
            from .automation import split_search_keywords

            kws = split_search_keywords(sf.get("keywords_text") or "")
            if not kws:
                kws = split_search_keywords(sf.get("keyword") or "")
        if kws:
            sf["keywords"] = kws
            sf["keywords_text"] = "\n".join(str(k) for k in kws)
            # 다중 검색어 표시 형식 유지
            sf["keyword"] = (
                " / ".join(str(k) for k in kws) if len(kws) > 1 else str(kws[0])
            )
            sf.setdefault("keyword_fallback", True)
            sf.setdefault("stop_on_first_keyword_hit", True)
            cfg["search_flow"] = sf

        save_config(self.config_path, cfg)
        try:
            (self.base_dir / "domains.txt").write_text(dom_text or "", encoding="utf-8")
            (self.base_dir / "keywords.txt").write_text(
                "\n".join(str(k) for k in kws), encoding="utf-8"
            )
        except Exception as exc:
            self.log(f"[WARN] domains/keywords 파일 저장 경고: {exc}")

        if not quiet:
            self.log(f"[Save] {self.config_path}")
            self.log(
                f"[Save] 자사 도메인 {len(sf.get('allowed_domains') or [])}개 · "
                f"검색어 {len(kws)}개 · "
                f"폴백={sf.get('keyword_fallback', True)} · "
                f"광고스킵={sf.get('skip_ads')}"
            )

    def save_settings(self) -> None:
        try:
            # 홈 원클릭 입력(계정·검색어·도메인·프록시) 먼저 반영 후 저장
            self._apply_quick_account_silent()
            if hasattr(self, "txt_quick_proxies"):
                q = self.txt_quick_proxies.get("1.0", tk.END).strip()
                if q and hasattr(self, "txt_proxies"):
                    self.txt_proxies.delete("1.0", tk.END)
                    self.txt_proxies.insert("1.0", q)
                    self.validate_proxies()
            cfg = self.collect_config()
            self._persist_config(cfg, quiet=False)
            # UI 표시 동기화 (저장 직후 검색어란에 다중 형식 반영)
            sf = cfg.get("search_flow") or {}
            kws = sf.get("keywords") or []
            if kws:
                disp = " / ".join(str(k) for k in kws) if len(kws) > 1 else str(kws[0])
                self.var_keyword.set(disp)
                if hasattr(self, "var_quick_keyword"):
                    self.var_quick_keyword.set(disp)
                if hasattr(self, "txt_keywords"):
                    self.txt_keywords.delete("1.0", tk.END)
                    self.txt_keywords.insert("1.0", "\n".join(str(k) for k in kws))
            if sf.get("target_domain"):
                self.var_own_domain.set(str(sf.get("target_domain")))
                if hasattr(self, "var_quick_domain"):
                    self.var_quick_domain.set(str(sf.get("target_domain")))
            if hasattr(self, "var_allow_ads"):
                self.var_allow_ads.set(not bool(sf.get("skip_ads", True)))
            messagebox.showinfo(
                "저장",
                "설정 저장 완료\n\n"
                f"· config.json\n"
                f"· keywords.txt ({len(kws)}개 검색어)\n"
                f"· domains.txt\n"
                f"· accounts.csv · proxies.txt\n\n"
                f"검색어 폴백: {'ON' if sf.get('keyword_fallback', True) else 'OFF'}\n"
                f"광고 스킵: {sf.get('skip_ads')}",
            )
            self.refresh_summary()
        except Exception as exc:
            messagebox.showerror("저장 실패", str(exc))

    def _load_initial(self) -> None:
        cfg: Dict[str, Any] = {}
        if self.config_path.is_file():
            try:
                cfg = load_config(self.config_path)
            except Exception as exc:
                self.log(f"[Load] {exc}")
        else:
            ex = self.base_dir / "config.example.json"
            if ex.is_file():
                try:
                    cfg = load_config(ex)
                except Exception:
                    cfg = {}

        self.var_token.set(str(cfg.get("octo_api_token") or ""))
        if "여기에" in self.var_token.get():
            self.var_token.set("")
        self.var_cloud.set(str(cfg.get("cloud_base") or self.var_cloud.get()))
        self.var_local.set(str(cfg.get("local_base") or self.var_local.get()))
        self.var_proxy_type.set(str(cfg.get("proxy_type") or "http"))
        self.var_proxy_mode.set(str(cfg.get("proxy_mode") or "round_robin"))
        self.var_delay.set(int(cfg.get("delay_between_jobs_sec") or 15))
        self.var_timeout.set(int(cfg.get("start_timeout_sec") or 120))
        self.var_max_jobs.set(int(cfg.get("max_jobs") or 0))
        self.var_headless.set(bool(cfg.get("headless", False)))
        self.var_stop_after.set(bool(cfg.get("stop_profile_after_job", True)))
        self.var_reuse.set(bool(cfg.get("reuse_existing_profiles", True)))
        self.var_create.set(bool(cfg.get("create_profile_if_missing", True)))

        g = cfg.get("google_login") or {}
        self.var_g_enabled.set(bool(g.get("enabled", True)))
        mode = str(g.get("mode") or "auto").lower()
        if mode == "autofill":
            mode = "auto"
        if mode not in ("auto", "manual", "skip"):
            mode = "auto"
        self.var_g_mode.set(mode)
        self.var_g_wait.set(int(g.get("manual_wait_sec") or 300))
        otp = dict(g.get("otp_fetch") or g.get("otp") or {})
        self.var_otp_enabled.set(
            bool(
                otp.get(
                    "enabled",
                    True if (otp.get("url") or otp.get("secret")) else True,
                )
            )
        )
        self.var_otp_secret.set(str(otp.get("secret") or otp.get("otp_secret") or ""))
        self.var_otp_url.set(str(otp.get("url") or "https://2fa-auth.com/"))
        self.var_otp_selector.set(str(otp.get("selector") or ""))

        ptext = str(cfg.get("proxies_text") or "")
        if not ptext:
            pf = self.base_dir / str(cfg.get("proxies_file") or "proxies.txt")
            if pf.is_file():
                ptext = pf.read_text(encoding="utf-8")
        self.txt_proxies.insert("1.0", ptext)
        if ptext.strip():
            self.validate_proxies()
            start = int(cfg.get("proxy_start_index") or 0)
            if self.validated_proxies and 0 <= start < len(self.validated_proxies):
                self.lst_proxies.selection_clear(0, tk.END)
                self.lst_proxies.selection_set(start)
                self._on_proxy_select()

        rows = cfg.get("accounts_rows")
        if rows:
            for r in rows:
                self.tree_acc.insert(
                    "",
                    tk.END,
                    values=(
                        r.get("email", ""),
                        r.get("password", ""),
                        r.get("profile_title", ""),
                        r.get("otp_secret", "") or r.get("totp_secret", ""),
                        r.get("otp_url", ""),
                        r.get("notes", ""),
                    ),
                )
        else:
            af = self.base_dir / str(cfg.get("accounts_file") or "accounts.csv")
            if af.is_file():
                with af.open(encoding="utf-8-sig", newline="") as fh:
                    for row in csv.DictReader(fh):
                        if row.get("email") or row.get("profile_title"):
                            self.tree_acc.insert(
                                "",
                                tk.END,
                                values=(
                                    row.get("email", ""),
                                    row.get("password", ""),
                                    row.get("profile_title", ""),
                                    row.get("otp_secret", "")
                                    or row.get("totp_secret", "")
                                    or row.get("secret", ""),
                                    row.get("otp_url", "") or row.get("otp_site", ""),
                                    row.get("notes", ""),
                                ),
                            )
        if not self.tree_acc.get_children():
            self.add_sample_account()

        sf = cfg.get("search_flow") or {}
        self.var_search_enabled.set(bool(sf.get("enabled", True if sf else True)))
        # 다중 검색어: keywords[] / keywords_text / keyword 모두 반영
        from .automation import split_search_keywords

        kw_parts = split_search_keywords(sf.get("keywords_text") or "")
        if not kw_parts:
            kw_parts = split_search_keywords(sf.get("keywords") or [])
        single_kw = str(sf.get("keyword") or "").strip()
        if single_kw:
            for p in split_search_keywords(single_kw):
                if p and p not in kw_parts:
                    kw_parts.append(p)
        # de-dupe preserve order
        seen_k: set[str] = set()
        uniq: List[str] = []
        for p in kw_parts:
            key = p.lower()
            if key not in seen_k:
                seen_k.add(key)
                uniq.append(p)
        kw_parts = uniq
        # 표시: 여러 개면 / 로 이어 기본 검색어란에
        kw = " / ".join(kw_parts) if len(kw_parts) > 1 else (kw_parts[0] if kw_parts else "")
        self.var_keyword.set(kw)
        self.var_own_domain.set(
            str(sf.get("target_domain") or sf.get("own_domain") or "")
        )
        contains = sf.get("target_url_contains") or []
        self.var_url_contains.set(
            ", ".join(str(x) for x in contains) if isinstance(contains, list) else str(contains or "")
        )
        self.var_url_regex.set(str(sf.get("url_regex") or sf.get("target_url_regex") or ""))
        tc = sf.get("title_contains") or []
        self.var_title_contains.set(
            ", ".join(str(x) for x in tc) if isinstance(tc, list) else str(tc or "")
        )
        self.var_title_regex.set(str(sf.get("title_regex") or ""))
        pt = sf.get("path_targets") or sf.get("path_targets_text") or []
        self.var_path_targets.set(
            ", ".join(str(x) for x in pt)
            if isinstance(pt, list)
            else str(pt or "")
        )
        pe = sf.get("path_exclude") or sf.get("path_exclude_text") or []
        self.var_path_exclude.set(
            ", ".join(str(x) for x in pe)
            if isinstance(pe, list)
            else str(pe or "")
        )
        self.var_require_domain.set(bool(sf.get("require_domain", True)))
        self.var_skip_ads.set(bool(sf.get("skip_ads", True)))
        self.var_allow_ads.set(not bool(self.var_skip_ads.get()))
        self.var_max_serp.set(int(sf.get("max_serp_pages") or 5))
        self.var_max_clicks.set(
            int(sf.get("max_result_clicks") or sf.get("clicks_per_keyword") or 8)
        )
        self.var_revisit.set(int(sf.get("revisit_count") or 0))
        # bulk lists
        if hasattr(self, "txt_domains"):
            dtext = str(sf.get("domains_text") or "")
            if not dtext and sf.get("allowed_domains"):
                dtext = "\n".join(str(x) for x in sf["allowed_domains"])
            if not dtext:
                df = self.base_dir / "domains.txt"
                if df.is_file():
                    dtext = df.read_text(encoding="utf-8")
            self.txt_domains.delete("1.0", tk.END)
            self.txt_domains.insert("1.0", dtext)
        if hasattr(self, "txt_keywords"):
            ktext = str(sf.get("keywords_text") or "")
            if not ktext and kw_parts:
                ktext = "\n".join(kw_parts)
            if not ktext and sf.get("keywords"):
                ktext = "\n".join(str(x) for x in sf["keywords"])
            if not ktext:
                kf = self.base_dir / "keywords.txt"
                if kf.is_file():
                    ktext = kf.read_text(encoding="utf-8")
            self.txt_keywords.delete("1.0", tk.END)
            self.txt_keywords.insert("1.0", ktext)
        self.var_warmup.set(bool(sf.get("warmup", True)))
        human = dict(sf.get("human") or {})
        self.var_dwell_min.set(int(human.get("dwell_ms_min") or 4000))
        self.var_dwell_max.set(int(human.get("dwell_ms_max") or 12000))
        self.var_scroll_min.set(int(human.get("scroll_steps_min") or 3))
        self.var_scroll_max.set(int(human.get("scroll_steps_max") or 8))
        self.var_human_scroll.set(bool(human.get("scroll", True)))
        self.var_mouse_wander.set(bool(human.get("mouse_wander", True)))
        self.var_read_pauses.set(bool(human.get("read_pauses", True)))
        self.var_internal_click.set(bool(human.get("random_internal_click", False)))

        banners = list(sf.get("banner_clicks") or [])
        targets = cfg.get("targets") or []
        if targets:
            t0 = targets[0]
            self.var_url.set(str(t0.get("url") or ""))
            self.var_wait_ms.set(int(t0.get("wait_ms") or 2000))
            if not banners:
                banners = list(t0.get("clicks") or [])
        if not sf and targets:
            self.var_search_enabled.set(False)

        for c in banners:
            self.tree_clicks.insert(
                "",
                tk.END,
                values=(
                    c.get("text_contains") or c.get("button_text") or "",
                    c.get("selector") or "",
                    int(c.get("wait_after_ms") or 1500),
                    "예" if c.get("optional", True) else "아니오",
                ),
            )

        # 홈 원클릭 폼에 기존 설정 채우기
        rows = cfg.get("accounts_rows") or []
        if rows:
            r0 = rows[0]
            if r0.get("email"):
                self.var_quick_email.set(str(r0.get("email") or ""))
            if r0.get("password"):
                self.var_quick_password.set(str(r0.get("password") or ""))
            sec = r0.get("otp_secret") or (cfg.get("google_login") or {}).get("otp_fetch", {}).get("secret")
            if sec:
                self.var_quick_secret.set(str(sec))
        if self.var_keyword.get().strip():
            # 홈 검색어란: 다중이면 / 유지
            self.var_quick_keyword.set(self.var_keyword.get().strip())
        elif kw_parts:
            self.var_quick_keyword.set(" / ".join(kw_parts))
        if self.var_own_domain.get().strip():
            self.var_quick_domain.set(self.var_own_domain.get().strip())
        if hasattr(self, "txt_quick_proxies") and hasattr(self, "txt_proxies"):
            p = self.txt_proxies.get("1.0", tk.END).strip()
            if p:
                self.txt_quick_proxies.delete("1.0", tk.END)
                self.txt_quick_proxies.insert("1.0", p)

        self.refresh_summary()
        self.log(f"[안내] {APP_TITLE} v{APP_VERSION}")
        self.log(
            "[안내] 홈에서 구글 아이디 + 2FA 시크릿 + 검색어만 넣고 "
            "▶ 원클릭 시작 하세요. 2FA 코드는 화면 위 「실시간 상태」에 표시됩니다."
        )
        self.var_live_hint.set(
            "준비: 홈 ② 에 아이디·비번·시크릿 입력 → ③ 검색어·도메인 → ▶ 원클릭 시작"
        )
        # 첫 실행 시 긴 메뉴얼 대신 홈에 머물기 (원클릭 UX)
        try:
            flag = self.base_dir / ".manual_seen"
            if not flag.is_file():
                flag.write_text("1", encoding="utf-8")
        except Exception:
            pass

    # ---------- run ----------
    def test_connection(self) -> None:
        token = self.var_token.get().strip()
        if not token:
            messagebox.showwarning("토큰", "API 토큰을 입력하세요.")
            return

        def work() -> None:
            try:
                client = OctoClient(
                    api_token=token,
                    cloud_base=self.var_cloud.get().strip(),
                    local_base=self.var_local.get().strip(),
                )
                n = client.test_connection()
                self.log(f"[Test] Cloud OK (profiles sample {n})")
                try:
                    user = client.local_username()
                    self.log(f"[Test] Local OK (user={user or 'ok'})")
                    msg = f"● Online — Cloud OK · Local OK ({user or 'ok'})"
                except OctoError as exc:
                    self.log(f"[Test] Local fail: {exc}")
                    msg = "● Cloud OK · Local OFF (Octo 앱 실행·로그인 확인)"
                self.root.after(0, lambda: self.var_conn_status.set(msg))
            except Exception as exc:
                self.log(f"[Test] fail: {exc}")
                self.root.after(
                    0, lambda: self.var_conn_status.set(f"● Offline — {exc}")
                )

        threading.Thread(target=work, daemon=True).start()
        self.var_status.set("Testing…")

    def start_jobs(self, dry_run: bool = False) -> None:
        if self.worker and self.worker.is_alive():
            # after STOP, worker may still wind down briefly
            if self.cancel_flag.is_set():
                self.worker.join(timeout=3.0)
            if self.worker and self.worker.is_alive():
                # 강제 해제 옵션: 너무 오래 잠기면 UI 풀어주기
                messagebox.showwarning(
                    "잠시만",
                    "이전 작업 정리 중입니다.\n"
                    "2~3초 후 다시 ▶ 원클릭 시작 하세요.\n\n"
                    "계속 안 되면 ■ 중지 를 누른 뒤 다시 시작하세요.",
                )
                return
        # 죽은 worker / 잠긴 UI 복구
        self.worker = None
        self.is_running = False
        self._unlock_ui_after_job()
        try:
            # 홈 원클릭 입력 → 계정·검색·프록시에 자동 반영
            self._apply_quick_account_silent()
            # 프록시: 재시작 후에도 텍스트에서 항상 재검증
            if not self.validated_proxies:
                self.validate_proxies()
            if not self.validated_proxies and hasattr(self, "txt_proxies"):
                # quick proxies fallback
                if hasattr(self, "txt_quick_proxies"):
                    q = self.txt_quick_proxies.get("1.0", tk.END).strip()
                    if q:
                        self.txt_proxies.delete("1.0", tk.END)
                        self.txt_proxies.insert("1.0", q)
                        self.validate_proxies()

            cfg = self.collect_config()
            cfg["dry_run"] = dry_run
            g = cfg.setdefault("google_login", {})
            otp = g.setdefault("otp_fetch", {})
            g["autofill_pause_ms"] = int(g.get("autofill_pause_ms") or 350)
            rows = self._account_rows()
            # empty email shell rows 제거 후 재확인
            rows = [
                r
                for r in rows
                if (r.get("email") or "").strip() or (r.get("profile_title") or "").strip()
            ]
            # any per-account secret → full auto google, no shared secret pollution
            any_secret = any((r.get("otp_secret") or "").strip() for r in rows)
            if any_secret:
                g["enabled"] = True
                g["mode"] = "auto"
                otp["enabled"] = True
                otp["secret"] = ""  # only per-account
                otp["url"] = ""
                cfg["google_login"] = g
            elif self.var_otp_secret.get().strip() or self.var_quick_secret.get().strip():
                sec = self.var_otp_secret.get().strip() or self.var_quick_secret.get().strip()
                g["enabled"] = True
                g["mode"] = "auto"
                otp["enabled"] = True
                otp["secret"] = sec
                cfg["google_login"] = g

            if not cfg["octo_api_token"]:
                messagebox.showwarning(
                    "토큰",
                    "홈 화면 ① 에 Octo API 토큰을 입력하세요.",
                )
                self._show_page("home")
                return
            if not self.validated_proxies:
                messagebox.showwarning(
                    "프록시",
                    "홈 ④ 에 프록시를 붙여넣고 [프록시에 반영·검증] 하거나\n"
                    "프록시 탭에서 검증하세요.",
                )
                self._show_page("home")
                return
            # 이메일 있는 계정만 우선 (빈 샘플 행이 자동로그인을 막는 문제 수정)
            all_accounts = accounts_from_rows(rows if rows else self._account_rows())
            accounts = [a for a in all_accounts if (a.email or "").strip()]
            if not accounts and all_accounts:
                # 프로필만 있는 경우 — 로그인 스킵/수동 가능
                accounts = all_accounts
            if g.get("enabled") and g.get("mode") == "auto":
                if not any(a.email for a in accounts):
                    messagebox.showwarning(
                        "구글 계정",
                        "자동 로그인을 위해 홈 ② 에 계정을 넣으세요.\n\n"
                        "형식: email|비밀번호|2FA시크릿\n"
                        "예: my@gmail.com|pass123|JBSWY3DPEHPK3PXP",
                    )
                    self._show_page("home")
                    return
                # 이메일은 있는데 비번 없는 경우 경고
                no_pw = [a for a in accounts if a.email and not a.password]
                if no_pw:
                    if not messagebox.askyesno(
                        "비밀번호 없음",
                        f"{len(no_pw)}개 계정에 비밀번호가 없습니다.\n"
                        "그래도 시작할까요? (이미 로그인된 세션이면 통과할 수 있음)",
                    ):
                        return
            # validate secrets
            v = validate_account_secrets(
                [
                    {
                        "email": a.email,
                        "otp_secret": a.otp_secret,
                        "password": a.password,
                    }
                    for a in accounts
                ]
            )
            if v.get("invalid"):
                bad = "\n".join(
                    f"· {x.get('email')}: {x.get('reason')}" for x in v["invalid"][:5]
                )
                if not messagebox.askyesno(
                    "2FA 시크릿 오류",
                    f"일부 시크릿이 잘못되었습니다:\n{bad}\n\n그래도 시작할까요?",
                ):
                    return
            from .automation import extract_totp_secret

            for a in accounts:
                if a.otp_secret:
                    a.otp_secret = extract_totp_secret(a.otp_secret) or a.otp_secret
            nv = validate_account_secrets(
                [{"email": a.email, "otp_secret": a.otp_secret} for a in accounts]
            )

            self.var_live_google.set(
                f"Google: {len(accounts)}계정 · 2FA {nv.get('valid_secret') or 0}개"
            )
            self.var_live_2fa.set("2FA: 실행 시 계정마다 자동 생성")
            sf = cfg.get("search_flow") or {}
            if sf.get("enabled"):
                has_kw = bool(
                    str(sf.get("keyword") or "").strip()
                    or (sf.get("keywords") or [])
                    or str(sf.get("keywords_text") or "").strip()
                )
                has_dom = bool(
                    str(sf.get("target_domain") or "").strip()
                    or (sf.get("allowed_domains") or [])
                    or str(sf.get("domains_text") or "").strip()
                )
                if not has_kw:
                    messagebox.showwarning(
                        "검색어",
                        "홈 ③ 에 검색어를 입력하세요.",
                    )
                    self._show_page("home")
                    return
                if not has_dom:
                    messagebox.showwarning(
                        "자사 도메인",
                        "홈 ③ 에 자사 도메인을 입력하세요.",
                    )
                    self._show_page("home")
                    return
            elif not cfg.get("targets"):
                messagebox.showwarning("대상", "검색 ON 또는 직접 URL 이 필요합니다.")
                self._show_page("search")
                return
        except Exception as exc:
            messagebox.showerror("설정 오류", str(exc))
            return

        try:
            # 시작 시에도 keywords/domains/accounts 전부 저장 (재시작 후 원클릭 유지)
            self._persist_config(cfg, quiet=True)
            self.log(
                f"[Save] 시작 전 설정 저장 · 검색어 "
                f"{len((cfg.get('search_flow') or {}).get('keywords') or [])}개"
            )
        except Exception as exc:
            self.log(f"[WARN] 시작 전 저장 경고: {exc}")
            try:
                save_config(self.config_path, cfg)
            except Exception:
                pass

        self.cancel_flag.clear()
        try:
            self._otp_result = None
            self._otp_event.set()
        except Exception:
            pass
        n_acc = len(accounts)
        self.var_live_step.set(f"단계: 시작 (0/{n_acc})")
        self.var_live_hint.set(
            f"{n_acc}개 계정 큐 실행 — 2FA 코드는 계정마다 위에 표시됩니다."
        )
        self._set_running(True)
        self.log("=" * 56)
        self.log("[Start] " + ("미리보기(DRY)" if dry_run else "원클릭 LIVE 매크로"))
        self.log(
            f"[Pipeline] 계정 {n_acc}개 × (프로필 → 프록시 → 로그인 → 2FA → 검색 → 클릭)"
        )
        self.log(
            f"[2FA] 시크릿 계정 {sum(1 for a in accounts if a.otp_secret)}개 — 팝업 없이 자동 인증"
        )

        def on_progress(info: Dict[str, Any]) -> None:
            def ui() -> None:
                phase = info.get("phase")
                if phase == "start":
                    j, t = info.get("job"), info.get("total")
                    em = info.get("email") or info.get("profile")
                    self.var_live_step.set(f"단계: 계정 {j}/{t}")
                    self.var_live_google.set(f"Google: {em}")
                    self.var_live_2fa.set(
                        "2FA: 시크릿 있음 → 자동" if info.get("has_2fa") else "2FA: 없음"
                    )
                    self.var_live_hint.set(
                        f"진행 {j}/{t} · 성공 {info.get('success', 0)} · 실패 {info.get('fail', 0)}"
                    )
                    self.var_status.set(f"실행 중 {j}/{t}")
                elif phase == "done_one":
                    self.var_live_hint.set(
                        f"완료 {info.get('job')}/{info.get('total')} · "
                        f"성공 {info.get('success')} · 실패 {info.get('fail')}"
                    )
                elif phase == "session_done":
                    self.var_live_step.set("단계: 전체 완료")
                    self.var_live_hint.set(
                        f"세션 끝 · 성공 {info.get('success')} / 실패 {info.get('fail')} "
                        f"/ 총 {info.get('total')}"
                    )
                    self.var_status.set("완료")

            self.root.after(0, ui)

        def work() -> None:
            try:
                runner = JobRunner(
                    cfg,
                    self.base_dir,
                    proxies=self.validated_proxies,
                    accounts=accounts,
                    log=self.log,
                    should_cancel=self.cancel_flag.is_set,
                    proxy_start_index=self._selected_proxy_index(),
                    ask_2fa=self.ask_2fa_code,
                    on_job_progress=on_progress,
                )
                self.current_runner = runner
                result = runner.run_all()
                self.log(
                    f"[Finish] 성공={result.get('success')} 실패={result.get('fail')} "
                    f"취소={result.get('cancelled')} 총={result.get('total')}"
                )
                self.root.after(
                    0,
                    lambda: messagebox.showinfo(
                        "완료",
                        f"작업 종료\n\n"
                        f"성공: {result.get('success')}\n"
                        f"실패: {result.get('fail')}\n"
                        f"취소: {result.get('cancelled')}\n"
                        f"총: {result.get('total')}\n\n"
                        "상세는 아래 로그를 확인하세요.",
                    )
                    if not dry_run
                    else None,
                )
            except Exception as exc:
                self.log(f"[Error] {exc}")
                self.root.after(0, lambda: messagebox.showerror("실행 오류", str(exc)))
            finally:
                self.current_runner = None
                self.root.after(0, self._unlock_ui_after_job)
                self.root.after(0, self.refresh_summary)

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _unlock_ui_after_job(self) -> None:
        """Always re-enable Start / Test after job ends or stop."""
        self.is_running = False
        buttons = [self.btn_start, self.btn_dry, self.btn_test, self.btn_save]
        if hasattr(self, "btn_start_home"):
            buttons.append(self.btn_start_home)
        for w in buttons:
            try:
                w.configure(state=tk.NORMAL)
            except Exception:
                pass
        try:
            self.btn_stop.configure(state=tk.DISABLED)
        except Exception:
            pass
        # worker 참조 정리 (재시작 후 원클릭 잠김 방지)
        if self.worker and not self.worker.is_alive():
            self.worker = None
        self.var_status.set("Ready")
        self.var_addr.set("octo://automation/ready")

    def stop_jobs(self) -> None:
        """Immediate STOP: unlock UI + cancel flag + force-stop Octo profiles."""
        self.cancel_flag.set()
        self.log("[STOP] 긴급 중지 요청 — UI 잠금 해제 · 프로필 중지 시도")
        # Unblock connection test / start immediately (beta UX fix)
        self._unlock_ui_after_job()
        self.var_status.set("중지됨 · 연결테스트/시작 가능")
        # Unblock 2FA popup waiter
        try:
            self._otp_result = None
            self._otp_event.set()
        except Exception:
            pass

        runner = self.current_runner

        def _force_stop() -> None:
            try:
                if runner is not None:
                    self.log("[STOP] 실행 중 프로필 force stop…")
                    runner.stop_started(force=True)
                    self.log("[STOP] 프로필 중지 완료")
            except Exception as exc:
                self.log(f"[STOP] 프로필 중지 경고: {exc}")

        threading.Thread(target=_force_stop, daemon=True).start()

    def _on_close(self) -> None:
        if self.is_running:
            if not messagebox.askyesno("종료", "실행 중입니다. 종료할까요?"):
                return
            self.cancel_flag.set()
        try:
            self.save_settings()
        except Exception:
            pass
        self.root.destroy()


def run_gui(base_dir: Path) -> None:
    root = tk.Tk()
    GuiApp(root, base_dir)
    root.mainloop()
