# AI 협업 개발 이력 (How AIs built this)

이 문서는 **다른 AI / 협업 도구가 이 프로젝트를 어떻게 설계·확장했는지** 기록합니다.  
GitHub에 올릴 때 후속 기여자(사람·AI)가 맥락을 바로 잡을 수 있게 하는 것이 목적입니다.

> **범위**: 허가된 **자사 사이트** 검색 노출·CTA 점검용 도구.  
> 광고 조작·경쟁사 타깃·무단 트래픽은 지원 대상이 아닙니다.

---

## 1. 프로젝트 한 줄

**Octo Browser Cloud/Local API + Playwright CDP**로 프로필·프록시를 돌리며  
Google 검색 → 자사 도메인 클릭 → 사람처럼 체류/스크롤/CTA 클릭을 자동화한다.

| 항목 | 값 |
|------|-----|
| 현재 UI | **로컬 웹 (FastAPI)** — 기본 |
| 레거시 UI | Tkinter GUI (`--gui`) |
| 코어 | `src/runner.py`, `src/automation.py`, `src/octo_client.py` |
| 버전 축 | v1.x GUI 중심 → **v2.0 웹 UI** |

---

## 2. 아키텍처 원칙 (AI가 지킨 설계)

```
┌──────────────────────────────────────────────────────────┐
│  UI 레이어 (교체 가능)                                     │
│   web/  FastAPI + HTML/JS   │   src/gui_app.py (Tkinter) │
│   main.py --cli                                           │
└────────────────────────────┬─────────────────────────────┘
                             │ config dict + JobRunner
                             ▼
┌──────────────────────────────────────────────────────────┐
│  오케스트레이션  src/runner.py  (JobRunner)                │
│   계정×프록시 배정 → 프로필 준비 → start → browser job     │
└──────────────┬─────────────────────────────┬─────────────┘
               ▼                             ▼
      src/octo_client.py              src/automation.py
      Cloud + Local API               Playwright over CDP
               │                             │
               └──────── Octo Browser ───────┘
```

**원칙**

1. **비즈니스 로직은 UI에 넣지 않는다** — 검색/로그인/클릭은 `automation.py`.
2. **설정은 JSON + side files** — `config.json`, `proxies.txt`, `accounts.csv`, `domains.txt`, `keywords.txt`.
3. **시크릿은 커밋 금지** — `.gitignore`에 런타임 파일 제외, 예제만 `*.example.*`.
4. **로컬 전제** — Octo Local API(`127.0.0.1:58888`) + 웹 UI도 `127.0.0.1` 바인딩.

---

## 3. 단계별 개발 타임라인 (재구성)

여러 AI/세션이 누적한 기능을 논리 순서로 정리한 것입니다.  
(실제 채팅 세션 ID는 환경마다 다를 수 있음.)

### Phase A — 기반 (CLI + Octo API)

| 작업 | 결과 모듈 | 설명 |
|------|-----------|------|
| Octo Cloud/Local 클라이언트 | `octo_client.py` | 프로필 CRUD, start/stop, 429 재시도 |
| 프록시 파싱·로테이션 | `proxy_manager.py` | `host:port:user:pass`, socks5, round_robin 등 |
| 작업 러너 | `runner.py` | 계정 루프, dry-run, 세션 로그 |
| 진입점 | `main.py` | CLI / 이후 GUI·웹 분기 |

### Phase B — 브라우저 자동화

| 작업 | 결과 | 설명 |
|------|------|------|
| CDP 세션 | `BrowserSession` | Playwright `connect_over_cdp` |
| Google 로그인 | `google_login` / `_auto_login_flow` | auto / manual / skip |
| 2FA | TOTP 시크릿, 선택적 수동 팝업 | `email\|pass\|secret` 파이프 형식 |
| 검색 플로우 | `run_search_flow` | 키워드 폴백, 도메인 매칭, 광고 스킵 |
| 휴먼 행동 | scroll / dwell / mouse | `search_flow.human` |
| CTA/배너 | 글자 기준 클릭 | CSS 몰라도 사용 |

### Phase C — Windows GUI (Tkinter)

| 작업 | 결과 | 설명 |
|------|------|------|
| 다크 탭 UI | `gui_app.py` (~3k lines) | Home / Proxy / Profile / Search / Settings |
| 원클릭 홈 | 계정 붙여넣기 + 시작 | 비개발자 UX |
| 백그라운드 실행 | thread + cancel_flag | 긴급 중지, 로그 큐 |
| 단계 테스트 | `step_tests.py` | 연결·로그인·검색 단위 점검 |
| 배포 | `bootstrap.py`, bat/vbs, PyInstaller | 더블클릭 실행 |

