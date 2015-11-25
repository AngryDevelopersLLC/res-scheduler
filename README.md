Resystem Scheduling Service
===========================

This Python3-only package provides running the task scheduler
process, which communicates with the world through RabbitMQ.
It heavily uses [asyncio](http://asyncio.org/). The state is
stored in PostgreSQL database. This scheduler is meant for speed
and fault tolerance.

Released under New BSD license.
