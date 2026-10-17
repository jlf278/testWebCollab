# Disney Dream cabana monitor

This repository replaces the previous PHP project. It targets the December 21–27, 2026 Disney Dream sailing:

| Date | Destination |
| --- | --- |
| December 22 | Castaway Cay |
| December 23 | Lookout Cay |
| December 26 | Castaway Cay |

The requested rules are automatic booking, no monetary cap, one cabana on each uncovered date, no adults-only cabanas, and every member of the party included. The code has no price ceiling. A review page must establish the correct date, location, eligible cabana, entire party, and free cancellation before submission.

**Status:** the scheduler and browser controls are implemented and tested with a local simulated booking form. Live Disney booking is **not configured or verified**, and no monitor is currently running. An authenticated session and site calibration are required. Cloud browser navigation currently fails TLS certificate validation; verification has not been disabled.

## Install and test

Requires Python 3.11+ and Chromium. This cloud environment already supplies `/usr/bin/chromium`.

```sh
cd /workspace/testWebCollab
python3 -m venv /workspace/.cabana-venv
/workspace/.cabana-venv/bin/python -m pip install -r requirements.txt
/workspace/.cabana-venv/bin/python -m unittest discover -s tests -v
```

The browser tests intercept every request and serve a local fixture in Chromium. They do not contact Disney, place real bookings, or prove compatibility with Disney's current interface.

## Private trip configuration and login

On your trusted computer, install Python 3.11 or newer, extract the supplied source bundle, and open a terminal inside the extracted `dream-cabanas` directory. The helper installs pinned dependencies into `.local-venv`, downloads Playwright's matching Chromium with verification enabled, creates the private trip config without overwriting existing settings, and opens an interactive sign-in browser.

macOS/Linux:

```sh
python3 scripts/local_login.py
```

Windows PowerShell:

```powershell
py -3 scripts/local_login.py
```

Enter the numeric Disney booking number in the local terminal prompt; it is not sent to this chat. Log into Disney in the browser, complete MFA yourself, and navigate to the December 22 port-adventures page. Return to the terminal and press Enter when prompted. The session remains on your computer; the helper does not start a monitor, inspect personal page content, or submit reservations. Tell the assistant whether the login step succeeded without sharing `.private` files. Live screen calibration is still required afterward. The portable logic is tested in the cloud on Linux; macOS and Windows interactive behavior has not been exercised on those operating systems here.

For manual setup on this cloud machine, the existing configuration is already present. On a different computer, replace `/workspace/testWebCollab` and the virtual environment path with their local equivalents. The `browser` setting may be a Chromium executable path or an empty string to use Playwright's installed Chromium. Interactive login needs a desktop display; this cloud chat has no interactive login browser. The local helper handles these settings for a new installation.

```sh
mkdir -p .private
chmod 700 .private
cp config.example.json .private/config.json
chmod 600 .private/config.json
```

Replace `YOUR_BOOKING_ID` in that private file with your booking identifier. The supplied booking URLs have already been entered in this instance's ignored `.private/config.json`. They are absent from the public example and tracked source. Do not overwrite an existing private config with the example.

```sh
/workspace/.cabana-venv/bin/python -m cabanas.cli validate
/workspace/.cabana-venv/bin/python -m cabanas.cli login
```

Complete login and MFA yourself in the browser, navigate to your port-adventures page, then press Enter in the terminal. The command stores cookies and local storage in `.private/session.json`. On macOS/Linux, session files have owner-only mode 0600. Windows uses the permissions of your account's folder; keep the extracted app in a private user folder rather than a shared directory. A failed write preserves the previous session. The command never asks for or stores your password. Treat that session file like a password; never commit it, paste it in chat, or include it in a shared environment snapshot. A session created on another machine may be rejected by Disney; do not work around security challenges.

For live cloud operation, authentication must be provisioned through a trusted private mechanism and TLS trust must work. Environment publication captures filesystem state, so keep any personal sessions out of shared snapshots. Reauthenticate if Disney expires or challenges the session.

## Calibrate the authenticated site

