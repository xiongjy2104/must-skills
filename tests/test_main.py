import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.storage import store


@pytest.fixture(autouse=True)
def _reset_store():
    store.clear()
    yield
    store.clear()


@pytest.fixture
def client():
    return TestClient(app)


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_and_get_skill(client):
    payload = {"name": "Python", "category": "programming", "level": "intermediate"}
    response = client.post("/skills", json=payload)
    assert response.status_code == 201
    created = response.json()
    assert created["id"] == 1
    assert created["name"] == "Python"
    assert created["level"] == "intermediate"

    response = client.get(f"/skills/{created['id']}")
    assert response.status_code == 200
    assert response.json()["name"] == "Python"


def test_list_skills_filter_by_category(client):
    client.post("/skills", json={"name": "Python", "category": "programming"})
    client.post("/skills", json={"name": "Guitar", "category": "music"})

    response = client.get("/skills", params={"category": "music"})
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["name"] == "Guitar"


def test_update_skill(client):
    created = client.post(
        "/skills", json={"name": "Go", "category": "programming"}
    ).json()

    response = client.patch(
        f"/skills/{created['id']}", json={"level": "advanced"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["level"] == "advanced"
    assert body["name"] == "Go"


def test_delete_skill(client):
    created = client.post(
        "/skills", json={"name": "Rust", "category": "programming"}
    ).json()

    response = client.delete(f"/skills/{created['id']}")
    assert response.status_code == 204

    response = client.get(f"/skills/{created['id']}")
    assert response.status_code == 404


def test_get_missing_returns_404(client):
    response = client.get("/skills/999")
    assert response.status_code == 404


def test_create_rejects_blank_name(client):
    response = client.post(
        "/skills", json={"name": "", "category": "programming"}
    )
    assert response.status_code == 422
