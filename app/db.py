import os
from functools import lru_cache

from sqlalchemy import Engine, create_engine


@lru_cache
def get_engine() -> Engine:
    return create_engine(
        os.environ["DATABASE_URL"],
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )
