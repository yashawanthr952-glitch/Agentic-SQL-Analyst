"""Database access layer: a Database object owning the engine and sessions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from api.config import Settings, get_settings


class Database:
    """Owns the SQLAlchemy engine and hands out sessions.

    Wrapping this in a class (rather than module-level globals) keeps the engine
    swappable -- tests and scripts can build their own Database against a
    different URL without patching module state.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: Engine = create_engine(
            settings.database_url,
            echo=settings.db_echo,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,  # drop connections killed by the server
            future=True,
        )
        self._session_factory = sessionmaker(
            bind=self._engine, autocommit=False, autoflush=False, expire_on_commit=False
        )

    @property
    def engine(self) -> Engine:
        return self._engine

    def session(self) -> Session:
        return self._session_factory()

    @contextmanager
    def readonly_connection(self) -> Iterator[Connection]:
        """A connection pinned read-only with a server-side statement timeout.

        Both settings are applied with SET LOCAL inside an explicit transaction,
        so they are scoped to this block and reverted on exit -- they cannot
        leak to the next checkout of the same pooled connection.

        statement_timeout must be set here rather than wrapped around the call
        in Python: an application-level timeout cancels our *waiting*, while
        Postgres happily keeps burning CPU on the query. Only the server-side
        setting actually kills it.
        """
        with self._engine.connect() as conn:
            with conn.begin():
                conn.execute(text("SET LOCAL transaction_read_only = on"))
                conn.execute(
                    text(f"SET LOCAL statement_timeout = {int(self._settings.statement_timeout_ms)}")
                )
                yield conn

    def healthcheck(self) -> bool:
        """Round-trip a trivial statement to prove the server is reachable."""
        try:
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def dispose(self) -> None:
        self._engine.dispose()


db = Database(get_settings())


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    session = db.session()
    try:
        yield session
    finally:
        session.close()
