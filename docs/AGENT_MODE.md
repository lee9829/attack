# Windows Octo 에이전트 연동

VPS 웹 UI + **내 PC Octo Browser** 조합입니다.

```
[웹 브라우저] → VPS 사이트 (설정·시작 버튼)
                    ↓ 작업 큐
[Windows 에이전트] → 로컬 Octo API :58888 → 프로필 Start → Google 클릭
                    ↓ 로그
                 VPS 사이트 로그 패널
```

## 사용 순서

### 1) PC (한 번 / 작업할 때마다)

1. **Octo Browser** 실행 후 로그인  
2. 프로젝트 폴더에서:

```bat
agent\start_agent.bat
```

검은 창에 `heartbeat` 유지되면 연결됨.

> `AGENT_PASS` 는 웹 로그인 비밀번호와 같아야 합니다.  
> (`Octo-Web-Login.txt` 참고)

### 2) 웹

1. http://서버IP:8787 접속  
2. 설정 → 브라우저 엔진 = **`agent`**  
3. 토큰·프록시·키워드·자사 도메인 확인  
4. **시작**

에이전트 창에 로그가 흐르고, 웹 로그에도 같이 표시됩니다.

## 엔진 선택

| 값 | 의미 |
|----|------|
| **agent** | PC Octo로 실행 (진짜 Octo 프로필) — **권장** |
| playwright | 서버 Chromium+프록시 (Octo 핑거프린트 없음) |
| octo | 서버 안 Local Client (이 VPS CPU에선 불가) |

## 문제 해결

| 증상 | 조치 |
|------|------|
| 에이전트 오프라인 | `start_agent.bat` 실행, 웹 비번 `AGENT_PASS` 확인 |
| Local 연결 실패 (에이전트) | PC에서 Octo 앱 실행·로그인 |
| 401 Unauthorized | 웹 Basic Auth 아이디/비번 확인 |
