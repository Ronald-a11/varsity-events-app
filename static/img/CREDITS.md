# Image credits

**Nothing in this directory is third-party.** Every image here was drawn by
this repository and can be regenerated from source:

| File | Made by |
| --- | --- |
| `hero-universities.jpg` | `python manage.py make_hero` |
| `icon-192.png`, `icon-512.png`, `icon-maskable-512.png`, `apple-touch-icon.png` | `python manage.py make_icons` |

The event banners, society logos and covers under `media/` come from
`core/imagegen.py` by way of `seed_demo`, on the same principle.

Keep it that way where you can. Generated art is reproducible, costs nothing,
carries no licence to honour, and produces the same bytes on every run — so a
rebuild is never a spurious diff.

---

## If you do add a third-party image

Record it here before you commit it: source, creator, licence, and the date you
downloaded it. A stock photo whose provenance nobody wrote down is a stock
photo somebody eventually has to remove in a hurry.

## What used to be here

`hero-crowd.jpg` — a licensed Unsplash photograph of a concert crowd
(ActionVance, Unsplash License), used briefly as the homepage hero and removed
on 2026-08-21.

Two things were wrong with it, and both are worth remembering if the temptation
returns. It said *music night* about a platform that is also careers fairs,
moot courts, blood drives and robotics showcases. And it was somebody else's
campus standing in for a Zimbabwean one, on a site whose entire argument is
that it tells students the truth.

`make_hero` says the actual thing instead: eighteen universities, the eight
kinds of event they put on, all of it arriving in one place.
