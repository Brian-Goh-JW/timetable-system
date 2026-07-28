import json
import uuid

from flask import has_request_context, request
from flask_login import current_user

from app import db
from app.models.system_audit import SystemAudit


SENSITIVE_KEYS = {'password', 'current_password', 'new_password', 'confirm_password', 'csrf_token'}


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
    """Record every successful state-changing admin request."""
    @app.before_request
    def assign_audit_request_id():
        request.audit_request_id = str(uuid.uuid4())

    @app.after_request
    def audit_admin_mutation(response):
        if (
            request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}
            and request.endpoint
            and request.endpoint.startswith('admin.')
            and response.status_code < 400
            and current_user.is_authenticated
            and current_user.role == 'admin'
        ):
            try:
                metadata = {
                    key: value
                    for key, value in request.form.items()
                    if key.lower() not in SENSITIVE_KEYS
                }
                record_audit(
                    action=request.endpoint,
                    entity_type='http_request',
                    summary=f'{request.method} {request.path}',
                    metadata=metadata,
                )
                db.session.commit()
            except Exception:
                db.session.rollback()
                app.logger.exception('Could not append admin audit record')
        return response
