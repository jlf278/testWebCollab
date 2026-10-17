"""Exercise the actual browser adapter against an isolated local UI fixture.

These tests verify behavior, not compatibility with Disney's authenticated DOM.
"""
import tempfile
import unittest
from pathlib import Path

from playwright.sync_api import sync_playwright

from cabanas.browser import DisneyBrowser, retry_delay
from cabanas.core import Config, Stop, Target

SELECTORS = {
    "page_ready": "#ready", "page_date": "#date", "page_port": "#port",
    "reservation_list_ready": "#reservations-ready", "reserved_cabanas": ".reserved",
    "reserved_title": ".title", "offer_cards": ".offer", "offer_title": ".title",
    "offer_eligibility": ".eligibility", "select_button": "button",
    "party_ready": "#party", "party_total": "#total", "party_checkboxes": "#party input",
    "party_names": "#party .name", "continue_button": "#continue",
    "review_ready": "#review", "review_title": "#review-title", "review_date": "#review-date",
    "review_port": "#review-port", "review_guests": "#review .guest",
    "free_cancellation": "#cancellation", "confirm_button": "#confirm", "confirmation_id": "#confirmation",
}

HTML = """<!doctype html><html><body>
<div id="ready">Available</div><div id="date">2026-12-22</div><div id="port">GOC</div>
<div id="reservations-ready">Loaded</div>
<div class="offer"><span class="title">Family Cabana</span><span class="eligibility">All Ages</span>
<button onclick="document.querySelector('#party').hidden=false">Select</button></div>
<div class="offer"><span class="title">Serenity Bay Cabana</span><span class="eligibility">18+</span><button>Select</button></div>
<div id="party" hidden><span id="total">3</span>
<label><input type="checkbox"><span class="name">Guest A</span></label>
<label><input type="checkbox"><span class="name">Guest B</span></label>
<label><input type="checkbox"><span class="name">Guest C</span></label>
<button id="continue" onclick="document.querySelector('#review').hidden=false">Continue</button></div>
<div id="review" hidden><span id="review-title">Family Cabana</span>
<span id="review-date">2026-12-22</span><span id="review-port">GOC</span>
<span class="guest">Guest A</span><span class="guest">Guest B</span><span class="guest">Guest C</span>
<p id="cancellation">Free cancellation</p>
<button id="confirm" onclick="document.querySelector('#confirmation').hidden=false">Confirm</button></div>
<div id="confirmation" hidden>fixture-123</div></body></html>"""


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = sync_playwright().start()
        options = {"headless": True}
        if Path("/usr/bin/chromium").is_file():
            options["executable_path"] = "/usr/bin/chromium"
        cls.browser = cls.engine.chromium.launch(**options)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.engine.stop()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = Path(self.tmp.name) / "booking.html"
        self.fixture.write_text(HTML)
        self.config = Config([], 1800, SELECTORS, ["Family Cabana", "Serenity Bay Cabana"], Path("unused"), Path("unused"), "", "test")
        self.adapter = DisneyBrowser(self.config)
        self.context = self.browser.new_context()
        self.adapter.page = self.context.new_page()
        # Only bypass the network-origin guard for the local fixture. Individual
        # tests below exercise the real guard against controlled HTTP responses.
        self.adapter.guard = lambda: None
        self.target = Target("2026-12-22", "GOC", "https://disneycruise.disney.go.com/fixture")
        # All browser requests are fulfilled in-process. Tests never contact
        # Disney or depend on managed-browser file:// navigation permissions.
        self.adapter.page.route('https://disneycruise.disney.go.com/**',
                                lambda r: r.fulfill(body=self.fixture.read_text(), content_type='text/html'))

    def tearDown(self):
        self.context.close()
        self.tmp.cleanup()

    def set_html(self, html):
        self.fixture.write_text(html)

    def test_selects_entire_party_and_submits_once(self):
        snapshot = self.adapter.snapshot(self.target)
        self.assertEqual([o.title for o in snapshot.offers], ["Family Cabana"])
        self.adapter.prepare(self.target, snapshot.offers[0])
        for box in self.adapter.locator("party_checkboxes").all():
            self.assertTrue(box.is_checked())
        self.assertEqual(self.adapter.submit(), "fixture-123")

    def test_disabled_party_member_stops_booking(self):
        self.set_html(HTML.replace('<input type="checkbox">', '<input type="checkbox" disabled>', 1))
        offer = self.adapter.snapshot(self.target).offers[0]
        with self.assertRaisesRegex(Stop, "cannot be included"):
            self.adapter.prepare(self.target, offer)

    def test_incomplete_review_party_stops_booking(self):
        self.set_html(HTML.replace('<span class="guest">Guest C</span>', ''))
        offer = self.adapter.snapshot(self.target).offers[0]
        with self.assertRaisesRegex(Stop, "complete party"):
            self.adapter.prepare(self.target, offer)

    def test_wrong_review_date_stops_booking(self):
        self.set_html(HTML.replace('<span id="review-date">2026-12-22</span>', '<span id="review-date">2026-12-23</span>'))
        offer = self.adapter.snapshot(self.target).offers[0]
        with self.assertRaisesRegex(Stop, "date or port"):
            self.adapter.prepare(self.target, offer)

    def test_conditional_cancellation_needs_specific_validation(self):
        self.set_html(HTML.replace('Free cancellation', 'Free cancellation until December 19'))
        offer = self.adapter.snapshot(self.target).offers[0]
        with self.assertRaisesRegex(Stop, "conditions"):
            self.adapter.prepare(self.target, offer)

    def test_negated_cancellation_does_not_pass(self):
        self.set_html(HTML.replace('Free cancellation', 'Free cancellation is not available'))
        offer = self.adapter.snapshot(self.target).offers[0]
        with self.assertRaisesRegex(Stop, "does not establish"):
            self.adapter.prepare(self.target, offer)

    def test_unavailable_cabana_is_not_an_offer(self):
        self.set_html(HTML.replace('>Select</button>', '>Check Availability Onboard</button>'))
        self.assertEqual(self.adapter.snapshot(self.target).offers, [])

    def test_existing_reservation_is_detected(self):
        self.set_html(HTML.replace('<div id="ready">', '<div class="reserved"><span class="title">Family Cabana</span></div><div id="ready">'))
        self.assertTrue(self.adapter.snapshot(self.target).reserved)

    def test_unknown_eligibility_is_not_an_offer(self):
        self.set_html(HTML.replace('All Ages', 'Eligibility unavailable'))
        self.assertEqual(self.adapter.snapshot(self.target).offers, [])

    def test_guard_stops_security_challenge(self):
        self.adapter.page.route('https://disneycruise.disney.go.com/**', lambda r: r.fulfill(body='<body>Verify you are human</body>', content_type='text/html'))
        self.adapter.page.goto('https://disneycruise.disney.go.com/fixture')
        with self.assertRaisesRegex(Stop, "security challenge"):
            DisneyBrowser.guard(self.adapter)

    def test_missing_selectors_fail_before_navigation(self):
        self.adapter.config = Config([], 1800, {}, [], Path('unused'), Path('unused'), '', 'test')
        with self.assertRaisesRegex(Stop, "calibration"):
            self.adapter.snapshot(self.target)

    def test_retry_after_seconds(self):
        self.assertEqual(retry_delay('7200'), 7200)
        self.assertEqual(retry_delay('bad'), 0)
        self.assertEqual(retry_delay('-1'), 0)


if __name__ == '__main__':
    unittest.main()
