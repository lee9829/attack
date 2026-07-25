# OPS Console (자사 전용)

공격형 **QA / 방어 점검** 모드입니다.  
**허용 도메인(자사) 밖 URL은 하드 차단**됩니다.

## 모드

| 모드 | 동작 |
|------|------|
| **SWARM** | 멀티 Octo 프로필 동시 검색·클릭 + 내부 링크 압박 |
| **HAMMER** | HTTP 병렬 부하 + 강한 브라우저 프리셋 |
| **FULL** | recon + hammer + browser |
| **RECON** | 헤더/쿠키/민감경로/폼 표면만 (HTTP) |
| **STEALTH** | 낮은 소음 프로브 |
| **BROWSER** | 기존 브라우저 자동화 위주 |

## 안전 장치

- `allowed_domains` / `target_domain` 만 대상
- 타 도메인 `assert_url_allowed` → `PermissionError`
- 자격 증명 스터핑·타사 공격 페이로드 없음
- 폼은 포커스/더미 입력만 (기본 submit 안 함)

## 리포트

`logs/ops_reports/ops_YYYYMMDD_HHMMSS.json`  
`logs/ops_reports/latest.json`

## API

- `POST /api/ops/run` — HTTP suite
- `GET /api/ops/latest` — 최근 리포트
