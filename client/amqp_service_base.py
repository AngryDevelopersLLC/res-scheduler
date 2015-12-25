from django.conf import settings
import json
import logging
from threading import Thread, Event
from uuid import uuid4

import rabbitpy
import requests
from bson import json_util
from overridable_settings import OverridableSettings
from functions import dameraulevenshtein, from_none


class AMQPConnectionError(Exception):
    pass


class AMQPServiceStub(object):
    logger = logging.getLogger("common.utilities.AMQPServiceStub")

    def request(self, obj, timeout=None):
        self.logger.warning("Service is offline: %s", obj)
        if obj["message_type"] == "get_service_info":
            return {"status": "ok", "denominator": 100, "version": "stub"}
        raise AMQPConnectionError("Service is offline")

    def shutdown(self):
        pass


class AMQPServiceBase(object):
    SETTINGS_NODE = None
    QUEUE_CONFIG_ATTR = None
    DIRECTION = True  # True for requests, False for responses
    DURABLE = True
    logger = None
    node_default_id = str(uuid4())

    def __init__(self, **kwargs):
        self._channel = None
        self._consumer_thread = None
        self._debug_io = kwargs.pop("debug_io", False)
        try:
            config = OverridableSettings(getattr(settings, self.SETTINGS_NODE),
                                         kwargs)
        except AttributeError:
            config = OverridableSettings(kwargs, {})
        self._check_vhost(**config)
        amqp_uri = "amqp://%(login)s:%(password)s@%(host)s:%(port)s/" % config
        amqp_uri += config.virtualhost.replace("/", r"%2F")
        amqp_uri += "?timeout=%s" % config.timeout
        self._connection = rabbitpy.Connection(amqp_uri)
        self._channel = rabbitpy.AMQP(self._connection.channel())
        self._queue_name = getattr(config, self.QUEUE_CONFIG_ATTR)
        arguments = None if self.DURABLE else {"x-message-ttl": 0}
        self._channel.queue_declare(
            self._queue_name, durable=self.DURABLE, auto_delete=True,
            arguments=arguments)
        self._message_properties = {
            "content_type": "application/json",
            "content_encoding": "utf-8"
        }
        if self.DIRECTION:
            self._message_properties.update({
                "delivery_mode": 2 if self.DURABLE else 1,
                "reply_to": "amq.rabbitmq.reply-to",
            })
            self._reply = None
            self._reply_event = Event()
            self._consume(
                self._reply_callback, "amq.rabbitmq.reply-to", no_ack=True)
        else:
            self._on_message_received = None
            self._reply_timeout = config.timeout
            self._consume(self._listen_callback, self._queue_name, no_ack=True)
        self._node_id = kwargs.get("node_id", self.node_default_id)
        self.logger.info("Node ID is %s", self._node_id)

    def __del__(self):
        self.shutdown()

    def shutdown(self):
        if self._channel is None:
            return
        self._channel.basic_cancel()
        self._connection.close()
        if self._consumer_thread is None:
            return
        self._consumer_thread.join(1)
        if self._consumer_thread.is_alive():
            self.logger.warning(
                "Failed to join with the consumer thread")

    @property
    def debug_io(self):
        return self._debug_io

    @debug_io.setter
    def debug_io(self, value):
        self._debug_io = value

    @property
    def on_message_received(self):
        """
        :return: The callback registered for incoming messages.
        """
        return self._on_message_received

    @on_message_received.setter
    def on_message_received(self, value):
        """
        Sets the callback for incoming messages.
        The callback signature is (received message. reply function).
        If callback returns a False-ish value, e.g. None, it is considered
        as successfull and not used reply(), so reply("ok") is sent afterwards,
        automatically. If callback returns a True-ish value, it is considered
        as used reply(). If an exception is thrown inside the callback,
        reply("error") is sent.
        :param value: The callback function.
        """
        assert callable(value)
        self._on_message_received = value

    def request(self, obj, timeout=None, reply_to=None):
        if not isinstance(obj, dict) or "message_type" not in obj:
            raise ValueError("obj must be a dict with \"message_type\" key")
        obj["node_id"] = self._node_id
        if self.DIRECTION:
            self._reply_event.clear()
        if self._debug_io:
            self.logger.debug("-> %s", obj)
        self._channel.basic_publish(
            routing_key=self._queue_name if self.DIRECTION else reply_to,
            body=json.dumps(obj, default=json_util.default),
            properties=self._message_properties)
        if not self.DIRECTION:
            return
        self._reply = None
        if not self._reply_event.wait(timeout):
            params = self._connection.args
            raise AMQPConnectionError(
                "timeout (%ss) waiting for service response. Does it run "
                "on %s:%d?" % (timeout, params["host"], params["port"]))
        assert self._reply is not None
        try:
            obj = json.loads(self._reply.decode("utf-8"),
                             object_hook=json_util.object_hook)
        except Exception as e:
            raise from_none(AMQPConnectionError("%s: %s" % (type(e), e)))
        if self._debug_io:
            self.logger.debug("<- %s", obj)
        return obj

    def _consume(self, callback, queue_name, **kwargs):
        def loop():
            try:
                for msg in self._channel.basic_consume(queue_name, **kwargs):
                    cbkw = dict(msg.__dict__)
                    del cbkw["name"]
                    callback(**cbkw)
            except (rabbitpy.exceptions.AMQPChannelError,
                    rabbitpy.exceptions.ChannelClosedException,
                    RuntimeError):
                self.logger.warning("Channel %s for %s is closed",
                                    self._queue_name,
                                    self._connection.args["virtual_host"])

        self._consumer_thread = Thread(
            target=loop, name="Consumer for %s" % self)
        self._consumer_thread.start()

    def _listen_callback(self, channel, method, properties, body):
        try:
            obj = json.loads(body, object_hook=json_util.object_hook)
        except ValueError as e:
            self._listen_reply({
                "status": "giveup",
                "detail": "Failed to parse JSON: %s" % e}, None,
                properties.reply_to)
            return

        def reply(status, detail=None):
            self._listen_reply({"status": status, "detail": detail}, obj,
                               properties["reply_to"])

        if self._debug_io:
            self.logger.debug("<- %s", obj)

        if self._on_message_received is not None:
            try:
                skip_reply = self._on_message_received(obj, reply)
            except Exception as e:
                self.logger.exception("Handler failure")
                reply("error", "Inside %s: %s: %s" % (
                    self._on_message_received.__name__, type(e), e))
            else:
                if not skip_reply:
                    reply("ok")
        else:
            reply("error", "no handler is registered")

    def _listen_reply(self, msg, received_obj, reply_to):
        msg["message_type"] = "reply"
        self.request(msg, self._reply_timeout, reply_to)

    def _reply_callback(self, channel, method, properties, body):
        self._reply = body
        self._reply_event.set()

    def _check_vhost(self, host, management_port, virtualhost, login, password,
                     timeout, **_):
        try:
            resp = requests.get(
                'http://%s:%d/api/vhosts' % (host, management_port),
                auth=requests.auth.HTTPBasicAuth(login, password),
                timeout=timeout)
        except requests.exceptions.ConnectTimeout:
            resp = None
        if resp is None or resp.status_code != 200:
            if not settings.DEBUG:
                raise AMQPConnectionError(
                    "Failed to connect to %s:%d as %s:%s" % host,
                    management_port, login, password)
            else:
                self.logger.warning(
                    "Unable to verify virtual host in RabbitMQ: connection "
                    "error")
                return
        vhosts = set(obj["name"] for obj in resp.json())
        if virtualhost in vhosts:
            return
        distances = [(dameraulevenshtein(virtualhost, h), h)
                     for h in vhosts]
        distances.sort()
        msg = "Virtual host \"%s\" is not registered in RabbitMQ. " \
            "Available vhosts are: %s. Did you mean \"%s\" instead?" % \
            (virtualhost, vhosts, distances[0][1])
        if not settings.DEBUG:
            raise AMQPConnectionError(msg)
        else:
            self.logger.error(msg)


class SingletonMixin(object):
    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def exists(cls):
        return cls._instance is not None
