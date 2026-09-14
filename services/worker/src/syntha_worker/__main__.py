import os

from redis import Redis
from rq import Queue, Worker

from syntha_worker import BACKTEST_QUEUE


def main() -> None:
    conn = Redis.from_url(os.environ.get("SYNTHA_REDIS_URL", "redis://localhost:6379/0"))
    Worker([Queue(BACKTEST_QUEUE, connection=conn)], connection=conn).work()


if __name__ == "__main__":
    main()
