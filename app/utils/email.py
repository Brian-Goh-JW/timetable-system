"""
Email notification utilities for the SIT Timetable System.
Uses Flask-Mail with Gmail SMTP (configured via MAIL_SERVER / MAIL_USERNAME in config.py).
"""

from flask import current_app, url_for
from flask_mail import Message
from app import mail


def _send(msg):
    """
    Attempt to send a Message object.
    Returns (success: bool, error: str|None).
    Catches SMTP errors gracefully so a mail failure never crashes the app.
    """
    try:
        mail.send(msg)
        return True, None
    except Exception as e:
        current_app.logger.error(f'[email] Failed to send "{msg.subject}": {e}')
        return False, str(e)


# ---------------------------------------------------------------------------
# Email A - Professor notified of conflict flag
# ---------------------------------------------------------------------------

def send_flag_notification(flag):
    """
    Sends an email to the professor informing them of a timetable conflict
    and asking them to respond by the deadline.

    Returns (success: bool, error: str|None).
    """
    prof      = flag.professor
    entry     = flag.timetable_entry
    session   = entry.class_session
    ts        = entry.timeslot
    decl_ts   = flag.declaration.timeslot
    deadline  = flag.response_deadline.strftime('%A, %d %B %Y') if flag.response_deadline else 'as soon as possible'
    login_url = url_for('auth.login', _external=True)
    trimester = entry.trimester

    group_label = session.student_group.group_label if session.student_group else 'N/A'

    subject = (
        f'[SIT Timetable] Conflict Notification - '
        f'{session.course.module_code} {session.session_type.capitalize()} '
        f'- Response Required'
    )

    body = f"""Dear {prof.user.name},

This is an automated notification from the SIT Timetable System.

Your availability declaration could not be fully accommodated in the
{trimester} timetable. Please review the details below and respond
by logging into the system.

──────────────────────────────────────────
AFFECTED SESSION
──────────────────────────────────────────
Module      : {session.course.module_code} - {session.course.title}
Session Type: {session.session_type.capitalize()}
Group       : {group_label}
Trimester   : {trimester}

ASSIGNED SLOT (conflicts with your declaration)
──────────────────────────────────────────
Day    : {ts.day_of_week}
Period : {ts.period_label}
Time   : {ts.start_time.strftime('%H:%M')} – {ts.end_time.strftime('%H:%M')}

YOUR DECLARATION
──────────────────────────────────────────
You declared unavailability for:
{decl_ts.day_of_week} {decl_ts.period_label}
({decl_ts.start_time.strftime('%H:%M')} – {decl_ts.end_time.strftime('%H:%M')})
Reason: {flag.declaration.reason}

──────────────────────────────────────────
ACTION REQUIRED BY: {deadline}
──────────────────────────────────────────

Please log in and go to "My Flags" to submit your response:
  → I can proceed (accept this slot)
  → I cannot proceed (admin will arrange a substitute)

Login: {login_url}

If you have any questions, please contact the timetable administrator.

Regards,
SIT Timetable System (Automated)
"""

    msg = Message(
        subject    = subject,
        recipients = [prof.user.email],
        body       = body,
    )
    return _send(msg)


# ---------------------------------------------------------------------------
# Email B - Admin notified that professor cannot proceed
# ---------------------------------------------------------------------------

def send_cannot_proceed_notification(flag, flag_response):
    """
    Sends an email to the admin informing them that a professor cannot proceed
    and manual intervention is required.

    Returns (success: bool, error: str|None).
    """
    prof    = flag.professor
    entry   = flag.timetable_entry
    session = entry.class_session
    ts      = entry.timeslot
    trimester = entry.trimester

    admin_email = current_app.config.get('ADMIN_EMAIL')
    flags_url   = url_for('admin.timetable_flags',
                          trimester=trimester, _external=True)

    group_label = session.student_group.group_label if session.student_group else 'N/A'
    comments    = flag_response.comments or 'No comments provided.'

    subject = (
        f'[SIT Timetable] Action Required - '
        f'{prof.user.name} Cannot Proceed - '
        f'{session.course.module_code}'
    )

    body = f"""Dear Admin,

This is an automated notification from the SIT Timetable System.

A professor has responded that they CANNOT PROCEED with their
assigned timetable slot. Manual intervention is required.

──────────────────────────────────────────
PROFESSOR
──────────────────────────────────────────
Name     : {prof.user.name}
Staff ID : {prof.staff_id}
Email    : {prof.user.email}

AFFECTED SESSION
──────────────────────────────────────────
Module      : {session.course.module_code} - {session.course.title}
Session Type: {session.session_type.capitalize()}
Group       : {group_label}
Trimester   : {trimester}

ASSIGNED SLOT
──────────────────────────────────────────
{ts.day_of_week} {ts.period_label}
({ts.start_time.strftime('%H:%M')} – {ts.end_time.strftime('%H:%M')})

PROFESSOR'S COMMENTS
──────────────────────────────────────────
{comments}

──────────────────────────────────────────
NEXT STEPS
──────────────────────────────────────────
1. Liaise with faculty heads to find a substitute professor.
2. Add the substitute to the system if not already present.
3. Reassign the session to the substitute professor.
4. Re-run the CP-SAT solver to regenerate the timetable.
5. Check the Conflict Flags page to confirm resolution.

Flags page: {flags_url}

Regards,
SIT Timetable System (Automated)
"""

    msg = Message(
        subject    = subject,
        recipients = [admin_email],
        body       = body,
    )
    return _send(msg)
