# Image credits

Where every image in this directory came from, and what it may be used for.
Keep it current: a stock photo whose provenance nobody wrote down is a stock
photo somebody eventually has to remove in a hurry.

---

## `hero-festival.jpg` — the homepage hero, currently in use

| | |
| --- | --- |
| **Source** | [Pexels](https://www.pexels.com/photo/34015049/) (photo 34015049) |
| **Photographer** | Tswegha |
| **Licence** | [Pexels License](https://www.pexels.com/license/) — free, commercial use allowed, no attribution required |
| **Downloaded** | 2026-08-21, resized to 2400px wide and re-encoded at q80 |
| **Scene** | An outdoor music festival in Casablanca, Morocco |

**Why this one and not the others.** The subject is a young woman photographed
**from behind**. She is plainly the subject and plainly a Black African
student-aged woman, and her face is not visible — so there is no identifiable
person in the frame and no model release in question. Everyone else is a
distant, anonymous crowd.

That distinction is the whole reason this file exists. A stock licence grants
you the *photographer's* rights, never the *subject's*. Neither Pexels nor
Unsplash provides model releases. A photograph of recognisable faces used to
advertise a commercial product is exactly the case where you would need one.

**It is Morocco, not Zimbabwe.** Replace it with a photograph of a real event on
this platform as soon as one exists. A site whose whole argument is that it
tells students the truth should not be illustrated with somebody else's
country indefinitely.

## `hero-universities.jpg` — the drawn alternative

`python manage.py make_hero`. Eighteen universities in the eight category
colours, each arcing into one hub. Committed even while the photograph is in
use, because switching back is one line in `core/templates/core/home.html` and
should not require a Pillow install.

## The icons

`icon-192.png`, `icon-512.png`, `icon-maskable-512.png`, `apple-touch-icon.png`
— all from `python manage.py make_icons`. The event banners, society logos and
covers under `media/` come from `core/imagegen.py` via `seed_demo`.

Generated art is reproducible, costs nothing, carries no licence to honour and
produces the same bytes on every run, so a rebuild is never a spurious diff.
Prefer it where it will do.

---

## Rejected, and why — so nobody re-adds them

**A concert crowd** (ActionVance, Unsplash) — legally fine and used briefly. It
said *music night* about a platform that is also careers fairs, moot courts and
blood drives.

**A street scene in Nairobi** (Hassan Kibwana, Unsplash, June 2024) — free
licence, so the photographer's rights were not the problem. Two men are in
sharp focus and identifiable, and the photograph is tagged *protest* and
*justice*: it is documentary work from the Kenyan Finance Bill demonstrations,
in which people died. Presenting those men as happy customers at a ticketed gig
misrepresents both them and this platform. Not a licensing question — a
question about them.

**Burundi football supporters, Burundian drummers** (Pexels) — excellent
photographs, every face sharp and identifiable, and neither scene is a campus
event.
