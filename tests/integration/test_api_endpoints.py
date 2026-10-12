"""Integration Tests for ApexGraph FastAPI REST API Service
"""

import os
import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from packages.core.database import Base, get_db_session, get_engine
from packages.core.models import RepositoryModel, WorkspaceModel

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "sample_repo")


@pytest.fixture(scope="module")
def client():
    # Initialize DB
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    return TestClient(app)


def test_health_check(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "healthy"


def test_workspaces_crud(client):
    # Create workspace
    res = client.post("/api/workspaces", json={"name": "API Test Workspace"})
    assert res.status_code == 201
    ws = res.json()
    assert "id" in ws
    assert ws["name"] == "API Test Workspace"

    # List workspaces
    res_list = client.get("/api/workspaces")
    assert res_list.status_code == 200
    workspaces = res_list.json()
    assert any(w["id"] == ws["id"] for w in workspaces)


def test_repository_lifecycle_and_intelligence(client):
    # 1. Create Workspace
    ws = client.post("/api/workspaces", json={"name": "Lifecycle Workspace"}).json()

    # 2. Register Repository
    repo_payload = {
        "workspace_id": ws["id"],
        "name": "Sample Repo API",
        "source_type": "local",
        "source_path": FIXTURES_DIR,
    }
    reg_res = client.post("/api/repositories", json=repo_payload)
    assert reg_res.status_code == 201
    repo = reg_res.json()
    assert repo["indexing_status"] == "pending"

    # 3. Index Repository
    idx_res = client.post(f"/api/repositories/{repo['id']}/index")
    assert idx_res.status_code == 200
    indexed = idx_res.json()
    assert indexed["indexing_status"] == "ready"
    assert indexed["stats"]["total_files"] == 5

    # 4. Search Code
    search_res = client.get(f"/api/repositories/{repo['id']}/search?q=Calculator")
    assert search_res.status_code == 200
    items = search_res.json()
    assert len(items) >= 1
    calc_id = items[0]["node"]["id"]

    # 5. Node Details
    details_res = client.get(f"/api/nodes/{calc_id}/details")
    assert details_res.status_code == 200
    details = details_res.json()
    assert details["node"]["name"] == "Calculator"

    # 6. Change Impact
    impact_res = client.get(f"/api/repositories/{repo['id']}/impact?target=add&depth=5")
    assert impact_res.status_code == 200
    impact = impact_res.json()
    assert impact["total_affected"] >= 1
    assert "calculator.py" in impact["affected_files"]

    # 7. Neighborhood
    neigh_res = client.get(f"/api/nodes/{calc_id}/neighborhood?radius=1")
    assert neigh_res.status_code == 200
    neigh = neigh_res.json()
    assert len(neigh["nodes"]) >= 2

    # 8. Serve Frontend Index
    index_res = client.get("/")
    assert index_res.status_code == 200
    assert "ApexGraph" in index_res.text


def test_security_rejections_via_api(client):
    ws = client.post("/api/workspaces", json={"name": "Security WS"}).json()

    # Attempt directory traversal / non-existent path
    bad_repo = {
        "workspace_id": ws["id"],
        "name": "Bad Path",
        "source_type": "local",
        "source_path": "/invalid/nonexistent/directory",
    }
    res = client.post("/api/repositories", json=bad_repo)
    assert res.status_code == 400
    assert "Security violation" in res.json()["detail"]

    # Attempt SSRF via git url
    ssrf_repo = {
        "workspace_id": ws["id"],
        "name": "SSRF Repo",
        "source_type": "git",
        "source_path": "https://127.0.0.1/malicious.git",
    }
    ssrf_res = client.post("/api/repositories", json=ssrf_repo)
    assert ssrf_res.status_code == 400
    assert "Security violation" in ssrf_res.json()["detail"]
