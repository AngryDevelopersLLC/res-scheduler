# -*- coding: utf-8 -*-
"""
Resystem Scheduling Service.
Released under New BSD License.
Copyright © 2015, Vadim Markovtsev :: Angry Developers LLC
All rights reserved.
Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:
    * Redistributions of source code must retain the above copyright
      notice, this list of conditions and the following disclaimer.
    * Redistributions in binary form must reproduce the above copyright
      notice, this list of conditions and the following disclaimer in the
      documentation and/or other materials provided with the distribution.
    * Neither the name of the Angry Developers LLC nor the
      names of its contributors may be used to endorse or promote products
      derived from this software without specific prior written permission.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL VADIM MARKOVTSEV BE LIABLE FOR ANY
DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""


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
                self._tasks_table).select(use_labels=True))
            rows = yield from rows.fetchall()
            return [(r.scheduled_tasks_id,
                     (True, r.pending_tasks_triggered_at,
                      r.scheduled_tasks_due_date, r.scheduled_tasks_expire_in,
                      r.scheduled_tasks_timeout, r.scheduled_tasks_name,
                      pickle.loads(r.scheduled_tasks_data)))
                    for r in rows]
