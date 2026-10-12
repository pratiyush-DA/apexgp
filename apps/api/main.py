"""ApexGraph FastAPI REST API Service

Exposes workspace management, repository indexing, symbol search,
change impact blast-radius analysis, and graph visualization endpoints.
Serves interactive developer dashboard at root.
"""

import os
from typing import Any, Dict, List, Optional
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from packages.core.database import get_db, init_db
from packages.core.indexer import RepositoryIndexer
from packages.core.models import (
    GraphNodeModel,
    ImpactAnalysisResult,
    RepositoryModel,
    RepositoryRead,
    RepositoryRegister,
    SearchResult,
    WorkspaceCreate,
    WorkspaceModel,
    WorkspaceRead,
)
from packages.core.query_engine import GraphQueryEngine
from packages.core.security import (
    SecurityValidationError,
    validate_git_url,
    validate_local_path,
)

init_db()

app = FastAPI(
    title="ApexGraph API",
    description="Independent Code Knowledge Graph & Developer Intelligence Engine",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WEB_DIR = os.path.realpath(os.path.join(os.path.dirname(os.path.dirname(__file__)), "web"))


# ============================================================================
# Health & Status
# ============================================================================

@app.get("/api/health")
def health_check(db: Session = Depends(get_db)):
    try:
        # Check database connection
        db.execute(GraphNodeModel.__table__.select().limit(1))
        return {"status": "healthy", "service": "apexgraph-api", "version": "1.0.0"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database check failed: {str(e)}")


@app.get("/favicon.ico", include_in_schema=False)
def get_favicon():
    svg_icon = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="45" fill="#3b82f6"/><text x="50" y="65" font-size="50" text-anchor="middle" fill="#ffffff" font-family="sans-serif" font-weight="bold">A</text></svg>'
    return Response(content=svg_icon, media_type="image/svg+xml")


# ============================================================================
# Workspaces API
# ============================================================================

@app.post("/api/workspaces", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED)
def create_workspace(payload: WorkspaceCreate, db: Session = Depends(get_db)):
    ws = WorkspaceModel(name=payload.name)
    db.add(ws)
    db.commit()
    db.refresh(ws)
    return ws


@app.get("/api/workspaces", response_model=List[WorkspaceRead])
def list_workspaces(db: Session = Depends(get_db)):
    return db.query(WorkspaceModel).order_by(WorkspaceModel.created_at.desc()).all()


@app.get("/api/workspaces/{workspace_id}", response_model=WorkspaceRead)
def get_workspace(workspace_id: str, db: Session = Depends(get_db)):
    ws = db.query(WorkspaceModel).filter_by(id=workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return ws


# ============================================================================
# Repositories API
# ============================================================================

@app.post("/api/repositories", response_model=RepositoryRead, status_code=status.HTTP_201_CREATED)
def register_repository(payload: RepositoryRegister, db: Session = Depends(get_db)):
    # Validate workspace exists
    ws = db.query(WorkspaceModel).filter_by(id=payload.workspace_id).first()
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")

    # Security validation
    try:
        if payload.source_type == "local":
            clean_path = validate_local_path(payload.source_path)
        else:
            clean_path = validate_git_url(payload.source_path)
    except SecurityValidationError as se:
        raise HTTPException(status_code=400, detail=f"Security violation: {str(se)}")

    repo = RepositoryModel(
        workspace_id=payload.workspace_id,
        name=payload.name,
        source_type=payload.source_type,
        source_path=clean_path,
        indexing_status="pending",
    )
    db.add(repo)
    db.commit()
    db.refresh(repo)
    return repo


@app.get("/api/repositories", response_model=List[RepositoryRead])
def list_repositories(
    workspace_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(RepositoryModel)
    if workspace_id:
        query = query.filter_by(workspace_id=workspace_id)
    return query.all()


@app.get("/api/repositories/{repo_id}", response_model=RepositoryRead)
def get_repository(repo_id: str, db: Session = Depends(get_db)):
    repo = db.query(RepositoryModel).filter_by(id=repo_id).first()
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    return repo


@app.post("/api/repositories/{repo_id}/index", response_model=RepositoryRead)
def index_repository(repo_id: str, db: Session = Depends(get_db)):
    indexer = RepositoryIndexer(db)
    try:
        repo = indexer.index_repository(repo_id)
        return repo
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Indexing failed: {str(e)}")


@app.delete("/api/repositories/{repo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_repository(repo_id: str, db: Session = Depends(get_db)):
    repo = db.query(RepositoryModel).filter_by(id=repo_id).first()
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    db.delete(repo)
    db.commit()
    return None


# ============================================================================
# Search & Code Intelligence API
# ============================================================================

@app.get("/api/repositories/{repo_id}/search", response_model=List[SearchResult])
def search_repository_code(
    repo_id: str,
    q: str = Query(..., min_length=1),
    type: Optional[str] = Query(None),
    file: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    engine = GraphQueryEngine(db)
    return engine.search_code(
        query_text=q,
        repo_id=repo_id,
        node_type=type,
        file_path_filter=file,
        limit=limit,
    )


@app.get("/api/nodes/{node_id:path}/details")
def get_node_details(node_id: str, db: Session = Depends(get_db)):
    engine = GraphQueryEngine(db)
    details = engine.get_symbol_details(node_id)
    if not details:
        raise HTTPException(status_code=404, detail="Symbol node not found")
    return details


@app.get("/api/nodes/{node_id:path}/callers")
def get_node_callers(
    node_id: str,
    depth: int = Query(5, ge=1, le=15),
    db: Session = Depends(get_db),
):
    engine = GraphQueryEngine(db)
    return engine.get_callers(node_id=node_id, max_depth=depth)


@app.get("/api/nodes/{node_id:path}/callees")
def get_node_callees(
    node_id: str,
    depth: int = Query(5, ge=1, le=15),
    db: Session = Depends(get_db),
):
    engine = GraphQueryEngine(db)
    return engine.get_callees(node_id=node_id, max_depth=depth)


@app.get("/api/nodes/{node_id:path}/dependencies")
def get_node_dependencies(node_id: str, db: Session = Depends(get_db)):
    engine = GraphQueryEngine(db)
    return engine.get_dependencies(node_id=node_id)


@app.get("/api/nodes/{node_id:path}/tests")
def get_node_tests(node_id: str, db: Session = Depends(get_db)):
    engine = GraphQueryEngine(db)
    tests = engine.find_related_tests(node_id=node_id)
    return [
        {
            "id": t.id,
            "name": t.name,
            "node_type": t.node_type,
            "file_path": t.file_path,
            "line": t.start_line,
            "signature": t.signature,
        }
        for t in tests
    ]


@app.get("/api/repositories/{repo_id}/trace")
def trace_relationship_path(
    repo_id: str,
    source: str = Query(...),
    target: str = Query(...),
    depth: int = Query(6, ge=1, le=15),
    db: Session = Depends(get_db),
):
    engine = GraphQueryEngine(db)
    path = engine.trace_relationships(source_id=source, target_id=target, max_depth=depth)
    if path is None:
        return {"path": [], "found": False}
    return {"path": path, "found": True}


@app.get("/api/repositories/{repo_id}/impact", response_model=ImpactAnalysisResult)
def analyze_change_impact(
    repo_id: str,
    target: str = Query(...),
    depth: int = Query(5, ge=1, le=15),
    db: Session = Depends(get_db),
):
    engine = GraphQueryEngine(db)
    try:
        return engine.analyze_change_impact(
            target_node_or_file=target,
            repo_id=repo_id,
            max_depth=depth,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/nodes/{node_id:path}/neighborhood")
def get_graph_neighborhood(
    node_id: str,
    radius: int = Query(1, ge=1, le=3),
    limit: int = Query(100, ge=1, le=300),
    db: Session = Depends(get_db),
):
    engine = GraphQueryEngine(db)
    return engine.get_graph_neighborhood(node_id=node_id, radius=radius, limit=limit)


# ============================================================================
# Static Files & Dashboard Single Page App
# ============================================================================

if os.path.exists(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
def serve_index():
    index_file = os.path.join(WEB_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "ApexGraph API online. Frontend dashboard under apps/web."}


def start_server():
    import uvicorn
    host = os.environ.get("APEX_HOST", "127.0.0.1")
    port = int(os.environ.get("APEX_PORT", 8000))
    uvicorn.run("apps.api.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    start_server()
