# SIT Timetable System

A constraint-based academic timetable scheduling system for SIT's engineering
cluster. Teaching requirements are imported or maintained in the system, and a
Google OR-Tools CP-SAT solver assigns every class session a room and a time slot
that satisfies a set of hard constraints (rules that can never be broken) while
optimising a set of weighted soft constraints (preferences). The result can be
reviewed, corrected, and exported in the institution's required Template 2
format.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, Flask 3.x |
| Database | SQLite (file-based, bundled in `database/`), SQLAlchemy ORM |
| Solver | Google OR-Tools CP-SAT |
| Frontend | Bootstrap 5, Bootstrap Icons, Jinja2 |
| Auth | Flask-Login (session-based, role-based access) |
| Security | Flask-WTF (CSRF protection on every form) |
| Import / export | openpyxl, pandas, xlrd |
| Calendar | `holidays` (Singapore public holidays) |
| Email | Flask-Mail (Gmail SMTP for demo) |
| AI summary | Anthropic API (optional) |

---

## Setup Instructions

### Step 1 — Download the project

```bash
git clone https://github.com/Brian-Goh-JW/timetable-system.git
cd timetable-system
```

### Step 2 — Create and activate a virtual environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

> You should see `(venv)` at the start of your prompt once it is active.

### Step 3 — Install dependencies

```bash
pip install -r requirements.txt
```

### Step 4 — The database

No database server, installation, or password is required. The application uses
SQLite, and the database file ships with the project at
`database/timetable.db`. It is created and read automatically, so there is
nothing to set up for this step.

### Step 5 — Configure environment variables (optional)

`config.py` is committed to Git and contains configuration logic only, with no
secrets. The database needs no configuration. The remaining values are read
from environment variables and are only needed if you want email notifications
or the timetable-summary feature:

```powershell
$env:FLASK_SECRET_KEY = 'replace-with-a-long-random-value'
$env:MAIL_USERNAME = 'your.email@gmail.com'
$env:MAIL_PASSWORD = 'your-app-password'
$env:MAIL_DEFAULT_SENDER = 'your.email@gmail.com'
$env:ADMIN_EMAIL = 'your.email@gmail.com'
```

If `FLASK_SECRET_KEY` is not set, a random key is generated per run, which
simply means login sessions do not survive a restart. `ANTHROPIC_API_KEY` is
optional and needed only for the timetable-summary feature. `FLASK_DEBUG=1` may
be set for local debugging; debug mode is off by default.

`DATABASE_URL` may be set to point at a different database (for example a MySQL
server) if one is ever needed, but this is not required.

**Getting a Gmail App Password:** enable 2FA on your Google account, then create
an app password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
and copy the 16-character code into `MAIL_PASSWORD`.

> **Why Gmail instead of SIT email?** Microsoft 365 SMTP AUTH is disabled for SIT
> student accounts by institutional policy, so Gmail with an App Password is used
> for demo purposes.

### Step 6 — Start the application

The bundled `database/timetable.db` already contains the full working dataset
(programmes, courses, sessions, professors, rooms, and generated timetables), so
you can go straight to Step 8 and run the app.

### Step 7 — Seed accounts and load data (only for a fresh, empty database)

Skip this step unless you are starting from an empty database. To build one from
scratch, first create the tables:

```bash
python -c "from app import create_app, db; app = create_app(); app.app_context().push(); db.create_all()"
```

The `bootstrap/` folder then contains numbered scripts that seed accounts, apply
schema migrations, and load the project's datasets. They are designed to run in
numeric order, and each is idempotent where possible (re-running a completed
migration is safe). The essential ones are:

```powershell
# Create the admin account
$env:SEED_ADMIN_PASSWORD = 'a-strong-temporary-password'
python bootstrap/1_seed_admin.py

# (Optional) create a test student account
$env:SEED_STUDENT_PASSWORD = 'a-strong-temporary-password'
python bootstrap/4_seed_student.py
```

The remaining scripts (`2_*` onward) apply incremental schema migrations and
load the engineering-cluster datasets from the supplied Excel files. They are
specific to this project's source data and file paths, and record the full
history of how the dataset was built. Data can also be created and maintained
through the admin interface once the app is running (see Feature Guide).

### Step 8 — Start the application

```bash
python run.py
```

Open **http://127.0.0.1:5000/login** in your browser. Keep the terminal open
while using the app; closing it stops the server.

---

## Running the Tests

```bash
python -m unittest discover -s tests -v
```

The suite covers the scheduling logic most likely to produce an invalid
timetable if broken: shared-module collapsing, fixed-session overlap handling,
effective group size for split cohorts, parent and subgroup relationships, and
the readiness checks that block generation when a synchronous session has no
student group. The tests run against an isolated in-memory database, so they
need no configuration and never touch the bundled `database/timetable.db`.

---

## Demo Accounts

Demo credentials are deployment-specific and are not stored in the repository.
Create the administrator and optional student accounts with the seed environment
variables above. Imported professor accounts receive random passwords and must
be assigned individual credentials by an administrator before distribution.

---

## Feature Guide

### Admin

- **Import** teaching requirements from Excel (Template 1), with validation and
  error reporting, or create and maintain data directly.
- **Manage data** — full create / edit / delete plus bulk import and export for
  professors, rooms, courses, student groups, students, time slots, and calendar
  events, each with search and filtering.
