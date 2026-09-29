import logging
import sys
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import insert

from app.db import get_engine
from app.models import Note

SAMPLE_NOTES = [
    {
        "id": UUID("00000000-0000-4000-8000-000000000001"),
        "text": "Welcome to preview-hub!",
    },
    {"id": UUID("00000000-0000-4000-8000-000000000002"), "text": "Try adding a note."},
]


def seed(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(Note)
            .values(SAMPLE_NOTES)
            .on_conflict_do_nothing(index_elements=["id"])
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    seed(get_engine())
    logging.info("Sample notes initialized")
