import asyncio
import pickle
import aiopg.sa
from psycopg2 import ProgrammingError, IntegrityError
import sqlalchemy as sa
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects.postgresql import BYTEA

from res.core.logger import Logger


class DBManager(Logger):
    def __init__(self, **kwargs):
        super(DBManager, self).__init__()
        self._engine = kwargs
        self._tasks_table = sa.Table(
            "scheduled_tasks", sa.MetaData(),
            sa.Column("id", sa.BigInteger(), primary_key=True, nullable=False),
            sa.Column("data", BYTEA(), nullable=False),
            sa.Column("name", sa.String(length=80), nullable=True,
                      unique=True),
            sa.Column("expire_in", sa.SmallInteger(), default=None),
            sa.Column("timeout", sa.SmallInteger(), default=None),
            sa.Column("due_date", sa.DateTime(timezone=True), nullable=False))
        self._pending_table = sa.Table(
            "pending_tasks", sa.MetaData(),
            sa.Column("id", sa.ForeignKey(self._tasks_table.c.id,
                                          ondelete="CASCADE"),
                      primary_key=True, nullable=False),
            sa.Column("triggered_at", sa.DateTime(timezone=True),
                      nullable=False))

    @asyncio.coroutine
    def initialize(self):
        self._engine = yield from aiopg.sa.create_engine(**self._engine)
        with (yield from self._engine) as conn:
            try:
                yield from conn.execute(CreateTable(self._tasks_table))
                self.info("Created table %s", self._tasks_table.name)
            except ProgrammingError:
                self.debug("Table %s already exists", self._tasks_table.name)
            try:
                yield from conn.execute(CreateTable(self._pending_table))
                self.info("Created table %s", self._pending_table.name)
            except ProgrammingError:
                self.debug("Table %s already exists", self._pending_table.name)

        self.info("Successfully connected to PostgreSQL")

    @asyncio.coroutine
    def shutdown(self):
        self._engine.close()
        yield from self._engine.wait_closed()

    @asyncio.coroutine
    def register_task(self, data, due_date, expire_in, timeout, name):
        self.debug("register_task: %d bytes -> %s",
                   len(data), due_date)
        with (yield from self._engine) as conn:
            row = yield from conn.execute(
                self._tasks_table.insert()
                .values(data=pickle.dumps(data),
                        expire_in=expire_in,
                        timeout=timeout,
                        due_date=due_date,
                        name=name))
            return (yield from row.first()).id

    @asyncio.coroutine
    def unregister_task(self, id_):
        with (yield from self._engine) as conn:
            yield from conn.execute(self._tasks_table.delete().where(
                self._tasks_table.c.id == id_))

    @asyncio.coroutine
    def trigger_task(self, task_id, triggered_at):
        with (yield from self._engine) as conn:
            try:
                yield from conn.execute(
                    self._pending_table.insert()
                    .values(id=task_id, triggered_at=triggered_at))
            except IntegrityError:
                yield from conn.execute(
                    self._pending_table.update()
                    .where(self._pending_table.c.id == task_id)
                    .values(triggered_at=triggered_at)
                )

    @asyncio.coroutine
    def fetch_all(self):
        with (yield from self._engine) as conn:
            rows = yield from conn.execute(self._tasks_table.select())
            rows = yield from rows.fetchall()
            return [(r.due_date, (r.name, r.id, r.expire_in, r.timeout,
                                  pickle.loads(r.data)))
                    for r in rows]

    @asyncio.coroutine
    def fetch_pending(self):
        with (yield from self._engine) as conn:
            rows = yield from conn.execute(self._pending_table.join(
                self._tasks_table).select())
            rows = yield from rows.fetchall()
            return [(r.id, (r.triggered_at, r.due_date, r.expire_in,
                            r.timeout, r.name, r.data)) for r in rows]
