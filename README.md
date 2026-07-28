# SIT Timetable System

A Flask application for generating and viewing SIT academic timetables. It
uses Google OR-Tools to enforce hard constraints such as room capacity,
professor availability, student-group clashes, fixed sessions, term breaks,
and public holidays.

The repository includes a working SQLite database with programmes, courses,
rooms, users, and published AY2526 T1-T3 timetables. No database server is
needed for the normal demo.

## Quick start

Requirements: Git and Python 3.12 or newer.

```bash
git clone https://github.com/Brian-Goh-JW/timetable-system.git
cd timetable-system
python -m venv venv
```

Activate the virtual environment:

```powershell
# Windows PowerShell
venv\Scripts\Activate.ps1
```

```bash
# macOS or Linux
source venv/bin/activate
```

Install and run:

```bash
python -m pip install -r requirements.txt
python run.py
```

Open <http://127.0.0.1:5000/login>. Keep the terminal open while using the
application.

## Log in and try it

### Student demo

- Email: `student@sit.edu.sg`
- Password: `Test1234`

This account is assigned to `DSC-Y1`. Try its T1, T2, and T3 list and weekly
timetable views.

`Test1234` is deliberately a local demo password. Do not deploy the bundled
database on a public server with this password. To replace it locally:

```powershell
$env:SEED_STUDENT_PASSWORD = 'choose-a-new-local-password'
python bootstrap/4_seed_student.py
```

### Administrator

Choose your own local admin password before signing in:

```powershell
$env:SEED_ADMIN_PASSWORD = 'choose-a-strong-local-password'
python bootstrap/1_seed_admin.py
```

Then log in as `admin@sit.edu.sg` with the password you chose.

Useful things to try as an administrator:

1. Open **Timetable** and browse the published AY2526 T1-T3 schedules.
2. Select a trimester and run **Generate Timetable**. Generation can take a
   few minutes.
3. If the hard-constraint guard finds invalid input, review its popup. It
   blocks the write, so the current published timetable stays safe.
4. Switch between list and weekly views and inspect the scheduling report.
5. Export the result as Template 2 or the weekly Excel view.
6. Use **Import / Export** on Modules, Professors, Students, Groups, and Rooms
   to try validated bulk Excel updates.
7. Assign a temporary password to a professor, then sign in as that professor
   to try the teaching timetable and availability pages.

Generation is atomic by complete programme. It never saves half a programme or
half a course. Mathematically impossible input cannot be forced into a valid
timetable; it is blocked or a complete infeasible programme is omitted and
reported.

## Data imports

The bundled database is ready to use. From the administrator account you can
import teaching requirements from Template 1 and maintain courses, sessions,
rooms, professors, and student groups.

The numbered files under `bootstrap/` document historical migrations and data
loads. They are not needed for the quick-start demo. Some old loaders require
source spreadsheets that are not included; run them only when you understand
their purpose and have supplied the requested path through environment
variables.

## Security and optional services

The default local demo does not use Gmail or MySQL:

- SQLite is read from `database/timetable.db`; it has no server password.
- Email delivery is disabled when `MAIL_USERNAME` or `MAIL_PASSWORD` is blank.
- MySQL is disabled unless `MYSQL_HOST` is set.
- The AI summary is disabled unless `ANTHROPIC_API_KEY` is set.
- `.env`, `.env.local`, `*.env`, and database backups are ignored by Git.

Do not put real passwords in source files. For optional email, use a dedicated
Gmail App Password, never the normal Gmail account password:

```powershell
$env:MAIL_USERNAME = 'demo-sender@example.com'
$env:MAIL_PASSWORD = 'app-password-from-your-secret-store'
$env:MAIL_DEFAULT_SENDER = 'demo-sender@example.com'
$env:ADMIN_EMAIL = 'demo-admin@example.com'
```

MySQL is optional. If used, create a least-privilege application account and
provide its values through the environment or a hosting secret manager:

```powershell
$env:MYSQL_HOST = '127.0.0.1'
$env:MYSQL_PORT = '3306'
$env:MYSQL_USER = 'timetable_app'
$env:MYSQL_PASSWORD = 'password-from-your-secret-store'
$env:MYSQL_DATABASE = 'timetable_db'
```

For an internet-facing deployment, use HTTPS and set:

```powershell
$env:APP_ENV = 'production'
$env:FLASK_SECRET_KEY = 'a-long-random-value-from-your-secret-store'
$env:SESSION_COOKIE_SECURE = 'true'
$env:HSTS_ENABLED = 'true'
```

Production startup fails when `FLASK_SECRET_KEY` is missing. Security headers,
CSRF protection, HTTP-only cookies, upload limits, and login throttling are
enabled by the application.

See `.env.example` for the complete list of optional variable names and safe
defaults. It contains no credentials.

## Run the tests

```bash
python -m unittest discover -s tests -v
```

The tests use an isolated in-memory database and do not modify the bundled
`database/timetable.db`.

## Main technology

- Flask, Flask-Login, Flask-WTF, and SQLAlchemy
- SQLite by default; optional MySQL through PyMySQL
- Google OR-Tools CP-SAT
- pandas, openpyxl, and xlrd for Excel import/export
- Bootstrap and Jinja templates

## Project layout

```text
app/           application, solver, routes, templates, and models
bootstrap/     setup, migration, and historical data-load scripts
database/      bundled SQLite database
tests/         automated regression tests
config.py      environment-driven configuration; no committed secrets
run.py         local application entry point
```

DSC2204 Integrative Team Project - Group 8, Singapore Institute of Technology.
