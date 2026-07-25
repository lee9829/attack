# Octo Google Site Automation

Local **web dashboard** + automation engine for **own-site** Google SERP / CTA checks via **Octo Browser** (Cloud + Local API) and **Playwright CDP**.

> For authorized QA of **your own** domains only. Not for ad fraud, competitor traffic, or unauthorized automation.

| | |
|--|--|
| Version | **2.0.0** (web UI default) |
| OS | Windows 10/11 |
| Python | 3.10+ |
| UI | FastAPI @ `http://127.0.0.1:8787` |
| Legacy GUI | `python main.py --gui` (Tkinter) |

## Features

- Proxy rotation injected into Octo profiles
- Optional Google login (`auto` / `manual` / `skip`) + TOTP 2FA
- Search keywords → click **your** domain only (skip ads option)
- Human-like scroll / dwell / CTA text clicks / revisit
- Live logs (SSE), save/load config, DRY RUN, emergency stop

## Quick start

```bash
python -m pip install -r requirements.txt
python main.py
```

Or double-click `start_web.bat` / `웹시작.bat` on Windows.

Open **http://127.0.0.1:8787** → paste API token, accounts (`email|password|2fa_secret`), keywords, domain, proxies → **Start**.

## Modes

```bash
python main.py              # web UI (default)
python main.py --web        # same
python main.py --gui        # Tkinter
python main.py --cli        # console
python main.py --cli --dry-run
```

## Project layout

```
main.py                 # entry
src/                    # core (runner, automation, octo, proxy)
web/                    # FastAPI + static UI
config.example.json     # safe template (copy → config.json)
docs/AI_DEVELOPMENT.md  # how AIs built / extended this
docs/GITHUB_SETUP.md    # commit & push checklist
```

## GitHub

Secrets stay local (see `.gitignore`):

- Do **not** commit `config.json`, `accounts.csv`, `proxies.txt`, tokens, passwords

Setup guide: [docs/GITHUB_SETUP.md](docs/GITHUB_SETUP.md)  
AI / collaboration history: [docs/AI_DEVELOPMENT.md](docs/AI_DEVELOPMENT.md)  
Korean user guide: [README-KO.md](README-KO.md)

## License / responsibility

Use only on sites and accounts you own or are authorized to test. Comply with Google, Octo, and local laws.
