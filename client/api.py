import inspect
from django.conf import settings
from django.db.models import Model
import logging
from datetime import datetime, timedelta, date
import pytz
import six
import sys

from amqp_service import AMQPServiceQuery, AMQPServiceTrigger
from functions import from_none


enabled = True
__registered__ = {}
logger = logging.getLogger("scheduling.API")


class SchedulingError(Exception):
    pass


def register(task_id=None, expire_in=None, timeout=None, unique=False):
    """
    Register the function into the list of functions which can be scheduled.
    :param task_id: Unique task name. It is used in schedule() to identify the
    task.
    :param expire_in: Number of hours which this task can maximum be delayed
    after the time it is scheduled at.
    :param timeout: Number of seconds this task can maximum last before being
    abandoned.
    :param unique: allow only one instance of this task.
    :return: The wrapped function.
    """
    if task_id is not None:
        assert isinstance(task_id, str)
        assert task_id not in __registered__
    assert timeout is None or isinstance(timeout, int)
    if timeout is not None:
        assert 0 < timeout <= 32767

    def decorator_register(fn):
        tid = task_id if task_id else fn.__name__
        if hasattr(fn, "im_func"):
            fn.im_func._schedule_task_id = tid
        else:
            fn._schedule_task_id = tid
        if tid in __registered__:
            raise ValueError("Task \"%s\" is already registered" % tid)
        __registered__[tid] = \
            fn, expire_in, timeout, _get_func_id(fn) if unique else None
        return fn
    return decorator_register


def schedulable_object(cls):
    """
    Class decorator which enables object_id usage on class methods with inline
    decoration with @register.
    :param cls: The class to decorate.
    :return: The fixed class.
    """
    for name, method in inspect.getmembers(cls, predicate=inspect.ismethod):
        task_id = getattr(method, "_schedule_task_id", None)
        if task_id is not None:
            _, expire_in, timeout, unique = __registered__[task_id]
            del __registered__[task_id]
            setattr(cls, name, register(
                task_id, expire_in, timeout, unique is not None)(method))
    return cls


def schedule(task_id, at, interval=None, object_id=None, task_name=None,
             *args, **kwargs):
    """
    Enqueue the task for the execution.
    :param task_id: The unique name of the task used in register().
    :param at: First time this task must be executed at.
    :param interval: If not None, makes a repeated task (datetime.timedelta).
    :param object_id: If not None, sets the bound model instance primary key.
    :param task_name: If not None, sets the string unique to this task
    instance, so that subsequent tasks with the same name are not enqueued
    until the first one is fired. If the task was registered as unique,
    overrides the original name.
    :param args: *args to pass to the function.
    :param kwargs: **kwargs to pass to the function.
    :return: Scheduler's task identifier.
    """
    if not isinstance(task_id, six.string_types):
        try:
            if hasattr(task_id, "im_func"):
                task_id = task_id.im_func._schedule_task_id
            else:
                task_id = task_id._schedule_task_id
        except AttributeError:
            raise from_none(ValueError(
                "task_id must be either a string or a registered function"))
    if not enabled:
        logger.warning("Scheduling is disabled, skipped schedule(%s)", task_id)
        return
    if task_name is not None and not isinstance(task_name, six.string_types):
        raise TypeError("task_name must be string (got %s)" % type(task_name))
    task_name = task_name or __registered__[task_id][3]
    if object_id is not None and \
            not hasattr(__registered__[task_id][0], "im_class"):
        raise TypeError(
            "Cannot use object_id with ordinary handling functions. You must "
            "have applied @register to a class method inplace - due to Python "
            "limitations, class type is not known at that time")
    if isinstance(at, date):
        at = datetime.combine(at, datetime.min.time())
    if at.tzinfo is None:
        pytz.utc.localize(at)
    response = AMQPServiceQuery.instance().request({
        "action": "enqueue", "id": task_name,
        "due_date": at, "expire_in": __registered__[task_id][1],
        "data": (task_id, object_id, task_name, args, kwargs,
                 interval.total_seconds() if interval is not None else None),
        "timeout":  __registered__[task_id][2],
        "message_type": "schedule"},
        timeout=settings.SCHEDULING_AMQP["timeout"])
    if response.get("status", "error") != "ok":
        raise SchedulingError(response.get("detail"))
    stid = response.get("id")
    logger.info("Scheduled \"%s\" at %s -> %s", task_id, at, stid)
    return stid


