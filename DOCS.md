# 프로젝트 문서 (Architecture & Modules)

**Octo Google Site Automation** — v2.0.0  
**로컬 웹 UI (FastAPI)** + 선택 Tkinter GUI + CLI로 Octo Browser 프로필을 프록시 로테이션하며 실행하고,  
Google 로그인 후 **검색어로 본인 사이트를 찾아 사람처럼 클릭·재방문·배너 클릭**하는 자동화 도구입니다.

AI 협업·전환 이력: [docs/AI_DEVELOPMENT.md](docs/AI_DEVELOPMENT.md)

---

## 1. 목적

| 목표 | 설명 |
|------|------|
| 멀티 프로필 | 계정마다 Octo 프로필을 생성/재사용 |
| 프록시 로테이션 | 작업마다 다른 프록시를 프로필에 주입 |
| Google 로그인 | `auto` 모드로 이메일·비밀번호 자동 입력 |
| 검색 흐름 | 검색어 입력 → SERP에서 **내 도메인** 링크 클릭 |
| 사람처럼 행동 | 타이핑 딜레이, 스크롤, 마우스 이동, 체류 시간 |
| 재방문 | 뒤로가기 후 검색결과에서 다시 클릭 |
| 배너 클릭 | 내 사이트 안 버튼/배너를 글자 기준으로 클릭 |
| 사이트 액션 | (선택) 직접 URL 이동 + 클릭 체인 |
| 운영 UI | 비개발자도 탭 UI로 설정·실행 가능 |

---

## 2. 전체 아키텍처

```
┌─────────────────────────────────────────────────────────┐
│  main.py                                                │
│   · --web (기본)  /  --gui  /  --cli                     │
└───────┬─────────────────────┬───────────────┬───────────┘
        ▼                     ▼               ▼
┌───────────────┐     ┌──────────────┐  runner CLI
│  web/app.py   │     │  gui_app.py  │
│  FastAPI+SSE  │     │  (Tkinter)   │
└───────┬───────┘     └──────┬───────┘
        │                    │
        └──────────┬─────────┘
                   ▼
           ┌───────────────┐
           │  runner.py    │  JobRunner
           └───────┬───────┘
                   │
     ┌─────────────┼─────────────┐
     ▼             ▼             ▼
 octo_client  proxy_manager  automation
     │                           │
     └──── Octo Browser CDP ─────┘
```

### 데이터 흐름 (한 작업)

1. `JobRunner` 가 계정 1건 + 프록시 1건 선택  
2. Cloud API: 프로필 검색/생성, `PATCH` 로 프록시 교체  
3. Local API: `/profiles/start` → `ws_endpoint`, `debug_port`  
4. Playwright `connect_over_cdp`  
5. `google_login()` → `run_search_flow()` (또는 `run_targets()`)  
6. Local API: 프로필 stop  

---

## 3. 모듈 설명

### 3.1 `main.py`

- 진입점. 기본은 GUI (`src.gui_app.run_gui`).
- `--cli` 시 `JobRunner.run_all()`.
- 최초 실행 시 example → `config.json` / `proxies.txt` / `accounts.csv` 복사.

### 3.2 `src/gui_app.py` (v1.2.0)

| 영역 | 역할 |
|------|------|
| 탭 1 | API 토큰, 연결 상태, 요약 |
| 탭 2 | 프록시 붙여넣기·검증·모드 |
| 탭 3 | 계정 테이블, Google 모드(auto/manual/skip) |
| 탭 4 | 검색어·내 도메인·재방문·배너 클릭 |
| 탭 5 | delay, timeout, headless, API base URL |
| 하단 | 저장 / 연결테스트 / DRY RUN / 시작 / 중지 / 로그 |

- 실행은 **백그라운드 스레드**에서 `JobRunner` 호출.
- `cancel_flag` 로 긴급 중지.
- 설정 수집: `collect_config()` → 디스크 저장.

### 3.3 `src/runner.py`

