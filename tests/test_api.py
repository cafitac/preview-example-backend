from datetime import datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text


def test_health(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_and_list(client: TestClient, db_engine: Engine) -> None:
    assert client.get("/api/notes").json() == []
    first = client.post("/api/notes", json={"text": "first"})
    assert first.status_code == 201
    note = first.json()
    assert set(note) == {"id", "text", "created_at"}
    UUID(note["id"])
    assert datetime.fromisoformat(note["created_at"]).tzinfo is not None
    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE notes SET created_at = now() - interval '1 day'")
        )
    second = client.post("/api/notes", json={"text": "second"})
    assert second.status_code == 201
    response = client.get("/api/notes")
    assert response.status_code == 200
    assert [row["text"] for row in response.json()] == ["second", "first"]
    # A fresh request/session observes the committed rows.
    assert len(client.get("/api/notes").json()) == 2


@pytest.mark.parametrize("value", ["", "x" * 501, None, 123])
def test_invalid_text(client: TestClient, value: object) -> None:
    assert client.post("/api/notes", json={"text": value}).status_code == 422
    assert client.get("/api/notes").json() == []


def test_maximum_length(client: TestClient) -> None:
    assert client.post("/api/notes", json={"text": "x" * 500}).status_code == 201
