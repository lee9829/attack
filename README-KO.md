# Octo Google Site Automation (Windows)

Octo Browser **Cloud API + Local API**로 프로필·프록시를 바꿔가며:

1. 프로필 시작 (CDP / Playwright 연결) + **프록시 주입**
2. **Google 자동 로그인** (선택, 이메일·비밀번호·2FA)
3. **검색어로 본인 사이트 검색 → 결과에서 내 도메인 클릭**
4. 사람처럼 스크롤·체류 후 **배너/버튼 클릭**, (설정 시) **다시 나가서 재클릭**

을 수행하는 **로컬 웹 UI + 자동화 엔진**입니다. (v2부터 기본 UI는 브라우저)

| 항목 | 값 |
|------|-----|
| 버전 | **2.0.0** (웹 UI) |
| 플랫폼 | Windows 10/11 |
| Python | 3.10+ |
| UI | FastAPI 웹 (`http://127.0.0.1:8787`) · 선택 Tkinter `--gui` |
| 핵심 연동 | Octo Browser Cloud + Local API, Playwright CDP |

---

## 한 줄 요약

프록시를 붙여넣고 → (선택) Google 계정 → **검색어 + 내 사이트 도메인 + 배너 글자**를 설정하면,  
**프로필마다 다른 프록시로 브라우저를 띄워 검색 후 내 사이트를 찾아 사람처럼 클릭**합니다.

웹으로 바꾼 이유: 설정·로그·확장이 GUI보다 편하고, 나중에 히스토리·리포트·스케줄을 붙이기 쉽습니다.  
자동화 코어(`src/runner.py`, `src/automation.py`)는 그대로입니다.

---

## 준비물

