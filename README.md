# SIT Timetable System
**DSC2204 IT Project — Group 8**  
Singapore Institute of Technology, Engineering Cluster  
Academic Year 2025/2026

---

## Project Overview

A web-based timetabling system for the DSC (Digital Supply Chain) programme at SIT. The system automates timetable generation using a CP-SAT constraint solver, manages professor availability declarations, handles conflict flags with email notifications, and provides role-based timetable views for admins, professors, and students.

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

### 1. Clone the repository
```bash
git clone https://github.com/Brian-Goh-JW/timetable-system.git
cd timetable-system
```

### 2. Create and activate virtual environment
```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate
```

### 3. Install dependencies
```bash
pip install flask flask-sqlalchemy flask-login flask-mail pymysql ortools pandas openpyxl
```

### 4. Create the database
In MySQL:
```sql
CREATE DATABASE timetable_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 5. Configure credentials
Create `config.py` in the project root (this file is gitignored — never commit it):
```python
class Config:
    SECRET_KEY = 'your-secret-key'
    SQLALCHEMY_DATABASE_URI = 'mysql+pymysql://root:yourpassword@127.0.0.1/timetable_db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Flask-Mail (Gmail App Password for demo)
    MAIL_SERVER   = 'smtp.gmail.com'
    MAIL_PORT     = 587
    MAIL_USE_TLS  = True
    MAIL_USE_SSL  = False
    MAIL_USERNAME = 'your.email@gmail.com'
    MAIL_PASSWORD = 'your-app-password'   # 16-char Gmail App Password
    MAIL_DEFAULT_SENDER = ('SIT Timetable System', 'your.email@gmail.com')

    # Admin inbox for "cannot proceed" notifications
    ADMIN_EMAIL = 'your.email@gmail.com'
```

> **Gmail App Password:** Go to myaccount.google.com/apppasswords (requires 2FA enabled) → create an app password → paste the 16-character code as MAIL_PASSWORD.

> **SIT SMTP note:** Microsoft 365 SMTP AUTH is disabled for student accounts by institutional policy. Gmail is used as a workaround for demo purposes. In production, a service account with SMTP AUTH enabled or a transactional email provider (e.g. SendGrid) would be used.

### 6. Initialise the database tables
```bash
python -c "from app import create_app, db; app = create_app(); app.app_context().push(); db.create_all()"
```

### 7. Run bootstrap scripts (in order)
```bash
# Create admin account
python bootstrap/seed_admin.py

# Load DSC programme data from Excel + CSV
# Edit the file paths in bootstrap/excel_loader.py first
python bootstrap/excel_loader.py

# Apply schema migrations (run once each)
python bootstrap/add_fixed_timeslot.py
python bootstrap/add_manual_edit_columns.py
python bootstrap/add_flag_deadline.py

# (Optional) Create a test student account
python bootstrap/seed_student.py
```

### 8. Start the application
```bash
python run.py
```
Open: http://127.0.0.1:5000/login

---

## Demo Accounts

| Role | Email | Password |
|------|-------|----------|
| **Admin** | admin@sit.edu.sg | Admin1234! |
| **Professor** | braingohjw@gmail.com | David123! |
| **Student** | student@sit.edu.sg | Student1234! |

> The professor account belongs to David Lin Weidong (Staff ID: A100909).  
> The student account is a generic test account — select a student group on the My Timetable page.

---

## Feature Guide

### Admin Workflow

#### 1. Set up courses and sessions
- **Courses** → select a course → **Sessions**
- Assign a professor and student group to each session
- Set a **Fixed Slot** if the course has a mandatory day/time (e.g. industry engagement events)

#### 2. Manage professors and rooms
- **Professors** → Add / Edit professors and set temporary passwords
- **Rooms** → Add / Edit teaching venues

#### 3. Classify availability declarations
- **Declarations** → review professor submissions
- Classify each as **Strict** (hard constraint — solver will never assign that slot) or **Preferred** (soft constraint — solver avoids if possible)

#### 4. Generate the timetable
- **Timetable** → enter Trimester Code (e.g. `2025-T3`) and Week 1 Start Date
- Click **Run CP-SAT Solver**
- Review the stats: Strict constraints applied, Preferred violations
- If preferred violations exist, a conflict report is shown with affected sessions

#### 5. Handle conflict flags
- **Flags** → view auto-created flags for preferred violations
- Set a response deadline and click **Notify** to email the professor
- Professor logs in, reviews the flag, and responds "Can Proceed" or "Cannot Proceed"
- If "Cannot Proceed", admin receives an email with next steps

#### 6. Manual editing
- **Timetable** → click the ✏️ pencil icon on any session → **View All Weeks**
- Edit individual weeks: change timeslot, room, or professor
- Conflict detection runs on save (warns on double-booking)
- Use **Force Save** to override warnings if needed
- All edits are logged in **Audit Log**

#### 7. Publish
- **Timetable** → click **Publish**
- Professors and students can now view their timetable

#### 8. View modes
- **List View** — traditional table showing recurring weekly slot per session
- **Weekly View** — calendar grid (Mon–Sun) showing a specific week, with week navigation

---

### Professor Workflow

1. Log in → **Dashboard** shows assigned sessions, pending declarations, open flags
2. **My Timetable** → view published schedule (List or Weekly view)
3. **Availability** → submit unavailability declarations with reason
   - Admin will classify as Strict or Preferred
   - Pending declarations can be deleted; classified ones cannot
4. **My Flags** → respond to conflict notifications from admin
   - **I can proceed** → flag resolved automatically
   - **I cannot proceed** → admin notified by email to arrange a substitute

---

### Student Workflow

1. Log in → **Dashboard** shows published trimester info
2. **My Timetable** → select your cohort group (e.g. DSC-Y1-A)
3. Toggle between **List View** and **Weekly View**
4. Click any session block to see full details

---

## Weekly View Guide

The weekly calendar grid is available on all timetable pages.

| Colour | Session Type |
|--------|-------------|
| 🟢 Green | Tutorial |
| 🔵 Blue | Lecture |
| 🟠 Orange | Lab |
| 🟣 Purple | Seminar |
| Striped | Online session |

- Use **← / →** buttons or the **week dropdown** to navigate between weeks
- Term break weeks show a warning banner
- Click any session block to open a **details popup**
- Admin popup includes an **Edit** link to the session week editor

---

## Database Schema (Key Tables)

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

- **Single professor:** All sessions are currently assigned to one professor (David Lin Weidong) as the DSC dataset only lists one teaching staff. The system fully supports multiple professors.
- **Student accounts:** Student accounts are created manually via `bootstrap/seed_student.py`. A self-registration or admin-managed student UI is planned.
- **Email SMTP:** SIT Microsoft 365 SMTP AUTH is blocked for student accounts. Gmail App Password is used for the demo.
- **No mobile layout:** Designed for laptop/desktop screens. Weekly grid uses horizontal scroll on smaller screens.

---

## Group Members

DSC2204 IT Project — Group 8  
Singapore Institute of Technology
