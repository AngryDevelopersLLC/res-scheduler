import asyncio
import logging
import signal
import sys
import os
import uuid

try:
    import res.core.argument_parser
except ImportError as e:
    print("PYTHONPATH: %s" % sys.path)
    raise e from None
from res.core.argument_parser import get_argument_parser, gather_parsers
from res.core.configuration import initialize as initialize_configuration, r
from res.core.logger import Logger
from res.scheduling.db_manager import DBManager
from res.scheduling.heap import Heap
from res.scheduling.worker import Worker
res.core.argument_parser.ARGPARSE_DESCRIPTION = "RES Scheduling Service"


@asyncio.coroutine
def main():
    parser = gather_parsers(get_argument_parser(), (res.scheduling,))
    args = parser.parse_args()
    Logger.setup_logging()
    initialize_configuration()
    logger = logging.getLogger("main")
    loop = asyncio.get_event_loop()
    session_id = str(uuid.uuid4())
    try:
        yield from Logger.duplicate_logs_to_mongo(session_id, "main")
    except:
        logger.exception("Failed to setup logging to MongoDB")
    db_manager = DBManager(**r.db)
    yield from db_manager.initialize()
    heap = Heap()
    heap.initialize((yield from db_manager.fetch_all()))
    worker = Worker(db_manager, heap, r.amqp, r.scheduler.poll_interval,
                    r.scheduler.timeout,
                    (yield from db_manager.fetch_pending()))
    yield from worker.initialize()
    yield from worker.work()

    for signame in "SIGINT", "SIGTERM":
        @asyncio.coroutine
        def terminate():
            if not args.disable_logging_to_mongo:
                yield from Logger.discard_logs_to_mongo()
            yield from worker.stop()
            yield from db_manager.shutdown()
            loop.stop()

        def sighandler():
            logger.critical("Received %s", signame)
            loop.create_task(terminate())
        loop.add_signal_handler(getattr(signal, signame), sighandler)
    logger.debug("Installed signal handlers")

if __name__ == "__main__":
    loop = asyncio.get_event_loop()

    @asyncio.coroutine
    def checked_main():
        try:
            yield from main()
        except SystemExit:
            loop.stop()
        except Exception as e:
            loop.stop()
            raise e from None

    asyncio.async(checked_main())
    try:
        loop.run_forever()
    finally:
        loop.close()
