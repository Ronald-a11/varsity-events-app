"""Populate the database with a term's worth of activity across Zimbabwe's universities.

    python manage.py seed_demo --reset
"""

import random
import shutil
from datetime import timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.imagegen import make_banner, make_cover, make_logo

from accounts.models import University, User
from events.models import (
    Bookmark,
    Category,
    Event,
    EventUpdate,
    Registration,
    Review,
    TicketOutlet,
    TicketStatus,
    Venue,
)
from organizations.models import Membership, Organization
from payments.models import Payment

# (name, short name, kind, city, province)
UNIVERSITIES = [
    ("University of Zimbabwe", "UZ", "state", "Harare", "Harare"),
    ("National University of Science and Technology", "NUST", "state", "Bulawayo", "Bulawayo"),
    ("Midlands State University", "MSU", "state", "Gweru", "Midlands"),
    ("Chinhoyi University of Technology", "CUT", "state", "Chinhoyi", "Mashonaland West"),
    ("Great Zimbabwe University", "GZU", "state", "Masvingo", "Masvingo"),
    ("Harare Institute of Technology", "HIT", "state", "Harare", "Harare"),
    ("Bindura University of Science Education", "BUSE", "state", "Bindura", "Mashonaland Central"),
    ("Lupane State University", "LSU", "state", "Lupane", "Matabeleland North"),
    ("Africa University", "AU", "church", "Mutare", "Manicaland"),
    ("Zimbabwe Open University", "ZOU", "state", "Harare", "Harare"),
    ("Women's University in Africa", "WUA", "private", "Harare", "Harare"),
    ("Solusi University", "Solusi", "church", "Bulawayo", "Matabeleland North"),
    ("Catholic University of Zimbabwe", "CUZ", "church", "Harare", "Harare"),
    ("Manicaland State University of Applied Sciences", "MSUAS", "state", "Mutare", "Manicaland"),
    ("Gwanda State University", "GSU", "state", "Gwanda", "Matabeleland South"),
    ("Marondera University of Agricultural Sciences and Technology", "MUAST", "state", "Marondera", "Mashonaland East"),
    ("Zimbabwe Ezekiel Guti University", "ZEGU", "church", "Bindura", "Mashonaland Central"),
    ("Reformed Church University", "RCU", "church", "Masvingo", "Masvingo"),
]

CATEGORIES = [
    ("Tech & Innovation", "💻", "brand", "Hackathons, demo nights and build sessions"),
    ("Careers", "💼", "azure", "Fairs, panels and employer drop-ins"),
    ("Music & Arts", "🎵", "violet", "Gigs, open mics, exhibitions and theatre"),
    ("Sports", "⚽", "emerald", "Trials, fixtures, tournaments and fitness"),
    ("Academic", "📚", "amber", "Guest lectures, seminars and research days"),
    ("Social", "🎉", "rose", "Mixers, galas, games nights and dinners"),
    ("Volunteering", "🤝", "teal", "Drives, clean-ups and community work"),
    ("Wellness", "🧘", "orange", "Mental health, fitness and mindfulness"),
]

# (venue, university short name, capacity)
VENUES = [
    ("Great Hall", "UZ", 900),
    ("New Lecture Theatre 400", "UZ", 400),
    ("Rugby Field, Sports Pavilion", "UZ", 2000),
    ("Student Union Grounds", "UZ", 1500),
    ("NUST Amphitheatre", "NUST", 700),
    ("Innovation Hub, Block 5", "NUST", 150),
    ("MSU Main Auditorium", "MSU", 800),
    ("Batanai Hall", "MSU", 350),
    ("CUT Technology Park", "CUT", 220),
    ("GZU Mashava Campus Hall", "GZU", 500),
    ("HIT Innovation Centre", "HIT", 180),
    ("BUSE Astra Hall", "BUSE", 400),
    ("Africa University Chapel Grounds", "AU", 1200),
    ("Solusi Assembly Hall", "Solusi", 600),
    ("LSU Community Hall", "LSU", 300),
    ("MSUAS Applied Sciences Block", "MSUAS", 250),
]

# (name, kind, university, tagline, verified)
ORGANIZATIONS = [
    ("UZ Computer Science Society", "society", "UZ", "We build, break and ship things together.", True),
    ("UZ Students Executive Council", "union", "UZ", "Representing every student, running the big nights.", True),
    ("UZ Debate Union", "society", "UZ", "Argue well, lose gracefully, win often.", True),
    ("NUST Engineering Students Association", "faculty", "NUST", "The home of every engineering student at NUST.", True),
    ("NUST Innovation Club", "club", "NUST", "From lab bench to launched product.", True),
    ("MSU Music Collective", "club", "MSU", "Open mics, gigs and studio sessions every term.", False),
    ("MSU Athletics Club", "sports", "MSU", "Track, field and everything in between.", True),
    ("CUT Entrepreneurship Hub", "club", "CUT", "From hostel-room idea to first paying customer.", True),
    ("GZU Cultural Society", "society", "GZU", "Mbira, marimba, poetry and the stories behind them.", True),
    ("HIT Robotics Society", "society", "HIT", "If it moves and we built it, we're happy.", True),
    ("BUSE Environmental Action", "club", "BUSE", "Tree planting, clean-ups and climate advocacy.", False),
    ("Africa University Rotaract", "club", "AU", "Service above self, one weekend at a time.", True),
    ("Solusi Health & Wellness Circle", "club", "Solusi", "Peer support, wellness circles and quiet spaces.", False),
    ("LSU Agricultural Society", "faculty", "LSU", "Field days, livestock shows and agronomy talks.", False),
    ("UZ Film & Photography Society", "society", "UZ", "Screenings, shoots and darkroom nights.", False),
    ("NUST Women in STEM", "society", "NUST", "Mentorship, workshops and a very loud network.", True),
]