| 심볼 | 역할 |
|------|------|
| `AccountJob` | email, password, profile_title, notes |
| `load_config` / `save_config` | JSON |
| `load_accounts` / `accounts_from_rows` | CSV 또는 GUI 행 |
| `JobRunner` | 연결 테스트, 프로필 준비, 작업 루프, 정리 |

주요 설정 키:

```text
octo_api_token, cloud_base, local_base
proxy_type, proxy_mode, proxy_start_index, proxies_text
accounts_rows / accounts_file
reuse_existing_profiles, create_profile_if_missing
headless, start_timeout_sec, delay_between_jobs_sec
stop_profile_after_job, max_jobs, dry_run
google_login { enabled, mode, login_url, success_url_contains,
               manual_wait_sec, autofill_pause_ms }
search_flow { enabled, keyword, keywords[], target_domain,
              target_url_contains[], max_serp_pages, revisit_count,
              human { dwell_ms_min, dwell_ms_max, scroll },
              banner_clicks[] }
targets [ { url, wait_until, wait_ms, clicks[] } ]  # 검색 OFF 시 직접 URL
```

### 3.4 `src/octo_client.py`

| 메서드 | API |
|--------|-----|
| `test_connection` | Cloud GET `/profiles` |
| `list_profiles` / `find_profile_by_title` | Cloud GET |
| `create_profile` | Cloud POST `/profiles` (+ inline proxy) |
| `update_profile_proxy` | Cloud PATCH `/profiles/{uuid}` |
| `local_username` | Local GET `/username` |
| `start_profile` | Local POST `/profiles/start` |
| `stop_profile` / force_stop | Local POST |
| `list_active_profiles` | Local GET `/profiles/active` |

- Cloud 헤더: `X-Octo-Api-Token`
- 429 시 `Retry-After` 대기 후 재시도
- Local 기본: `http://127.0.0.1:58888/api`

### 3.5 `src/proxy_manager.py`

| 심볼 | 역할 |
|------|------|
| `Proxy` | host/port/login/password/type → `to_octo_inline()` |
| `parse_proxy_line` / `parse_proxy_text` | 붙여넣기 파싱 |
| `ProxyRotator` | `round_robin` / `from_selected` / `fixed` |

지원 형식:

- `host:port`
- `host:port:user:pass`
- `http://user:pass@host:port`
- `socks5://...`
- 선택: 줄 끝 `|change_ip_url`

### 3.6 `src/automation.py` (Google 자동 로그인 핵심)

| 심볼 | 역할 |
|------|------|
| `BrowserSession` | CDP 연결, 첫 페이지 확보, disconnect only |
| `google_login` | 모드별 로그인 오케스트레이션 |
| `_auto_login_flow` | 계정선택 → 이메일 → 비번 → 중간화면 |
| `_dismiss_interstitials` | Not now / 나중에 / 확인 등 |
| `wait_login_success` | 성공 URL·세션 폴링 |
| `run_targets` | URL 이동 + 클릭 체인 |
| `run_search_flow` | Google 검색 → 내 사이트 클릭 → 배너 → 재방문 |
| `_google_search` / `_type_search_query` | 사람처럼 검색어 입력 |
| `_find_and_click_own_site` | SERP에서 내 도메인 링크 탐지·클릭 |
| `_browse_own_site` | 체류·스크롤·배너 클릭 |
| `run_browser_job` / `_sync` | 한 프로필 브라우저 잡 |

#### Google `auto` 상세

```
goto login_url
  → 이미 로그인? return True
  → Account Chooser: 이메일 매칭 클릭 or "다른 계정 사용"
  → email input → type → Next
  → password input → type → Next
  → dismiss: 나중에 / Not now / Skip / 예(유지) 등
  → success URL? OK
  → challenge/2FA 감지? manual_wait_sec 동안 대기
  → 그 외 need_manual → 동일 대기
```

성공 판정 힌트 URL:

- `myaccount.google.com`, `mail.google.com`
- `accounts.google.com/b/`, `drive.google.com` 등

모드 별칭: `autofill` → `auto`

---

## 4. 설정 파일

### `config.json` (실행용, git/공유 금지)

