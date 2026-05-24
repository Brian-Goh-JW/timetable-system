# SIT Timetable System

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, Flask 3.x |
| Database | MySQL 8, SQLAlchemy ORM |
| Solver | Google OR-Tools CP-SAT |
| Frontend | Bootstrap 5, Bootstrap Icons, Jinja2 |
| Email | Flask-Mail (Gmail SMTP for demo) |
| Auth | Flask-Login |

---

## Setup Instructions

### Step 1 — Download the project

Open a terminal (VS Code → Terminal → New Terminal) and run:

```bash
git clone https://github.com/Brian-Goh-JW/timetable-system.git
cd timetable-system
```

> This downloads the project files to your machine and moves into the project folder.

---

### Step 2 — Create a virtual environment

Still in the same terminal:

```bash
python -m venv venv
```

Then activate it:

```bash
# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

> A virtual environment keeps project dependencies isolated from your system Python. You should see `(venv)` appear at the start of your terminal prompt once it's active.

---

### Step 3 — Install dependencies

```bash
pip install flask flask-sqlalchemy flask-login flask-mail pymysql ortools pandas openpyxl
```

> This installs all the Python packages the app needs to run. It may take a minute.

---

### Step 4 — Create the MySQL database

Open **MySQL Workbench** (or any MySQL client) and run:

```sql
CREATE DATABASE timetable_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

> This creates a blank database that the app will write its tables into. You only need to do this once.

---

### Step 5 — Create the config file

In VS Code, create a new file called `config.py` in the **project root** (same folder as `run.py`).

> This file holds your database password and email credentials. It is listed in `.gitignore` and will never be committed to GitHub.

Paste this template and fill in your own values:

```python
class Config:
    SECRET_KEY = 'your-secret-key'
    SQLALCHEMY_DATABASE_URI = 'mysql+pymysql://root:yourpassword@127.0.0.1/timetable_db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Flask-Mail — Gmail SMTP (see note below)
    MAIL_SERVER   = 'smtp.gmail.com'
    MAIL_PORT     = 587
    MAIL_USE_TLS  = True
    MAIL_USE_SSL  = False
    MAIL_USERNAME = 'your.email@gmail.com'
    MAIL_PASSWORD = 'your-app-password'   # 16-character Gmail App Password
    MAIL_DEFAULT_SENDER = ('SIT Timetable System', 'your.email@gmail.com')

    # Admin inbox — receives "cannot proceed" notifications from professors
    ADMIN_EMAIL = 'your.email@gmail.com'
```

**Getting a Gmail App Password:**
1. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) (requires 2FA to be enabled on your Google account first)
2. Create a new app password with any name (e.g. "SIT Timetable")
3. Copy the 16-character code and paste it as `MAIL_PASSWORD`

> **Why Gmail instead of SIT email?** Microsoft 365 SMTP AUTH is disabled for SIT student accounts by institutional policy, so the SIT email cannot send emails programmatically. Gmail with an App Password is used as a workaround for demo purposes.

---

### Step 6 — Create the database tables

Back in your VS Code terminal (make sure `(venv)` is still active), run:

```bash
python -c "from app import create_app, db; app = create_app(); app.app_context().push(); db.create_all()"
```

> This tells SQLAlchemy to read all the model definitions and create the corresponding tables inside `timetable_db`. Run this once before any data is loaded.

---

### Step 7 — Run the bootstrap scripts (in order)

These one-time scripts populate the database with initial data.

| Script | What it does |
|--------|-------------|
| `seed_admin.py` | Creates the admin login account in the `users` table |
| `excel_loader.py` | Loads courses, rooms, professors, student groups, and timeslots from Excel/CSV into the database |
| `add_fixed_timeslot.py` | Adds `fixed_timeslot_id` column to `class_sessions` (for pinning sessions to a specific slot) |
| `add_manual_edit_columns.py` | Adds `override_professor_id` to `timetable_entries` and creates the `audit_logs` table |
| `add_flag_deadline.py` | Adds `response_deadline` and `notification_sent` columns to `timetable_flags` |
| `fix_role_enum.py` | Updates `users.role` ENUM to include `'professor'` — only needed if the column was created without it |
| `seed_student.py` | Creates a test student login account in the `users` table |

Run them in this order:

```bash
# 1. Create the admin account
python bootstrap/seed_admin.py

# 2. Load DSC programme data (courses, rooms, professors, student groups)
#    Open bootstrap/excel_loader.py first and update the file paths to your Excel/CSV files
python bootstrap/excel_loader.py

# 3. Apply schema additions (run each once)
python bootstrap/add_fixed_timeslot.py
python bootstrap/add_manual_edit_columns.py
python bootstrap/add_flag_deadline.py

# 4. (Optional) Create a test student account
python bootstrap/seed_student.py
```

> Each script prints a confirmation message when done. If a script says the record already exists, that's fine — it means you have already run it before.

---

### Step 8 — Start the application

```bash
python run.py
```

Then open your browser and go to: **http://127.0.0.1:5000/login**

> The app runs locally on your machine. Keep the terminal open while using the app — closing it stops the server.

---

## Demo Accounts

| Role | Email | Password |
|------|-------|----------|
| **Admin** | admin@sit.edu.sg | Admin1234! |
| **Professor** | braingohjw@gmail.com | David123! |
| **Student** | student@sit.edu.sg | Student1234! |