# (title, category, society, summary, description, tags, capacity,
#  is_free, price, days_ahead, hours, venue)
EVENTS = [
    ("Zim Varsity Hackathon 2026", "Tech & Innovation", "UZ Computer Science Society",
     "36 hours, free food and USD 1,000 in prizes.",
     "Teams of up to four, building something that makes student life in Zimbabwe better. "
     "Mentors from Harare's startup scene float through all weekend, hardware is provided, and "
     "Sunday's demo session is open to everyone.\n\n"
     "Bring a laptop, a charger and a blanket if you're staying overnight. Teams from other "
     "universities are very welcome — travel bursaries available for the first ten out-of-Harare entries.",
     "hackathon, coding, startups, prizes", 120, True, 0, 3, 36, "Innovation Hub, Block 5"),

    ("National Tech Careers Fair", "Careers", "CUT Entrepreneurship Hub",
     "Internships, graduate roles and CV clinics under one roof.",
     "Thirty employers, one afternoon — banks, telcos, mining houses, NGOs and startups, all "
     "sending hiring managers rather than recruiters. Bring printed CVs; the clinic in the side "
     "room will pull yours apart constructively before you hand it over.\n\n"
     "Open to students from every university. Bring your student ID.",
     "careers, internships, hiring, cv", 400, True, 0, 9, 6, "CUT Technology Park"),

    ("UZ vs NUST: The Varsity Derby", "Sports", "MSU Athletics Club",
     "Two years of trash talk ends on Saturday.",
     "The biggest fixture on the inter-varsity calendar. Gates open an hour early, the marching "
     "band plays at half time, and the winning university keeps the shield until next season.\n\n"
     "Student tickets are cheaper with a valid ID. Bring your scarf.",
     "football, derby, rivalry, sports", 2000, False, 3, 5, 3, "Rugby Field, Sports Pavilion"),

    ("Open Mic Night: Term Finale", "Music & Arts", "MSU Music Collective",
     "Twelve slots, five minutes each, one very loud room.",
     "Sign up on the night from 18:00 — poetry, comedy, acoustic sets, mbira, anything goes. "
     "The house band backs anyone who needs it. Doors at 18:00, first act at 19:00.",
     "music, open mic, poetry, live", 350, False, 2, 2, 4, "Batanai Hall"),

    ("The Great Debate: AI and the Future of Work", "Academic", "UZ Debate Union",
     "Four speakers, one motion, no easy answers.",
     "This house believes automation will make graduate degrees obsolete. Two teams, British "
     "Parliamentary format, judged by a panel from the Faculty of Law and the Computer Science "
     "department. The audience vote decides the winner.",
     "debate, ai, future of work", 400, True, 0, 12, 3, "New Lecture Theatre 400"),

    ("Freshers' Welcome Night 2026", "Social", "UZ Students Executive Council",
     "The one night everyone talks about for the rest of the year.",
     "Live DJ sets until midnight, food stalls on the grounds, and society tables where you can "
     "sign up for everything in one go. First-years get in free with a student ID; everyone else pays at the gate.",
     "freshers, party, social, music", 1500, False, 5, 16, 6, "Student Union Grounds"),

    ("Blood Drive & Community Health Day", "Volunteering", "Africa University Rotaract",
     "Twenty minutes of your time, someone else's whole year.",
     "The National Blood Service sets up on the chapel grounds from 09:00. Free health screening, "
     "juice and a biscuit afterwards, and a certificate if your faculty is competing for the donation shield.",
     "volunteering, health, blood drive", None, True, 0, 7, 8, "Africa University Chapel Grounds"),

    ("Exam Season Wellness Circle", "Wellness", "Solusi Health & Wellness Circle",
     "A quiet hour before the storm.",
     "A facilitated peer-support session ahead of finals week. No pressure to speak — plenty of "
     "people come just to sit. Tea afterwards, and a counsellor on hand for anyone who wants to talk privately.",
     "wellness, mental health, exams", 40, True, 0, 4, 2, "Solusi Assembly Hall"),

    ("Zimbabwe Student Film Festival", "Music & Arts", "UZ Film & Photography Society",
     "Fourteen student shorts, one red carpet.",
     "A year of student filmmaking from six universities, screened properly for once. Directors "
     "do a Q&A after each block, and the audience award is decided by ballot on the way out.",
     "film, screening, festival", 900, False, 4, 20, 5, "Great Hall"),

    ("National Moot Court Final", "Academic", "UZ Debate Union",
     "The closest thing to the real bench you'll see before graduation.",
     "Two finalist teams argue a constitutional matter before a panel of practising advocates. "
     "Open to all students — first-years thinking about law should come and watch.",
     "law, moot, competition", 400, True, 0, 14, 4, "New Lecture Theatre 400"),

    ("Intro to Machine Learning Workshop", "Tech & Innovation", "NUST Innovation Club",
     "Four hours from zero to your first trained model.",
     "Hands-on and laptop-required. We cover the maths only where it's unavoidable and spend the "
     "rest of the time in notebooks. Python basics assumed; everything else is taught.",
     "machine learning, python, workshop", 60, True, 0, 6, 4, "Innovation Hub, Block 5"),

    ("Engineering Design Expo", "Academic", "NUST Engineering Students Association",
     "Final-year projects, judged by industry.",
     "Every final-year group shows their project to a panel of practising engineers from across "
     "the country. Robotics, water systems, structures and power — plus a public vote for the crowd favourite.",
     "engineering, expo, projects", 700, True, 0, 11, 7, "NUST Amphitheatre"),

    ("Startup Pitch Night", "Careers", "CUT Entrepreneurship Hub",
     "Eight teams, five minutes each, real investors watching.",
     "Pitch to a panel of angel investors and alumni founders. The winner takes a USD 2,500 grant "
     "and three months of mentorship. Even if you're not pitching, come for the Q&A.",
     "startups, pitching, investors", 220, False, 2, 4, 4, "CUT Technology Park"),

    ("Campus Clean-Up Saturday", "Volunteering", "BUSE Environmental Action",
     "Two hours, gloves provided, breakfast after.",
     "We split into teams by zone and work through the grounds, halls of residence and the stream "
     "behind the science block. Wear closed shoes. Breakfast in Astra Hall when we're done.",
     "volunteering, environment, community", 80, True, 0, 10, 3, "BUSE Astra Hall"),

    ("Mbira & Marimba Cultural Night", "Music & Arts", "GZU Cultural Society",
     "The old instruments, played properly, very loud.",
     "Traditional ensembles from four universities, a poetry interlude in Shona and Ndebele, and "
     "an open floor at the end for anyone who can hold a rhythm. Food stalls outside from 17:00.",
     "culture, mbira, marimba, music", 500, False, 2, 8, 4, "GZU Mashava Campus Hall"),

    ("Robotics Showcase & Line-Follower Race", "Tech & Innovation", "HIT Robotics Society",
     "Sixteen robots, one track, plenty of collisions.",
     "Teams race line-following robots they built this term, then the showcase opens: drones, "
     "robotic arms and one very ambitious autonomous wheelbarrow. Free entry for HIT students.",
     "robotics, engineering, competition", 180, True, 0, 6, 5, "HIT Innovation Centre"),

    ("Women in STEM Mentorship Breakfast", "Careers", "NUST Women in STEM",
     "Thirty mentors, ninety students, one very good breakfast.",
     "Round-table mentoring with women working in engineering, mining, software and research "
     "across Zimbabwe. Rotate tables every fifteen minutes. Bring questions, not a CV.",
     "mentorship, women in stem, careers", 90, False, 2, 13, 3, "NUST Amphitheatre"),

    ("Inter-Varsity Athletics Trials", "Sports", "MSU Athletics Club",
     "Making the national student squad starts here.",
     "Open trials for sprints, middle distance, jumps and throws. Register on the day from 07:00, "
     "warm-up at 07:30, first heats at 08:00. Bring your own spikes if you have them.",
     "athletics, trials, track", 300, True, 0, 1, 5, "Rugby Field, Sports Pavilion"),

    # --- Varsity Gigs: the entertainment end of the calendar -----------------
    ("Battle of the Bands: Semi-Final", "Music & Arts", "MSU Music Collective",
     "Eight campus bands, two make the final.",
     "Two rounds, forty minutes each, judged by a panel from the national radio "
     "stations and decided partly on crowd noise. Bring your faculty scarf and shout.\n\n"
     "Bar and food stalls open from 17:00. Over-18s only after 21:00.",
     "gig, live music, bands, competition", 600, False, 3, 6, 5, "Batanai Hall"),

    ("Amapiano Night: End of Term", "Social", "UZ Students Executive Council",
     "Three DJs, one very long set, no seats.",
     "The last night before everyone scatters for the holidays. Resident DJs from 20:00, "
     "headline set at 23:00, doors close at 01:00 and nobody gets back in.\n\n"
     "Student ID at the gate. Tickets are cheaper before the day.",
     "gig, amapiano, dj, party", 900, False, 6, 8, 6, "Student Union Grounds"),

    ("Acoustic Sundowner on the Lawn", "Music & Arts", "MSU Music Collective",
     "Four acoustic sets as the sun goes down.",
     "Blankets on the grass, four singer-songwriters, and the marimba ensemble to close. "
     "Starts at 16:30 so the last set finishes in the dark.",
     "gig, acoustic, live music, chill", 250, True, 0, 2, 4, "Student Union Grounds"),

    ("Jazz & Poetry: Late Session", "Music & Arts", "GZU Cultural Society",
     "A quartet, six poets, and a very late finish.",
     "The house quartet plays between sets while poets read. Signup sheet on the door "
     "for anyone who wants five minutes at the mic.",
     "gig, jazz, poetry, live music", 180, False, 2, 11, 4, "GZU Mashava Campus Hall"),

    ("Album Launch: Campus Sessions Vol. 3", "Music & Arts", "UZ Film & Photography Society",
     "The compilation launch, played live front to back.",
     "Every artist on this year's student compilation plays their track live, in order. "
     "Physical copies and download codes at the door.",
     "gig, album launch, live music", 400, False, 4, 17, 4, "Great Hall"),

    ("Field Day: Smallholder Irrigation", "Academic", "LSU Agricultural Society",
     "Out in the fields, not in a lecture theatre.",
     "A working demonstration of low-cost drip irrigation on the university farm, run with farmers "
     "from the surrounding district. Transport leaves the LSU gate at 08:00 sharp.",
     "agriculture, field day, irrigation", 120, True, 0, 15, 6, "LSU Community Hall"),

    ("Applied Sciences Research Symposium", "Academic", "MSUAS Applied Sciences Block",
     "Postgraduate research, presented in plain language.",
     "Twelve postgraduate researchers present in ten minutes each, with a prize for the clearest "
     "explanation. Undergraduates thinking about postgraduate study should come.",
     "research, symposium, postgraduate", 250, True, 0, 18, 5, "MSUAS Applied Sciences Block"),
]

