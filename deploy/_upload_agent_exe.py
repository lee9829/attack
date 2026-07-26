# -*- coding: utf-8 -*-
"""Upload OctoAgent.exe to VPS downloads/ for website download button."""
from __future__ import annotations

from pathlib import Path

import paramiko

HOST = "66.29.149.197"
KEY = Path.home() / ".ssh" / "attack_deploy"
APP = "/opt/octo-google-site-automation"
LOCAL_CANDIDATES = [
    Path(__file__).resolve().parent.parent / "downloads" / "OctoAgent.exe",
    Path(__file__).resolve().parent.parent / "dist" / "OctoAgent.exe",
    Path(__file__).resolve().parent.parent / "agent" / "OctoAgent.exe",
    Path.home() / "Desktop" / "OctoAgent.exe",
]


def main() -> int:
    local = next((p for p in LOCAL_CANDIDATES if p.is_file()), None)
    if not local:
        print("OctoAgent.exe not found locally")
        return 2
    size = local.stat().st_size
    print(f"local={local} size_mb={size/1024/1024:.1f}")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        HOST,
        username="root",
        key_filename=str(KEY),
        timeout=30,
        allow_agent=False,
        look_for_keys=False,
        banner_timeout=60,
    )
    print("SSH OK")
    # ensure dir + pull code first is caller's job; here only upload binary
    _, stdout, _ = client.exec_command(
        f"mkdir -p {APP}/downloads; chown -R octo:octo {APP}/downloads 2>/dev/null || true",
        timeout=30,
    )
    stdout.channel.recv_exit_status()

    remote = f"{APP}/downloads/OctoAgent.exe"
    sftp = client.open_sftp()
    print(f"uploading → {remote} …")
    sftp.put(str(local), remote)
    sftp.chmod(remote, 0o644)
    sftp.close()

    cmd = (
        f"ls -lh {remote}; "
        f"chown octo:octo {remote} 2>/dev/null || true; "
        f"systemctl restart octo-web 2>/dev/null || true; "
        f"sleep 2; systemctl is-active octo-web 2>/dev/null || true"
    )
    _, stdout, _ = client.exec_command(cmd, timeout=60, get_pty=True)
    print(stdout.read().decode("utf-8", "replace")[-1500:])
    client.close()
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
