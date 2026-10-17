from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import Error as BrowserError

from .browser import DisneyBrowser, login
from .core import Config, Monitor, State, Stop, locked


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Disney Dream cabana monitor")
    parser.add_argument("--config", type=Path, default=Path(".private/config.json"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="Validate private trip configuration")
    commands.add_parser("login", help="Human login on a trusted desktop")
    commands.add_parser("inspect", help="Save private authenticated DOM without booking")
    commands.add_parser("status", help="Show persisted scheduling and reservation state")
    commands.add_parser("resume", help="Clear a pause after diagnosing it; never clears uncertain submissions")
    for name in ("check", "run"):
        p = commands.add_parser(name)
        p.add_argument("--book", action="store_true", help="Automatically submit reservations within configured rules")
    args = parser.parse_args()
    try:
        config = Config.load(args.config.resolve())
        if args.command == "validate":
            print("Trip configuration valid. Live authentication and selector calibration are separate checks.")
            return 0
        with locked(config.state.with_suffix(".lock")):
            if args.command == "login":
                login(config)
                print("Private session saved. Treat it like a password; never commit or paste it in chat.")
                return 0
            state = State(config.state, config.scope)
            try:
                if args.command == "status":
                    print(json.dumps({"paused": state.get("paused"), "next_check_epoch": state.get("next_check"),
                                      "dates": {t.day: state.get(t.day, "not_checked") for t in config.targets}}, indent=2))
                    return 0
                if args.command == "resume":
                    state.put("paused", "")
                    print("Pause cleared; persisted timing and uncertain submissions remain enforced.")
                    return 0
                if args.command == "inspect":
                    with DisneyBrowser(config) as browser:
                        browser.page.goto(config.targets[0].url, wait_until="domcontentloaded")
                        browser.guard()
                        out = args.config.resolve().parent / "inspection.html"
                        out.write_text(browser.page.content())
                        os.chmod(out, 0o600)
                        print("Saved private inspection.html beside the config. It may contain personal information.")
                    return 0
                while True:
                    if state.get("paused"):
                        raise Stop(state.get("paused"))
                    now = time.time()
                    active = [t for t in config.targets if t.open(now)]
                    if not active:
                        print("All target online-checking windows have ended.")
                        return 0
                    if now >= float(state.get("next_check", "0")):
                        # Fresh browser per cycle; only one cycle can run at once.
                        with DisneyBrowser(config) as browser:
                            for message in Monitor(config, state, browser).check(args.book):
                                print(message, flush=True)
                    elif args.command == "check":
                        print("Waiting for the persisted check interval")
                    if args.command == "check":
                        return 0
                    time.sleep(min(30, max(1, float(state.get("next_check", "0")) - time.time())))
            finally:
                state.close()
    except BrowserError:
        print("Stopped: browser navigation failed. Verify certificate trust, connectivity and session; TLS verification remains enabled.", file=sys.stderr)
        return 2
    except (Stop, ValueError, KeyError, OSError) as e:
        # Browser errors are converted to sanitized Stop messages by the adapter.
        print(f"Stopped: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except EOFError:
        print("Stopped: interactive input ended before login finished.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
