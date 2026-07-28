"""Add persistent scheduling, security, enrolment, and resource features.

Revision ID: 20260728_01
Revises: None
"""

from alembic import op
import sqlalchemy as sa


revision = '20260728_01'
down_revision = None
branch_labels = None
depends_on = None


def _column_names(inspector, table_name):
    return {column['name'] for column in inspector.get_columns(table_name)}


def _add_missing_columns(inspector, table_name, columns):
    existing = _column_names(inspector, table_name)
    for column in columns:
        if column.name not in existing:
            op.add_column(table_name, column)


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # A fresh installation is created from the current SQLAlchemy metadata.
    # Existing installations continue below and receive only missing fields.
    if 'users' not in existing_tables:
        from app import db
        from app import models  # noqa: F401
        db.metadata.create_all(bind=bind)
        return

    _add_missing_columns(inspector, 'users', [
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('must_change_password', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('password_changed_at', sa.DateTime(timezone=True), nullable=True),
    ])
    _add_missing_columns(inspector, 'professors', [
        sa.Column('qualifications', sa.Text(), nullable=True),
        sa.Column('max_weekly_hours', sa.Integer(), nullable=True),
        sa.Column('max_daily_hours', sa.Integer(), nullable=True),
        sa.Column('home_building', sa.String(length=20), nullable=True),
    ])
    _add_missing_columns(inspector, 'rooms', [
        sa.Column('equipment', sa.Text(), nullable=True),
        sa.Column('is_accessible', sa.Boolean(), nullable=False, server_default=sa.false()),
    ])
    _add_missing_columns(inspector, 'class_sessions', [
        sa.Column('required_equipment', sa.Text(), nullable=True),
        sa.Column('accessibility_required', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('required_qualification', sa.String(length=100), nullable=True),
    ])
    _add_missing_columns(inspector, 'availability_declarations', [
        sa.Column('academic_year', sa.String(length=10), nullable=True),
        sa.Column('trimester', sa.Integer(), nullable=True),
    ])
    if 'events' in existing_tables:
        _add_missing_columns(inspector, 'events', [
            sa.Column('course_id', sa.Integer(), nullable=True),
        ])

    # Models own indexes, constraints, and foreign keys for these new tables.
    from app.models.solver_job import SolverJob
    from app.models.schedule_version import ScheduleVersion
    from app.models.system_audit import SystemAudit
    from app.models.login_throttle import LoginThrottle
    from app.models.password_reset_token import PasswordResetToken
    from app.models.student_enrollment import StudentEnrollment, StudentSectionAssignment
    from app.models.room_availability import RoomAvailability
    for model in (
        SolverJob, ScheduleVersion, SystemAudit, LoginThrottle,
        PasswordResetToken, StudentEnrollment, StudentSectionAssignment,
        RoomAvailability,
    ):
        model.__table__.create(bind=bind, checkfirst=True)


def downgrade():
    for table_name in (
        'room_unavailability', 'student_section_assignments',
        'student_enrollments', 'password_reset_tokens', 'login_throttles',
        'system_audits', 'schedule_versions', 'solver_jobs',
    ):
        op.drop_table(table_name)
