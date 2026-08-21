# Varsity Events

Every university event in Zimbabwe, in one place. Societies publish events, students sign in and
see what's on across all 18 universities, and everyone can tell at a glance whether tickets are
still going — and exactly where to buy them.

Built with **Django 5.2** and **Tailwind CSS 3**.

---

## What it does

**For students**

- Sign in with your details and land straight on a feed of what's on **right now**, nationwide —
  your own university first, every other Zimbabwean university behind it
- Filter by university, category, date, society, price or **ticket availability**
- See exactly **where to buy a ticket**: SRC offices, partner outlets, EcoCash, online, on the door —
  each outlet showing whether it still has stock
- Anything gone is marked **ALL SOLD OUT** or **NOT CURRENTLY AVAILABLE**, so nobody makes a wasted trip
- **Pay with Pesepay** — EcoCash, OneMoney, InnBucks, Zimswitch or card — and the ticket confirms itself
- One-tap registration issuing a ticket with a unique code and QR; automatic waitlists when full
- Save events, follow societies, export to calendar, review events you attended
- **⌘K** anywhere to search events, societies and universities; light and dark themes
- A **live pulse board** at `/live/` showing what's happening across the country as it happens

**For organizers**

- Register a society, invite members, promote admins — or **claim** one we already
  listed for your campus and be handed the page
- Create events as drafts, then publish when ready. A new society's first events
  get one look from us; once you're verified they go straight up
- **Scan tickets at the door** with the camera, or type the code
- List every ticket outlet and untick one the moment it runs out
- Override ticket status manually when tickets are sold somewhere the platform can't see
- Live dashboard with **money collected**, door check-in desk, announcements
- An **earnings statement** showing what we hold for you, what we kept and what
  we've sent — every payout openable down to the individual tickets
- CSV export including payment method and gateway reference per attendee

**For platform staff**

- An in-app admin at `/staff/` covering every event at every university
- Filter by university, status, or whether it's been picked
- **Pick** events to lead the site, publish, unpublish, cancel, mark sold out, or delete
- Work the **review queue** — a new society's events wait there before students see them
- Verify and suspend societies at `/staff/societies/`, and hand pages to their real
  committees at `/staff/claims/`
- **Pay the societies** at `/pay/payouts/`: what each is owed, and the record of what went
- Full Django admin at `/admin/` for everything else

---

## Getting started

```bash
python -m venv .venv
```

```bash
.venv\Scripts\activate
```

```bash
pip install -r requirements.txt
```

```bash
npm install
```

Build the stylesheet (or `npm run dev` to rebuild on change):

```bash
npm run build
```

Set up the database and load a term's worth of demo data:

```bash
python manage.py migrate
```

```bash
python manage.py seed_demo --reset
```

This also **draws the sample artwork** — a banner for every event, plus a logo and cover for
every society — using Pillow (`core/imagegen.py`). They're generated rather than downloaded, so
the seed stays reproducible and offline with no licensing questions, and each image is keyed off
its subject so reseeding gives the same art back. Category colour drives the palette. Pass
`--no-images` to skip it if you're in a hurry.

```bash
python manage.py runserver
```

Open http://127.0.0.1:8000.

### Demo accounts

Password for all three: `demo12345`

| Username    | Role                                                             |
| ----------- | ---------------------------------------------------------------- |
| `admin`     | Platform staff — event curation at `/staff/`, Django admin        |
| `organizer` | Runs several UZ societies, has events and outlets to manage       |
| `student`   | A UZ student with tickets, saved events and interests             |

The seed data includes one event deliberately marked **sold out** and one marked
**not currently available**, so both states are visible straight away.

> These three passwords are in a public README, which is exactly why
> `manage.py preflight` refuses to pass while any account still has one — and it
> checks by hashing rather than by username. See **Going live** below.

---

## Project layout

```
varsity/          Project settings and root URLs
accounts/         University, custom User, auth, profiles, tickets, saved events
organizations/    Societies and clubs, memberships, followers
events/           Categories, venues, events, ticket outlets, registrations, check-in
payments/         Pesepay client, Payment model, checkout / callback / simulator
activity/         Activity stream, live pulse board, activity simulator
core/             Home, discover feed, staff curation, ⌘K search, seed_demo command
templates/        Base layout and shared partials
static/src/       Tailwind source — compiled to static/css/app.css
```

### Data model

- **University** — the 18 Zimbabwean institutions, each with an abbreviation (UZ, NUST, MSU…),
  city and province. Students, societies and venues all hang off one.
- **User** — extends `AbstractUser` with a role (student / organizer / staff), university,
  course, interests and avatar.
- **Organization** — a society, club, faculty body, sports team or union at one university.
  Members join through **Membership** (owner / admin / member); followers are a plain M2M.
- **Event** — belongs to a society. Carries scheduling, location, capacity, pricing and a
  `ticket_status` override. Registration, waitlist and availability rules live on the model.
- **TicketOutlet** — one place a student can actually buy a ticket, with its own `is_available`
  flag. An event can have as many as it needs.
- **Registration** — one person's ticket. Unique `VE-XXXX-XXXX` code, status and check-in fields.
- **Bookmark**, **EventUpdate**, **Review** — saved events, announcements and feedback.
- **OrganizationClaim** — somebody saying "this is my society", with the evidence
  and the verdict. Approving one hands over the page.
- **Payout** — one settlement from the platform to a society, claiming the sales
  it covers so the same money can't go out twice.

### Payments — direct EcoCash

