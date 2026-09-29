from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.models import Note
from app.seed import seed


def test_seed_is_idempotent(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        session.add(Note(text="User note"))
        session.commit()
    seed(db_engine)
    with Session(db_engine) as session:
        before = session.execute(select(Note.id, Note.text, Note.created_at)).all()
    seed(db_engine)
    with Session(db_engine) as session:
        after = session.execute(select(Note.id, Note.text, Note.created_at)).all()
    assert set(before) == set(after)
    assert len(after) == 3
    assert "User note" in [row.text for row in after]