The professor account is David Lin Weidong (Staff ID: A100909).  
The student account is a generic test account — select a student group on the My Timetable page.

---

## Feature Guide

### Admin Workflow

#### 1. Set up courses and sessions
- Go to **Courses** → select a course → **Sessions**
- Assign a professor and student group to each session
- Set a **Fixed Slot** if the course must always fall on a specific day/time (e.g. industry engagement events)

#### 2. Manage professors and rooms
- Go to **Professors** → Add / Edit professors and set their temporary passwords
- Go to **Rooms** → Add / Edit teaching venues

#### 3. Classify availability declarations
- Go to **Declarations** → review what professors have submitted
- Classify each as **Strict** (the solver will never assign that slot) or **Preferred** (the solver avoids it but may use it if necessary)

#### 4. Generate the timetable
- Go to **Timetable** → enter a Trimester Code (e.g. `2025-T3`) and the Week 1 Start Date
- Click **Run CP-SAT Solver**
- After it finishes, check the summary: how many strict constraints were applied and how many preferred violations occurred
- If there are preferred violations, a conflict report appears listing the affected sessions

#### 5. Handle conflict flags
- Go to **Flags** → flags are automatically created for any preferred violations
- Set a response deadline and click **Notify** to email the professor
- The professor logs in, reviews the flag, and responds either "Can Proceed" or "Cannot Proceed"
- If they say "Cannot Proceed", the admin receives an email to arrange next steps

#### 6. Manual editing
- Go to **Timetable** → click the ✏️ pencil icon on any session → **View All Weeks**
- You can change the timeslot, room, or professor for individual weeks
- The system checks for double-booking conflicts on save
- Use **Force Save** to override a warning if needed
- All manual edits are recorded in the **Audit Log**

#### 7. Publish the timetable
- Go to **Timetable** → click **Publish**
- Once published, professors and students can see their timetable when they log in

#### 8. View modes
- **List View** — a table showing the recurring weekly slot for each session
- **Weekly View** — a calendar grid (Mon–Sun) for a specific week, with navigation arrows

---

### Professor Workflow

1. Log in → the **Dashboard** shows your assigned sessions, any pending declarations, and open flags
2. Go to **My Timetable** → view your published schedule in List or Weekly view
3. Go to **Availability** → submit dates/times you are unavailable, with a reason
   - The admin will classify your submission as Strict or Preferred
   - You can delete a pending declaration; once classified you cannot
4. Go to **My Flags** → respond to conflict notifications sent by the admin
   - **I can proceed** → the flag is resolved automatically
   - **I cannot proceed** → the admin is notified by email to arrange a substitute

---

### Student Workflow

1. Log in → the **Dashboard** shows published trimester information
2. Go to **My Timetable** → select your cohort group (e.g. DSC-Y1-A)
3. Toggle between **List View** and **Weekly View**
4. Click any session block to see full details

---

## Weekly View

The weekly calendar grid is available on all timetable pages (admin, professor, student).

| Colour | Session Type |
|--------|-------------|
| Green | Tutorial |
| Blue | Lecture |
| Orange | Lab |
| Purple | Seminar |
| Striped overlay | Online session |

- Use the **← / →** buttons or the **week dropdown** to move between weeks
- Weeks that fall in a term break show a warning banner and no sessions
- Click any session block to open a details popup
- The admin popup includes a direct **Edit** link to the week editor for that session

---

## Database Schema

| Table | Purpose |
|-------|---------|
| `users` | Login accounts (admin / professor / student) |
| `professors` | Professor profiles linked to users |
| `courses` | Module catalogue |
| `class_sessions` | Individual teaching sessions per course |
| `timeslots` | SIT period blocks (P1–P4, Lab AM/PM/EV) |
| `rooms` | Teaching venues |
| `timetable_entries` | Generated schedule (one row per session per week) |
| `academic_calendar` | Week dates and term break flags |
| `availability_declarations` | Professor unavailability submissions |
| `timetable_flags` | Conflict notifications |
| `flag_responses` | Professor responses to flags |
| `audit_logs` | Manual edit history |

---

## Project Structure

```
timetable-system/
├── app/
│   ├── engine/
│   │   ├── solver.py          # CP-SAT timetable generator
│   │   └── checker.py         # Pre-solve validation
│   ├── models/                # SQLAlchemy ORM models
│   ├── routes/
│   │   ├── admin.py           # Admin blueprint
│   │   ├── teacher.py         # Professor blueprint
│   │   ├── student.py         # Student blueprint
│   │   └── auth.py            # Login / logout
│   ├── templates/             # Jinja2 HTML templates
│   └── utils/
│       └── email.py           # Flask-Mail notifications
├── bootstrap/                 # One-time setup scripts
├── config.py                  # Credentials (gitignored)
├── run.py                     # App entry point
└── README.md
```

---

## Known Limitations

- **Single professor:** The DSC dataset only lists one teaching staff (David Lin Weidong), so all sessions are assigned to him. The system is built to handle multiple professors.
- **Student account creation:** Student accounts must be created manually via `bootstrap/seed_student.py`. An admin UI for managing student accounts is planned.
- **Email SMTP:** SIT Microsoft 365 SMTP AUTH is blocked for student accounts. Gmail App Password is used for the demo.
- **No mobile layout:** The app is designed for laptop/desktop screens. The weekly calendar grid uses horizontal scroll on smaller screens.

---

## Group Members

DSC2204 IT Project — Group 8  
Singapore Institute of Technology
