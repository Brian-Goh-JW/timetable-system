# SIT Timetable System

A Flask timetable application for administrators, professors, and students. It generates complete programme timetables with Google OR-Tools CP-SAT, checks hard constraints before and after generation, and keeps published schedules separate from drafts.

## Quick start

Install Git and Python 3.12 or newer, then run:

```bash
git clone https://github.com/Brian-Goh-JW/timetable-system.git
cd timetable-system
python -m venv venv
```

Activate the environment:

```powershell
# Windows PowerShell
venv\Scripts\Activate.ps1
```

```bash
# macOS or Linux
source venv/bin/activate
```

Install, create the database, load synthetic demo data, and start:

```bash
python -m pip install -r requirements.txt
flask --app wsgi.py db upgrade
python bootstrap/seed_demo.py
python run.py
```

Open <http://127.0.0.1:5000/login>.

## Demo accounts

The synthetic seed creates no real people or institutional records.

| Role | Email | Password |
|---|---|---|
| Administrator | `admin@example.com` | `Test1234` |
| Professor | `professor@example.com` | `Test1234` |
| Student | `student@example.com` | `Test1234` |

These credentials are only for a local demo. The seed refuses to run when `APP_ENV=production` and refuses to overwrite a non-empty database. Passwords can be changed before seeding with `DEMO_ADMIN_PASSWORD`, `DEMO_PROFESSOR_PASSWORD`, and `DEMO_STUDENT_PASSWORD`.

### Sample account for the populated project database

If you were given the populated project `timetable.db` separately, use these accounts to view already-published timetables:

| Role | Email | Password | Published timetable sample |
|---|---|---|---|
| Professor | `desmond.chong@sit.edu.sg` | `Test1234` | `SBE1101` and `ASE1011` |
| Student | `student@sit.edu.sg` | `Test1234` | Student group `DSC-Y1` |

After signing in, open **My Timetable**, select **AY25/26 Tri 1**, and view **Week 1**. The professor account has published `SBE1101` classes in Week 1, while the student account shows the published DSC-Y1 timetable.

The populated database is intentionally not included in the public GitHub ZIP because operational databases may contain institutional data. A fresh GitHub download uses the synthetic `@example.com` accounts above after running `python bootstrap/seed_demo.py`.

## What to try

- Generate or re-generate AY2526-T1 and review the hard-constraint guard.
- Change a class manually and use ranked repair suggestions.
- Preview an Excel import before applying it. Modules, professors, students, student enrolments/sections, groups, and rooms support bulk data workflows.
- Submit a professor availability request, review it under **Manage > Teacher Availability**, and follow any generated exception under **Schedule Responses**.
- Set room closures, qualifications, workload limits, equipment, and accessibility requirements.
- Sign in as the demo student or professor and download the timetable as an `.ics` calendar file.
- Review the audit trail, scheduling report, and system-information pages.

Generation is atomic by complete programme batch. A batch is saved only when all its in-scope synchronous modules have sessions and the resulting entries pass the final hard-constraint audit. If one programme has invalid data, valid independent programmes may still be generated; excluded programmes and reasons are reported.

## Hard and soft constraints

Hard constraints can never be traded away. They include professor, student-group, and room clashes; room type/capacity/equipment/accessibility; fixed placements; teaching weeks; institutional hours and lunch windows; holidays and scoped events; term breaks; availability; staff qualifications; and configured workload limits.

Soft constraints guide the best valid result, such as preferred slots, continuity with a historical timetable, compact teaching patterns, and reduced room changes. The scheduling report shows penalties separately from hard-constraint compliance.

## How AI was applied

The project keeps timetable optimisation and generative AI separate:

1. **Timetable generation uses mathematical constraint optimisation.** Google OR-Tools CP-SAT searches for a schedule satisfying every hard constraint and minimising weighted soft penalties. It is not a language model, does not call an external service, and does not train on timetable data.
2. **Generative AI is optional and descriptive only.** If an administrator explicitly enables `EXTERNAL_SUMMARY_ENABLED=true`, supplies `ANTHROPIC_API_KEY`, and clicks **Generate Summary**, the system can turn aggregate timetable statistics into a short plain-language summary. It does not create, move, approve, or publish classes.
3. **AI-assisted development was used as a review aid.** It helped trace constraint paths, identify edge cases, propose defensive checks, and expand automated tests and documentation. Final behaviour is defined by the source code, database constraints, test suite, and administrator decisions.

