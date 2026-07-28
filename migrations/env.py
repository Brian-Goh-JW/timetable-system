from logging.config import fileConfig

from alembic import context
from flask import current_app


config = context.config
fileConfig(config.config_file_name)
target_metadata = current_app.extensions['migrate'].db.metadata


def get_url():
    engine = current_app.extensions['migrate'].db.get_engine()
    return str(engine.url).replace('%', '%%')


def run_migrations_offline():
    context.configure(
        url=get_url(), target_metadata=target_metadata, literal_binds=True,
        dialect_opts={'paramstyle': 'named'}, compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = current_app.extensions['migrate'].db.get_engine()
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata,
            compare_type=True, render_as_batch=connection.dialect.name == 'sqlite',
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
