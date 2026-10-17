"""Bootstrap an interactive login on the user's trusted desktop.

Run with Python 3.11+: python3 scripts/local_login.py (macOS/Linux) or
py -3 scripts/local_login.py (Windows). No account data is uploaded.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def initialize_config(root: Path, booking_id: str) -> Path:
    if not re.fullmatch(r"[0-9]{1,32}", booking_id):
        raise ValueError("Use the numeric Disney booking number, not your account password")
    destination = root / ".private" / "config.json"
    destination.parent.mkdir(mode=0o700, exist_ok=True)
    # Exclusive creation preserves previously calibrated selectors and settings.
    if destination.exists():
        return destination
    raw = json.loads((root / "config.example.json").read_text(encoding="utf-8"))
    raw["browser"] = ""  # Use the Playwright-managed browser on this computer.
    for target in raw["targets"]:
        target["url"] = target["url"].replace("YOUR_BOOKING_ID", booking_id)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2)
        f.write("\n")
    return destination


def run(command: list[str], root: Path):
    subprocess.run(command, cwd=root, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Install a local browser and open Disney's interactive login. No bookings are made.")
    parser.parse_args()
    if sys.version_info < (3, 11):
        print("Python 3.11 or newer is required.", file=sys.stderr)
        return 2
    if sys.platform not in ("darwin", "win32") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        print("Run this helper on your computer with a desktop display, not inside the cloud task.", file=sys.stderr)
        return 2
    os.umask(0o077)
    root = Path(__file__).resolve().parents[1]
    try:
        config = root / ".private" / "config.json"
        if not config.exists():
            booking_id = getpass.getpass("Disney booking number (stored only on this computer): ").strip()
            initialize_config(root, booking_id)
        venv = root / ".local-venv"
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run([sys.executable, "-m", "venv", str(venv)], root)
        run([str(python), "-m", "pip", "install", "--no-input", "--cache-dir", str(venv / "pip-cache"), "-r", str(root / "requirements.txt")], root)
        run([str(python), "-m", "playwright", "install", "chromium"], root)
        run([str(python), "-m", "cabanas.cli", "validate"], root)
        print("Log in directly in the browser. Complete any MFA yourself. Leave the December 22 port-adventures page open, then press Enter in its terminal prompt.", flush=True)
        run([str(python), "-m", "cabanas.cli", "login"], root)
        print("Login step finished. The session stays in .private/session.json on this computer.")
        print("Keep .private private. Monitoring and automatic booking remain inactive until the real booking screens are calibrated.")
        return 0
    except (EOFError, KeyboardInterrupt):
        print("Login canceled. No monitor was started.", file=sys.stderr)
        return 130
    except (OSError, ValueError, subprocess.CalledProcessError):
        print("Local setup or login stopped. Review the message above; do not share credentials or session files.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
