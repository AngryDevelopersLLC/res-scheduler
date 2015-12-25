import logging

from amqp_service_base import AMQPServiceBase, SingletonMixin


class AMQPServiceQuery(AMQPServiceBase, SingletonMixin):
    """
    Allows sending AMQP requests and receiving responses in a blocking manner.
    """

    SETTINGS_NODE = "SCHEDULING_AMQP"
    QUEUE_CONFIG_ATTR = "queue_source"
    logger = logging.getLogger("scheduling.AMQPServiceQuery")

    def __init__(self, *args, **kwargs):
        kwargs["debug_io"] = True
        super(AMQPServiceQuery, self).__init__(*args, **kwargs)


class AMQPServiceTrigger(AMQPServiceBase, SingletonMixin):
    """
    Allows sending AMQP requests and receiving responses in a blocking manner.
    """

    SETTINGS_NODE = "SCHEDULING_AMQP"
    QUEUE_CONFIG_ATTR = "queue_trigger"
    DIRECTION = False
    DURABLE = False
    logger = logging.getLogger("scheduling.AMQPServiceTrigger")

    def __init__(self, *args, **kwargs):
        kwargs["debug_io"] = True
        super(AMQPServiceTrigger, self).__init__(*args, **kwargs)

    def _listen_reply(self, msg, received_obj, reply_to):
        sched_id = received_obj[0]
        msg["task"] = sched_id
        super(AMQPServiceTrigger, self)._listen_reply(
            msg, received_obj, reply_to)