`config.example.json` 을 복사한 뒤 토큰·URL을 채웁니다.

```json
{
  "octo_api_token": "...",
  "proxy_mode": "round_robin",
  "google_login": { "enabled": true, "mode": "auto", "manual_wait_sec": 300 },
  "targets": [
    {
      "url": "https://example.com",
      "wait_ms": 2000,
      "clicks": [
        { "selector": "a", "text_contains": "", "wait_after_ms": 1500, "optional": true }
      ]
    }
  ]
}
```

### `accounts.csv`

```csv
email,password,profile_title,notes
user@gmail.com,secret,auto-google-1,auto 로그인
```

### `proxies.txt`

```text
host:port:user:pass
```

GUI **설정 저장** 시 위 세 파일이 함께 갱신됩니다.

---

## 5. 실행 모드

| 방식 | 명령 |
|------|------|
| GUI | `실행하기.bat` 또는 `python main.py` |
| CLI 전체 | `python main.py --cli` |
| CLI dry-run | `python main.py --cli --dry-run` |
| 설정 경로 | `python main.py --cli --config path\to\config.json` |

---

## 6. 의존성

```text
requests>=2.31.0
playwright>=1.40.0
```

설치 스크립트가 `playwright install chromium` 을 수행합니다.  
실제 브라우저는 **Octo 프로필 프로세스**이며, Playwright는 CDP로 원격 제어만 합니다.

---

## 7. 에러 처리 정책

| 계층 | 동작 |
|------|------|
| Cloud 429 | 헤더 기반 sleep 후 재시도 |
| Local 연결 실패 | 명확한 한국어 메시지 (앱 실행 안내) |
| 프로필 시작 실패 | 작업 실패 카운트, 다음 작업 계속 |
| Google 자동 실패 | 수동 대기로 폴백 (timeout 내) |
| 클릭 optional | 요소 없어도 계속 |
| 긴급 중지 | `should_cancel` 폴링, 다음 job 스킵 |

---

## 8. 보안 메모

- 비밀번호·API 토큰 평문 저장
- 로그에 이메일은 출력, **비밀번호는 로그에 찍지 않음** (`비밀번호 입력` 만 표시)
- 헤드리스는 Google 로그인·2FA에 불리 → 기본 끔
- 본인 계정·허용된 사이트만 사용

---

## 9. 버전 이력

| 버전 | 내용 |
|------|------|
| 1.0.x | 초기 GUI, 프록시 로테이션, manual Google 로그인 |
| 1.1.0 | 프록시 붙여넣기 UI, 탭 구성 정리 |
| 1.2.0 | Google `auto` 로그인 (이메일/비번 자동, 중간화면 처리) |
| 1.3.0 | 2FA 코드 팝업 입력, 사이트 클릭을 화면 글자 중심으로 단순화 |
| 1.4.0 | 검색어로 본인 사이트 찾기, 사람처럼 행동, 재방문, 배너 클릭 |
| 1.5.0 | 더블클릭 실행, Octo 다크 UI, START 원클릭 자동화 |
| **1.6.0** | **URL/정규식 매칭**, **인간행동 고도화**, **브라우저형 UI**, 앱 내 사용법 |

### search_flow 실행 순서

```
Google 홈 → 검색어 타이핑 → Enter
  → 결과 페이지 스크롤
  → href 가 target_domain 인 링크 클릭 (내 사이트만)
  → 체류 + 스크롤 + banner_clicks
  → (revisit_count > 0) 뒤로가기 → 다시 내 사이트 클릭 …
```

---

## 10. 확장 아이디어 (미구현)

- 다중 검색어 순회 UI
- TOTP/2FA 시드 자동 입력
- 작업 결과 CSV 리포트
- 프록시 헬스체크 (실제 접속 테스트)

---

## 11. 관련 외부 문서

- [Octo Automation API](https://docs.octobrowser.net/en/api/start-api/)
- [Octo Local Client API](https://github.com/octobrowser/documentation/blob/main/api/local-client.md)
- [Playwright CDP](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)
