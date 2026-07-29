"""Enforce scoped professor-availability declarations.

Revision ID: 20260729_02
Revises: 20260728_01
"""

from alembic import op


revision = '20260729_02'
down_revision = '20260728_01'
branch_labels = None
depends_on = None


def upgrade():
    # The first production migration added the scope columns to existing
    # installations but did not add the constraints already declared by the
    # SQLAlchemy model. Batch mode keeps this portable to SQLite.
    with op.batch_alter_table('availability_declarations') as batch_op:
        batch_op.create_check_constraint(
            'ck_availability_trimester',
            'trimester IS NULL OR trimester IN (1, 2, 3)',
        )
        batch_op.create_unique_constraint(
            'uq_availability_prof_slot_term',
            ['professor_id', 'timeslot_id', 'academic_year', 'trimester'],
        )


def downgrade():
    with op.batch_alter_table('availability_declarations') as batch_op:
        batch_op.drop_constraint(
            'uq_availability_prof_slot_term', type_='unique'
        )
        batch_op.drop_constraint(
            'ck_availability_trimester', type_='check'
        )