### Phase D — 웹 UI 전면 전환 (v2.0)

| 작업 | 결과 | 설명 |
|------|------|------|
| FastAPI 서버 | `web/app.py` | REST + SSE 로그 |
| 설정 I/O 공용 | `web/config_io.py` | GUI와 동일 파일 포맷 |
| 잡 매니저 | `web/job_manager.py` | 스레드 실행, 2FA 브리지 |
| 프론트 | `web/templates` + `static` | 다크 원클릭 대시보드 |
| 기본 진입 | `main.py` 기본 = `--web` | GUI는 `--gui` 유지 |
| GitHub 준비 | `.gitignore`, 본 문서, README | 시크릿 제외 + 협업 맥락 |

---

## 4. AI에게 자주 맡긴 작업 패턴

후속 AI에게 그대로 넘기기 좋은 프롬프트 패턴:

1. **기능 추가**  
   “`JobRunner` / `automation` 은 유지하고 UI만 확장해.”
2. **셀렉터·로그인 깨짐**  
   “`src/automation.py` 의 Google 로그인·SERP 매칭만 수정, API 계약 유지.”
3. **설정 키 추가**  
   “`config.example.json` + `config_io.normalize_config` + 웹 폼 동시 반영.”
4. **절대 하지 말 것**  
   - 광고 클릭 강제, 타인 도메인 기본값  
   - 토큰·비밀번호·프록시를 저장소에 커밋  
   - Local API를 외부 공개 바인딩(`0.0.0.0`)으로 바꾸기 (명시 요청 없을 때)

---

## 5. 모듈 지도 (읽기 순서)

| 순서 | 파일 | 왜 |
|------|------|-----|
| 1 | `README-KO.md` | 사용자 관점 |
| 2 | `DOCS.md` | 아키텍처 상세 |
| 3 | `src/runner.py` | 작업 루프 |
| 4 | `src/automation.py` | 실제 브라우저 동작 |
| 5 | `src/octo_client.py` | Octo API |
| 6 | `web/app.py` | 웹 API 표면 |
| 7 | `web/static/js/app.js` | UI 상태·API 호출 |
| 8 | `src/gui_app.py` | 레거시 GUI (참고) |

---

## 6. 데이터·시크릿 규칙 (GitHub)

| 커밋 O | 커밋 X |
|--------|--------|
| `config.example.json` | `config.json` |
| `proxies.example.txt` | `proxies.txt` |
| `accounts.example.csv` | `accounts.csv` |
| `domains.example.txt` / `keywords.example.txt` | `domains.txt` / `keywords.txt` |
| `src/`, `web/`, docs | `logs/`, `.env`, `dist/`, `*.exe` |

푸시 전 체크:

```bash
git status
# config.json / accounts.csv / proxies.txt 가 staged 되면 안 됨
```

---

## 7. 실행 모드 요약

```bash
# 웹 UI (기본) — http://127.0.0.1:8787
python main.py
python main.py --web

# 예전 데스크톱 GUI
python main.py --gui

# 콘솔
python main.py --cli
python main.py --cli --dry-run
```

Windows 더블클릭: `웹시작.bat` / `start_web.bat`

---

## 8. v2 웹 전환 시 의도적으로 유지한 것

- `JobRunner.run_all` 계약
- 계정 파이프 형식 `email|password|2fa_secret`
- 프록시 텍스트 형식
- `search_flow` JSON 스키마
- dry-run / 긴급 중지 / 세션 로그 파일

**바꾼 것**: 기본 UX를 브라우저 기반으로 — 반응형 UI, SSE 실시간 로그, REST로 설정 저장·시작, 나중에 원격 대시보드·히스토리·멀티 워커로 확장하기 쉽게.

---

## 9. 다음 AI에게 추천하는 확장 (우선순위)

1. 작업 히스토리 DB (성공/실패 URL 통계)
2. 다중 키워드×다중 도메인 매트릭스 리포트 HTML
3. 스케줄 실행 (cron / Windows 작업 스케줄러 연동)
4. 웹 UI 다국어 (EN)
5. 선택적 간단 로컬 인증 (같은 LAN 공유 시)

---

## 10. 면책

이 도구와 문서는 **본인 소유·운영 사이트의 QA** 를 전제로 합니다.  
자동화 사용 시 Google·Octo·해당 사이트 ToS 및 현지 법령을 준수할 책임은 사용자에게 있습니다.
