import json

from flask import has_request_context, request, session
from flask_login import current_user

from app import db
from app.models.system_audit import SystemAudit


SENSITIVE_KEYS = {'password', 'current_password', 'new_password', 'confirm_password', 'csrf_token'}


# Only administrator actions that change scheduling or managed data belong in
# the user-facing audit trail.  Technical request details (POST paths, request
# ids and raw form payloads) remain application-log concerns, not audit events.
AUDITED_ADMIN_ACTIONS = {
    'admin.course_add': ('data.module.created', 'module', 'Module created', 'Data'),
    'admin.course_edit': ('data.module.updated', 'module', 'Module updated', 'Data'),
    'admin.course_import': ('data.modules.imported', 'module', 'Module workbook imported', 'Import'),
    'admin.professor_add': ('data.professor.created', 'professor', 'Professor created', 'Data'),
    'admin.professor_edit': ('data.professor.updated', 'professor', 'Professor updated', 'Data'),
    'admin.professor_import': ('data.professors.imported', 'professor', 'Professor workbook imported', 'Import'),
    'admin.student_add': ('data.student.created', 'student', 'Student created', 'Data'),
    'admin.student_edit': ('data.student.updated', 'student', 'Student updated', 'Data'),
    'admin.student_delete': ('data.student.deleted', 'student', 'Student deleted', 'Data'),
    'admin.user_toggle_active': ('data.account.status_changed', 'user', 'Account status changed', 'Data'),
    'admin.student_enrolments': ('data.enrolments.updated', 'student', 'Student enrolments updated', 'Data'),
    'admin.student_import': ('data.students.imported', 'student', 'Student workbook imported', 'Import'),
    'admin.student_enrolment_import': ('data.enrolments.imported', 'enrolment', 'Student enrolments imported', 'Import'),
    'admin.room_add': ('data.room.created', 'room', 'Room created', 'Data'),
    'admin.room_edit': ('data.room.updated', 'room', 'Room updated', 'Data'),
    'admin.room_availability_add': ('data.room_closure.created', 'room', 'Room closure added', 'Data'),
    'admin.room_availability_delete': ('data.room_closure.deleted', 'room', 'Room closure removed', 'Data'),
    'admin.room_toggle': ('data.room.status_changed', 'room', 'Room status changed', 'Data'),
    'admin.room_delete': ('data.room.deleted', 'room', 'Room deleted', 'Data'),
    'admin.room_import': ('data.rooms.imported', 'room', 'Room workbook imported', 'Import'),
    'admin.student_group_add': ('data.group.created', 'student_group', 'Student group created', 'Data'),
    'admin.student_group_edit': ('data.group.updated', 'student_group', 'Student group updated', 'Data'),
    'admin.student_group_generate': ('data.subgroups.generated', 'student_group', 'Student sub-groups generated', 'Data'),
    'admin.student_group_delete': ('data.group.deleted', 'student_group', 'Student group deleted', 'Data'),
    'admin.student_group_import': ('data.groups.imported', 'student_group', 'Student-group workbook imported', 'Import'),
    'admin.course_session_add': ('data.session.created', 'class_session', 'Module session created', 'Data'),
    'admin.session_assign': ('data.session.updated', 'class_session', 'Session assignments updated', 'Data'),
    'admin.declarations': ('coordination.availability.reviewed', 'availability', 'Professor availability reviewed', 'Coordination'),
    'admin.flag_notify': ('coordination.response.sent', 'timetable_flag', 'Schedule response sent to professor', 'Coordination'),
    'admin.flag_resolve': ('coordination.response.resolved', 'timetable_flag', 'Schedule response marked as handled', 'Coordination'),
    'admin.academic_terms': ('settings.academic_term.updated', 'academic_term', 'Academic term updated', 'Settings'),
    'admin.constraint_settings': ('settings.constraints.updated', 'constraint_setting', 'Constraint settings updated', 'Settings'),
    'admin.import_template1': ('data.sessions.imported', 'class_session', 'SIT timetable workbook imported', 'Import'),
    'admin.event_add': ('data.event.created', 'event', 'Calendar event created', 'Data'),
    'admin.event_delete': ('data.event.deleted', 'event', 'Calendar event deleted', 'Data'),
}

TIMETABLE_ACTIONS = {
    'generate': ('timetable.generated', 'Timetable generated'),
    'publish': ('timetable.published', 'Timetable published'),
    'reset': ('timetable.cleared', 'Generated timetable cleared'),
    'reset_ay': ('timetable.year_cleared', 'Academic-year timetables cleared'),
}


def record_audit(action, entity_type=None, entity_id=None, summary=None, metadata=None,
                 user_id=None, commit=False):
    """Append a safe, metadata-only operational audit record."""
    if user_id is None and has_request_context() and current_user.is_authenticated:
        user_id = current_user.id
    safe_metadata = {
        key: value for key, value in (metadata or {}).items()
        if key.lower() not in SENSITIVE_KEYS
    }
    row = SystemAudit(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        summary=summary,
        metadata_json=json.dumps(safe_metadata, default=str),
        request_id=getattr(request, 'audit_request_id', None) if has_request_context() else None,
        ip_address=(request.remote_addr or None) if has_request_context() else None,
    )
    db.session.add(row)
    if commit:
        db.session.commit()
    return row


def install_request_audit(app):
    """Record completed, user-meaningful administrator changes only."""
    @app.before_request
    def remember_existing_flashes():
        request.audit_existing_flash_count = len(session.get('_flashes', ()))

    @app.after_request
    def audit_admin_mutation(response):
        endpoint = request.endpoint or ''
        if (
            request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}
            and endpoint.startswith('admin.')
            and response.status_code < 400
            and current_user.is_authenticated
            and current_user.role == 'admin'
        ):
            try:
                config = AUDITED_ADMIN_ACTIONS.get(endpoint)
                if endpoint == 'admin.timetable':
                    timetable_action = request.form.get('action', '')
                    action_config = TIMETABLE_ACTIONS.get(timetable_action)
                    if action_config:
                        action, summary = action_config
                        config = (action, 'timetable', summary, 'Timetable')

                # Preview-only imports do not change data and do not belong in
                # the audit trail. Template 1 uses `confirm=1`; the other
                # workbook imports use `import_mode=apply`.
                if endpoint == 'admin.import_template1' and request.form.get('confirm') != '1':
                    config = None
                elif endpoint.endswith('_import') and request.form.get('import_mode') != 'apply':
                    config = None

                # Most successful mutation routes announce completion with a
                # success/info flash. Rejected submissions normally redirect
                # too, so status code alone is not enough to call them edits.
                start = getattr(request, 'audit_existing_flash_count', 0)
                new_flashes = session.get('_flashes', ())[start:]
                success_messages = [message for category, message in new_flashes if category == 'success']
                info_messages = [message for category, message in new_flashes if category == 'info']
                completion_message = (success_messages or info_messages or [None])[-1]
                if not config or not completion_message:
                    return response

                action, entity_type, summary, category = config
                summary = str(completion_message)[:500] or summary
                view_args = request.view_args or {}
                entity_id = next(
                    (value for key, value in view_args.items() if key.endswith('_id')),
                    None,
                )
                metadata = {
                    'category': category,
                    'trimester': request.form.get('trimester') or None,
                    'academic_year': request.form.get('academic_year') or request.form.get('clear_ay') or None,
                }
                metadata = {key: value for key, value in metadata.items() if value}
                record_audit(
                    action=action,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    summary=summary,
                    metadata=metadata,
                )
                db.session.commit()
            except Exception:
                db.session.rollback()
                app.logger.exception('Could not append admin audit record')
        return response
