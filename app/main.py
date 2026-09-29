import json
import logging
import os
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Annotated
from urllib.request import Request as URLRequest
from urllib.request import urlopen
from uuid import UUID, uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_engine
from app.models import Note

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)


def parse_origins(value: str) -> list[str]:
    return [origin.strip() for origin in value.split(",") if origin.strip()]


app = FastAPI(title="Preview notes API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_origins(os.getenv("CORS_ORIGINS", "")),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    request: Request, exc: RequestValidationError
) -> Response:
    errors = [
        {key: value for key, value in error.items() if key != "input"}
        for error in exc.errors()
    ]
    # Escape surrogates in any remaining error metadata as well.
    return Response(
        content=json.dumps({"detail": jsonable_encoder(errors)}, ensure_ascii=True),
        status_code=422,
        media_type="application/json",
    )


class NoteCreate(BaseModel):
    text: str = Field(min_length=1, max_length=500)

    @field_validator("text", mode="before")
    @classmethod
    def validate_text(cls, value: object) -> object:
        if isinstance(value, str) and (
            "\x00" in value or any("\ud800" <= char <= "\udfff" for char in value)
        ):
            # Do not echo lone surrogates in validation errors: JSON responses
            # must themselves be UTF-8 encodable.
            raise HTTPException(
                status_code=422, detail="Text contains invalid characters"
            )
        return value


class NoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    text: str
    created_at: datetime


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session


@app.get("/healthz")
def health() -> dict[str, str]:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, KeyError):
        logger.warning("Database health check failed")
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    return {"status": "ok"}


@app.get("/api/notes", response_model=list[NoteResponse])
def list_notes(session: Annotated[Session, Depends(get_session)]) -> list[Note]:
    try:
        return list(
            session.scalars(
                select(Note).order_by(Note.created_at.desc(), Note.id.desc())
            )
        )
    except SQLAlchemyError:
        logger.warning("Database notes read failed")
        raise HTTPException(status_code=503, detail="Database unavailable") from None


def notify_note_created(notifier_url: str, note_id: str, note_text: str) -> None:
    try:
        request = URLRequest(
            f"{notifier_url.rstrip('/')}/api/notify",
            data=json.dumps(
                {"event": "note.created", "payload": {"id": note_id, "text": note_text}}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as result:
            if not 200 <= result.status < 300:
                raise ValueError(f"Notifier returned HTTP {result.status}")
    except Exception as exc:
        logger.warning("Note notification failed: %s", exc)


@app.post("/api/notes", response_model=NoteResponse, status_code=201)
def create_note(
    payload: NoteCreate,
    session: Annotated[Session, Depends(get_session)],
    background_tasks: BackgroundTasks,
) -> NoteResponse:
    note = Note(id=uuid4(), text=payload.text, created_at=datetime.now(UTC))
    response = NoteResponse.model_validate(note)
    try:
        session.add(note)
        session.commit()
    except SQLAlchemyError:
        logger.warning("Database note creation failed")
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    logger.info("Created note %s", response.id)
    if notifier_url := os.getenv("NOTIFIER_URL"):
        background_tasks.add_task(
            notify_note_created, notifier_url, str(response.id), response.text
        )
    return response
