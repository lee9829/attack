# Octo API 구조 (Cloud + Local Client)

Octo 공식 문서 기준, 자동화는 **두 API**를 같이 씁니다.

| API | 주소 | 하는 일 |
|-----|------|---------|
| **Cloud API** | `https://app.octobrowser.net/api/v2/automation` | 프로필 생성/수정/삭제, 프록시, 태그, force_stop (토큰) |
| **Local Client API** | `http://127.0.0.1:58888/api` | **프로필 Start/Stop**, CDP `ws_endpoint` 반환 |

Cloud API만으로는 브라우저를 띄울 수 없습니다.  
`POST /profiles/start` 는 **Local Client** 전용입니다  
(문서: Local Client listens on `127.0.0.1:58888` only).

## VPS에서 “웹 API처럼” 쓰는 방법 (권장)

웹 UI와 **같은 서버**에 Octo Linux 클라이언트(headless)를 올립니다.

```
[브라우저] → VPS 웹 UI
               ├─ Cloud API (토큰) → 프로필 관리
               └─ Local API 127.0.0.1:58888 → 같은 서버의 Octo Client → Playwright CDP
```

### 1) 서버에 Octo Client 설치

```bash
cd /opt/octo-google-site-automation
sudo bash deploy/setup-octo-client.sh
sudo systemctl status octo-client
curl -s http://127.0.0.1:58888/api/username
```

### 2) 웹 UI에 Octo 계정 입력

설정 탭:

- **API 토큰** (Cloud)
- **Octo 로그인 이메일 / 비밀번호** (Local Client 자동 로그인)
- Local API: `http://127.0.0.1:58888/api` (서버 기본값 유지)

저장 → 연결 테스트 → `Cloud OK · Local OK`

### 3) 실행

VPS에서는 **headless ON** 권장.

---

## Windows PC에서만 돌릴 때

Octo 데스크톱 앱 실행 + 로그인 후:

```bat
웹시작.bat
```

`http://127.0.0.1:8787` 에서 연결 테스트.

---

## 자주 묻는 질문

**Q. Cloud API로 start 하면 안 되나요?**  
A. Octo가 Local Client 전용으로만 start를 제공합니다. Cloud는 관리 API입니다.

**Q. 내 PC Octo + VPS 웹?**  
A. 서버의 `127.0.0.1` ≠ PC. Local OFF가 정상입니다.  
→ 서버에 클라이언트 설치 또는 웹을 PC에서 실행.