def cancel(stid, raise_non_exists=True):
    if not enabled:
        logger.warning("Scheduling is disabled, skipped cancel(%s)", stid)
        return
    response = AMQPServiceQuery.instance().request({
        "message_type": "schedule", "action": "cancel", "id": stid},
        timeout=settings.SCHEDULING_AMQP["timeout"])
    if response.get("status", "error") != "ok":
        if raise_non_exists:
            raise SchedulingError()
        else:
            logger.info("Task %s does not exist, so nothing to cancel", stid)
    else:
        logger.info("Cancelled task %s", stid)


class RescheduleTask(Exception):
    pass


def launch():
    AMQPServiceQuery.instance()
    AMQPServiceTrigger.instance().on_message_received = _handle_task
    logger.info("Initialized AMQP services")


def shutdown():
    if AMQPServiceQuery.exists():
        AMQPServiceQuery.instance().shutdown()
    if AMQPServiceTrigger.exists():
        AMQPServiceTrigger.instance().shutdown()


def _get_func_id(func):
    cls = getattr(func, "im_class", None)
    if cls is not None:
        return "%s.%s.%s" % (cls.__module__, cls.__name__, func.__name__)
    return "%s.%s" % (func.__module__, func.__name__)


def _retrue(fn):
    def wrapped_retrue(*args, **kwargs):
        fn(*args, **kwargs)
        return True

    return wrapped_retrue


@_retrue
def _handle_task(obj, reply):
    due_date, (task_id, object_id, task_name, args, kwargs, interval) = obj[1:]
    logger.debug("Received task %s -> %s", task_id, due_date)
    try:
        fn, expire, timeout, _ = __registered__[task_id]
    except KeyError:
        logger.error("No such task is registered: \"%s\"", task_id)
        reply("giveup", "No such task is registered")
        return
    if expire is not None and \
            datetime.now() - due_date > timedelta(hours=expire):
        logger.warning("Expired task %s: %s", task_id, due_date)
        reply("giveup", "The task is expired")
        return
    cls = getattr(fn, "im_class", None)
    if cls is not None and issubclass(cls, Model) and object_id is not None:
        try:
            obj = cls.objects.get(id=object_id)
        except cls.DoesNotExist:
            logger.error("Task %s: %s with id %s does not exist",
                         task_id, cls.__name__, object_id)
            reply("giveup", "Task handler does not exist")
            return
        fn = getattr(obj, fn.im_func.__name__)
    timestamp = datetime.now() if timeout is not None else None
    rescheduled = None
    try:
        fn(*args, **kwargs)
    except RescheduleTask as r:
        assert interval is None
        rescheduled = r
    except Exception as e:
        logger.exception("Error in task %s", task_id)
        reply("error", "Internal handler failure: %s: %s" % (type(e), e))
        return
    finally:
        if timeout is not None and \
                (datetime.now() - timestamp) > timedelta(seconds=timeout):
            logger.warning("Task %s handler execution time exceeded the "
                           "allowed timeout %s secs", task_id, timeout)
    logger.info(
        "Fulfilled task %s (scheduled at %s) with args %s, kwargs %s%s",
        task_id, due_date, args, kwargs,
        " for object %s" % object_id if object_id is not None else "")
    reply("ok")
    if rescheduled is not None:
        schedule(task_id, rescheduled.at, None, object_id, task_name,
                 *args, **kwargs)
    try:
        if interval is not None:
            interval = timedelta(seconds=interval)
            schedule(task_id, due_date + interval, interval, object_id,
                     task_name, *args, **kwargs)
    except:
        logger.exception("Failed to schedule the repeating task %s", task_id)
