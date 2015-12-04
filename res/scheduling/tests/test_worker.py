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
from asyncio_mongo._bson import json_util
from datetime import datetime, timedelta
import json
import logging
import unittest
import pytz
from random import randint

from res.core.configuration import Config
from res.core.logger import Logger
from res.scheduling.heap import Heap
from res.scheduling.worker import Worker


class DBManagerMock(object):
    def __init__(self):
        self.counter = 0

    @asyncio.coroutine
    def register_task(self, data, due_date, expires, timeout, uid):
        self.counter += 1
        return self.counter

    @asyncio.coroutine
    def unregister_task(self, task_id):
        pass

    @asyncio.coroutine
    def trigger_task(self, task_id, triggered_at):
        pass


class AMQPChannelMock(object):
    def __init__(self):
        self.published = []

    @asyncio.coroutine
    def publish(self, *args, **kwargs):
        self.published.append((args, kwargs))


class EnvelopeMock(object):
    delivery_tag = "test_tag"


class PropertiesMock(object):
    content_type = "application/json"
    content_encoding = "utf-8"
    reply_to = "amq.rabbitmq.reply-to"


class SchedulingAPITest(unittest.TestCase):
    @staticmethod
    def setUpClass():
        Logger.setup_logging(logging.DEBUG)

    def setUp(self):
        self.logger = logging.getLogger("test")
        self.loop = asyncio.get_event_loop()

    def test_no_amqp(self):
        self.loop.run_until_complete(self._test_no_db_amqp())

    @asyncio.coroutine
    def _test_no_db_amqp(self):
        db_mamanger = DBManagerMock()
        heap = Heap()
        cfg = Config('test')
        worker = Worker(db_mamanger, heap, cfg, poll_interval=100500,
                        default_timeout=10050, pending=tuple())
        worker._amqp_channel_source = AMQPChannelMock()
        worker._amqp_channel_trigger = AMQPChannelMock()
        date = datetime.now(pytz.utc)
        date -= timedelta(microseconds=date.microsecond % 1000)

        def make_msg_bytes(msg):
            return json.dumps(msg, default=json_util.default).encode("utf-8")

        msg = {"action": "enqueue", "due_date": date, "data": "hello"}
        msg_data = make_msg_bytes(msg)
        yield from worker._amqp_callback_source(
            msg_data, EnvelopeMock(), PropertiesMock())
        self.assertEqual(1, worker._heap.size())
        self.assertEqual(date, worker._heap.min()[0])
        self.assertEqual("", worker._heap.min()[1][0])
        self.assertEqual(1, worker._heap.min()[1][1])
        self.assertIsNone(worker._heap.min()[1][2])
        self.assertEqual("hello", worker._heap.min()[1][4])
        date -= timedelta(days=10)
        msg = {"action": "enqueue", "due_date": date, "data": "world",
               "id": "unique"}
        msg_data = make_msg_bytes(msg)
        yield from worker._amqp_callback_source(
            msg_data, EnvelopeMock(), PropertiesMock())
        self.assertEqual(2, worker._heap.size())
        self.assertEqual(date, worker._heap.min()[0])
        self.assertEqual("unique", worker._heap.min()[1][0])
        self.assertEqual(2, worker._heap.min()[1][1])
        self.assertIsNone(worker._heap.min()[1][2])
        self.assertEqual("world", worker._heap.min()[1][4])
        date += timedelta(days=5)
        msg = {"action": "enqueue", "due_date": date, "data": "other",
               "expire_in": 1}
        msg_data = make_msg_bytes(msg)
        yield from worker._amqp_callback_source(
            msg_data, EnvelopeMock(), PropertiesMock())
        self.assertEqual(3, worker._heap.size())
        self.assertEqual("world", worker._heap.min()[1][4])
        self.assertEqual(date, worker._heap._list[2][0])
        self.assertEqual("", worker._heap._list[2][1][0])
        self.assertEqual(3, worker._heap._list[2][1][1])
        self.assertEqual(1, worker._heap._list[2][1][2])
        self.assertEqual("other", worker._heap._list[2][1][4])
        tasks = worker._poll()
        for task in tasks:
            yield from task
        self.assertEqual(0, worker._heap.size())
        self.assertEqual(2, len(worker._amqp_channel_trigger.published))
        self.assertEqual(2, len(worker._pending_tasks))

        msg = {"action": "enqueue", "due_date": date, "data": "hello"}
        msg_data = make_msg_bytes(msg)
        yield from worker._amqp_callback_source(
            msg_data, EnvelopeMock(), PropertiesMock())
        self.assertEqual(1, worker._heap.size())
        msg = {"action": "cancel", "id": 4}
        msg_data = make_msg_bytes(msg)
        yield from worker._amqp_callback_source(
            msg_data, EnvelopeMock(), PropertiesMock())
        self.assertEqual(1, worker._heap.size())
        self.assertIn(4, worker._cancelled_tasks)
        tasks = worker._poll()
        self.assertEqual(0, len(tasks))


if __name__ == "__main__":
    unittest.main()
