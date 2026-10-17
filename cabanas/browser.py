from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from .core import Config, HOST, Offer, Retry, Snapshot, Stop, Target, eligible

# These are a contract to calibrate against an authenticated page. No guessed
# production selectors are shipped, especially for irreversible actions.
REQUIRED = {
    "page_ready", "page_date", "page_port", "reservation_list_ready",
    "reserved_cabanas", "reserved_title", "offer_cards", "offer_title",
    "offer_eligibility", "select_button", "party_ready", "party_total",
    "party_checkboxes", "party_names", "continue_button", "review_ready",
    "review_title", "review_date", "review_port", "review_guests",
    "free_cancellation", "confirm_button", "confirmation_id",
}


def launch_options(config: Config, *, headless: bool) -> dict:
    options = {"headless": headless}
    if config.browser:
        options["executable_path"] = config.browser
    return options


def desktop_available() -> bool:
    return sys.platform in ("darwin", "win32") or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def save_session(context, path: Path):
    """Replace session state atomically; a failed save keeps the last session."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    if hasattr(os, "fchmod"):
        os.fchmod(fd, 0o600)
    else:
        os.chmod(temporary, 0o600)
    os.close(fd)
    try:
        context.storage_state(path=str(temporary))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def retry_delay(value: str | None) -> float:
    if not value:
        return 0
    try:
        return max(0, float(value))
    except ValueError:
        try:
            return max(0, parsedate_to_datetime(value).timestamp() - datetime.now(timezone.utc).timestamp())
        except (ValueError, TypeError, OverflowError):
            return 0


class DisneyBrowser:
    def __init__(self, config: Config):
        self.config = config
        self.engine = None
        self.browser = None
        self.context = None
        self.page = None
        self.network_delay = 0.0
        self.network_denied = False
        self.authenticated = False

    def __enter__(self):
        if not self.config.session.is_file():
            raise Stop("No authenticated browser session. Run login on a trusted machine first")
        os.chmod(self.config.session, 0o600)
        self.engine = sync_playwright().start()
        try:
            self.browser = self.engine.chromium.launch(**launch_options(self.config, headless=True))
            self.context = self.browser.new_context(storage_state=str(self.config.session))
            self.context.set_default_timeout(20000)
            self.page = self.context.new_page()
            self.page.on("response", self._response)
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise Stop("Browser startup failed; verify the configured Chromium executable") from None

    def __exit__(self, exc_type, *_):
        try:
            if self.context and exc_type is None and self.authenticated and not self.network_denied:
                # Retain legitimate session refreshes. Never commit this state or
                # persist a challenged session over the last successful session.
                save_session(self.context, self.config.session)
        finally:
            try:
                if self.context:
                    self.context.close()
            finally:
                try:
                    if self.browser:
                        self.browser.close()
                finally:
                    if self.engine:
                        self.engine.stop()

    def _response(self, response):
        # Include first-party XHR responses, not merely the main HTML document.
        host = urlsplit(response.url).hostname or ""
        if not (host == HOST or host.endswith(".disney.go.com")):
            return
        if response.status in (401, 403):
            self.network_denied = True
        elif response.status == 429:
            self.network_delay = max(self.network_delay, retry_delay(response.headers.get("retry-after")), 1800)
        elif response.status >= 500:
            self.network_delay = max(self.network_delay, 1800)

    def guard(self):
        if self.network_denied:
            raise Stop("Disney denied access or the session expired; authenticate or inspect access before resuming")
        if self.network_delay:
            raise Retry(self.network_delay)
        if urlsplit(self.page.url).hostname != HOST:
            raise Stop("The browser left the booking site; authentication or site inspection is required")
        text = self.page.locator("body").inner_text()
        if re.search(r"verify (?:that )?you(?:'re| are) (?:a )?human|unusual (?:traffic|activity)|access denied|captcha|security challenge", text, re.I):
            raise Stop("Disney displayed a security challenge; automatic checks paused")
        if re.search(r"/(?:login|sign-?in)(?:[/?]|$)", self.page.url, re.I):
            raise Stop("Disney login session expired")

    def locator(self, key, root=None):
        selector = self.config.selectors.get(key)
        if not selector:
            raise Stop(f"Missing calibrated selector: {key}")
        return (root if root is not None else self.page).locator(selector)

    def one(self, key, root=None):
        loc = self.locator(key, root)
        if loc.count() != 1 or not loc.is_visible():
            raise Stop(f"Site structure did not match the calibrated selector: {key}")
        return loc

    def text(self, key, root=None):
        return self.one(key, root).inner_text().strip()

    def require_ready(self, key):
        self.locator(key).wait_for(state="visible")
        self.guard()
        self.one(key)

    def check_identity(self, target, review=False):
        prefix = "review" if review else "page"
        # These selectors must point to explicit date/port identity text, not
        # hidden content or a guessed position in the itinerary.
        node = self.one(prefix + "_date")
        displayed_date = node.get_attribute("datetime") or node.inner_text().strip()
        parsed_date = None
        for fmt in ("%Y-%m-%d", "%A, %B %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%a, %b %d, %Y"):
            try:
                parsed_date = datetime.strptime(displayed_date, fmt).date().isoformat()
                break
            except ValueError:
                pass
        port_names = {
            "GOC": {"GOC", "Castaway Cay", "Disney Castaway Cay"},
            "LPT": {"LPT", "Lookout Cay", "Disney Lookout Cay at Lighthouse Point"},
        }
        if parsed_date != target.day or self.text(prefix + "_port") not in port_names[target.port]:
            raise Stop("Displayed reservation date or port does not match the requested target")

    def snapshot(self, target: Target) -> Snapshot:
        missing = REQUIRED - self.config.selectors.keys()
        if missing:
            raise Stop("Authenticated site selectors need calibration before checking or booking")
        if not self.config.allowed_titles:
            raise Stop("Non-adult cabana titles must be verified against the authenticated site first")
        response = self.page.goto(target.url, wait_until="domcontentloaded")
        self.guard()
        if not response or response.status >= 400:
            raise Retry()
        self.require_ready("page_ready")
        self.check_identity(target)
        # A populated availability page alone cannot prove there are no existing
        # reservations. This marker must certify the reservation list is loaded.
        self.require_ready("reservation_list_ready")
        self.authenticated = True
        reserved = self.locator("reserved_cabanas")
        for card in reserved.all():
            if re.search(r"\bcabana\b", self.text("reserved_title", card), re.I):
                return Snapshot(True, [])
        offers = []
        for index, card in enumerate(self.locator("offer_cards").all()):
            title = self.text("offer_title", card)
            if not eligible(title) or title not in self.config.allowed_titles:
                continue
            eligibility = self.text("offer_eligibility", card)
            from .core import ADULTS_ONLY
            if ADULTS_ONLY.search(eligibility):
                continue
            if not re.search(r"all ages|family|no (?:minimum age|age restriction)", eligibility, re.I):
                continue
            button = self.locator("select_button", card)
            if button.count() == 1 and button.is_visible() and button.is_enabled() and button.inner_text().strip() == "Select":
                offers.append(Offer(title, index))
        return Snapshot(False, offers)

    def prepare(self, target: Target, offer: Offer):
        # Refresh both availability and existing reservations immediately before
        # beginning a reservation; never select from a stale snapshot.
        current = self.snapshot(target)
        if current.reserved:
            raise Stop("A cabana is now reserved for this date; rerun reconciliation")
        matches = [o for o in current.offers if o.title == offer.title]
        if not matches:
            raise Stop("Selected cabana is no longer available; inspect availability")
        self.guard()
        card = self.locator("offer_cards").nth(matches[0].index)
        self.one("select_button", card).click()
        self.require_ready("party_ready")
        total = self.text("party_total")
        if not re.fullmatch(r"[1-9]\d*", total):
            raise Stop("Could not verify the complete party size")
        boxes = self.locator("party_checkboxes")
        names = [s.strip() for s in self.locator("party_names").all_inner_texts()]
        if boxes.count() != int(total) or len(names) != int(total) or any(not n for n in names):
            raise Stop("The complete party roster is not loaded")
        for box in boxes.all():
            if not box.is_visible() or not box.is_enabled():
                raise Stop("A party member cannot be included; booking stopped")
            box.check()
            if not box.is_checked():
                raise Stop("Could not select every party member")
        self.guard()
        self.one("continue_button").click()
        self.require_ready("review_ready")
        self.check_identity(target, review=True)
        title = self.text("review_title")
        if title != offer.title or not eligible(title):
            raise Stop("Review shows an unexpected cabana")
        guests = [s.strip() for s in self.locator("review_guests").all_inner_texts()]
        if sorted(guests) != sorted(names):
            raise Stop("Review does not include the complete party")
        cancellation = self.text("free_cancellation")
        # A calibrated marker must contain the applicable policy, not an FAQ or
        # unrelated promotional message. Conditional or expired policies need a
        # site-specific implementation rather than an automatic assumption.
        if re.search(r"until|before|within|except|unless|non.?refundable|fee applies|penalty applies", cancellation, re.I):
            raise Stop("Cancellation has conditions needing site-specific validation")
        if not re.fullmatch(r"(?:free cancellation|cancel(?:lation)? (?:at no charge|without (?:a )?(?:fee|penalty)))\.?", cancellation, re.I):
            raise Stop("Review does not establish free cancellation")
        confirm = self.one("confirm_button")
        if not confirm.is_enabled():
            raise Stop("Final confirmation is unavailable")

    def submit(self) -> str:
        self.guard()
        self.one("confirm_button").click()
        self.require_ready("confirmation_id")
        confirmation = self.text("confirmation_id")
        if not confirmation:
            raise Stop("No confirmation received; reconcile this date before any retry")
        return confirmation


def login(config: Config):
    """Human login only. Never automate passwords, MFA or security challenges."""
    if not desktop_available():
        raise Stop("Interactive login needs a trusted desktop with a display; this cloud task has no login UI")
    config.session.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_options(config, headless=False))
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(config.targets[0].url)
            input("Complete Disney login and navigate to the December 22 port-adventures page, then press Enter here: ")
            expected = urlsplit(config.targets[0].url)
            actual = urlsplit(page.url)
            if actual.scheme != "https" or actual.hostname != HOST or actual.path != expected.path:
                raise Stop("The browser is not on the configured port-adventures page")
            save_session(context, config.session)
        finally:
            browser.close()
