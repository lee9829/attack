# GitHub에 올리기 (커밋·푸시 가이드)

이 저장소는 **로컬 시크릿 파일은 커밋하지 않도록** `.gitignore`가 설정되어 있습니다.

## 1. 최초 1회

```powershell
cd C:\Users\sta11\Desktop\Octo-Google-Site-Automation

git init
git add .
git status
# 아래가 보이면 안 됨: config.json, accounts.csv, proxies.txt, domains.txt, keywords.txt, logs/
```

```powershell
git commit -m "Initial commit: Octo Google Site Automation web UI (v2)"
```

GitHub에서 빈 저장소 생성 후:

```powershell
git branch -M main
git remote add origin https://github.com/<USER>/<REPO>.git
git push -u origin main
```

## 2. 이후 변경 푸시

```powershell
git add -A
git status
git commit -m "설명 메시지"
git push
```

## 3. 클론한 사람이 할 일

```powershell
git clone https://github.com/<USER>/<REPO>.git
cd <REPO>
python -m pip install -r requirements.txt
python main.py
```

브라우저가 `http://127.0.0.1:8787` 을 엽니다.  
`config.example.json` 등이 첫 실행 시 `config.json` 등으로 복사됩니다.

## 4. 절대 올리지 말 것

- Octo API 토큰
- Google 계정·비밀번호·2FA 시크릿
- 프록시 목록
- 실제 자사 도메인/검색어 운영 목록 (민감하면 example만)

실수로 커밋했다면 **토큰·비밀번호는 즉시 폐기·재발급**하고, 히스토리에서 제거(`git filter-repo` 등)하세요.