# (title, category, society, summary, days_ago, hours, venue, capacity)
PAST_EVENTS = [
    ("Semester Kick-Off Mixer", "Social", "UZ Students Executive Council",
     "Where half of this year's friendships started.", -21, 4, "Student Union Grounds", 900),
    ("Web Development Bootcamp", "Tech & Innovation", "UZ Computer Science Society",
     "A weekend of HTML, CSS and finally understanding flexbox.", -14, 8, "Innovation Hub, Block 5", 60),
    ("Charity Fun Run 10K", "Sports", "Africa University Rotaract",
     "Rain, mud and USD 1,800 raised for the children's ward.", -30, 4, "Africa University Chapel Grounds", 400),
    ("Guest Lecture: Constitutional Reform", "Academic", "UZ Debate Union",
     "A packed hall for a genuinely contested topic.", -7, 2, "New Lecture Theatre 400", 400),
]

# Ticket outlets attached to paid events. (kind, name, detail, price_note, available)
OUTLET_POOL = [
    ("campus", "SRC Offices, Student Union Building", "Weekdays 09:00–16:00, cash or swipe", "USD 3 students / USD 5 general", True),
    ("campus", "Faculty notice desk", "Mondays and Wednesdays, 10:00–14:00", "USD 3 students", True),
    ("phone", "EcoCash — dial *151# and use merchant code", "Confirmation SMS is your receipt", "USD 3", True),
    ("online", "Buy online", "Instant e-ticket, shown on your phone at the gate", "USD 5", True),
    ("door", "On the door", "Cash only, subject to space on the night", "USD 6 on the door", True),
    ("partner", "Book Café, Harare", "Tuesday to Saturday, 10:00–18:00", "USD 5", True),
    ("partner", "Bulawayo Theatre box office", "Weekdays 09:00–17:00", "USD 5", True),
]