The default and fastest route sends money **straight to your own EcoCash wallet**, no gateway
in the middle and no merchant code required:

1. Student picks *EcoCash — send straight to us* at checkout.
2. They get the amount, your wallet number from `ECOCASH_MERCHANT_NUMBER` (with a copy
   button), the `*151#` steps and a reference. **The seat is held for 2 hours**, longer
   than a gateway push.
3. They send the money and paste back the EcoCash confirmation code.
4. It lands in **`/pay/verify/`**, where you check the code against your wallet statement and
   hit *Money received* — the ticket confirms itself.
5. If nothing matches, *Can't find it* sends them back to try again with the seat still held.

Organizers only see transfers for their own events; platform staff see everything. The number
lives in `ECOCASH_MERCHANT_NUMBER` so it never has to be edited in code, and
`ECOCASH_DIRECT_ENABLED=False` removes the option entirely.

> **Worth knowing:** no API can push money into a personal EcoCash wallet on its own — that
> needs an EcoCash merchant/biller code. This flow is the honest version: the money genuinely
> arrives in your wallet, with a human confirming it. If you later register a Paynow merchant
> account you can set that same number as your settlement destination in the Paynow dashboard
> and the gateway routes takings there automatically.

### Payments (Pesepay)

Paid events don't confirm a ticket until the money lands. `Event.register()` creates the
registration as **awaiting payment**, which *holds the seat for 30 minutes* — so two students
can't buy the last ticket at once — then checkout hands off to Pesepay:

- **Hosted checkout** — Pesepay's own page (card, Zimswitch, any wallet).
- **Seamless payment** — we pass the mobile number and wallet code, Pesepay pushes a PIN
  prompt straight to the phone, and the status page polls until it settles.

Every request and response is wrapped in Pesepay's AES-256-CBC envelope: the JSON is
PKCS7-padded and encrypted under your 32-character encryption key, with the IV taken from its
first 16 characters, then base64'd into a single `payload` field. `payments/pesepay.py`
implements that, and the test suite pins the ciphertext **byte-for-byte against CryptoJS** —
the library Pesepay's own SDKs use — so a subtle drift in padding or key handling fails loudly
instead of being silently rejected by the gateway.

Pesepay calls back to `/pay/<reference>/result/` when a transaction moves. That POST is
treated as **a nudge, not evidence**: the view ignores the body entirely and re-asks Pesepay
via `check-payment` before touching anything. That's why the endpoint can be CSRF-exempt and
unauthenticated — a forged "SUCCESS" post confirms nothing. Abandoned checkouts expire and
release their seat automatically.

**Without credentials the app runs a built-in simulator** at `/pay/<reference>/simulate/`, so
the entire flow — push, wait, approve, decline, fail — works offline. Set
`PESEPAY_INTEGRATION_KEY` and `PESEPAY_ENCRYPTION_KEY` in `.env` and the simulator switches
itself off. The wallet codes default to `PZW201` (EcoCash), `PZW204` (OneMoney) and `PZW211`
(InnBucks) — check them against your merchant dashboard, since they vary by account.

Paynow is kept as a legacy gateway (`Payment.gateway`) so payments taken before the switch can
still settle through their signed callback; nothing new is routed to it.

### Email

Five transactional emails, all rendered as an HTML and plain-text pair from
`templates/emails/`:

| When | Who gets it |
| --- | --- |
| A ticket is confirmed — free sign-up, or the moment a payment settles | the student |
| A payment settles | the student, as a receipt |
| A seat frees up and the waitlist moves | the student promoted |
| An EcoCash code is submitted | the society's owners and admins |
| An EcoCash code doesn't match the wallet | the student, quoting the code they sent |

Plus the forgotten-password flow at `/accounts/password/reset/`.

**Sending is deliberately best-effort.** `core.mail.send_mail` catches everything and
logs it — a ticket is confirmed the moment the money lands, and an SMTP timeout at
that instant must not unwind a payment. There's a test that kills the mail server
mid-settlement and asserts the ticket still confirms.

The consequence is that a broken mail server is *quiet*. This makes it loud:

```bash
python manage.py check_email --to you@example.com
```

With `EMAIL_HOST` unset, mail prints to the console instead of sending, so the whole
flow works offline. Links inside emails come from `SITE_BASE_URL`, since an email is
read long after the request that triggered it.

### The activity stream

Every domain action — registering, paying, checking in, publishing, following, reviewing,
selling out — appends one row to an **append-only `Activity` stream**. It surfaces as the
*Recent activity* panel on each event page, which doubles as social proof ("12 today"). Names
are shortened to a first name and last initial.

Recording is deliberately failure-tolerant: the stream is decorative, and a problem writing to
it must never take down a registration or a payment.

> The standalone live-pulse board was removed. The stream itself stays because event pages use
> it, and because `simulate_activity` needs somewhere to write.

### Simulating live activity

```bash
python manage.py simulate_activity
```

This doesn't fake the feed. It performs the same domain calls a real student would trigger —
registering, paying through the Pesepay simulator, checking in, following, reviewing, and even
signing up new accounts — so capacity, waitlists, revenue and the stream all move together and
stay consistent.

| Flag | Effect |
| ---- | ------ |
| `--rate 120` | Actions per minute (default 30) |
| `--burst 200` | Fire one batch and exit |
| `--duration 300` | Stop after five minutes |
| `--clean` | Remove simulated activity and payments |

It **refuses to run with `DEBUG=False`** unless you pass `--force`, because it writes real
registrations and payments.

### Structure

Navigation is three tiers, so nothing competes for the same slot:

| Tier | What's in it |
| ---- | ------------ |
| **Browse** | Discover · Events · Societies — the public destinations, always visible |
| **Manage** | One menu holding everything an organizer runs, grouped Events / Money / Societies, with a badge when EcoCash transfers are waiting |
| **Account** | The signed-in person's own pages — tickets, saved, profile — under their avatar |

Staff keep one extra top-level **Admin** link, because moderating the platform is a different
job from running your own events.

Every interior page uses the same header — breadcrumb, title, one line of subtitle, actions on
the right — from `partials/_page_header.html`. Crumbs and actions are passed as plain data
(`[{label, url}]`), so a view declares its own place in the hierarchy and the markup stays in
one file.

### The events page

`/events/` follows the agreed design: a centred **EXPLORE UPCOMING EVENTS** band, a
**FEATURED EVENTS** row of three, a **FILTER EVENTS** panel of tappable pills grouped by
university / category / when / price / tickets, then the full listing sectioned by day.
**SELLING FASTEST** closes the page with fill bars.

Featured and Selling fastest only appear on an unfiltered first page — once you've asked a
specific question, everything below is the answer, and the heading changes from *All events* to
*Matching events*. The university and category pills compose with each other, so
UZ + Careers is one tap from either.

Filters live in a panel across the page rather than a left column, which gives the listing the
full width and stops the page feeling like a form.

### Varsity Gigs

`/events/gigs/` is the entertainment end of the calendar — live music, open mics, parties and
film nights — with its own masthead ("Gig Guide"), strapline and tabs. It runs on the same
listing view with the categories fixed (`GIG_CATEGORY_SLUGS`), so filters, tabs, pagination and
the news format all come for free and there's one code path to maintain rather than two.

### Tickets left

`Event.tickets_left_display` is the single answer to "can I still get in?", and every surface
uses it: **"139 of 220 tickets left"** on cards and listing rows, a **large counter with a fill
bar** on the event page, and `tickets_left_short` where space is tight. `tickets_tone` colours
it green / amber / red as the event fills, so the urgency reads before the words do. Sold-out,
waitlist-only and unlimited-capacity events each get their own phrasing.

### The societies page is a directory

`/societies/` groups societies **under their university**, verified ones first and alphabetical
within each — so it reads like a directory rather than an arbitrary grid. Each heading carries
a count and a link straight to that university's events.

The grouping only applies to the default order. Ask for *Most active* or *Most followed* and it
flattens into a single league table, because a ranking that restarts at every university isn't
a ranking.

### Loading states

- **Route progress bar** starts on link *click* rather than unload, so navigation feels
  immediate, and resets correctly when you come back through the bfcache.
- **Buttons** swap their label for a spinner on submit without the width jumping
  (`data-loading`), and hand themselves back if the browser blocks the submit.
- **Skeletons** (`partials/_skeletons.html`, kinds: `card`, `row`, `stat`) carry the shape of
  the answer while it loads — used by the ⌘K palette and available to any async view.
- **Images** fade in once decoded rather than snapping into place.
- `.spinner` and `.is-loading` are available anywhere inline async work needs to show itself.

### The front end

**Palette — indigo, orange and teal on off-white**, taken from the agreed design:

| Token | 500 | Where it lands |
| ----- | --- | -------------- |
| `brand` | `#4f56a8` — indigo | Header (`brand-950`), primary buttons, links, focus rings |
| `flame` | `#f97316` — orange | Logo mark, the VARSITY half of the wordmark, badges |
| `teal`  | `#14b8a6` | The single bright **Get Started** call to action |
| `azure` | `#4180a6` | Secondary accent |

The page sits on `#f5f5f1` off-white with cards a shade brighter, and every neutral carries a
trace of the indigo so nothing looks grey next to the header. Ink is a dark indigo (`#1a1b33`),
never pure black; dark mode is the same indigo night as the header.

Tailwind with a semantic token layer: `surface`, `ink`, `hairline` and friends are CSS
variables, so one `dark` class on `<html>` re-skins the whole app. The theme is applied before
first paint (no white flash), remembered in `localStorage`, and falls back to the OS setting.

On top of that: a **⌘K command palette** searching events, societies and universities with
live availability badges; reveal-on-scroll for cards; a navigation progress bar; skeleton
loaders; and `prefers-reduced-motion` respected throughout.

### Who can put something in front of students

The platform is national. An event on the feed is seen at eighteen universities,
and a paid one takes real money from real students — so publishing is not a
button anybody gets by signing up.

Three gates, in order of how much they cost the person passing them:

| | What it stops |
| --- | --- |
| **A confirmed email address** | A throwaway signup registering a society or claiming one. Nothing else is gated: browsing, saving and getting a ticket work exactly as before. |
| **Review of a new society's events** | An invented society selling tickets to an event that will never happen. Their events wait in a queue at `/staff/?status=review` until a person has looked. |
| **Verification** | Nothing — it's what *removes* the queue. Verify a society and its events go straight up. |

Verification carries to people as well as societies. Verifying a society at
`/staff/societies/` also sets `is_verified_organizer` on whoever runs it, so the
same committee starting a second society isn't reviewed from scratch. That flag
had existed since the first migration and was checked nowhere; it means
something now.

**Confirming an address** is a signed, expiring token rather than a row in a
table — nothing here needs revoking individually, and a token that carries its
own expiry can't be left behind by a cleanup job that never ran. The signature
covers the current address, so changing your email invalidates any link already
sent to the old one. The link itself needs no sign-in: people read email on a
different device from the one they signed up on, and demanding a password first
is how a confirmation link goes unclicked.