The optional summary request contains the term key, module codes, aggregate session/type/week counts, up to ten room codes, and a professor count. It does **not** include names, email addresses, student records, passwords, password hashes, availability reasons, or login data. The feature is disabled by default, scheduling works without it, and failed summary requests do not affect a timetable.

## Database and migrations

SQLite is the default and needs no server password. Operational `.db` files and backups are intentionally ignored by Git; each installation owns its data.

Apply schema changes after every pull:

```bash
flask --app wsgi.py db upgrade
```

Create a recoverable SQLite backup:

```bash
flask --app wsgi.py backup-database
```

Backups are stored under `database/backups/` and the command retains the 14 newest copies. For MySQL, use the database platform's managed backup and point-in-time recovery features.

The numbered scripts under `bootstrap/` are historical data loaders and repairs. They are not part of a fresh setup. Use only `bootstrap/seed_demo.py` for the public synthetic demo unless you understand a historical script and provide its source data yourself.

## Security and secrets

No Gmail, database, API, or real user password is stored in the repository.

- SQLite is used when `MYSQL_HOST` and `DATABASE_URL` are blank.
- Gmail is inactive when `MAIL_USERNAME` or `MAIL_PASSWORD` is blank. If enabled, use a dedicated App Password, never a normal Gmail password.
- MySQL is inactive unless selected through environment variables. Use a least-privilege database account.
- The external summary is inactive unless both its enable flag and API key are supplied.
- `.env`, `*.env`, SQLite databases, and backups are ignored by Git.
- The tracked Template 2 workbook contains formatting, headers, and validation lists only. Staff, course, room, and timetable lookup rows are rebuilt from the local database for each export.
- Admin state changes are audited. CSRF protection, secure headers, HTTP-only cookies, upload limits, account deactivation, one-time password resets, and database-backed login throttling are enabled.

Copy variable names from `.env.example` into your operating system or deployment secret manager. Never commit a populated `.env` file.

For an internet-facing deployment, at minimum set:

```powershell
$env:APP_ENV = 'production'
$env:FLASK_SECRET_KEY = 'a-long-random-secret-from-your-secret-manager'
$env:SESSION_COOKIE_SECURE = 'true'
$env:HSTS_ENABLED = 'true'
$env:SOLVER_RUN_IN_WEB_PROCESS = 'false'
```

Production startup fails if `FLASK_SECRET_KEY` is absent. Put the app behind HTTPS and a trusted reverse proxy. Trusted-proxy SSO is available only when `TRUSTED_SSO_ENABLED=true` and `SSO_SHARED_SECRET` is configured; it never creates accounts automatically.

## Production processes

On Windows, start the web process with Waitress:

```powershell
waitress-serve --listen=127.0.0.1:8000 wsgi:app
```

Run one or more persistent solver workers separately:

```powershell
flask --app wsgi.py solver-worker
```

Generation jobs, progress, cancellation, results, and errors are stored in the database, so they remain visible across web workers and restarts. Use `GET /healthz` for database-aware health monitoring.

## Tests

```bash
python -m unittest discover -s tests -v
```

Tests use an isolated in-memory database and do not modify local operational data. GitHub Actions runs compilation, regression tests, and dependency consistency checks on every push and pull request.

## Main components

- Flask, Flask-Login, Flask-WTF, Flask-Mail, Flask-Migrate, and SQLAlchemy
- SQLite by default; optional MySQL through PyMySQL
- Google OR-Tools CP-SAT for constraint optimisation
- pandas, openpyxl, and xlrd for Excel workflows
- Optional Anthropic client for aggregate plain-language summaries
- Waitress production WSGI server

```text
app/          application, constraints, routes, services, models, and templates
bootstrap/    synthetic seed plus historical migration/data scripts
migrations/   Alembic schema migrations
tests/        isolated regression and solver tests
config.py     environment-driven settings
run.py        local development entry point
wsgi.py       production and Flask CLI entry point
```

DSC2204 Integrative Team Project - Group 8, Singapore Institute of Technology.