FIRST_NAMES = [
    "Tendai", "Rutendo", "Tafadzwa", "Chiedza", "Farai", "Nyasha", "Tanaka", "Rumbidzai",
    "Takudzwa", "Anesu", "Kudzai", "Munashe", "Simbarashe", "Vimbai", "Tinashe", "Panashe",
    "Nokuthula", "Sibusiso", "Thandeka", "Bongani", "Nomsa", "Mthokozisi", "Lindiwe", "Sipho",
    "Blessing", "Grace", "Tapiwa", "Ropafadzo", "Shamiso", "Learnmore",
]
LAST_NAMES = [
    "Moyo", "Ncube", "Sibanda", "Dube", "Chikwanha", "Mutasa", "Marufu", "Gwenzi",
    "Nyoni", "Chirwa", "Madziva", "Mangwiro", "Zvobgo", "Muchena", "Bhebhe", "Mpofu",
    "Chigumba", "Makoni", "Zhou", "Mabhena",
]
COURSES = [
    "BSc Computer Science", "LLB Law", "BSc Civil Engineering", "BSc Economics",
    "BSc Nursing Science", "BCom Accounting", "BA Media Studies", "BSc Actuarial Science",
    "BSc Agriculture", "BEd Sciences", "BSc Biotechnology", "BSc Psychology",
    "BEng Electronic Engineering", "BSc Mining Engineering", "BA Development Studies",
]
REVIEW_COMMENTS = [
    "Genuinely well organised — started on time, which never happens.",
    "Great turnout and the speakers actually knew their stuff.",
    "Solid event. The venue was a bit tight for the numbers though.",
    "Best thing I've been to all semester. Do it again next term.",
    "Good content, but it ran over by almost an hour.",
    "Loved it. Met three people I'm now working on a project with.",
    "Worth the trip from Gweru. The Q&A was the strongest part.",
]


