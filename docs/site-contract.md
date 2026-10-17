# Authenticated site contract

These selectors must be calibrated against the real authenticated Disney UI. The test fixture is not a source of production selectors. A selector must be scoped to the current port date and unambiguous; a missing or ambiguous identity/checkout element stops the process.

| Config key | Required meaning |
| --- | --- |
| `page_ready` | Availability has fully loaded for the selected date |
| `page_date` | Current date; ISO text, full English date with year, US numeric date, or a `<time datetime="YYYY-MM-DD">` |
| `page_port` | Current port code or full destination name |
| `reservation_list_ready` | Authoritative existing reservations have finished loading, including a verified empty result |
| `reserved_cabanas` | Reservation cards for this date; zero is valid only after the readiness marker |
| `reserved_title` | Cabana title relative to each reservation card |
| `offer_cards` | Availability cards |
| `offer_title` | Exact title relative to each offer card |
| `offer_eligibility` | Eligibility text relative to each offer card; must explicitly establish family/all-age access |
| `select_button` | Button relative to each offer card; visible, enabled and labeled exactly `Select` |
| `party_ready` | Complete guest picker has loaded |
| `party_total` | Total number of people in the booking party, as digits only; **not** the current selected count or cabana capacity |
| `party_checkboxes` | All guest checkboxes, including disabled ones so exclusion can be detected |
| `party_names` | Full guest labels, one per checkbox |
| `continue_button` | Advance from guest selection to review; must not submit the reservation |
| `review_ready` | Final review is fully loaded |
| `review_title` | Exact selected cabana title |
| `review_date` | Selected reservation date, using the same date formats as above |
| `review_port` | Selected destination |
| `review_guests` | One label per included guest, identical to the guest picker labels |
| `free_cancellation` | Applicable cancellation terms for this actual reservation, not an unrelated FAQ or promotion |
| `confirm_button` | Final reservation submission button, called once only after all checks |
| `confirmation_id` | Nonempty, unique confirmation identifier displayed only after successful reservation |

The adapter must be extended if the real flow differs. In particular, separate itinerary pages, conditional cancellation cutoffs, multiple timeslots, and custom non-checkbox guest controls require real UI inspection and additional validation. Do not mark the integration verified just because selectors happen to match a page.

Read-only inspection may be performed with real authentication. Test submission behavior against a controlled fixture, then inspect the real review flow without submitting. Any actual live booking must remain within the user's authorization: the three configured dates, one cabana per uncovered date, all party members, no adults-only cabanas, unlimited price, and verified free cancellation.