- **Fixed sessions** — mark a session's room and time as fixed so the solver
  never moves it; every other session is scheduled around it.
- **Generate** a timetable for a chosen trimester (e.g. `AY2526-T1`) with the
  CP-SAT solver.
- **Review** the result as a list or a weekly grid, with any clash flagged
  directly on screen; compare schedules across trimesters for consistency.
- **Correct** a single entry or a full run of weeks, with non-overlap re-checked
  before the change is saved. All manual edits are recorded in the audit log.
- **Constraint settings** — adjust the weight of every soft constraint through a
  settings page, without touching code.
- **Constraint reference** — inspect every hard and soft constraint in effect.
- **Export** the schedule in the institution's Template 2 format (export is
  blocked while any unresolved hard conflict remains), or a full weekly Excel
  view.
- **Reports & oversight** — a per-run scheduling report, a system-status page
  disclosing data-quality gaps and assumptions, and an audit log of admin
  actions.

### Teaching staff

- View their own schedule as a weekly grid.
- Declare their own availability for a trimester.

### Student

- View their own cohort's schedule as a list or weekly grid.

---

## The Constraint Model

**Hard constraints** define whether a timetable is valid. They cover professor,
room, and student-group non-overlap (compared by actual wall-clock start and end
time, not time-slot label); room type and capacity; fixed timeslots and rooms;
strict professor availability; online-vs-in-person room rules; odd/even week
patterns; public-holiday and term-break exclusion; a guaranteed daily lunch
window; university-wide module day separation; cross-programme shared-module
equality; and named institutional windows (no classes before 09:00 or after
18:00, none on Saturday, Wednesday afternoon blocked, Friday 12:00–14:00
protected, and no Friday class ending after 17:00).

**Soft constraints** rank valid schedules by preference. They include avoiding
online/in-person mode switches back-to-back, professor idle gaps, excessive
consecutive teaching hours, spreading a group's classes across too many days,
under-utilised or over-sized rooms, first/last slots of the day, late finishes,
venue inconsistency, and clustering a programme's online classes onto one day.
Each carries a weight editable through the admin settings page.

The solver runs 8 parallel search workers within a 400-second budget and returns
one of three statuses: **Optimal** (a valid schedule proven best under the
current weights), **Feasible** (a valid schedule found within the budget without
that proof), or **Infeasible** (no valid schedule exists within the current
scope). A Feasible schedule satisfies every hard constraint exactly as an Optimal
one does.

---

## Database Schema

| Table | Purpose |
|-------|---------|
| `users` | Login accounts (admin / professor / student) |
| `professors` | Professor profiles linked to users |
| `programmes` | Degree programmes |
| `courses` | Module catalogue per programme and trimester |
| `class_sessions` | Individual teaching sessions per course |
| `class_session_professors` | Session-to-professor links (supports co-teaching) |
| `student_groups` | Cohort groups (e.g. DSC-Y1) |
| `shared_module_groups` | Links sessions that are one class taught under several codes |
| `timeslots` | SIT period blocks (P1–P4, Lab AM/PM/EV, etc.) |
| `rooms` | Teaching venues with type and capacity |
| `timetable_entries` | Generated schedule (one row per session per week) |
| `solve_runs` | Per-trimester record of each generation run's status and stats |
| `solver_settings` | Editable soft-constraint weights |
| `academic_calendar` | Week dates and term-break flags |
| `events` | Calendar events and blocked dates |
| `availability_declarations` | Professor unavailability submissions |
| `timetable_flags` | Conflict notifications |
| `flag_responses` | Professor responses to flags |
| `audit_logs` | Manual edit and admin-action history |

---

## Project Structure

```
timetable-system/
├── app/
│   ├── engine/
│   │   ├── solver.py            # CP-SAT timetable generator (hard + soft constraints)
│   │   ├── checker.py           # Pre-generation validation and blocking checks
│   │   └── template1_parser.py  # Template 1 (Excel) import parsing
│   ├── models/                  # SQLAlchemy ORM models (one file per table)
│   ├── routes/
│   │   ├── admin.py             # Admin blueprint (/admin/*)
│   │   ├── teacher.py           # Professor blueprint (/teacher/*)
│   │   ├── student.py           # Student blueprint (/student/*)
│   │   └── auth.py              # Login / logout
│   ├── templates/               # Jinja2 templates (admin/, teacher/, student/, auth/)
│   └── utils/
│       └── email.py             # Flask-Mail notifications
├── bootstrap/                   # Numbered one-time setup, migration, and data-load scripts
├── database/
│   └── timetable.db             # SQLite database (no server or password needed)
├── tests/                       # Unit tests (python -m unittest discover -s tests)
├── config.py                    # Configuration (env-var driven, no secrets)
├── requirements.txt             # Python dependencies
├── run.py                       # App entry point
└── README.md
```

---

## Operational Scope

The system holds data for the engineering cluster's 16 degree programmes. Each
generation run schedules a group of programmes whose shared-professor
connections allow them to be solved together, selected to meet the required
minimum of 20 programme-year schedules. Data for every programme remains in the
database regardless of which group a given run covers; scope is a property of
each run, set through a per-session flag, not a limit on what the system stores.

---

## Group Members

DSC2204 Integrative Team Project — Group 8
Singapore Institute of Technology