Sending is best-effort like every other email here. **A dead mail server must
not close the front door** — the account is created, the person is signed in,
and they simply can't publish until they confirm. There's a test that points
sign-up at a refused SMTP port and asserts the account still exists.

Staff are exempt from the whole thing: `createsuperuser` sends nothing to click,
and locking the only administrator out of the admin is not a security posture.

**Review** adds one status between draft and published, and it fails closed —
every existing query for `PUBLISHED` already hides it, and `can_be_seen_by`
refuses a queued event to anybody but its organizer and platform staff, however
they came by the URL. Approving emails the society; sending it back returns it
as a draft **carrying the reason**, because "no" on its own is not something an
organizer can act on. Editing an event that is already live never pulls it off
the feed — an organizer fixing a typo must not strand their own ticket-holders.

### Claiming a society

A directory only fills up if it can be populated ahead of its members. Societies
can be listed from public information — a campus has a debate union whether or
not anybody from it has found us — and their real committee takes the page over
afterwards at `/societies/<slug>/claim/`.

Without this, every society page waits for the one person who both runs it and
finds us first, and what actually happens is a duplicate page beside the
original, leaving students choosing between two.

A claim asks for a position and for evidence somebody who doesn't know you could
check. Approving it makes the claimant the **owner** — not an admin — so they can
add the rest of their committee without coming back to us. It does *not* verify
the society: proving you run something isn't the same as us vouching for it, so
their first events still go through review.

One open claim per person per society, enforced by a partial unique constraint
as well as by the view. Re-asking louder is not evidence, and a queue full of
duplicates is a queue nobody works. A rejected claim can be followed by a new
one, because people do come back with more.

The queue is at `/staff/claims/`.

### Payouts — what the platform owes the societies

Every ticket is paid into *our* Pesepay account and *our* EcoCash wallet, because
that is the only way to hold a seat against money that hasn't landed yet. The
societies still ran the events. Until that debt is written down somewhere it
exists only as an argument waiting to happen — so it's written down.

```
PLATFORM_FEE_PERCENT=0
PLATFORM_FEE_FIXED=0
```

**Both default to zero**, deliberately: every deployment that existed before the
ledger took no fee, and switching one on by upgrading would be taking money
nobody agreed to. This deployment runs at **10%** — set it as a Railway variable,
not in code, and set it *before the first sale*, since a society's first
statement is written under whatever the rate said that day.

The fee is worked out and **frozen onto each payment the moment it settles**, and
never recomputed on read. The rate is a business decision that will change, and a
society's statement from last term has to still say what it said last term. A
payment with no fee assessed at all — every row taken before this existed —
counts as owed in full. Rounding is half-up, not Python's default half-even: a
society checking our arithmetic by hand would otherwise find us short, and be
right.

A **payout** claims the sales it covers by stamping `Payment.payout`, so the same
money cannot go out twice — prepare a second payout with nothing new sold and it
returns `None` rather than an empty settlement. Preparing locks the rows, so two
staff pressing the button at once can't each build a payout from the same
tickets. Cancelling a prepared payout releases its tickets back into the pool;
one already sent can't be cancelled at all.

Marking a payout sent **requires the wallet's own confirmation code**. Without it
the row can't be reconciled against a statement later, which is most of what it
is for.

| Who | Where | What they see |
| --- | --- | --- |
| A society | `/pay/earnings/` | What we hold for them, what we kept, what we've sent |
| Anybody on it | `/pay/payouts/<ref>/` | One settlement, broken down to the individual tickets |
| Staff | `/pay/payouts/` | Every society owed, largest first, and the button that pays them |

Every figure on a statement can be taken apart back to the tickets that make it
up, because a number a society can't check is a number they won't believe.

### The door can scan now

The app issued a QR on every ticket and couldn't read one. A door person squinted
at a phone and typed twelve characters per person, in a queue, in the dark.

`static/js/scanner.js` uses the browser's own `BarcodeDetector`. No library, for
three reasons: the page has to work on a bundle that ran out on the walk over, a
scanning library is a hundred kilobytes of WASM, and the one thing worse than
typing codes is a scanner that fails to load and leaves nothing in its place.

Where `BarcodeDetector` is missing — Safari, Firefox — **nothing is rendered at
all** and the typed field stays exactly as it was. The panel ships hidden and JS
reveals it. A dead camera button at a door is worse than no camera button, the
same rule this app already follows for push.

A scan fills the existing field and submits the existing form, so it goes through
the same view, the same permissions and the same duplicate handling as a typed
code. It is a faster way to fill the field in, not a second way to check somebody
in. One ticket held in front of a lens is dozens of frames, so the same code is
ignored for four seconds after it lands — otherwise the second frame reports the
person as already checked in, which at a door reads as a rejection.

The QR encodes the ticket's **URL**, so `CheckInForm` now takes either: a bare
code, or anything containing one. A phone's built-in camera, a generic scanner
app and our own scanner all hand over a link, and the door is the worst possible
place to ask somebody to retype the interesting part.

The camera is released on `pagehide` and whenever the tab is hidden — a held
camera keeps the indicator lit and the battery draining, and on some Androids
the next page can't open it at all.

### Going live

Two commands, answering two different halves of "is this ready?".

```bash
python manage.py check --deploy    # the settings half
python manage.py preflight         # the database half
```

`core/checks.py` adds three settings checks to Django's own deployment list,
covering the things that **fail silently rather than crashing** — the app starts,
serves pages and looks healthy while doing the wrong thing:

| | What it catches |
| --- | --- |
| `varsity.W001` | `EMAIL_HOST` unset, so every ticket, receipt and confirmation link is discarded. `core.mail.send_mail` catches everything by design, which is what makes this quiet. |
| `varsity.W002` | Uploads on a container filesystem that the next deploy rebuilds. Mount a volume or set `AWS_STORAGE_BUCKET_NAME`. |
| `varsity.W003` | `SITE_BASE_URL` still localhost, so every link we email points at the machine that sent it. |

CI fails on all three, and the deploy-check job sets real-shaped values to prove
they can be satisfied.

`preflight` asks the questions that only a running deployment can answer, and
exits non-zero on anything that would actually hurt — so it works as the last
step of a deploy pipeline. It's read-only, so it's safe to point at production.

- **Any account still carrying the demo password** — checked by hashing, not by
  username, because renaming `admin` to `admin2` is exactly the sort of thing
  that feels like a fix and isn't. Staff accounts are named; the rest are
  counted, since eighty identical lines is a way of not being read.
- No universities (nobody could sign up), no superuser (nobody could reach
  `/staff/`), paid events with no way to take money
- Seed artwork still on events, payouts prepared but never sent, search vectors
  never rebuilt, and the features sitting inert for want of a key

**Clearing the demo and keeping the real reference data** is two commands:

```bash
python manage.py clear_demo --yes                  # societies, events, students, tickets
python manage.py seed_demo --reference-only        # the 18 real universities, and nothing else
```

`--reference-only` loads the only part of `seed_demo` that is true and invents
nothing. Run it on a fresh production database too: without universities the
sign-up form has an empty required field and nobody can register at all.

### How availability is decided

`Event.availability` returns one of `free`, `on_sale`, `waitlist`, `sold_out`, `closed` or
`unavailable`, with a label, an explanation and a colour tone that the templates render directly.
It works from capacity, the closing date and the event's status — but an organizer's manual
`ticket_status` override always wins, for the common case where tickets are sold off-platform and
the site can't count them.

---

## Tests

```bash
python manage.py test
```

554 tests, about half a minute. It used to be far worse: PBKDF2 hashes the handful of users almost
every `setUp` creates, at roughly a second apiece and much more on a slow machine, which put a
full run into the tens of minutes and meant nobody ran one. Tests now hash with MD5 — see the
`TESTING` block at the foot of `varsity/settings.py`, which also pins the cache to local memory,
runs tasks inline and leaves throttling off.

They cover registration and waitlist promotion, capacity and deadline enforcement, every
availability state and the manual override, ticket outlets and the sold-out / not-available
messaging, the post-login feed and its university ordering, staff curation permissions and actions,
the ⌘K search endpoint, permission boundaries on every management view, ticket privacy, CSV and
`.ics` export, QR rendering, and the sign-up / sign-in flows.

Payments get their own suite: the **Pesepay cipher pinned byte-for-byte against CryptoJS**,
every transaction status mapped, **a forged callback proved unable to confirm a ticket**,
Paynow hash generation and signature rejection for the legacy path, seat holds and expiry,
waitlist promotion on paid events, wallet prefix validation, checkout and simulator flows,
and mocked live poll/initiate responses.

The live system too: activity recording for every verb, sold-out announced exactly once,
incremental feed paging by since-id, ordering guaranteed oldest-first for prepending, private
rows kept off the feed, shortened display names, and the simulator's production guard.

And the plumbing: that counting seats issues **no writes**, that a lapsed hold stops counting
before anything sweeps for it, that a listing costs the same number of queries whether it shows
three events or twenty, that an annotated event still notices its own registrations, that ten
polls in a second ask the gateway once but still spot a payment settled by callback, that a
queued email arrives when the broker is down, that a job whose row has been deleted logs rather
than raises, and that hammering the gateway callback earns a 429 without blocking anybody else's
payment.

Trust gets its own suite: that a signed link confirms an address and a tampered,
expired or forwarded-after-an-email-change one doesn't; that a dead mail server
still lets somebody sign up; that an unconfirmed address can't register or claim
a society; that a new society's events go to the queue and a verified one's go
straight up; that a queued event is invisible to students and visible to the
staff who must review it; that editing a live event doesn't unpublish it; and
that verifying a society carries that trust to the people running it.

Claims are tested for the handover: that approving makes the claimant the owner
and an organizer but does *not* verify the society, that a rejection carries its
reason, that a decided claim isn't decided twice, and that one society can't read
another's statement.

The ledger is tested where money would go wrong: that the fee is frozen at
settlement and a later rate change doesn't restate it, that settling twice
doesn't charge twice, that a half-cent rounds up rather than to even, that the
fee can never exceed the ticket nor go negative, that preparing a payout twice
can't pay the same tickets out twice, that cancelling returns them to the pool
and a sent payout can't be cancelled at all.

The door is tested on what a scanner actually hands over: a bare code, a scanned
ticket URL, one from a hostname the site no longer uses, a long one that the
field's own length check would otherwise reject before the code was found — and
that a string with look-alike characters is *not* read as a code.

The PWA gets its own: that the QR is inline rather than a second request, that
the endpoint and the inline copy encode the same thing, that money and identity
are on the worker's never-cache list, that tickets survive a deploy, that signing
out clears them, and that a ticket is still private. CI renders the worker and
runs `node --check` over it, because a stray template tag there is a syntax error
whose only symptom is "offline tickets quietly stopped working".

