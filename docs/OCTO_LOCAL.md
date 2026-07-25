# Cloud OK · Local OFF 설명

## 증상

```
Cloud OK · Local OFF: 로컬 API 연결 실패 (http://127.0.0.1:58888/api/...)
```

Octo Browser를 켰는데도 Local이 OFF로 나옵니다.

## 원인

| 구성 요소 | 어디서 도나 |
|-----------|-------------|
| 웹 UI (지금 VPS) | `66.29.149.197` Ubuntu 서버 |
| Octo Browser 데스크톱 | **내 Windows PC** |
| Local API 주소 | `127.0.0.1:58888` = **그 프로그램이 돌아가는 컴퓨터 자신** |

웹이 **서버**에서 돌면 `127.0.0.1:58888` 은 **서버 안**을 가리킵니다.  
PC에 Octo를 켜 둬도 서버는 PC 포트를 보지 못합니다.

Cloud API는 인터넷으로 Octo 클라우드에 붙기 때문에 **Cloud OK** 가 됩니다.  
프로필 **시작·브라우저 제어(CDP)** 는 Local API가 필요합니다.

```
[브라우저] → VPS 웹 UI → 127.0.0.1:58888  (서버 자기 자신, Octo 없음)  ❌
[브라우저] → Windows 웹 UI → 127.0.0.1:58888 (같은 PC의 Octo)          ✅
```

## 해결 (권장): Windows에서 웹 실행

1. Octo Browser 실행 후 로그인  
2. 이 프로젝트 폴더에서:

```bat
웹시작.bat
```

또는:

```powershell
cd C:\Users\sta11\Desktop\Octo-Google-Site-Automation
python main.py --web
```

3. 브라우저에서 **http://127.0.0.1:8787/** 접속  
4. 토큰 입력 후 **연결 테스트** → Cloud OK · Local OK 확인  
5. 그다음 작업 시작  

VPS(`http://66.29.149.197:8787/`)는 설정 확인·백업용으로 두고,  
**실제 자동화 실행은 반드시 Octo PC**에서 하세요.

## VPS만으로 Local을 살릴 수 있나?

터널로 Local API 포트만 넘기면 “연결 테스트”는 통과할 수 있어도,  
프로필 시작 후 Playwright가 붙는 CDP(`ws_endpoint` / debug_port)도  
PC 쪽 `127.0.0.1` 이라 **서버에서 브라우저 제어가 깨집니다.**

→ 원격 서버 + 로컬 Octo 조합으로는 **전체 자동화가 설계상 맞지 않습니다.**  
→ **웹 UI와 Octo를 같은 Windows PC**에 두는 것이 정답입니다.