| 항목 | 설명 |
|------|------|
| Windows 10/11 | 권장 |
| Python 3.10+ | [python.org](https://www.python.org/downloads/) — 설치 시 **Add to PATH** 체크 |
| Octo Browser | 데스크톱 앱 실행 + 로그인 |
| Octo API | **Base 이상** 요금제, 설정 → Additional → **API Token** |
| 프록시 | `host:port:user:pass` 목록 |
| Google 계정 | auto 모드 시 **이메일 + 비밀번호** (+ 가능하면 2FA 시크릿) |

---

## 빠른 시작 (웹 UI · 권장)

1. PC에 **Python 3.10+** (PATH 등록)
2. **Octo Browser** 실행 후 로그인
3. 아래 중 하나 실행:
   - **`웹시작.bat`** / **`start_web.bat`** 더블클릭  
   - 또는 `python main.py`
4. 브라우저가 **`http://127.0.0.1:8787`** 을 엽니다 (최초 1회 패키지 자동 설치)
5. 홈에서 토큰 · 계정 · 검색어 · 도메인 · 프록시 입력 → **▶ 시작**

| 파일 | 용도 |
|------|------|
| **`웹시작.bat`** / **`start_web.bat`** | **권장** 웹 UI |
| `python main.py --gui` | 예전 Tkinter GUI |
| `python main.py --cli` | 콘솔만 |
| `install.bat` | 패키지 미리 설치 |
| `build_exe.bat` | exe 빌드 (선택) |

### 권장 설정 순서

1. **홈 ①** — API 토큰 → 연결 테스트  
2. **②** — `email\|비밀번호\|2FA시크릿` 붙여넣기  
3. **③** — 검색어 · 자사 도메인 · CTA 글자  
4. **④** — 프록시 붙여넣기 → 검증  
5. **DRY RUN** → 배정 확인 후 **▶ 시작**

### 웹 화면 메뉴

| 메뉴 | 하는 일 |
|------|---------|
| **홈 · 원클릭** | 토큰, 계정, 검색, 프록시, 실시간 상태 |
| **프록시** | 목록 · 검증 · 로테이션 |
| **계정 · Google** | 표 편집 · auto/manual/skip |
| **검색 · 점검** | SERP · 재방문 · 휴먼 행동 |
| **설정** | API URL · 간격 · 프로필 옵션 |
| **로그** | SSE 실시간 로그 |

### 상단 버튼

| 버튼 | 설명 |
|------|------|
| **설정 저장** | `config.json` / `proxies.txt` / `accounts.csv` 등 |
| **연결 테스트** | Cloud + Local API |
| **DRY RUN** | 브라우저 없이 배정만 |
| **▶ 시작** | 실제 실행 |
| **■ 중지** | 긴급 중지 |

### 레거시 GUI

`python main.py --gui` 또는 기존 `시작.vbs` 계열로 Tkinter UI를 쓸 수 있습니다.

---

## Google 자동 로그인 (v1.3)

### 모드

| mode | 설명 |
|------|------|
| **`auto`** (기본) | 아이디·비번 자동 + 중간화면 처리. **2차 인증 코드만 팝업으로 입력** |
| **`manual`** | 브라우저에서 직접 로그인 (코드 칸이 보이면 팝업도 지원) |
| **`skip`** | 로그인 단계 생략 (이미 쿠키/세션이 있을 때) |

### auto 모드 흐름 (비개발자용)

1. 로그인 페이지 이동  
2. 이미 로그인되어 있으면 성공  
3. **이메일(아이디)** 자동 입력 → 다음  
4. **비밀번호** 자동 입력 → 다음  
5. 중간 안내 화면 자동 닫기  
6. **2차 인증**이 뜨면:
   - 인증 앱 코드 입력 화면으로 이동 시도  
   - 프로그램 창에 **★ 2차 인증 코드 입력** 팝업  
   - 2FA 사이트/앱에서 코드 복사 → 붙여넣기 → 확인  
   - 프로그램이 코드를 넣고 다음 진행  
7. **검색 → 내 사이트 클릭 → 배너 → 재방문** 자동  

> 전화 “예” 알림·보안키는 자동이 어렵습니다. **인증 앱 6자리 숫자**를 쓰세요.

### 검색 · 내 사이트 (v1.4)

| 입력 | 설명 | 예 |
|------|------|-----|
| **검색어** | Google에 입력할 키워드 | `내브랜드 공식몰` |
| **내 도메인** | 결과 중 **내 사이트만** 클릭 | `mysite.com` |
| **재방문** | 클릭 후 뒤로 나가 다시 클릭 횟수 | `1` → 총 2회 방문 |
| **배너 글자** | 사이트 안에서 누를 버튼/배너 | `자세히 보기` |

사람처럼: 검색어 한 글자씩 입력, 스크롤, 마우스 이동, 체류 시간 랜덤.

### 사이트 안 클릭 (비개발자)

CSS를 몰라도 됩니다. **화면에 보이는 글자**만 적으세요.

| 입력 | 예 |
|------|-----|
| 배너/버튼 글자 | `자세히 보기` / `신청하기` / `이벤트` |
| CSS (선택) | 비워 두어도 됨 |

### 계정 입력 예시

| email | password | profile_title | 결과 |
|-------|----------|---------------|------|
| a@gmail.com | `실제비번` | auto-a | 자동 로그인 시도 |
| b@gmail.com | (비움) | auto-b | 이메일까지만 자동, 비번은 수동 |
| (비움) | (비움) | saved-profile | 쿠키만 사용 / skip 권장 |

**GUI**: 계정·Google 탭 → 이메일·비밀번호·프로필명 → **행 추가**  
**파일**: `accounts.csv` 또는 `config.json` 의 `accounts_rows`

---

## 프록시 사용 방식

| 모드 | 의미 |
|------|------|
| `round_robin` | 목록을 처음부터 돌아가며 사용 |
| `from_selected` | 목록에서 **클릭한 줄부터** 순서대로 |
| `fixed` | 목록에서 **선택한 프록시만** 계속 사용 |

### 프록시 한 줄 형식

```text
proxy.example.net:10001:user:password
1.2.3.4:8080
socks5://user:pass@1.2.3.4:1080
```

1. 프록시를 텍스트 칸에 여러 줄 붙여넣기  
2. **✓ 검증 · 목록 반영** 클릭  
3. 아래 목록에서 원하는 줄을 클릭해 시작 순번 선택  
4. 사용 방식을 바꿔가며 DRY RUN / 시작  

---

## 동작 흐름

```
계정 목록 각 행
  → 선택한 방식에 따라 프록시 1개 배정
  → Cloud API: 프로필 생성 또는 기존 프로필에 프록시 교체
  → Local API: 프로필 Start (CDP)
  → Playwright 연결
  → Google 로그인 (auto / manual / skip)  [선택]
  → Google 검색어 입력 (사람처럼 타이핑)
  → 결과에서 내 도메인 링크 클릭
  → 사이트 체류·스크롤·배너 클릭
  → (재방문) 뒤로가기 → 다시 내 사이트 클릭
  → 프로필 중지
  → 다음 계정 / 다음 프록시
```

---

## 검색 · 배너 클릭 예시

| 항목 | 값 |
|------|-----|
| 검색어 | `우리카페 강남` |
| 내 도메인 | `ourcafe.co.kr` |
| 재방문 | `1` |
| 배너 글자 | `메뉴 보기` / `예약하기` |

- 검색 OFF 시: 예전처럼 **직접 URL** 로 열고 클릭 가능
- 없어도 계속: 배너 요소가 없어도 다음 단계로

---

## 명령줄 (선택)

```bat
python main.py
python main.py --cli
python main.py --cli --dry-run
python main.py --cli --config config.json
```

---

## 프로젝트 구조

```text
Octo-Google-Site-Automation/
├── main.py                 # 진입점 (GUI 기본 / --cli)
├── 설치하기.bat / 실행하기.bat
├── config.example.json     # 설정 템플릿
├── accounts.example.csv
├── proxies.example.txt
├── requirements.txt
├── README-KO.md            # 사용 가이드 (이 파일)
├── DOCS.md                 # 아키텍처·모듈 상세
└── src/
    ├── gui_app.py          # Windows GUI
    ├── runner.py           # 작업 오케스트레이션
    ├── automation.py       # Playwright: Google 로그인·클릭
    ├── octo_client.py      # Octo Cloud / Local API
    └── proxy_manager.py    # 프록시 파싱·로테이션
```

저장 시 생성되는 실행 파일(공유 금지):

- `config.json` — 토큰·타깃·Google 설정  
- `proxies.txt` — 프록시 목록  
- `accounts.csv` — 이메일/비밀번호/프로필명  

자세한 모듈·API 설명은 **[DOCS.md](DOCS.md)** 를 보세요.

---

## 문제 해결

| 증상 | 조치 |
|------|------|
| Local API 연결 실패 | Octo 데스크톱 실행 + 로그인, 포트 **58888** |
| 401 | Base 이상 요금제, API 토큰 재복사 |
| 프록시 0개 | 붙여넣기 후 **검증** 필수 |
| Google 자동 로그인 실패 | 이메일·비번 확인, 모드=`manual`로 직접 로그인, 2FA 앱/SMS 확인 |
| 비밀번호 입력란이 안 나옴 | 프록시/IP 평판, captcha, “비정상적인 활동” 차단 여부 확인 |
| Playwright 오류 | `설치하기.bat` 재실행 |
| 프로필 시작 실패 | Octo에서 동일 프로필이 이미 열려 있는지 확인 |

---

## 보안 · 이용 안내

- 토큰·비밀번호가 `config.json` / `accounts.csv` 에 평문 저장됩니다. **폴더 공유·업로드 금지**
- 본인 계정·권한 있는 사이트 용도로만 사용하세요.
- 검색광고 부정클릭·불법 트래픽 조작 용도로 사용하지 마세요.
- Google 정책상 자동화 로그인은 계정 추가 인증·제한을 유발할 수 있습니다.

---

## 공식 문서 (외부)

- [Octo API 시작](https://docs.octobrowser.net/en/api/start-api/)
- [Local Client API](https://github.com/octobrowser/documentation/blob/main/api/local-client.md)
- [Playwright Python](https://playwright.dev/python/)