After authentication and working TLS, `python -m cabanas.cli inspect` saves a private copy of the first target page's DOM beside the config. It may contain personal information and must remain private. Inspection is read-only. This is a starting point; the guest picker, final review, confirmation, and authoritative existing-reservations view also need inspection. Availability currently reads “Check Availability Onboard”; booking must wait for a genuine, enabled “Select” button.

Populate `allowed_titles` with the exact titles of all eligible family cabana types at both destinations. Unknown types are skipped until verified. Populate `selectors` with **observed** CSS selectors matching the contract in [docs/site-contract.md](docs/site-contract.md). Production selectors are intentionally not guessed. Verify the authoritative reservation list can distinguish an empty itinerary from a list that has not loaded. If it is on a separate page, the adapter must be extended to read that page; do not substitute an availability list.

The generic cancellation check only accepts explicit, unconditional free cancellation. If Disney shows a cutoff date or other conditions, implement and test a policy check against the displayed deadline and current time before enabling booking. The current code stops on those terms rather than assuming a refund is available. Additional steps such as time-slot selection, capacity limits, multiple guest pages, or terms checkboxes also need site-specific implementation if present.

Once selectors have been checked against all three dates and the booking flow has been inspected without submitting a purchase, run an observation cycle first:

```sh
/workspace/.cabana-venv/bin/python -m cabanas.cli check
/workspace/.cabana-venv/bin/python -m cabanas.cli status
```

The user has authorized automatic booking under the rules above. Once live integration is verified, enable submission with:

```sh
/workspace/.cabana-venv/bin/python -u -m cabanas.cli run --book
```

This is a foreground, long-running process. Keep its host awake and connected, and supervise it with a suitable service manager for unattended operation. A cloud coding task or published environment snapshot does not provide a continuous scheduler. No recurring service has been deployed by this project. Processes do not survive environment restoration.

## Check frequency and failure handling

The default is one sequential cycle every 30 minutes, not continuous page refreshing. The interval is configurable but cannot be shorter than 30 minutes. Each cycle reads the three applicable dates; a booking candidate triggers another reservation/availability check before selection. There is no known interval that guarantees Disney will not flag an account. Follow any published restrictions; this project does not conceal automation or bypass security controls.

The next eligible check time is persisted before network activity. Restarting does not reset it. HTTP 429 respects `Retry-After`; temporary failures use exponential backoff up to six hours, or longer when requested by Disney. Five consecutive temporary failures pause the monitor. Login expiry, HTTP 401/403, security challenges, and unexpected interface changes stop it. The browser maintains TLS verification. Checking for each port day ends at midnight America/New_York at the start of that day; Disney may close online booking earlier, in which case the site will not offer “Select”.

A file lock prevents concurrent workers on one machine. Do not run multiple hosts against the same sailing. Before selecting a cabana, the browser refreshes both availability and existing reservations. It selects every guest, stops if anyone is ineligible or missing, and compares the complete guest list with the review page. It records `pending` **before** clicking final confirmation. A timeout or crash after submission is never blindly retried. Local state is checked against the live itinerary, including reservations made or canceled outside this program.

`status` shows a persisted pause and per-date state. After diagnosing a temporary issue, use `resume`; this clears the pause but preserves the check interval and uncertain submissions. If a submission is uncertain, verify the actual itinerary first. A live reservation automatically reconciles that date. If there is none, the pending state requires an explicit repair after manual verification; do not delete the database merely to force another attempt.

## Files and privacy

- `cabanas/core.py`: validation, scheduling, state, locking and booking coordination.
- `cabanas/browser.py`: browser controls and the site-calibration contract.
- `cabanas/cli.py`: login, inspection, monitoring and status commands.
- `scripts/local_login.py`: portable desktop setup and interactive login helper.
- `.private/`: ignored config, session, inspection output, and SQLite state. These files are not encrypted; protect the containing user folder.

Keep the private state database persistent across restarts. Booking identifiers, party names, session cookies and account details do not belong in tracked files, logs, issue reports, or shared snapshots. Back up state privately if moving hosts. Git history retains the original project; the working tree carries over none of its application content.
