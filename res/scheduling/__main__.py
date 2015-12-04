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
import logging
import signal
import sys
import uuid

try:
    import res.core.argument_parser
except ImportError as e:
    print("PYTHONPATH: %s" % sys.path)
    raise e from None
from res.core.argument_parser import get_argument_parser, gather_parsers
from res.core.configuration import initialize as initialize_configuration, r
from res.core.logger import Logger
from res.core.systemd_watchdog import install_watchdog
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
    install_watchdog(r.watchdog.interval)
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