Push is tested for what it doesn't do as much as what it does: a dead
subscription is deleted rather than retried, a transient failure is counted, a
send that raises never reaches the caller, nobody is reminded twice, and an
ordinary free sign-up pushes nothing at all. The VAPID command is checked by
having pywebpush's own loader sign with the key it emits — the keys we tell an
operator to use have to actually work.

Search is tested on both databases. The shared behaviour — finding an event by title, summary,
tags, society or venue — runs everywhere; ranking, stemming and websearch syntax are skipped on
SQLite and run against Postgres.

CI runs all of it on every push — against **both SQLite and Postgres**, because they disagree
often enough that green on one proves little about the other. See `.github/workflows/ci.yml`,
which also fails the build on a model changed without a migration, on `check --deploy` raising
so much as a warning, and on `static/css/app.css` drifting from its Tailwind source.

---

## Dependencies

Declared in `pyproject.toml`, resolved into `uv.lock`, and exported to
`requirements.txt` so that anything pip-based — the Dockerfile, CI, a plain
`pip install -r` — gets the same pinned, hash-verified set without needing uv
installed.

```bash
# change a dependency
vim pyproject.toml
uv lock
uv export --frozen --no-dev --no-emit-project --format requirements.txt -o requirements.txt
```

`requirements.txt` used to carry `>=` ranges, which meant two builds a week
apart could ship different Django patch releases and nobody would know. CI fails
if the exported file has drifted from the lock, and the Docker build installs
with `--require-hashes`.

## Configuration

Copy `.env.example` to `.env` and adjust. Everything has a sensible development default.

| Variable                      | Default                        | Notes                                     |
| ----------------------------- | ------------------------------ | ----------------------------------------- |
| `DJANGO_SECRET_KEY`           | insecure dev key               | **Must** be changed for production        |
| `DJANGO_DEBUG`                | `True`                         | `False` turns on the security settings    |
| `DJANGO_ALLOWED_HOSTS`        | `localhost,127.0.0.1,[::1]`    | Comma-separated                           |
| `DJANGO_TIME_ZONE`            | `Africa/Harare`                |                                           |
| `EMAIL_*`                     | console backend in debug       | SMTP settings used when `DEBUG=False`     |
| `PESEPAY_INTEGRATION_KEY`     | blank                          | Blank runs the checkout simulator         |
| `PESEPAY_ENCRYPTION_KEY`      | blank                          | 32 chars, from your Pesepay dashboard     |
| `PESEPAY_CODE_ECOCASH`        | `PZW201`                       | Wallet method code, per merchant account  |
| `PESEPAY_CODE_ONEMONEY`       | `PZW204`                       | Wallet method code                        |
| `PESEPAY_CODE_INNBUCKS`       | `PZW211`                       | Wallet method code                        |
| `PAYNOW_INTEGRATION_ID`       | blank                          | Legacy gateway, for pre-switch payments   |
| `PAYNOW_INTEGRATION_KEY`      | blank                          | Legacy gateway                            |
| `ECOCASH_MERCHANT_NUMBER`     | blank                          | Wallet students send money to             |
| `ECOCASH_DIRECT_ENABLED`      | `True`                         | `False` hides the direct-transfer option  |
| `ECOCASH_DIRECT_HOLD_MINUTES` | `120`                          | Seat hold for a hand-made transfer        |
| `PLATFORM_FEE_PERCENT`        | `0`                            | Kept per ticket; frozen at settlement      |
| `PLATFORM_FEE_FIXED`          | `0`                            | Flat amount kept per ticket                |

Setting `DJANGO_DEBUG=False` enables HSTS, SSL redirect, secure cookies, and WhiteNoise's
compressed manifest storage. Run `python manage.py collectstatic` before deploying.

**`DJANGO_SECRET_KEY` has no production default.** With `DEBUG=False` and no key set, the app
refuses to start rather than fall back to one committed to this repository — anyone holding that
key can forge session cookies and password-reset tokens.

### Infrastructure

All optional. Leave every one of these blank and the app behaves exactly as it did before any of
it existed: cache in local memory, tasks inline, uploads on the local disk, logs to stderr, no
error reporting.

| Variable                  | Default  | Notes                                              |
| ------------------------- | -------- | -------------------------------------------------- |
| `REDIS_URL`               | blank    | Shared cache, and the task broker when set          |
| `DJANGO_TASKS_ASYNC`      | `False`  | Turn on **only** alongside a running `qcluster`     |
| `AWS_STORAGE_BUCKET_NAME` | blank    | Uploads to object storage; S3, R2, B2 or Spaces     |
| `DJANGO_DB_POOL`          | `False`  | psycopg pooling; excludes persistent connections    |
| `DJANGO_LOG_FORMAT`       | by DEBUG | `json` in production, `console` in development      |
| `DJANGO_LOG_SQL`          | `False`  | Prints every query — how you find an N+1            |
| `SENTRY_DSN`              | blank    | Error reporting; inert without it                   |
| `DJANGO_RATELIMIT_ENABLE` | `True`   | Only turn off to debug a limit you think is wrong   |

#### Redis

The login throttle counts failed attempts in the cache. With local memory each Gunicorn worker
keeps its own tally, so eight allowed attempts really means eight *per worker*. `REDIS_URL` makes
the count shared and the lockout mean what it says.

#### Background work

Two jobs shouldn't happen while a student waits for a page: sending mail — `Event.register()` put
an SMTP round trip inside the request that issues a ticket — and releasing seats held by abandoned
checkouts.

```bash
DJANGO_TASKS_ASYNC=True
python manage.py qcluster
```

