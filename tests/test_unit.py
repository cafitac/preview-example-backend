import json
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.main import NoteCreate, app, get_session, parse_origins


def test_origins() -> None:
    assert parse_origins("") == []
    assert parse_origins(" , ") == []
    assert parse_origins(" http://localhost:3000,https://example.test, ") == [
        "http://localhost:3000",
        "https://example.test",
    ]


def test_text_boundaries() -> None:
    assert NoteCreate(text="a").text == "a"
    assert len(NoteCreate(text="a" * 500).text) == 500
    for value in ("", "a" * 501):
        with pytest.raises(ValidationError):
            NoteCreate(text=value)


def test_unreachable_database(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable() -> None:
        raise OperationalError("SELECT 1", {}, Exception("unavailable"))

    monkeypatch.setattr("app.main.get_engine", unavailable)
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}


def test_locked_build_dependencies() -> None:
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile").read_text()
    assert "COPY pyproject.toml uv.lock ./" in dockerfile
    assert "uv sync --frozen --no-dev --no-install-project" in dockerfile
    assert "run: uv sync --locked\n" in (root / ".github/workflows/ci.yml").read_text()
    assert (root / "uv.lock").is_file()


@pytest.mark.parametrize("value", ["a\x00b", "\ud800", "\udfff", "a\ud800b"])
def test_invalid_characters_return_json_422(value: str) -> None:
    session = Mock(spec=Session)
    app.dependency_overrides[get_session] = lambda: session
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/notes",
                content=json.dumps({"text": value}),
                headers={"Content-Type": "application/json"},
            )
        assert response.status_code == 422
        assert response.json() == {"detail": "Text contains invalid characters"}
        session.add.assert_not_called()
        session.commit.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_session)


def test_valid_unicode_text() -> None:
    value = "한글 😀\n"
    assert NoteCreate(text=value).text == value


@pytest.mark.parametrize(
    ("payload", "error_type", "location"),
    [
        ({"text": ["\ud800"]}, "string_type", ["body", "text"]),
        ({"note": "\ud800"}, "missing", ["body", "text"]),
        ("\ud800", "model_attributes_type", ["body"]),
        ({"text": ""}, "string_too_short", ["body", "text"]),
    ],
)
def test_validation_errors_omit_raw_input(
    payload: object, error_type: str, location: list[str]
) -> None:
    session = Mock(spec=Session)
    app.dependency_overrides[get_session] = lambda: session
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/notes",
                content=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            )
        assert response.status_code == 422
        assert response.headers["content-type"] == "application/json"
        response.content.decode("utf-8")
        errors = response.json()["detail"]
        assert len(errors) == 1
        assert errors[0]["type"] == error_type
        assert errors[0]["loc"] == location
        assert "input" not in errors[0]
        session.add.assert_not_called()
        session.commit.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_session)


@pytest.mark.parametrize("operation", ["scalars", "add", "commit"])
def test_notes_database_failure_returns_json_503(operation: str) -> None:
    session = Mock(spec=Session)
    getattr(session, operation).side_effect = OperationalError(
        "query", {}, Exception("unavailable")
    )
    app.dependency_overrides[get_session] = lambda: session
    try:
        with TestClient(app) as client:
            if operation == "scalars":
                response = client.get("/api/notes")
            else:
                response = client.post("/api/notes", json={"text": "valid note"})
        assert response.status_code == 503
        assert response.json() == {"detail": "Database unavailable"}
    finally:
        app.dependency_overrides.pop(get_session)


def test_create_note_does_not_refresh_after_commit() -> None:
    session = Mock(spec=Session)
    session.refresh.side_effect = OperationalError(
        "refresh", {}, Exception("unavailable")
    )
    app.dependency_overrides[get_session] = lambda: session
    try:
        with TestClient(app) as client:
            response = client.post("/api/notes", json={"text": "valid note"})
        assert response.status_code == 201
        body = response.json()
        assert UUID(body["id"])
        assert datetime.fromisoformat(body["created_at"]).tzinfo is not None
        assert body["text"] == "valid note"
        note = session.add.call_args.args[0]
        assert body["id"] == str(note.id)
        assert datetime.fromisoformat(body["created_at"]) == note.created_at
        session.commit.assert_called_once_with()
        session.refresh.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_session)


def test_create_note_succeeds_when_database_fails_after_commit() -> None:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    # This regression exercises real commit expiration without PostgreSQL setup.
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE notes (id CHAR(32), text VARCHAR(500), "
                "created_at DATETIME)"
            )
        )
    committed = False
    post_commit_queries: list[str] = []

    def fail_after_commit(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if committed:
            post_commit_queries.append(statement)
            raise OperationalError(statement, {}, Exception("unavailable"))

    def mark_committed(session: Session) -> None:
        nonlocal committed
        committed = True

    event.listen(engine, "before_cursor_execute", fail_after_commit)
    try:
        with Session(engine) as session:
            event.listen(session, "after_commit", mark_committed)
            app.dependency_overrides[get_session] = lambda: session
            with TestClient(app) as client:
                response = client.post("/api/notes", json={"text": "committed note"})
            assert committed
            assert response.status_code == 201
            assert response.json()["text"] == "committed note"
            assert post_commit_queries == []
        event.remove(engine, "before_cursor_execute", fail_after_commit)
        with engine.connect() as connection:
            row = connection.execute(text("SELECT id, text FROM notes")).one()
            assert str(UUID(row.id)) == response.json()["id"]
            assert row.text == "committed note"
    finally:
        app.dependency_overrides.pop(get_session, None)
        engine.dispose()
