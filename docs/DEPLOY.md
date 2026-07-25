# 서버 자동 배포 (Git push → 서버 업데이트)

로컬에서 `git push` 하면 **GitHub Actions**가 서버에 SSH 접속해  
`git pull` + 패키지 설치 + 웹 서비스 재시작을 수행합니다.

```
[내 PC] commit/push → [GitHub lee9829/attack] → [Actions SSH] → [서버 deploy.sh]
```

시크릿 파일(`config.json`, `accounts.csv` 등)은 `.gitignore`에 있어 **서버 로컬에만** 둡니다.

---

## 1) 서버 최초 1회 설치 (Linux)

SSH로 서버 접속 후:

```bash
# 저장소가 아직 없으면
sudo git clone https://github.com/lee9829/attack.git /opt/octo-google-site-automation
cd /opt/octo-google-site-automation
sudo bash deploy/setup-server.sh
```

또는 이미 클론했다면:

```bash
cd /opt/octo-google-site-automation
sudo bash deploy/setup-server.sh
```

확인:

```bash
sudo systemctl status octo-web
curl -sI http://127.0.0.1:8787/ | head
```

브라우저: `http://서버IP:8787/`  
(방화벽에서 8787 포트 허용 필요)

### 서버에서 수동 배포

```bash
cd /opt/octo-google-site-automation
bash deploy/deploy.sh
```

---

## 2) Windows 서버인 경우

```powershell
cd C:\apps
git clone https://github.com/lee9829/attack.git Octo-Google-Site-Automation
cd Octo-Google-Site-Automation
python -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\deploy\deploy.ps1
```

이후 업데이트:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\deploy.ps1
```

---

## 3) GitHub Actions 자동 배포 연결

리포지토리 → **Settings → Secrets and variables → Actions → New repository secret**

| Secret 이름 | 내용 | 예시 |
|-------------|------|------|
| `DEPLOY_HOST` | 서버 IP 또는 도메인 | `203.0.113.10` |
| `DEPLOY_USER` | SSH 사용자 | `ubuntu` / `root` |
| `DEPLOY_SSH_KEY` | **프라이빗** SSH 키 전체 | `-----BEGIN ... KEY-----` |
| `DEPLOY_PATH` | 앱 경로 (선택) | `/opt/octo-google-site-automation` |
| `DEPLOY_PORT` | SSH 포트 (선택, 기본 22) | `22` |

### 서버에 배포용 SSH 키 등록 (예시)

**로컬 PC에서:**

```powershell
# 배포 전용 키 생성 (이메일 대신 식별자)
ssh-keygen -t ed25519 -C "github-deploy-attack" -f $env:USERPROFILE\.ssh\attack_deploy -N '""'
Get-Content $env:USERPROFILE\.ssh\attack_deploy.pub
```

**서버에서** `~/.ssh/authorized_keys` 에 위 **공개키(.pub)** 한 줄 추가.

**GitHub Secret `DEPLOY_SSH_KEY`** 에는 **개인키** 내용 전체를 붙여넣기:

```powershell
Get-Content $env:USERPROFILE\.ssh\attack_deploy -Raw
```

### 동작 확인

1. 코드 수정 후 `git push origin main`
2. GitHub → **Actions** 탭에서 **Deploy to server** 워크플로 확인
3. 서버에서 `git log -1` / `systemctl status octo-web` 확인

Secrets가 비어 있으면 워크플로는 **실행되지 않습니다** (로컬 개발만 할 때 안전).

---

## 4) 환경 변수

| 변수 | 기본 | 설명 |
|------|------|------|
| `OCTO_HOST` | `127.0.0.1` | 서버 공개 시 `0.0.0.0` |
| `OCTO_PORT` | `8787` | 웹 포트 |
| `OCTO_NO_BROWSER` | (off) | `1` 이면 브라우저 자동 실행 안 함 |

```bash
OCTO_HOST=0.0.0.0 OCTO_NO_BROWSER=1 python3 main.py --web
```

---

## 5) 보안 메모

- 이 웹 UI는 **API 토큰·계정 비밀번호**를 다룹니다. 공인 인터넷에 그대로 노출하지 마세요.
- 권장: VPN / SSH 터널 / Nginx + Basic Auth 또는 IP 허용 목록
- `config.json`, `accounts.csv`, `proxies.txt` 는 서버에만 두고 Git에 올리지 마세요
- Octo Browser **Local API**는 보통 서버가 아니라 **Octo가 설치된 Windows PC**에서 동작합니다.  
  원격 Linux VPS만 올리면 **웹 UI 설정 화면**은 띄울 수 있어도, 실제 브라우저 자동화는 Octo가 있는 머신에서 돌려야 합니다.

---

## 6) 일상 워크플로

```powershell
cd C:\Users\sta11\Desktop\Octo-Google-Site-Automation
# 코드 수정 후
git add -A
git status
git commit -m "기능 수정 설명"
git push
# → Actions가 서버에 반영
```