Set the flag **only in the same change that starts the worker**. A queue with nothing behind it
accepts jobs and silently never runs them, and this is deliberately not inferred from `REDIS_URL`
for exactly that reason. Left off, every task runs inline where it always did. The queue rides on
Redis when there is one and on the database when there isn't, so it doesn't require Redis — it
prefers it.

The cluster also runs a sweep every minute that retires timed-out checkouts and offers the freed
seats to the waitlist. Without a worker, run it from cron instead:

```bash
python manage.py expire_holds
```

Seats stay **correctly counted** either way. `Event.reserved_count` discounts a lapsed hold in the
query rather than relying on something having released it, so a sweep that never runs leaves stale
rows but never wrongly turns a student away. It used to release them itself, which meant rendering
nine event cards on a listing issued nine sets of UPDATEs on a GET request.

#### The live board

`/live/` shows every sign-up, ticket and check-in across the country as it
happens. The model always had what it needed — `public()`, `since()`,
`for_feed()`, `as_dict()` — and the README had been advertising the page for a
while; `activity/views.py` is the part that was missing.

The feed reads strictly **forward from the last id the client saw**. No
timestamps to reconcile, no overlap window to tune, and no way to get a
duplicate or a gap, because ids are monotonic and the index is on `-id`. Rows
come back oldest-first so the client can prepend them and keep the order, and a
client that has been asleep in a background tab gets the newest slice rather
than crawling forward an hour at a time. Polling stops entirely while the tab is
hidden, and the server sets the interval — 5s when things are happening, 15s
when they aren't.

#### Installable, and tickets that work offline

The one thing this app produces that genuinely has to work without a network is
a ticket. A student arrives at a hall on the edge of campus, in a crowd, on a
bundle that ran out on the walk over, and has to show a QR code. Everything here
serves that moment; installability falls out of it for free.

- The QR is **inline in the page** as a data URI, not an `<img>` pointing at our
  own endpoint. Whatever cached the page cached the code with it, and there is no
  second request to fail. `events/qr.py` is the single source both it and the
  `qr.png` endpoint read, so a door scanner and a student's screen can't drift
  apart.
- Ticket pages are cached the moment they are first opened, in a cache that is
  **not** versioned — a deploy must not delete somebody's ticket.
- Signing out clears the cached pages. Campus devices get shared, and a cached
  ticket outliving its session is the next person's business.
- `/pay/`, `/admin/`, `/staff/` and the auth pages are **never** stored or served
  from a cache. A stale payment page can tell somebody their money failed when it
  went through.

The manifest and the worker are rendered by `core/pwa.py` rather than served as
static files, because both need the hashed stylesheet URL and a version that
moves on every deploy. `sw.js` answers from the root on purpose: a worker can
only control pages at or below its own path.

Icons are drawn, not designed — `python manage.py make_icons`, the same approach
`seed_demo` takes to its artwork. Commit what it writes; the production image has
no Pillow step, exactly as with `static/css/app.css`.

#### Notifications

Push is not email. A browser gives a site **one** chance to ask, and a denied
permission is buried in a settings menu more or less forever — so there is no
"enable notifications?" prompt on page load anywhere in this app, and there
should never be one. The ask lives on a ticket somebody has just been issued,
where the question answers itself.

Three things are considered worth interrupting a student for, all of them
time-critical and actionable:

| | Why it's a push and not an email |
| --- | --- |
| A waitlist seat opened up | The hold expires. Read it tomorrow and it's gone. |
| The money landed | They were watching a spinner for it. |
| Doors open in an hour | They wanted to be there. |

Everything else is an email. Reminders are stamped on the registration when they
go out, so a cluster that restarts or catches up can't nag — being nagged is how
the permission gets revoked.

```bash
python manage.py make_vapid_keys
```

Both halves come out as single-line base64url, which is what `subscribe()` wants
and what fits an environment variable. Keep the pair stable once set: a browser
ties its subscription to the key that created it, so rotating it silently
invalidates every permission anybody has granted. Leave the keys blank and push
is a no-op everywhere — no dead button is rendered and nothing else changes.

#### Search

Postgres gets a stored `tsvector` on `Event`, behind a GIN index, weighted so a
word in the title outranks the same word buried in a description, stemmed so
"parties" finds "party", and ranked so the best match is listed first. The search
box accepts the syntax people already expect: `"quoted phrases"`, `OR`, and a
leading `-` to exclude.

SQLite has none of that and development runs on SQLite, so both paths live behind
one function in `events/search.py` and the fallback is the `icontains` chain this
app used everywhere before. The migration that adds the GIN index is a no-op off
Postgres — see `varsity/dbops.py` — so the migration history stays single.

Each event rewrites its own vector when it is saved. Two cases that doesn't cover:

```bash
python manage.py rebuild_search                     # everything
python manage.py rebuild_search --organization=uz-jazz-society
```

Run it after deploying the field for the first time, when every row is still
null, and after a society or venue is renamed — that name is baked into the
vector of every event they host, and renaming one re-saves none of them.

#### Counting seats

`Event.availability` asks `is_full`, which asks `reserved_count`. On a listing
that is once per card, and each card reads `availability` four times over — for
the badge, the tickets-left phrase, the short form and the colour. Twelve events
cost **260 queries**.

`EventQuerySet.with_counts()` annotates both figures with subqueries, and the
properties read the annotation when it is there. The same page is now **6
queries, flat** however long the list gets, and `availability` is cached per
instance.

