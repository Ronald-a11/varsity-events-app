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

- Register a society, invite members, promote admins
- Create events as drafts, then publish when ready
- List every ticket outlet and untick one the moment it runs out
- Override ticket status manually when tickets are sold somewhere the platform can't see
- Live dashboard with **money collected**, door check-in desk, announcements
- CSV export including payment method and gateway reference per attendee

**For platform staff**

- An in-app admin at `/staff/` covering every event at every university
- Filter by university, status, or whether it's been picked
- **Pick** events to lead the site, publish, unpublish, cancel, mark sold out, or delete
- Verify and suspend societies at `/staff/societies/`
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

135 tests covering registration and waitlist promotion, capacity and deadline enforcement, every
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

---

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

Setting `DJANGO_DEBUG=False` enables HSTS, SSL redirect, secure cookies, and WhiteNoise's
compressed manifest storage. Run `python manage.py collectstatic` before deploying.

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

Posters and society logos are written to `MEDIA_ROOT`, which is ephemeral by default — they
survive until the next deploy. For anything beyond a demo, mount a Railway volume and point
`DJANGO_MEDIA_ROOT` at it, or move to object storage. Django serves media itself
(`DJANGO_SERVE_MEDIA`, on by default) since there's no separate web server in front of it;
that's fine at campus scale and wants a CDN beyond it.

### Other notes for production

- Refunds are recorded (`Payment.mark_refunded`, plus a Django admin action) but not *issued* —
  they're processed in the Pesepay merchant dashboard. Automating that against Pesepay's refund
  API is the natural next step.
- `.env` is gitignored and must stay that way. Production credentials belong in Railway's
  variables, never in the repo.
