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

import aioamqp
import asyncio
from bidict import bidict
from asyncio_mongo._bson import json_util
from datetime import datetime, timedelta
import json
import pytz
import requests
import socket

from res.core.logger import Logger
from res.core.utils import ellipsis, dameraulevenshtein

json_default = json_util.default
json_hook = json_util.object_hook


class AMQPServerError(Exception):
    pass


class AMQPConnectionError(Exception):
    pass


class Worker(Logger):
    MESSAGE_PROPERTIES = {
        "content_type": "application/json",
        "content_encoding": "utf-8"
    }
    RECONNECT_INTERVAL = 1
    MAX_MESSAGE_SIZE = 65536

    def __init__(self, db_manager, heap, cfg, poll_interval, default_timeout,
                 pending):
        super(Worker, self).__init__()
        self._source_handler_map = {
            "enqueue": self._source_handle_enqueue,
            "cancel": self._source_handle_cancel,
        }
        self._db_manager = db_manager
        self._amqp_transport = None
        self._amqp_protocol = None
        self._amqp_channel_source = None
        self._amqp_channel_trigger = None
        self._queue_trigger_name = None
        self._stopped = False
        self._working = False
        self._heap = heap
        self._unique_tasks = bidict()
        for _, (uid, task_id, _, _, _) in heap:
            if uid:
                self._unique_tasks[uid] = task_id
        self._cancelled_tasks = set()
        self._cfg = cfg
        self._default_timeout = default_timeout
        self._poll_interval = poll_interval
        self._poll_handle = None
        self._reconnect_amqp_task = None
        self._pending_tasks = dict(pending)
        self._timed_out_tasks = set()
        self.info("Initial heap size: %d; pending tasks count: %d; "
                  "unique tasks count: %d", heap.size(),
                  len(self._pending_tasks), len(self._unique_tasks))

    @asyncio.coroutine
    def initialize(self):
        yield from self._connect_amqp()
        loop = asyncio.get_event_loop()
        self._reconnect_amqp_task = loop.create_task(self._reconnect_amqp())

    @asyncio.coroutine
    def work(self):
        self._working = True
        loop = asyncio.get_event_loop()
        self._poll_handle = loop.call_later(self._poll_interval, self._poll)
        yield from self._amqp_channel_source.basic_consume(
            self._cfg.channel.queue_source,
            callback=self._amqp_callback_source, no_ack=True)
        yield from self._amqp_channel_trigger.basic_consume(
            "amq.rabbitmq.reply-to", callback=self._amqp_callback_trigger,
            no_ack=True)
        self.info("Entered working mode")

    @asyncio.coroutine
    def stop(self):
        self._stopped = True
        self._working = False
        yield from self._amqp_protocol.close()
        self._poll_handle.cancel()
        self._reconnect_amqp_task.cancel()

    def _poll(self):
        now = datetime.now(pytz.utc)
        self.debug("Poll at %s - %d tasks", now, self._heap.size())
        tasks = []
        # Detect timed out tasks
        timed_out = []
        for task_id, (restored, trigger_date, due_date, expire_in, timeout,
                      uid, data) in self._pending_tasks.items():
            if now > (trigger_date + timedelta(seconds=timeout)):
                if not restored:
                    self._heap.push(due_date,
                                    (uid, task_id, expire_in, timeout, data))
                timed_out.append(task_id)
                self._timed_out_tasks.add(task_id)
                self.warning(
                    "Task %s scheduled at %s, triggered at %s: timeout %s "
                    "seconds was exceeded", task_id, due_date, trigger_date,
                    timeout)
        for task_id in timed_out:
            del self._pending_tasks[task_id]
        # Trigger tasks
        while self._heap.size() > 0 and self._heap.min()[0] < now:
            due_date, (uid, task_id, expire_in, timeout, data) = \
                self._heap.pop()
            if expire_in is not None and \
                    (now - due_date) > timedelta(hours=expire_in):
                self.warning("Dropped task scheduled on %s: %s",
                             due_date, data)
                continue
            if task_id in self._cancelled_tasks:
                self._cancelled_tasks.remove(task_id)
                self.info("Skipped cancelled task #%d", task_id)
                continue
            if task_id in self._pending_tasks:
                if not self._pending_tasks[task_id][0]:
                    self.error("Consistency failure: popped task is pending "
                               "but was not restored from DB")
                else:
                    self.debug("Skipped pending restored task #%d")
                continue
            self.info("Trigger: %s -> %s", due_date, data)
            self._pending_tasks[task_id] = \
                False, now, due_date, expire_in, timeout, uid, data
            tasks.append(task_id)
        loop = asyncio.get_event_loop()
        task_aio_tasks = []
        for task_id in tasks:
            task_aio_tasks.append(loop.create_task(self._trigger(task_id)))
        self._poll_handle = loop.call_later(self._poll_interval, self._poll)
        return task_aio_tasks

    @asyncio.coroutine
    def _trigger(self, task_id):
        _, triggered_at, due_date, _, _, _, data = self._pending_tasks[task_id]
        yield from self._db_manager.trigger_task(task_id, triggered_at)
        props = dict(self.MESSAGE_PROPERTIES)
        props["reply_to"] = "amq.rabbitmq.reply-to"
        msg = task_id, due_date, data
        self.debug("trigger -> %s", msg)
        yield from self._amqp_channel_trigger.publish(
            json.dumps(msg, default=json_default).encode("utf-8"),
            "", self._queue_trigger_name, properties=props)

    @asyncio.coroutine
    def _reconnect_amqp(self):
        while not self._stopped:
            yield from self._amqp_protocol.connection_closed.wait()
            self.info("Leaved working mode")
            while not self._amqp_protocol.is_open:
                yield from asyncio.sleep(self.RECONNECT_INTERVAL)
                if self._stopped:
                    break
                try:
                    yield from self._connect_amqp()
                except Exception as e:
                    self.error("AMQP connection failure: %s: %s", type(e), e)
            if self._working:
                yield from self.work()

    @asyncio.coroutine
    def _connect_amqp(self):
        self.debug("Connecting to AMQP...")
        self._check_vhost(**self._cfg.connection)
        self._amqp_transport, self._amqp_protocol = \
            yield from aioamqp.connect(**self._cfg.connection)
        amqp_props = self._amqp_protocol.server_properties
        if amqp_props["product"] != "RabbitMQ":
            raise AMQPServerError("RabbitMQ server is required (yours is %s)",
                                  amqp_props["product"])
        amqp_version = tuple(map(int, amqp_props["version"].split('.')))
        if amqp_version < (3, 4, 0):
            raise AMQPServerError(
                "RabbitMQ >= 3.4.0 is required (yours is %s)",
                amqp_props["version"])
        # http://lists.rabbitmq.com/pipermail/rabbitmq-discuss/2010-November/009916.html
        self._amqp_transport._sock.setsockopt(
            socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._amqp_channel_source = yield from self._amqp_protocol.channel()
        yield from asyncio.wait_for(self._amqp_channel_source.queue(
            self._cfg.channel.queue_source, durable=True, auto_delete=True),
            timeout=self._cfg.channel.timeout)
        self.info("Successfully acquired the connection to source AMQP queue "
                  "%s (RabbitMQ %s)", self._cfg.channel.queue_source,
                  amqp_props["version"])
        self._amqp_channel_trigger = yield from self._amqp_protocol.channel()
        yield from asyncio.wait_for(self._amqp_channel_trigger.queue(
            self._cfg.channel.queue_trigger, durable=False, auto_delete=True),
            timeout=self._cfg.channel.timeout)
        self.info("Successfully acquired the connection to trigger AMQP queue "
                  "%s (RabbitMQ %s)", self._cfg.channel.queue_trigger,
                  amqp_props["version"])
        self._queue_trigger_name = self._cfg.channel.queue_trigger

    def _check_vhost(self, host, management_port, virtualhost, login, password,
                     timeout, **_):
        try:
            response = requests.get(
                'http://%s:%d/api/vhosts' % (host, management_port),
                auth=requests.auth.HTTPBasicAuth(login, password),
                timeout=timeout)
        except requests.exceptions.ConnectTimeout:
            response = None
        if response is None or response.status_code != 200:
            self.warning("Failed to connect to %s:%d as %s:%s => unable to "
                         "check vhost", host, management_port, login, password)
            return
        vhosts = set(obj["name"] for obj in response.json())
        if virtualhost not in vhosts:
            distances = [(dameraulevenshtein(virtualhost, h), h)
                         for h in vhosts]
            distances.sort()
            raise AMQPConnectionError(
                "Virtual host \"%s\" is not registered in RabbitMQ. Available "
                "vhosts are: %s. Did you mean \"%s\" instead?" %
                (virtualhost, vhosts, distances[0][1]))

    @asyncio.coroutine
    def _amqp_callback_source(self, body, envelope, properties):
        @asyncio.coroutine
        def reply(obj=None):
            if obj is None:
                obj = {}
            if "status" not in obj:
                obj["status"] = "ok"
            self.debug("source -> %s", obj)
            yield from self._amqp_channel_source.publish(
                json.dumps(obj, default=json_default).encode("utf-8"), "",
                properties.reply_to, properties=self.MESSAGE_PROPERTIES)

        @asyncio.coroutine
        def reply_error(msg):
            yield from reply({"status": "error", "detail": msg})

        dtag = envelope.delivery_tag
        if len(body) > self.MAX_MESSAGE_SIZE:
            self.error("%s: max message length exceeded (%d > %d)",
                       dtag, len(body), self.MAX_MESSAGE_SIZE)
            yield from reply_error(
                "max message length exceeded (%d > %d)" % (
                    len(body), self.MAX_MESSAGE_SIZE))
            return

        if properties.content_type != "application/json" or \
                properties.content_encoding != "utf-8":
            self.error("%s: invalid/missing content_type or content_encoding",
                       dtag)
            yield from reply_error(
                "invalid/missing content_type or content_encoding")
            return

        try:
            data = json.loads(body.decode("utf-8"), object_hook=json_hook)
        except ValueError:
            self.error("%s: failed to parse %s%s", dtag, *ellipsis(body))
            yield from reply_error("%s: failed to parse JSON" % dtag)
            return

        self.debug("source <- [%s] %s", dtag, data)
        try:
            action = data["action"]
        except KeyError:
            self.error("%s: no action", dtag)
            yield from reply_error("%s: no action was specified" % dtag)
            return
        try:
            yield from self._source_handler_map[action](
                data, dtag, reply, reply_error)
        except KeyError:
            self.error("%s: invalid action \"%s\"", dtag, action)
            yield from reply_error("%s: invalid action: %s" % (dtag, action))
        except Exception as e:
            self.exception("%s: internal \"%s\" handler error", dtag, action)
            yield from reply_error("%s: internal error: %s: %s" % (
                dtag, type(e).__name__, e))
            # the best thing we can do without further corruption is terminate
            raise SystemExit()

    @asyncio.coroutine
    def _source_handle_cancel(self, data, dtag, reply, reply_error):
        try:
            task_id = int(data["id"])
        except KeyError:
            self.error("%s: missing task id", dtag)
            yield from reply_error("%s: missing task id" % dtag)
            return
        except ValueError:
            self.error("%s: invalid task id %s", dtag, data["id"])
            yield from reply_error("%s: invalid task id: %s" % (
                dtag, data["id"]))
            return
        self._cancelled_tasks.add(task_id)
        yield from self._db_manager.unregister_task(task_id)
        yield from reply({"status": "ok"})

    @asyncio.coroutine
    def _source_handle_enqueue(self, data, dtag, reply, reply_error):
        uid = data.get("id")
        if uid is not None:
            if not isinstance(uid, str) or len(uid) > 80 or not uid:
                self.error("%s: invalid unique id: %s", dtag, uid)
                yield from reply_error("invalid id value: must be nonempty "
                                       "str <= 80")
                return
            task_id = self._unique_tasks.get(uid)
            if task_id is not None and isinstance(task_id, asyncio.Future):
                task_id = yield from task_id
            if task_id is not None:
                yield from reply({"status": "ok", "size": self._heap.size(),
                                  "already_exists": True, "id": task_id})
                return
            self._unique_tasks[uid] = asyncio.Future()
        else:
            uid = ""
        result = yield from self._source_handle_enqueue_core(
            data, uid, dtag, reply, reply_error)
        if not result and uid:
            self._unique_tasks[uid].set_result(None)
            del self._unique_tasks[uid]

    @asyncio.coroutine
    def _source_handle_enqueue_core(self, data, uid, dtag, reply, reply_error):
        timeout = data.get("timeout")
        if timeout is None:
            timeout = self._default_timeout
        elif not isinstance(timeout, int):
            self.error("%s: timeout must be an integer", dtag)
            yield from reply_error("timeout must be an integer")
            return
        if timeout > 32767 or timeout < 1:
            self.error("%s: timeout must be in [1, 32767] (got %d)", dtag,
                       timeout)
            yield from reply_error("timeout must be in [1, 32767]")
            return
        expire_in = data.get("expire_in", None)
        if expire_in is not None and (expire_in > 32767 or expire_in < 0):
            self.error("%s: expire_in must be >=0 and <= 32767", dtag)
            yield from reply_error("expire_in must be >=0 and <= 32767")
            return
        try:
            due_date = data["due_date"]
            data = data["data"]
        except (KeyError, ValueError) as e:
            self.error("%s: invalid format: %s: %s", dtag, type(e), e)
            yield from reply_error("invalid format of the message: %s" % e)
            return
        if not isinstance(due_date, datetime):
            self.error("%s: due_date must be a datetime object", dtag)
            yield from reply_error("due_date must be a datetime object")
            return
        task_id = yield from self._db_manager.register_task(
            data, due_date, expire_in, timeout, uid or None)
        try:
            self._heap.push(due_date, (uid, task_id, expire_in, timeout, data))
        except Exception as e:
            yield from self._db_manager.unregister_task(task_id)
            self.error("%s: heap push failure: %s: %s", dtag, type(e), e)
            yield from reply_error("heap push failure: %s: %s" % (type(e), e))
            return
        if uid:
            self._unique_tasks[uid].set_result(task_id)
            self._unique_tasks[uid] = task_id
        yield from reply({"status": "ok", "size": self._heap.size(),
                          "already_exists": False, "id": task_id})
        return True

    @asyncio.coroutine
    def _amqp_callback_trigger(self, body, envelope, properties):
        dtag = envelope.delivery_tag
        if len(body) > self.MAX_MESSAGE_SIZE:
            self.error("%s: max message length exceeded (%d > %d)",
                       dtag, len(body), self.MAX_MESSAGE_SIZE)
            return
        if properties.content_type != "application/json" or \
                properties.content_encoding != "utf-8":
            self.error("%s: invalid/missing content_type or content_encoding",
                       dtag)
            return
        try:
            data = json.loads(body.decode("utf-8"), object_hook=json_hook)
        except ValueError:
            self.error("%s: failed to parse %s%s", dtag, *ellipsis(body))
            return
        self.debug("trigger <- [%s] %s", dtag, data)
        try:
            task_id = data["task"]
            node_id = data["node_id"]
            status = data["status"]
        except KeyError as e:
            self.error("%s: invalid format: %s: %s", dtag, type(e), e)
            return
        timed_out = task_id in self._timed_out_tasks
        if timed_out:
            self._timed_out_tasks.remove(task_id)
        if task_id not in self._pending_tasks:
            if timed_out:
                self.error(
                    "Received status %s from timed out task %s from %s => "
                    "double trigger", status, task_id, node_id)
            else:
                self.error("%s: %s is not a valid pending task identifier",
                           dtag, task_id)
            return
        _, _, due_date, expire_in, timeout, uid, data = \
            self._pending_tasks.pop(task_id)
        if status != "ok":
            self.warning("%s: task %s was reported to be in status %s",
                         dtag, task_id, status)
            if status != "giveup":
                self._heap.push(
                    due_date, (uid, task_id, expire_in, timeout, data))
                return
        if task_id in ~self._unique_tasks:
            del self._unique_tasks[:task_id]
        yield from self._db_manager.unregister_task(task_id)
        self.info("%s: task %s was fulfilled by %s", dtag, task_id, node_id)