The catch is that an annotated instance is carrying numbers from whenever its
query ran. Anything that changes them has to call `event.forget_counts()` — the
mutating methods already do, because for `is_full` a stale count is the
difference between selling the last seat once and selling it twice.

#### Payment status

The status page polls, and each poll used to make us ask the gateway — roughly
forty outbound calls per student per checkout, all about the same transaction.
`PAYMENT_POLL_MIN_SECONDS` (default 4) is a floor on how often that question
gets asked. Local state still updates on every poll, so a payment confirmed by
callback is picked up on the very next tick; only the network call is rationed.
A nudge from the gateway itself is never debounced — it means something changed.

The response also carries `retry_in`, and the client obeys it: 2s while a wallet
prompt is likely to be answered, 5s after half a minute, 10s after ninety
seconds. The pace can be widened for a struggling gateway without redeploying
the front end.

SSE was the obvious alternative and was rejected: production runs sync Gunicorn
workers, so a handful of open streams would block the whole site, and a long-held
connection is the wrong shape for a patchy mobile network anyway. It is worth
revisiting behind an ASGI migration, as its own piece of work.

#### Throttling

Per-view limits on the endpoints that cost money, reach a payment gateway, or answer a question
worth automating — the Pesepay callback (open to the internet, and each hit makes us call the
gateway), checkout, wallet-prompt resends, status polling, sign-up, username availability, and
event registration. Throttled callers get a **429 with `Retry-After`**, not the 403 the library
raises by default: a gateway retries one and gives up on the other.

## Deploying to Railway

The repo carries everything Railway needs: a `Procfile`, `railway.json`, gunicorn, and a
`/healthz` probe that fails if the database is unreachable — so a container that can't reach
Postgres is pulled out of rotation instead of serving 500s.

**1. Push the code**

```bash
railway login
railway init
railway up
```

**2. Add Postgres — this is not optional**

```bash
railway add --database postgres
```

Railway's filesystem is rebuilt on every deploy. On SQLite, every ticket, payment and account
would vanish the next time you push. The Postgres service sets `DATABASE_URL`, which
`settings.py` picks up on its own.

**3. Set the variables**

| Variable | Value |
| --- | --- |
| `DJANGO_SECRET_KEY` | a fresh 50-character random string — never the development one |
| `DJANGO_DEBUG` | `False` |
| `PESEPAY_INTEGRATION_KEY` | from your Pesepay dashboard |
| `PESEPAY_ENCRYPTION_KEY` | from your Pesepay dashboard |
| `PESEPAY_CODE_ECOCASH` | `PZW211` |
| `PESEPAY_CODE_INNBUCKS` | `PZW212` |
| `PESEPAY_CODE_ONEMONEY` | leave unset unless your account offers it |
| `ECOCASH_MERCHANT_NUMBER` | the wallet direct transfers go to |
| `PLATFORM_FEE_PERCENT` | `10` — what we keep per ticket. **Set it before the first sale**: it is frozen onto each payment as it settles, so a society's first statement is written under whatever this said that day |
| `EMAIL_HOST` etc. | **not optional.** Confirming an address gates registering and claiming a society, so with no SMTP nobody but you can create one |
| `DJANGO_MEDIA_ROOT` | `/data/media` if you mount a volume (see below) |

Generate the secret key with:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

`ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` need no attention — Railway injects
`RAILWAY_PUBLIC_DOMAIN` and the settings trust it automatically. Add `DJANGO_ALLOWED_HOSTS`
and `DJANGO_CSRF_TRUSTED_ORIGINS` only when you attach your own domain.

**4. Confirm the gateway from the deployed instance**

```bash
railway run python manage.py check_pesepay
```

This matters more in production than locally: Pesepay's callback has to reach you over the
public internet, which it never could on `localhost`. Once deployed, payments confirm
themselves without waiting for the browser to poll.

**5. Seed, if you want the demo content**

```bash
railway run python manage.py seed_demo
railway run python manage.py createsuperuser
```

### Uploaded images

Posters and society logos are written to `MEDIA_ROOT`, which is **ephemeral unless you mount a
volume** — otherwise every image disappears on the next deploy while the database still points
at it, and the site fills with broken thumbnails.

The better answer is object storage, which survives a deploy without a volume and takes that
traffic off the web workers entirely:

```bash
AWS_STORAGE_BUCKET_NAME=varsity-events-media
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com   # R2; omit for AWS
```

Set a bucket and `DJANGO_SERVE_MEDIA` defaults to off, because Django then has no reason to serve
uploads itself. The volume route below still works and is what the rest of this section covers.

```bash
railway volume add --mount-path /data
```

Then set `DJANGO_MEDIA_ROOT=/data/media`. Two things are worth knowing, both learned the hard way:

- **The pre-deploy container does not mount the volume.** Running `seed_demo` there generates
  images onto a filesystem that is thrown away. Seed the database there if you like, but push
  the files separately: `railway volume files upload media /media --overwrite`.
- Django serves media itself (`DJANGO_SERVE_MEDIA`, on by default), because there is no
  separate web server in front of it. Note that `django.conf.urls.static.static()` **silently
  returns an empty list when `DEBUG` is False**, so `varsity/urls.py` wires the route by hand.
  That's fine at campus scale and wants a CDN beyond it.

### Other notes for production

- Refunds are recorded (`Payment.mark_refunded`, plus a Django admin action) but not *issued* —
  they're processed in the Pesepay merchant dashboard. Automating that against Pesepay's refund
  API is the natural next step.
- `.env` is gitignored and must stay that way. Production credentials belong in Railway's
  variables, never in the repo.
