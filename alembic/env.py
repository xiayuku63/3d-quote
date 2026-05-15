from logging.config import fileConfig
import os, sys

from alembic import context
from sqlalchemy import engine_from_config, pool

# Add project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db import Base, engine
from app import models_orm  # noqa — register all models
from app.config import DB_PATH

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(
        url=f"sqlite:///{os.path.abspath(DB_PATH)}",
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