class Command(BaseCommand):
    help = "Seed demo universities, societies, events and registrations across Zimbabwe."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete existing data first.")
        parser.add_argument("--students", type=int, default=80, help="Student accounts to create.")
        parser.add_argument(
            "--no-images",
            action="store_true",
            help="Skip generating banners and logos (much faster, plainer cards).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        random.seed(20260816)  # stable output across runs
        self.with_images = not options["no_images"]

        if options["reset"]:
            self.stdout.write("Clearing existing data…")
            Payment.objects.all().delete()
            Review.objects.all().delete()
            Bookmark.objects.all().delete()
            Registration.objects.all().delete()
            EventUpdate.objects.all().delete()
            TicketOutlet.objects.all().delete()
            Event.objects.all().delete()
            Membership.objects.all().delete()
            Organization.objects.all().delete()
            Venue.objects.all().delete()
            Category.objects.all().delete()
            User.objects.filter(is_superuser=False).delete()
            University.objects.all().delete()

            # Generated art is disposable; clear it so reseeding doesn't pile up.
            for folder in ("events/banners", "orgs/logos", "orgs/covers"):
                shutil.rmtree(settings.MEDIA_ROOT / folder, ignore_errors=True)

        universities = self._create_universities()
        categories = self._create_categories()
        venues = self._create_venues(universities)
        organizers = self._create_organizers(universities)
        students = self._create_students(universities, categories, options["students"])
        organizations = self._create_organizations(universities, organizers)
        self._create_memberships(organizations, students)
        events = self._create_events(organizations, categories, venues, organizers)
        past = self._create_past_events(organizations, categories, venues, organizers)
        self._create_outlets(events)
        self._create_registrations(events, students)
        self._create_past_registrations(past, students)
        self._create_payments(events)
        if self.with_images:
            self._create_images(organizations, events + past)
        self._create_bookmarks(events, students)
        self._create_updates(events, organizers)
        self._create_admin(universities)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Demo data ready."))
        self.stdout.write(
            f"  {University.objects.count()} universities · "
            f"{Organization.objects.count()} societies · "
            f"{Event.objects.count()} events · "
            f"{TicketOutlet.objects.count()} ticket outlets · "
            f"{User.objects.count()} users · "
            f"{Registration.objects.count()} registrations"
        )
        self.stdout.write("")
        self.stdout.write(self.style.WARNING("Sign in with any of these (password: demo12345):"))
        self.stdout.write("  admin      — platform staff: event curation + Django admin")
        self.stdout.write("  organizer  — runs several societies")
        self.stdout.write("  student    — a UZ student with tickets and saved events")

    # -- builders -------------------------------------------------------

    def _create_universities(self):
        universities = {}
        for name, short, kind, city, province in UNIVERSITIES:
            uni, _ = University.objects.get_or_create(
                name=name,
                defaults={
                    "short_name": short,
                    "kind": kind,
                    "city": city,
                    "province": province,
                },
            )
            universities[short] = uni
        self.stdout.write(f"Universities: {len(universities)}")
        return universities

    def _create_categories(self):
        categories = {}
        for name, icon, color, description in CATEGORIES:
            category, _ = Category.objects.get_or_create(
                name=name, defaults={"icon": icon, "color": color, "description": description}
            )
            categories[name] = category
        self.stdout.write(f"Categories: {len(categories)}")
        return categories

    def _create_venues(self, universities):
        venues = {}
        for name, uni_short, capacity in VENUES:
            uni = universities[uni_short]
            venue, _ = Venue.objects.get_or_create(
                name=name,
                defaults={
                    "university": uni,
                    "capacity": capacity,
                    "address": f"{uni.name}, {uni.city}",
                },
            )
            venues[name] = venue
        self.stdout.write(f"Venues: {len(venues)}")
        return venues

    def _create_organizers(self, universities):
        organizers = []
        specs = [
            ("organizer", "Rutendo", "Moyo", "rutendo@uz.ac.zw", "UZ"),
            ("tmutasa", "Tendai", "Mutasa", "tmutasa@nust.ac.zw", "NUST"),
            ("nsibanda", "Nokuthula", "Sibanda", "nsibanda@msu.ac.zw", "MSU"),
            ("fchirwa", "Farai", "Chirwa", "fchirwa@cut.ac.zw", "CUT"),
            ("bdube", "Blessing", "Dube", "bdube@africau.edu", "AU"),
        ]
        for username, first, last, email, uni_short in specs:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "email": email,
                    "role": User.Role.ORGANIZER,
                    "is_verified_organizer": True,
                    "university": universities[uni_short],
                    "course": random.choice(COURSES),
                    "bio": "Runs events, chases sponsors, sleeps occasionally.",
                },
            )
            if created:
                user.set_password("demo12345")
                user.save()
            organizers.append(user)
        self.stdout.write(f"Organizers: {len(organizers)}")
        return organizers

    def _create_students(self, universities, categories, count):
        students = []
        uni_list = list(universities.values())
        category_list = list(categories.values())

        demo, created = User.objects.get_or_create(
            username="student",
            defaults={
                "first_name": "Tanaka",
                "last_name": "Ncube",
                "email": "tanaka@students.uz.ac.zw",
                "role": User.Role.STUDENT,
                "university": universities["UZ"],
                "course": "BSc Computer Science",
                "year_of_study": 3,
                "student_id": "R2135678K",
                "bio": "Third-year CS at UZ. Here for the hackathons and the free food.",
            },
        )
        if created:
            demo.set_password("demo12345")
            demo.save()
            demo.interests.set(random.sample(category_list, 3))
        students.append(demo)

        for i in range(count):
            first = random.choice(FIRST_NAMES)
            last = random.choice(LAST_NAMES)
            username = f"{first[0].lower()}{last.lower()}{i:02d}"
            uni = random.choice(uni_list)
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "first_name": first,
                    "last_name": last,
                    "email": f"{username}@students.ac.zw",
                    "role": User.Role.STUDENT,
                    "university": uni,
                    "course": random.choice(COURSES),
                    "year_of_study": random.randint(1, 5),
                    "student_id": f"R{random.randint(20, 25)}{random.randint(10000, 99999)}{random.choice('BFHKMPT')}",
                },
            )
            if created:
                user.set_password("demo12345")
                user.save()
                user.interests.set(random.sample(category_list, random.randint(1, 3)))
            students.append(user)

        self.stdout.write(f"Students: {len(students)}")
        return students

    def _create_organizations(self, universities, organizers):
        organizations = {}

        for index, (name, kind, uni_short, tagline, verified) in enumerate(ORGANIZATIONS):
            uni = universities[uni_short]
            owner = organizers[index % len(organizers)]
            handle = "".join(c for c in name.lower() if c.isalnum())[:20]

            org, created = Organization.objects.get_or_create(
                name=name,
                defaults={
                    "kind": kind,
                    "tagline": tagline,
                    "description": (
                        f"{tagline}\n\n"
                        f"Based at {uni.name} in {uni.city}, we meet every other week during term "
                        f"and run several events a semester — everything from small workshops to the "
                        f"big nights on the calendar. Students from other universities are welcome at "
                        f"most of what we do; just check the event page."
                    ),
                    "university": uni,
                    "is_verified": verified,
                    "email": f"{handle}@{uni_short.lower()}.ac.zw",
                    "website": "https://societies.ac.zw",
                    "instagram": f"@{handle[:15]}",
                    "created_by": owner,
                },
            )
            if created:
                Membership.objects.create(
                    organization=org, user=owner, role=Membership.Role.OWNER, title="Chairperson"
                )
            organizations[name] = org

        self.stdout.write(f"Societies: {len(organizations)}")
        return organizations

    def _create_memberships(self, organizations, students):
        created = 0
        for org in organizations.values():
            for student in random.sample(students, random.randint(6, 18)):
                _, made = Membership.objects.get_or_create(
                    organization=org, user=student, defaults={"role": Membership.Role.MEMBER}
                )
                created += int(made)
            for follower in random.sample(students, random.randint(12, 40)):
                org.followers.add(follower)
        self.stdout.write(f"Memberships: {created}")

    def _create_events(self, organizations, categories, venues, organizers):
        now = timezone.now()
        events = []

        for spec in EVENTS:
            (title, category_name, org_name, summary, description, tags,
             capacity, is_free, price, days_ahead, duration_hours, venue_name) = spec

            org = organizations.get(org_name)
            if org is None:  # venue-named society in the fixture list
                org = list(organizations.values())[0]

            starts_at = (now + timedelta(days=days_ahead)).replace(
                hour=random.choice([9, 10, 14, 16, 18, 19]), minute=0, second=0, microsecond=0
            )

            event, created = Event.objects.get_or_create(
                title=title,
                defaults={
                    "summary": summary,
                    "description": description,
                    "organization": org,
                    "created_by": org.created_by or random.choice(organizers),
                    "category": categories[category_name],
                    "tags": tags,
                    "venue": venues[venue_name],
                    "starts_at": starts_at,
                    "ends_at": starts_at + timedelta(hours=duration_hours),
                    "registration_deadline": starts_at - timedelta(hours=2),
                    "capacity": capacity,
                    "is_free": is_free,
                    "price": price,
                    "currency": "USD",
                    "status": Event.Status.PUBLISHED,
                    "is_featured": days_ahead <= 6,
                    "views_count": random.randint(60, 1400),
                    "ticket_notes": ""
                    if is_free
                    else "Student price needs a valid student ID at the gate.",
                },
            )
            if created:
                events.append(event)

        # One event running right now, so the live board has a pulse and the
        # check-in desk has somebody to scan the moment you open it.
        started = now - timedelta(hours=1)
        live_event, created = Event.objects.get_or_create(
            title="Careers Week: Employer Speed Networking",
            defaults={
                "summary": "Ten minutes each with fifteen employers. Running right now.",
                "description": (
                    "Rotating tables, ten minutes per employer, a bell every time you move. "
                    "Bring copies of your CV — most of them take them on the spot.\n\n"
                    "Latecomers welcome; join at whichever table has a free seat."
                ),
                "organization": organizations["CUT Entrepreneurship Hub"],
                "created_by": organizations["CUT Entrepreneurship Hub"].created_by,
                "category": categories["Careers"],
                "tags": "careers, networking, employers",
                "venue": venues["CUT Technology Park"],
                "starts_at": started,
                "ends_at": now + timedelta(hours=3),
                "registration_deadline": now + timedelta(hours=2),
                "capacity": 220,
                "is_free": True,
                "currency": "USD",
                "status": Event.Status.PUBLISHED,
                "is_featured": True,
                "views_count": random.randint(400, 900),
            },
        )
        if created:
            events.append(live_event)

        # A draft, so the dashboards have something to show.
        starts_at = (now + timedelta(days=25)).replace(hour=17, minute=0, second=0, microsecond=0)
        Event.objects.get_or_create(
            title="End of Year Gala Dinner",
            defaults={
                "summary": "Black tie, awards and the last night before everyone scatters.",
                "description": "Still confirming the venue and the caterer — details to follow.",
                "organization": organizations["UZ Students Executive Council"],
                "created_by": organizations["UZ Students Executive Council"].created_by,
                "category": categories["Social"],
                "tags": "gala, awards, formal",
                "venue": venues["Great Hall"],
                "starts_at": starts_at,
                "ends_at": starts_at + timedelta(hours=5),
                "capacity": 400,
                "is_free": False,
                "price": 15,
                "currency": "USD",
                "status": Event.Status.DRAFT,
            },
        )

        # One explicitly sold out and one withdrawn, so both states are visible.
        if events:
            sold_out = next((e for e in events if not e.is_free), events[0])
            sold_out.ticket_status = TicketStatus.SOLD_OUT
            sold_out.save(update_fields=["ticket_status"])

            withdrawn = next(
                (e for e in events if e.pk != sold_out.pk and not e.is_free), events[-1]
            )
            withdrawn.ticket_status = TicketStatus.UNAVAILABLE
            withdrawn.ticket_notes = "Sales paused while we move to a bigger venue — back on Monday."
            withdrawn.save(update_fields=["ticket_status", "ticket_notes"])

            self.stdout.write(
                f"  marked sold out: {sold_out.title}\n  marked unavailable: {withdrawn.title}"
            )

        self.stdout.write(f"Upcoming events: {len(events)} (+1 draft)")
        return events

    def _create_past_events(self, organizations, categories, venues, organizers):
        now = timezone.now()
        past = []

        for title, category_name, org_name, summary, days_ago, duration, venue_name, cap in PAST_EVENTS:
            org = organizations[org_name]
            starts_at = (now + timedelta(days=days_ago)).replace(
                hour=random.choice([10, 15, 18]), minute=0, second=0, microsecond=0
            )
            event, created = Event.objects.get_or_create(
                title=title,
                defaults={
                    "summary": summary,
                    "description": f"{summary}\n\nThanks to everyone who came out — see you at the next one.",
                    "organization": org,
                    "created_by": org.created_by or random.choice(organizers),
                    "category": categories[category_name],
                    "venue": venues[venue_name],
                    "starts_at": starts_at,
                    "ends_at": starts_at + timedelta(hours=duration),
                    "capacity": cap,
                    "is_free": True,
                    "currency": "USD",
                    "status": Event.Status.PUBLISHED,
                    "views_count": random.randint(300, 2000),
                },
            )
            if created:
                past.append(event)

        self.stdout.write(f"Past events: {len(past)}")
        return past

    def _create_outlets(self, events):
        """Paid events get two or three sales points; some have already run dry."""
        made = 0
        for event in events:
            if event.is_free:
                continue
            for order, (kind, name, detail, price_note, _) in enumerate(
                random.sample(OUTLET_POOL, random.randint(2, 4))
            ):
                available = event.ticket_status != TicketStatus.SOLD_OUT and random.random() < 0.75
                _, created = TicketOutlet.objects.get_or_create(
                    event=event,
                    name=name,
                    defaults={
                        "kind": kind,
                        "detail": detail,
                        "price_note": price_note,
                        "url": "https://tickets.ac.zw" if kind == "online" else "",
                        "phone": f"+263 77 {random.randint(100, 999)} {random.randint(1000, 9999)}"
                        if kind == "phone"
                        else "",
                        "is_available": available,
                        "sort_order": order,
                    },
                )
                made += int(created)
        self.stdout.write(f"Ticket outlets: {made}")

    def _create_registrations(self, events, students):
        total = 0
        for event in events:
            ceiling = event.capacity or 120
            target = min(len(students), random.randint(int(ceiling * 0.3), int(ceiling * 1.1)))

            for student in random.sample(students, min(target, len(students))):
                confirmed = event.confirmed_registrations.count()
                status = (
                    Registration.Status.WAITLISTED
                    if event.capacity and confirmed >= event.capacity
                    else Registration.Status.CONFIRMED
                )
                _, created = Registration.objects.get_or_create(
                    event=event,
                    user=student,
                    defaults={
                        "status": status,
                        "notes": random.choice(
                            ["", "", "", "Vegetarian please", "I'll be about 15 minutes late",
                             "Travelling in from Gweru", "Wheelchair access needed"]
                        ),
                    },
                )
                total += int(created)

        self.stdout.write(f"Registrations: {total}")

    def _create_past_registrations(self, past_events, students):
        reviews = 0
        for event in past_events:
            attendees = random.sample(students, min(random.randint(30, 70), len(students)))
            for student in attendees:
                reg, created = Registration.objects.get_or_create(
                    event=event, user=student, defaults={"status": Registration.Status.CONFIRMED}
                )
                if created and random.random() < 0.78:
                    reg.checked_in_at = event.starts_at + timedelta(minutes=random.randint(-20, 45))
                    reg.checked_in_by = event.created_by
                    reg.save(update_fields=["checked_in_at", "checked_in_by"])

            for student in random.sample(attendees, min(6, len(attendees))):
                _, made = Review.objects.get_or_create(
                    event=event,
                    user=student,
                    defaults={
                        "rating": random.choices([5, 4, 3], weights=[5, 4, 1])[0],
                        "comment": random.choice(REVIEW_COMMENTS),
                    },
                )
                reviews += int(made)

        self.stdout.write(f"Reviews: {reviews}")

    def _create_payments(self, events):
        """Give paid events a realistic Paynow history: mostly settled, a few in flight."""
        methods = [
            Payment.Method.ECOCASH,
            Payment.Method.ECOCASH,
            Payment.Method.ECOCASH,  # EcoCash dominates in practice
            Payment.Method.ONEMONEY,
            Payment.Method.INNBUCKS,
            Payment.Method.WEB,
        ]
        made = 0

        for event in events:
            if event.is_free:
                continue

            confirmed = event.registrations.filter(
                status=Registration.Status.CONFIRMED
            ).select_related("user")

            for registration in confirmed:
                if Payment.objects.filter(registration=registration).exists():
                    continue

                method = random.choice(methods)
                paid_at = event.created_at + timedelta(
                    hours=random.randint(1, 240), minutes=random.randint(0, 59)
                )
                Payment.objects.create(
                    registration=registration,
                    user=registration.user,
                    amount=event.price,
                    currency=event.currency,
                    method=method,
                    phone=f"07{random.choice(['7', '8', '1'])}{random.randint(1000000, 9999999)}"
                    if method != Payment.Method.WEB
                    else "",
                    status=Payment.Status.PAID,
                    paynow_reference=f"PN{random.randint(1000000, 9999999)}",
                    paid_at=paid_at,
                    is_simulated=True,
                )
                made += 1

        self.stdout.write(f"Payments: {made} settled")

    def _create_images(self, organizations, events):
        """Draw banners, covers and logos so the cards look like the real thing.

        Generated rather than downloaded: it keeps the seed reproducible and
        offline, and every image is keyed off its subject so reseeding gives
        the same art back.
        """
        made = 0

        for org in organizations.values():
            colour = org.events.first().category.color if org.events.exists() and org.events.first().category else "brand"

            if not org.logo:
                org.logo.save(
                    f"{org.slug}-logo.png",
                    ContentFile(make_logo(org.initials, org.slug, colour).read()),
                    save=False,
                )
                made += 1
            if not org.cover:
                org.cover.save(
                    f"{org.slug}-cover.jpg",
                    ContentFile(make_cover(org.slug, colour).read()),
                    save=False,
                )
                made += 1
            org.save()

        for event in events:
            if event.banner:
                continue
            colour = event.category.color if event.category else "brand"
            event.banner.save(
                f"{event.slug}-banner.jpg",
                ContentFile(make_banner(event.slug, colour).read()),
                save=True,
            )
            made += 1

        self.stdout.write(f"Images generated: {made}")

    def _create_bookmarks(self, events, students):
        total = 0
        for student in students:
            for event in random.sample(events, min(random.randint(0, 5), len(events))):
                _, created = Bookmark.objects.get_or_create(user=student, event=event)
                total += int(created)
        self.stdout.write(f"Bookmarks: {total}")

    def _create_updates(self, events, organizers):
        samples = [
            ("Venue moved", "We've outgrown the original room — we're now in the main hall. "
                            "Same time, same day, just follow the signs."),
            ("Schedule confirmed", "Doors open 30 minutes before the start. Registration desk is "
                                   "at the main entrance; have your ticket code ready."),
            ("Extra tickets released", "We've opened another block at the SRC offices. First come, "
                                       "first served — they went quickly last time."),
            ("Waitlist moving", "A block of places just opened up — if you're on the waitlist, "
                                "check your tickets page, you may already be confirmed."),
        ]
        made = 0
        for event in random.sample(events, min(6, len(events))):
            title, body = random.choice(samples)
            _, created = EventUpdate.objects.get_or_create(
                event=event,
                title=title,
                defaults={"body": body, "author": event.created_by or random.choice(organizers)},
            )
            made += int(created)
        self.stdout.write(f"Announcements: {made}")

    def _create_admin(self, universities):
        # A superuser whose password is published in this file is a convenience
        # locally and a way in anywhere else. Deployments make their own with
        # `createsuperuser`; this one never leaves a developer's machine.
        if not settings.DEBUG:
            self.stdout.write(
                self.style.WARNING(
                    "Skipped the demo 'admin' superuser: DEBUG is off, so this "
                    "looks like a real deployment. Use createsuperuser instead."
                )
            )
            return

        if not User.objects.filter(username="admin").exists():
            User.objects.create_superuser(
                username="admin",
                email="admin@varsityevents.co.zw",
                password="demo12345",
                first_name="Platform",
                last_name="Admin",
                role=User.Role.STAFF,
                university=universities["UZ"],
            )
            self.stdout.write("Superuser 'admin' created (development only).")
