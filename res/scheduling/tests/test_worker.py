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
    def register_task(self, data, due_date, expires):
        self.counter += 1
        return "test_task_id%d" % self.counter


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
        worker = Worker(db_mamanger, heap, cfg, poll_interval=100500)
        worker._amqp_channel_source = AMQPChannelMock()
        worker._amqp_channel_trigger = AMQPChannelMock()
        date = datetime.now(pytz.utc)
        date -= timedelta(microseconds=date.microsecond % 1000)

        def make_msg_bytes(msg):
            return json.dumps(msg, default=json_util.default).encode("utf-8")

        msg = {"due_date": date, "data": "hello"}
        msg_data = make_msg_bytes(msg)
        yield from worker._amqp_callback_source(
            msg_data, EnvelopeMock(), PropertiesMock())
        self.assertEqual(1, worker._heap.size())
        self.assertEqual(date, worker._heap.min()[0])
        self.assertEqual("test_task_id1", worker._heap.min()[1][0])
        self.assertEqual(None, worker._heap.min()[1][1])
        self.assertEqual("hello", worker._heap.min()[1][2])
        date -= timedelta(days=10)
        msg = {"due_date": date, "data": "world"}
        msg_data = make_msg_bytes(msg)
        yield from worker._amqp_callback_source(
            msg_data, EnvelopeMock(), PropertiesMock())
        self.assertEqual(2, worker._heap.size())
        self.assertEqual(date, worker._heap.min()[0])
        self.assertEqual("test_task_id2", worker._heap.min()[1][0])
        self.assertEqual(None, worker._heap.min()[1][1])
        self.assertEqual("world", worker._heap.min()[1][2])
        date += timedelta(days=5)
        msg = {"due_date": date, "data": "other", "expire_in": 1}
        msg_data = make_msg_bytes(msg)
        yield from worker._amqp_callback_source(
            msg_data, EnvelopeMock(), PropertiesMock())
        self.assertEqual(3, worker._heap.size())
        self.assertEqual("world", worker._heap.min()[1][2])
        self.assertEqual(date, worker._heap._list[2][0])
        self.assertEqual("test_task_id3", worker._heap._list[2][1][0])
        self.assertEqual(1, worker._heap._list[2][1][1])
        self.assertEqual("other", worker._heap._list[2][1][2])
        yield from worker._poll_async()
        self.assertEqual(0, worker._heap.size())
        self.assertEqual(2, len(worker._amqp_channel_trigger.published))
        self.assertEqual(2, len(worker._pending_tasks))


if __name__ == "__main__":
    unittest.main()
