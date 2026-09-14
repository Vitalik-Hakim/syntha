import os

from redis import Redis
from rq import Queue, Worker

from syntha_worker import BACKTEST_QUEUE


def main() -> None:
    conn = Redis.from_url(os.environ.get("SYNTHA_REDIS_URL", "redis://localhost:6379/0"))
    # SimpleWorker runs jobs in the worker process: macOS forbids fork() after Objective-C
    # frameworks load (numpy/polars can trigger this), which breaks RQ's forking worker.
    from rq.worker import SimpleWorker

    worker_cls = SimpleWorker if os.environ.get("SYNTHA_WORKER_FORK") != "1" else Worker
    worker_cls([Queue(BACKTEST_QUEUE, connection=conn)], connection=conn).work()


if __name__ == "__main__":
    main()
