# -*- coding: utf-8 -*-
"""Generate agent/embedded_config.py with server URL + agent token for EXE bake-in."""
from __future__ import annotations

import json
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENT_DIR = Path(__file__).resolve().parent
OUT = AGENT_DIR / "embedded_config.py"
WEB_AUTH = ROOT / "web_auth.env"
CONFIG = ROOT / "config.json"

DEFAULT_SERVER = "http://66.29.149.197:8787"


def _read_env(path: Path) -> dict:
    data = {}
    if not path.is_file():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip().strip('"').strip("'")
    return data


def main() -> int:
    env = _read_env(WEB_AUTH)
    token = (env.get("OCTO_AGENT_TOKEN") or "").strip()
    server = (env.get("AGENT_SERVER") or DEFAULT_SERVER).strip()

    if CONFIG.is_file():
        try:
            cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
            token = token or str(cfg.get("agent_token") or "").strip()
            server = str(cfg.get("agent_server") or server).strip() or server
        except Exception:
            pass

    if not token:
        token = secrets.token_urlsafe(32)
        # persist for server
        lines = []
        if WEB_AUTH.is_file():
            lines = WEB_AUTH.read_text(encoding="utf-8").splitlines()
        # remove old token lines
        lines = [ln for ln in lines if not ln.strip().startswith("OCTO_AGENT_TOKEN=")]
        lines.append(f"OCTO_AGENT_TOKEN={token}")
        WEB_AUTH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        print(f"[write] saved OCTO_AGENT_TOKEN to {WEB_AUTH}")

        if CONFIG.is_file():
            try:
                cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
                cfg["agent_token"] = token
                cfg["browser_engine"] = cfg.get("browser_engine") or "agent"
                CONFIG.write_text(
                    json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(f"[write] saved agent_token to config.json")
            except Exception as exc:
                print(f"[warn] config.json update: {exc}")

    content = (
        "# AUTO-GENERATED for OctoAgent.exe — do not edit by hand\n"
        f'AGENT_SERVER = "{server}"\n'
        f'AGENT_TOKEN = "{token}"\n'
        f'AGENT_NAME = "windows-pc"\n'
    )
    OUT.write_text(content, encoding="utf-8")
    print(f"[write] {OUT}")
    print(f"[info] server={server}")
    print(f"[info] token_len={len(token)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
