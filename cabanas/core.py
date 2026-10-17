from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

if os.name == "nt":
    import msvcrt
else:
    import fcntl

HOST = "disneycruise.disney.go.com"
MIN_INTERVAL = 1800
ADULTS_ONLY = re.compile(r"18\s*(?:\+|(?:years?\s*)?(?:and\s*)?(?:over|older|up)|years?\b)|adults?\s*[- ]?(?:only|exclusive)|serenity\s+bay", re.I)


class Stop(RuntimeError):
    """A condition requiring diagnosis, authentication or reconciliation."""


class Retry(RuntimeError):
    def __init__(self, delay: float = 0):
        self.delay = delay
        super().__init__("Temporary request failure")


@dataclass(frozen=True)
class Target:
    day: str
    port: str
    url: str

    def open(self, now: float) -> bool:
        return datetime.fromtimestamp(now, ZoneInfo("America/New_York")).date() < date.fromisoformat(self.day)


@dataclass
class Config:
    targets: list[Target]
    interval: int
    selectors: dict[str, str]
    allowed_titles: list[str]
    session: Path
    state: Path
    browser: str
    scope: str

    @classmethod
    def load(cls, path: Path) -> Config:
        raw = json.loads(path.read_text())
        interval = raw.get("interval_seconds", MIN_INTERVAL)
        if not isinstance(interval, int) or isinstance(interval, bool) or interval < MIN_INTERVAL:
            raise ValueError("Check interval must be at least 1800 seconds")
        targets = [Target(**t) for t in raw["targets"]]
        expected = {("2026-12-22", "GOC"), ("2026-12-23", "LPT"), ("2026-12-26", "GOC")}
        if len(targets) != 3 or {(t.day, t.port) for t in targets} != expected:
            raise ValueError("Expected the three agreed December 2026 port dates")
        sailing_ids = set()
        for t in targets:
            u = urlsplit(t.url)
            m = re.fullmatch(r"/my-disney-cruise/(\d+)/PORTADVENTURES/(\d{4}-\d{2}-\d{2})/", u.path)
            q = parse_qs(u.query)
            if u.scheme != "https" or u.hostname != HOST or u.port or u.username or u.password or u.fragment or not m:
                raise ValueError("Use a Disney Cruise Line port-adventures URL")
            if m[2] != t.day or q.get("ship") != ["DD"] or q.get("port") != [t.port]:
                raise ValueError("URL does not match the target date, ship and port")
            sailing_ids.add(m[1])
        if len(sailing_ids) != 1:
            raise ValueError("All dates must belong to the same sailing")
        titles = raw.get("allowed_titles", [])
        if not isinstance(titles, list) or any(not isinstance(t, str) or not eligible(t) for t in titles):
            raise ValueError("Allowed titles must identify non-adult cabanas")
        selectors = raw.get("selectors", {})
        if not isinstance(selectors, dict) or any(not isinstance(v, str) or not v.strip() for v in selectors.values()):
            raise ValueError("Selectors must be nonempty strings")
        scope = hashlib.sha256("|".join(sorted(t.url for t in targets)).encode()).hexdigest()
        def local(name: str) -> Path:
            return (path.parent / raw.get(name, name + (".json" if name == "session" else ".sqlite3"))).resolve()
        browser = raw.get("browser", "")
        if not isinstance(browser, str):
            raise ValueError("Browser must be an executable path or an empty string for Playwright Chromium")
        return cls(targets, interval, selectors, titles, local("session"), local("state"), browser, scope)


def eligible(title: str) -> bool:
    return bool(re.search(r"\bcabana\b", title, re.I)) and not ADULTS_ONLY.search(title)


@dataclass(frozen=True)
class Offer:
    title: str
    index: int


@dataclass(frozen=True)
class Snapshot:
    reserved: bool
    offers: list[Offer]


class Adapter(Protocol):
    def snapshot(self, target: Target) -> Snapshot: ...
    def prepare(self, target: Target, offer: Offer) -> None: ...
    def submit(self) -> str: ...


class State:
    def __init__(self, path: Path, scope: str):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.scope = scope
        self.db.execute("CREATE TABLE IF NOT EXISTS state (scope TEXT, key TEXT, value TEXT, PRIMARY KEY(scope,key))")

    def get(self, key: str, default: str = "") -> str:
        row = self.db.execute("SELECT value FROM state WHERE scope=? AND key=?", (self.scope, key)).fetchone()
        return row[0] if row else default

    def put(self, key: str, value: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO state VALUES (?,?,?)", (self.scope, key, str(value)))

    def close(self) -> None:
        self.db.close()


@contextlib.contextmanager
def locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+b") as f:
        os.chmod(path, 0o600)
        try:
            if os.name == "nt":
                f.seek(0, 2)
                if not f.tell():
                    f.write(b"\0")
                    f.flush()
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Stop("Another monitor or browser command is running") from None
        try:
            yield
        finally:
            if os.name == "nt":
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


class Monitor:
    def __init__(self, config: Config, state: State, adapter: Adapter, clock=time.time):
        self.config, self.state, self.adapter, self.clock = config, state, adapter, clock

    def check(self, book: bool = False) -> list[str]:
        now = self.clock()
        if self.state.get("paused"):
            raise Stop(self.state.get("paused"))
        if now < float(self.state.get("next_check", "0")):
            return ["Waiting for the persisted check interval"]
        # Persist before making any request; restarting cannot accelerate polling.
        self.state.put("next_check", str(now + self.config.interval))
        messages = []
        try:
            for target in self.config.targets:
                if not target.open(self.clock()):
                    messages.append(f"{target.day}: online checking ended")
                    continue
                status = self.state.get(target.day)
                snapshot = self.adapter.snapshot(target)
                if snapshot.reserved:
                    self.state.put(target.day, "covered")
                    messages.append(f"{target.day}: existing cabana reservation; skipped")
                    continue
                if status == "pending":
                    raise Stop(f"{target.day}: previous submission outcome is uncertain; reconcile in Disney before resuming")
                if status.startswith("confirmed:") or status == "covered":
                    # The live itinerary is authoritative if a reservation was
                    # canceled outside this process.
                    self.state.put(target.day, "")
                offers = [o for o in snapshot.offers if eligible(o.title) and o.title in self.config.allowed_titles]
                if not offers:
                    messages.append(f"{target.day}: no eligible cabana available")
                    continue
                if not book:
                    messages.append(f"{target.day}: eligible cabana available (observation mode)")
                    continue
                offer = offers[0]
                self.adapter.prepare(target, offer)
                # Never retry a final submission after a timeout or crash.
                self.state.put(target.day, "pending")
                confirmation = self.adapter.submit()
                if not confirmation:
                    raise Stop("No reservation confirmation; manual reconciliation required")
                self.state.put(target.day, "confirmed:" + confirmation)
                messages.append(f"{target.day}: cabana reserved for the entire party")
            self.state.put("failures", "0")
        except Retry as e:
            failures = int(self.state.get("failures", "0")) + 1
            self.state.put("failures", str(failures))
            delay = max(e.delay, min(self.config.interval * 2 ** failures, 21600))
            self.state.put("next_check", str(self.clock() + delay))
            if failures >= 5:
                self.state.put("paused", "Five consecutive temporary failures; inspect connectivity before resuming")
            messages.append(f"Temporary failure; next check no earlier than {int(delay)} seconds")
        except Exception as e:
            # Raw browser exceptions can contain URLs or personal data; do not persist them.
            reason = str(e) if isinstance(e, Stop) else "Unexpected browser failure; inspect the site before resuming"
            self.state.put("paused", reason)
            raise Stop(reason) from None
        return messages
