"""Integration and Indexing Tests for Phase 9.8: JSON Parser and Graph Support.

Validates:
- Multi-file JSON indexing (schemas, configs, manifests)
- Graph node extraction (TYPE for objects, VARIABLE for properties/primitives, FILE)
- Relationship building: DEFINES, IMPORTS, and REFERENCES edges
- JSON Schema $ref and configuration extends resolution
- Zero artificial CALLS edges guarantee for declarative JSON
- Import-cycle detection in mutually referencing JSON schemas/configs
- God-Node architectural hub analysis on shared base configurations/schemas
- Incremental indexing via sync_repository (add, modify, delete)
- Polyglot repository isolation across JSON, Python, TypeScript, Go, Rust, SQL, and Shell
"""

import os
import shutil
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from packages.core.indexer import RepositoryIndexer
from packages.core.models import (
    Base,
    GraphEdgeModel,
    GraphNodeModel,
    RepositoryModel,
    WorkspaceModel,
)
from packages.core.query_engine import GraphQueryEngine


@pytest.fixture
def db_session():
    """Provides an isolated in-memory SQLite database session."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def json_repo_dir(tmp_path):
    """Creates a temporary multi-file JSON repository with schemas and configs."""
    repo_dir = tmp_path / "json_test_repo"
    repo_dir.mkdir()

    schemas_dir = repo_dir / "schemas"
    schemas_dir.mkdir()

    config_dir = repo_dir / "config"
    config_dir.mkdir()

    # 1. schemas/common.json
    common_json = schemas_dir / "common.json"
    common_json.write_text(
        '{\n'
        '  "$schema": "http://json-schema.org/draft-07/schema#",\n'
        '  "title": "CommonDefinitions",\n'
        '  "definitions": {\n'
        '    "Address": {\n'
        '      "type": "object",\n'
        '      "properties": {\n'
        '        "street": { "type": "string" },\n'
        '        "city": { "type": "string" }\n'
        '      }\n'
        '    },\n'
        '    "GeoCoord": {\n'
        '      "type": "object",\n'
        '      "properties": {\n'
        '        "lat": { "type": "number" },\n'
        '        "lng": { "type": "number" }\n'
        '      }\n'
        '    }\n'
        '  }\n'
        '}\n',
        encoding="utf-8",
    )

    # 2. schemas/user.json (references common.json)
    user_json = schemas_dir / "user.json"
    user_json.write_text(
        '{\n'
        '  "$schema": "http://json-schema.org/draft-07/schema#",\n'
        '  "title": "UserProfile",\n'
        '  "type": "object",\n'
        '  "properties": {\n'
        '    "id": { "type": "integer" },\n'
        '    "name": { "type": "string" },\n'
        '    "address": {\n'
        '      "$ref": "./common.json#/definitions/Address"\n'
        '    }\n'
        '  }\n'
        '}\n',
        encoding="utf-8",
    )

    # 3. config/base.json
    base_json = config_dir / "base.json"
    base_json.write_text(
        '{\n'
        '  "env": "base",\n'
        '  "port": 8080,\n'
        '  "database": {\n'
        '    "host": "localhost",\n'
        '    "pool_size": 10\n'
        '  }\n'
        '}\n',
        encoding="utf-8",
    )

    # 4. config/production.json (extends base.json)
    prod_json = config_dir / "production.json"
    prod_json.write_text(
        '{\n'
        '  "extends": "./base.json",\n'
        '  "env": "production",\n'
        '  "database": {\n'
        '    "host": "prod-db.internal",\n'
        '    "pool_size": 50\n'
        '  }\n'
        '}\n',
        encoding="utf-8",
    )

    return repo_dir


def test_json_full_indexing_and_graph_nodes(db_session, json_repo_dir):
    """Verifies that full indexing extracts JSON nodes, DEFINES, IMPORTS, and zero CALLS."""
    ws = WorkspaceModel(id="ws-json-1", name="JSON Workspace")
    db_session.add(ws)
    repo = RepositoryModel(
        id="repo-json-1",
        workspace_id="ws-json-1",
        name="json_test_repo",
        source_path=str(json_repo_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    updated_repo = indexer.index_repository(repo.id)

    assert updated_repo.indexing_status == "ready"
    assert updated_repo.error_message is None
    stats = updated_repo.stats or {}
    assert stats.get("total_nodes", 0) > 0
    assert stats.get("total_edges", 0) > 0

    # Verify nodes
    nodes = db_session.query(GraphNodeModel).filter_by(repository_id=repo.id).all()
    node_types = {n.node_type for n in nodes}
    assert "FILE" in node_types
    assert "TYPE" in node_types
    assert "VARIABLE" in node_types

    # Ensure no artificial FUNCTION or CALLS nodes/edges
    assert "FUNCTION" not in node_types

    json_nodes = [n for n in nodes if n.language == "json" and n.node_type != "FILE"]
    for n in json_nodes:
        assert n.id.startswith(f"{repo.id}:json:")
        assert n.language == "json"

    # Verify edges
    edges = db_session.query(GraphEdgeModel).filter_by(repository_id=repo.id).all()
    edge_types = {e.edge_type for e in edges}
    assert "DEFINES" in edge_types
    assert "IMPORTS" in edge_types
    assert "REFERENCES" in edge_types
    # Strictly zero CALLS edges
    assert "CALLS" not in edge_types


def test_json_cross_file_ref_and_extends_resolution(db_session, json_repo_dir):
    """Verifies that $ref and extends create IMPORTS and REFERENCES edges queryable via GraphQueryEngine."""
    ws = WorkspaceModel(id="ws-json-2", name="JSON Workspace")
    db_session.add(ws)
    repo = RepositoryModel(
        id="repo-json-2",
        workspace_id="ws-json-2",
        name="json_test_repo",
        source_path=str(json_repo_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    engine = GraphQueryEngine(db_session)

    # 1. user.json imports common.json via $ref
    user_file_id = f"{repo.id}:file:schemas/user.json"
    user_deps = engine.get_dependencies(user_file_id)
    user_dep_paths = {d["node"]["file_path"] for d in user_deps["internal_dependencies"]}
    assert "schemas/common.json" in user_dep_paths

    # 2. production.json imports base.json via extends
    prod_file_id = f"{repo.id}:file:config/production.json"
    prod_deps = engine.get_dependencies(prod_file_id)
    prod_dep_paths = {d["node"]["file_path"] for d in prod_deps["internal_dependencies"]}
    assert "config/base.json" in prod_dep_paths

    # 3. Direct REFERENCES edge to common.json:definitions.Address
    edges = db_session.query(GraphEdgeModel).filter_by(
        repository_id=repo.id,
        source_id=user_file_id,
        edge_type="REFERENCES",
    ).all()
    target_ids = {e.target_id for e in edges}
    assert any("definitions.Address" in tid or "Address" in tid for tid in target_ids)


def test_json_cycle_detection(db_session, tmp_path):
    """Verifies that circular references between JSON files are detected."""
    cycle_dir = tmp_path / "cycle_json_repo"
    cycle_dir.mkdir()

    # schema_a.json references schema_b.json
    (cycle_dir / "schema_a.json").write_text(
        '{\n'
        '  "title": "A",\n'
        '  "properties": {\n'
        '    "b": { "$ref": "./schema_b.json#/definitions/B" }\n'
        '  }\n'
        '}\n',
        encoding="utf-8",
    )

    # schema_b.json references schema_a.json
    (cycle_dir / "schema_b.json").write_text(
        '{\n'
        '  "title": "B",\n'
        '  "properties": {\n'
        '    "a": { "$ref": "./schema_a.json#/definitions/A" }\n'
        '  }\n'
        '}\n',
        encoding="utf-8",
    )

    ws = WorkspaceModel(id="ws-cycle-json", name="Cycle JSON Workspace")
    db_session.add(ws)
    repo = RepositoryModel(
        id="repo-cycle-json",
        workspace_id="ws-cycle-json",
        name="cycle_json_repo",
        source_path=str(cycle_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    engine = GraphQueryEngine(db_session)
    cycles = engine.detect_import_cycles(repo.id)

    assert len(cycles) > 0
    cycle_files = set(cycles[0])
    assert "schema_a.json" in cycle_files
    assert "schema_b.json" in cycle_files


def test_json_god_node_analysis(db_session, tmp_path):
    """Verifies that heavily referenced base schemas/configs are identified as God-Nodes."""
    god_dir = tmp_path / "god_json_repo"
    god_dir.mkdir()

    # Common base schema
    (god_dir / "base_schema.json").write_text(
        '{\n'
        '  "title": "BaseSchema",\n'
        '  "definitions": {\n'
        '    "Metadata": { "type": "object" }\n'
        '  }\n'
        '}\n',
        encoding="utf-8",
    )

    # 4 distinct schemas referencing base_schema.json
    for i in range(1, 5):
        (god_dir / f"service_{i}.json").write_text(
            '{\n'
            f'  "title": "Service{i}",\n'
            '  "properties": {\n'
            '    "meta": { "$ref": "./base_schema.json#/definitions/Metadata" }\n'
            '  }\n'
            '}\n',
            encoding="utf-8",
        )

    ws = WorkspaceModel(id="ws-god-json", name="God JSON Workspace")
    db_session.add(ws)
    repo = RepositoryModel(
        id="repo-god-json",
        workspace_id="ws-god-json",
        name="god_json_repo",
        source_path=str(god_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    engine = GraphQueryEngine(db_session)
    god_nodes = engine.get_god_nodes(repo.id, limit=10, scope="file")

    assert len(god_nodes) > 0
    god_node_paths = {g.node.file_path for g in god_nodes}
    assert "base_schema.json" in god_node_paths


def test_json_incremental_indexing(db_session, json_repo_dir):
    """Verifies incremental synchronization (add, modify, delete) on JSON repositories."""
    ws = WorkspaceModel(id="ws-inc-json", name="Inc JSON Workspace")
    db_session.add(ws)
    repo = RepositoryModel(
        id="repo-inc-json",
        workspace_id="ws-inc-json",
        name="inc_json_repo",
        source_path=str(json_repo_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    # Initial counts
    init_nodes = db_session.query(GraphNodeModel).filter_by(repository_id=repo.id).count()
    init_edges = db_session.query(GraphEdgeModel).filter_by(repository_id=repo.id).count()

    # 1. ADD: Add new schemas/account.json referencing common.json
    account_path = json_repo_dir / "schemas" / "account.json"
    account_path.write_text(
        '{\n'
        '  "title": "Account",\n'
        '  "properties": {\n'
        '    "address": { "$ref": "./common.json#/definitions/Address" }\n'
        '  }\n'
        '}\n',
        encoding="utf-8",
    )

    res_add = indexer.sync_repository(repo.id)
    assert "schemas/account.json" in res_add["files_added"]
    assert len(res_add["files_modified"]) == 0
    assert len(res_add["files_deleted"]) == 0

    after_add_nodes = db_session.query(GraphNodeModel).filter_by(repository_id=repo.id).count()
    assert after_add_nodes > init_nodes

    # Check that account.json -> common.json edge exists
    acc_import = db_session.query(GraphEdgeModel).filter_by(
        repository_id=repo.id,
        source_id=f"{repo.id}:file:schemas/account.json",
        edge_type="IMPORTS",
    ).first()
    assert acc_import is not None
    assert acc_import.target_id == f"{repo.id}:file:schemas/common.json"

    # 2. MODIFY: Update account.json to add new property
    account_path.write_text(
        '{\n'
        '  "title": "AccountUpdated",\n'
        '  "properties": {\n'
        '    "address": { "$ref": "./common.json#/definitions/Address" },\n'
        '    "balance": { "type": "number" }\n'
        '  }\n'
        '}\n',
        encoding="utf-8",
    )
    # Ensure mtime advances
    mtime = os.path.getmtime(str(account_path)) + 2.0
    os.utime(str(account_path), (mtime, mtime))

    res_mod = indexer.sync_repository(repo.id)
    assert "schemas/account.json" in res_mod["files_modified"]

    # Verify balance node exists
    balance_node = db_session.query(GraphNodeModel).filter_by(
        repository_id=repo.id,
        file_path="schemas/account.json",
        name="balance",
    ).first()
    assert balance_node is not None

    # 3. DELETE: Remove account.json
    account_path.unlink()
    res_del = indexer.sync_repository(repo.id)
    assert "schemas/account.json" in res_del["files_deleted"]

    # Verify nodes and edges are purged cleanly
    del_nodes = db_session.query(GraphNodeModel).filter_by(
        repository_id=repo.id,
        file_path="schemas/account.json",
    ).all()
    assert len(del_nodes) == 0

    del_edges = db_session.query(GraphEdgeModel).filter_by(
        repository_id=repo.id,
        file_path="schemas/account.json",
    ).all()
    assert len(del_edges) == 0


def test_json_polyglot_repository_isolation(db_session, tmp_path):
    """Verifies that JSON entities coexist safely with Python, TS, Go, Rust, SQL, and Shell entities."""
    poly_dir = tmp_path / "polyglot_repo"
    poly_dir.mkdir()

    # Python file
    (poly_dir / "app.py").write_text("def run(): pass\n", encoding="utf-8")
    # TypeScript file
    (poly_dir / "index.ts").write_text("export function start(): void {}\n", encoding="utf-8")
    # Go file
    (poly_dir / "main.go").write_text("package main\nfunc Execute() {}\n", encoding="utf-8")
    # Rust file
    (poly_dir / "lib.rs").write_text("pub fn compute() {}\n", encoding="utf-8")
    # SQL file
    (poly_dir / "schema.sql").write_text("CREATE TABLE users (id INT);\n", encoding="utf-8")
    # Shell file
    (poly_dir / "deploy.sh").write_text("#!/bin/bash\nrun_step() { echo 1; }\n", encoding="utf-8")
    # JSON file
    (poly_dir / "package.json").write_text('{"name": "polyglot", "version": "1.0.0"}\n', encoding="utf-8")

    ws = WorkspaceModel(id="ws-poly-json", name="Polyglot Workspace")
    db_session.add(ws)
    repo = RepositoryModel(
        id="repo-poly-json",
        workspace_id="ws-poly-json",
        name="polyglot_repo",
        source_path=str(poly_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    nodes = db_session.query(GraphNodeModel).filter_by(repository_id=repo.id).all()
    languages = {n.language for n in nodes}
    assert "python" in languages
    assert "typescript" in languages
    assert "go" in languages
    assert "rust" in languages
    assert "sql" in languages
    assert "shell" in languages
    assert "json" in languages

    # Check distinct runtime prefixes
    prefixes = {n.id.split(":")[1] for n in nodes if n.node_type != "FILE"}
    assert "py" in prefixes
    assert "ts" in prefixes
    assert "go" in prefixes
    assert "rs" in prefixes
    assert "sql" in prefixes
    assert "sh" in prefixes
    assert "json" in prefixes
