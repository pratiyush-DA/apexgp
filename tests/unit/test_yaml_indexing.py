"""Integration and Indexing Tests for Phase 9.9: YAML Parser and Graph Support.

Validates:
- Multi-file YAML repository indexing (.yaml, .yml)
- Graph node extraction (TYPE for mappings, VARIABLE for keys/sequences, FILE)
- Relationship building: DEFINES, IMPORTS, and REFERENCES edges
- YAML Schema $ref and configuration extends resolution
- Zero artificial CALLS edges guarantee for declarative YAML
- Import-cycle detection in mutually referencing YAML schemas/configs
- God-Node architectural hub analysis on shared base configurations/schemas
- Incremental indexing via sync_repository (add, modify, delete)
- Polyglot repository isolation across YAML, JSON, Python, TypeScript, Go, Rust, SQL, and Shell
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
def yaml_repo_dir(tmp_path):
    """Creates a temporary multi-file YAML repository with schemas and configs."""
    repo_dir = tmp_path / "yaml_test_repo"
    repo_dir.mkdir()

    schemas_dir = repo_dir / "schemas"
    schemas_dir.mkdir()

    config_dir = repo_dir / "config"
    config_dir.mkdir()

    # 1. schemas/common.yaml
    common_yaml = schemas_dir / "common.yaml"
    common_yaml.write_text(
        'title: CommonDefinitions\n'
        'definitions:\n'
        '  Address:\n'
        '    type: object\n'
        '    properties:\n'
        '      street: string\n'
        '      city: string\n'
        '  GeoCoord:\n'
        '    type: object\n'
        '    properties:\n'
        '      lat: number\n'
        '      lng: number\n',
        encoding="utf-8",
    )

    # 2. schemas/user.yaml (references common.yaml)
    user_yaml = schemas_dir / "user.yaml"
    user_yaml.write_text(
        'title: UserProfile\n'
        'type: object\n'
        'properties:\n'
        '  id: integer\n'
        '  name: string\n'
        '  address:\n'
        '    $ref: "./common.yaml#/definitions/Address"\n',
        encoding="utf-8",
    )

    # 3. config/base.yaml
    base_yaml = config_dir / "base.yaml"
    base_yaml.write_text(
        'env: base\n'
        'port: 8080\n'
        'database:\n'
        '  host: localhost\n'
        '  pool_size: 10\n',
        encoding="utf-8",
    )

    # 4. config/production.yml (extends base.yaml)
    prod_yml = config_dir / "production.yml"
    prod_yml.write_text(
        'extends: "./base.yaml"\n'
        'env: production\n'
        'database:\n'
        '  host: prod-db.internal\n'
        '  pool_size: 50\n',
        encoding="utf-8",
    )

    return repo_dir


def test_yaml_full_indexing_and_graph_nodes(db_session, yaml_repo_dir):
    """Verifies that full indexing extracts YAML nodes, DEFINES, IMPORTS, and zero CALLS."""
    ws = WorkspaceModel(id="ws-yaml-1", name="YAML Workspace")
    db_session.add(ws)
    repo = RepositoryModel(
        id="repo-yaml-1",
        workspace_id="ws-yaml-1",
        name="yaml_test_repo",
        source_path=str(yaml_repo_dir),
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

    # Ensure no artificial FUNCTION nodes
    assert "FUNCTION" not in node_types

    yaml_nodes = [n for n in nodes if n.language == "yaml" and n.node_type != "FILE"]
    for n in yaml_nodes:
        assert n.id.startswith(f"{repo.id}:yaml:")
        assert n.language == "yaml"

    # Verify edges
    edges = db_session.query(GraphEdgeModel).filter_by(repository_id=repo.id).all()
    edge_types = {e.edge_type for e in edges}
    assert "DEFINES" in edge_types
    assert "IMPORTS" in edge_types
    assert "REFERENCES" in edge_types
    # Strictly zero CALLS edges
    assert "CALLS" not in edge_types


def test_yaml_cross_file_ref_and_extends_resolution(db_session, yaml_repo_dir):
    """Verifies that $ref and extends create IMPORTS and REFERENCES edges queryable via GraphQueryEngine."""
    ws = WorkspaceModel(id="ws-yaml-2", name="YAML Workspace")
    db_session.add(ws)
    repo = RepositoryModel(
        id="repo-yaml-2",
        workspace_id="ws-yaml-2",
        name="yaml_test_repo",
        source_path=str(yaml_repo_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    engine = GraphQueryEngine(db_session)

    # 1. user.yaml imports common.yaml via $ref
    user_file_id = f"{repo.id}:file:schemas/user.yaml"
    user_deps = engine.get_dependencies(user_file_id)
    user_dep_paths = {d["node"]["file_path"] for d in user_deps["internal_dependencies"]}
    assert "schemas/common.yaml" in user_dep_paths

    # 2. production.yml imports base.yaml via extends
    prod_file_id = f"{repo.id}:file:config/production.yml"
    prod_deps = engine.get_dependencies(prod_file_id)
    prod_dep_paths = {d["node"]["file_path"] for d in prod_deps["internal_dependencies"]}
    assert "config/base.yaml" in prod_dep_paths

    # 3. Direct REFERENCES edge to common.yaml:definitions.Address
    edges = db_session.query(GraphEdgeModel).filter_by(
        repository_id=repo.id,
        source_id=user_file_id,
        edge_type="REFERENCES",
    ).all()
    target_ids = {e.target_id for e in edges}
    assert any("definitions.Address" in tid or "Address" in tid for tid in target_ids)


def test_yaml_cycle_detection(db_session, tmp_path):
    """Verifies that circular references between YAML files are detected."""
    cycle_dir = tmp_path / "cycle_yaml_repo"
    cycle_dir.mkdir()

    # schema_a.yaml references schema_b.yaml
    (cycle_dir / "schema_a.yaml").write_text(
        'title: A\n'
        'properties:\n'
        '  b:\n'
        '    $ref: "./schema_b.yaml#/definitions/B"\n',
        encoding="utf-8",
    )

    # schema_b.yaml references schema_a.yaml
    (cycle_dir / "schema_b.yaml").write_text(
        'title: B\n'
        'properties:\n'
        '  a:\n'
        '    $ref: "./schema_a.yaml#/definitions/A"\n',
        encoding="utf-8",
    )

    ws = WorkspaceModel(id="ws-cycle-yaml", name="Cycle YAML Workspace")
    db_session.add(ws)
    repo = RepositoryModel(
        id="repo-cycle-yaml",
        workspace_id="ws-cycle-yaml",
        name="cycle_yaml_repo",
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
    assert "schema_a.yaml" in cycle_files
    assert "schema_b.yaml" in cycle_files


def test_yaml_god_node_analysis(db_session, tmp_path):
    """Verifies that heavily referenced base schemas/configs are identified as God-Nodes."""
    god_dir = tmp_path / "god_yaml_repo"
    god_dir.mkdir()

    # Common base schema
    (god_dir / "base_schema.yaml").write_text(
        'title: BaseSchema\n'
        'definitions:\n'
        '  Metadata:\n'
        '    type: object\n',
        encoding="utf-8",
    )

    # 4 distinct schemas referencing base_schema.yaml
    for i in range(1, 5):
        (god_dir / f"service_{i}.yaml").write_text(
            f'title: Service{i}\n'
            'properties:\n'
            '  meta:\n'
            '    $ref: "./base_schema.yaml#/definitions/Metadata"\n',
            encoding="utf-8",
        )

    ws = WorkspaceModel(id="ws-god-yaml", name="God YAML Workspace")
    db_session.add(ws)
    repo = RepositoryModel(
        id="repo-god-yaml",
        workspace_id="ws-god-yaml",
        name="god_yaml_repo",
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
    assert "base_schema.yaml" in god_node_paths


def test_yaml_incremental_indexing(db_session, yaml_repo_dir):
    """Verifies incremental synchronization (add, modify, delete) on YAML repositories."""
    ws = WorkspaceModel(id="ws-inc-yaml", name="Inc YAML Workspace")
    db_session.add(ws)
    repo = RepositoryModel(
        id="repo-inc-yaml",
        workspace_id="ws-inc-yaml",
        name="inc_yaml_repo",
        source_path=str(yaml_repo_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    # Initial counts
    init_nodes = db_session.query(GraphNodeModel).filter_by(repository_id=repo.id).count()

    # 1. ADD: Add new schemas/account.yaml referencing common.yaml
    account_path = yaml_repo_dir / "schemas" / "account.yaml"
    account_path.write_text(
        'title: Account\n'
        'properties:\n'
        '  address:\n'
        '    $ref: "./common.yaml#/definitions/Address"\n',
        encoding="utf-8",
    )

    res_add = indexer.sync_repository(repo.id)
    assert "schemas/account.yaml" in res_add["files_added"]
    assert len(res_add["files_modified"]) == 0
    assert len(res_add["files_deleted"]) == 0

    after_add_nodes = db_session.query(GraphNodeModel).filter_by(repository_id=repo.id).count()
    assert after_add_nodes > init_nodes

    # Check that account.yaml -> common.yaml edge exists
    acc_import = db_session.query(GraphEdgeModel).filter_by(
        repository_id=repo.id,
        source_id=f"{repo.id}:file:schemas/account.yaml",
        edge_type="IMPORTS",
    ).first()
    assert acc_import is not None
    assert acc_import.target_id == f"{repo.id}:file:schemas/common.yaml"

    # 2. MODIFY: Update account.yaml to add new property
    account_path.write_text(
        'title: AccountUpdated\n'
        'properties:\n'
        '  address:\n'
        '    $ref: "./common.yaml#/definitions/Address"\n'
        '  balance: 1000\n',
        encoding="utf-8",
    )
    mtime = os.path.getmtime(str(account_path)) + 2.0
    os.utime(str(account_path), (mtime, mtime))

    res_mod = indexer.sync_repository(repo.id)
    assert "schemas/account.yaml" in res_mod["files_modified"]

    # Verify balance node exists
    balance_node = db_session.query(GraphNodeModel).filter_by(
        repository_id=repo.id,
        file_path="schemas/account.yaml",
        name="balance",
    ).first()
    assert balance_node is not None

    # 3. DELETE: Remove account.yaml
    account_path.unlink()
    res_del = indexer.sync_repository(repo.id)
    assert "schemas/account.yaml" in res_del["files_deleted"]

    # Verify nodes and edges are purged cleanly
    del_nodes = db_session.query(GraphNodeModel).filter_by(
        repository_id=repo.id,
        file_path="schemas/account.yaml",
    ).all()
    assert len(del_nodes) == 0

    del_edges = db_session.query(GraphEdgeModel).filter_by(
        repository_id=repo.id,
        file_path="schemas/account.yaml",
    ).all()
    assert len(del_edges) == 0


def test_yaml_polyglot_repository_isolation(db_session, tmp_path):
    """Verifies that YAML entities coexist safely with Python, TS, Go, Rust, SQL, Shell, and JSON entities."""
    poly_dir = tmp_path / "polyglot_repo"
    poly_dir.mkdir()

    # Python
    (poly_dir / "app.py").write_text("def run(): pass\n", encoding="utf-8")
    # TypeScript
    (poly_dir / "index.ts").write_text("export function start(): void {}\n", encoding="utf-8")
    # Go
    (poly_dir / "main.go").write_text("package main\nfunc Execute() {}\n", encoding="utf-8")
    # Rust
    (poly_dir / "lib.rs").write_text("pub fn compute() {}\n", encoding="utf-8")
    # SQL
    (poly_dir / "schema.sql").write_text("CREATE TABLE users (id INT);\n", encoding="utf-8")
    # Shell
    (poly_dir / "deploy.sh").write_text("#!/bin/bash\nrun_step() { echo 1; }\n", encoding="utf-8")
    # JSON
    (poly_dir / "package.json").write_text('{"name": "polyglot", "version": "1.0.0"}\n', encoding="utf-8")
    # YAML
    (poly_dir / "config.yaml").write_text("app:\n  name: polyglot\n  version: 1.0.0\n", encoding="utf-8")

    ws = WorkspaceModel(id="ws-poly-yaml", name="Polyglot Workspace")
    db_session.add(ws)
    repo = RepositoryModel(
        id="repo-poly-yaml",
        workspace_id="ws-poly-yaml",
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
    assert "yaml" in languages

    # Check distinct runtime prefixes across all 8 languages
    prefixes = {n.id.split(":")[1] for n in nodes if n.node_type != "FILE"}
    assert "py" in prefixes
    assert "ts" in prefixes
    assert "go" in prefixes
    assert "rs" in prefixes
    assert "sql" in prefixes
    assert "sh" in prefixes
    assert "json" in prefixes
    assert "yaml" in prefixes
