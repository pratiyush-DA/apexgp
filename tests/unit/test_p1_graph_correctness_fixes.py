"""Regression Tests for Phase 9.11: P1 Graph-Correctness Defects

Validates:
1. SQL Graph Semantics:
   - Foreign keys, views, and DML query references generate REFERENCES edges (NOT CALLS edges).
   - Stored procedure invocations (CALL / EXEC) retain CALLS edges.
   - Both full and incremental indexing behave consistently with zero false CALLS edges.
2. Deterministic, Safe File Resolution:
   - Sibling relative references resolve to the local directory, not a distant candidate.
   - Identical basenames across multiple directories do not trigger arbitrary fallback matching.
   - Unambiguous single basenames across the repository resolve correctly.
   - Full and incremental indexing consistency for JSON, YAML, Shell, and SQL.
3. YAML Language Isolation:
   - Unresolved YAML $ref and extends strings cannot leak into TypeScript/JavaScript
     path-alias or relative resolution.
   - Zero cross-language edges between YAML files and TypeScript/JavaScript files.
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


def test_fix1_sql_graph_semantics_no_calls_edges_for_tables(db_session, tmp_path):
    """Verifies that SQL table-to-table foreign keys and view-to-table dependencies

    produce strictly REFERENCES edges, while procedure calls produce CALLS edges.
    """
    repo_dir = tmp_path / "sql_semantics_repo"
    repo_dir.mkdir()

    # 1. tables.sql: Table definitions with foreign keys
    tables_sql = repo_dir / "tables.sql"
    tables_sql.write_text(
        "CREATE TABLE departments (\n"
        "    dept_id INT PRIMARY KEY,\n"
        "    dept_name VARCHAR(50)\n"
        ");\n\n"
        "CREATE TABLE employees (\n"
        "    emp_id INT PRIMARY KEY,\n"
        "    dept_id INT REFERENCES departments(dept_id),\n"
        "    salary DECIMAL(10, 2)\n"
        ");\n",
        encoding="utf-8",
    )

    # 2. views.sql: View querying employees and departments
    views_sql = repo_dir / "views.sql"
    views_sql.write_text(
        "CREATE VIEW dept_summary AS\n"
        "SELECT d.dept_name, COUNT(e.emp_id) as headcount\n"
        "FROM departments d\n"
        "JOIN employees e ON d.dept_id = e.dept_id\n"
        "GROUP BY d.dept_name;\n",
        encoding="utf-8",
    )

    # 3. procs.sql: Stored procedure definitions and calls
    procs_sql = repo_dir / "procs.sql"
    procs_sql.write_text(
        "CREATE OR REPLACE PROCEDURE audit_log(msg VARCHAR) AS $$\n"
        "BEGIN\n"
        "    -- audit logic\n"
        "END;\n"
        "$$ LANGUAGE plpgsql;\n\n"
        "CREATE OR REPLACE PROCEDURE update_payroll() AS $$\n"
        "BEGIN\n"
        "    CALL audit_log('payroll updated');\n"
        "END;\n"
        "$$ LANGUAGE plpgsql;\n",
        encoding="utf-8",
    )

    ws = WorkspaceModel(name="SQL Semantics WS")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="sql_semantics_repo",
        source_type="local",
        source_path=str(repo_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    # Fetch nodes
    dept_node = (
        db_session.query(GraphNodeModel)
        .filter_by(repository_id=repo.id, name="departments", node_type="CLASS")
        .first()
    )
    emp_node = (
        db_session.query(GraphNodeModel)
        .filter_by(repository_id=repo.id, name="employees", node_type="CLASS")
        .first()
    )
    view_node = (
        db_session.query(GraphNodeModel)
        .filter_by(repository_id=repo.id, name="dept_summary", node_type="TYPE")
        .first()
    )
    audit_proc = (
        db_session.query(GraphNodeModel)
        .filter_by(repository_id=repo.id, name="audit_log", node_type="FUNCTION")
        .first()
    )
    payroll_proc = (
        db_session.query(GraphNodeModel)
        .filter_by(repository_id=repo.id, name="update_payroll", node_type="FUNCTION")
        .first()
    )

    assert dept_node is not None
    assert emp_node is not None
    assert view_node is not None
    assert audit_proc is not None
    assert payroll_proc is not None

    # Check 1: Employees -> Departments (Foreign Key)
    emp_to_dept_edges = (
        db_session.query(GraphEdgeModel)
        .filter_by(repository_id=repo.id, source_id=emp_node.id, target_id=dept_node.id)
        .all()
    )
    assert len(emp_to_dept_edges) > 0
    emp_dept_types = {e.edge_type for e in emp_to_dept_edges}
    assert "REFERENCES" in emp_dept_types
    assert "CALLS" not in emp_dept_types, f"False CALLS edge found on table foreign key: {emp_dept_types}"

    # Check 2: View -> Departments and View -> Employees
    view_edges = (
        db_session.query(GraphEdgeModel)
        .filter_by(repository_id=repo.id, source_id=view_node.id)
        .all()
    )
    view_edge_types = {e.edge_type for e in view_edges}
    assert "REFERENCES" in view_edge_types
    assert "CALLS" not in view_edge_types, f"False CALLS edge found on view definition: {view_edge_types}"

    # Check 3: Stored Procedure -> Stored Procedure (Procedure Call) MUST have CALLS edge
    proc_call_edge = (
        db_session.query(GraphEdgeModel)
        .filter_by(
            repository_id=repo.id,
            source_id=payroll_proc.id,
            target_id=audit_proc.id,
            edge_type="CALLS",
        )
        .first()
    )
    assert proc_call_edge is not None, "Procedure call failed to generate CALLS edge"

    # Query engine check: get_callees on table must NOT return other tables
    query_engine = GraphQueryEngine(db_session)
    table_callees = query_engine.get_callees(emp_node.id)
    assert len(table_callees) == 0, f"Table node returned non-empty callees: {table_callees}"

    # Query engine check: get_callees on payroll_proc MUST return audit_proc
    proc_callees = query_engine.get_callees(payroll_proc.id)
    assert len(proc_callees) == 1
    assert proc_callees[0]["node"]["name"] == "audit_log"


def test_fix1_sql_incremental_indexing_preserves_semantics(db_session, tmp_path):
    """Verifies that incremental indexing (sync_repository) does not create artificial CALLS edges."""
    repo_dir = tmp_path / "sql_inc_repo"
    repo_dir.mkdir()

    t1 = repo_dir / "t1.sql"
    t1.write_text("CREATE TABLE t1 (id INT PRIMARY KEY);\n", encoding="utf-8")

    ws = WorkspaceModel(name="SQL Inc WS")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="sql_inc_repo",
        source_type="local",
        source_path=str(repo_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    # Incrementally add t2.sql referencing t1
    t2 = repo_dir / "t2.sql"
    t2.write_text(
        "CREATE TABLE t2 (id INT PRIMARY KEY, t1_id INT REFERENCES t1(id));\n",
        encoding="utf-8",
    )

    sync_res = indexer.sync_repository(repo.id)
    assert sync_res["status"] == "synchronized"
    assert sync_res["total_changes"] == 1

    t1_node = db_session.query(GraphNodeModel).filter_by(repository_id=repo.id, name="t1", node_type="CLASS").first()
    t2_node = db_session.query(GraphNodeModel).filter_by(repository_id=repo.id, name="t2", node_type="CLASS").first()
    assert t1_node is not None and t2_node is not None

    inc_edges = (
        db_session.query(GraphEdgeModel)
        .filter_by(repository_id=repo.id, source_id=t2_node.id, target_id=t1_node.id)
        .all()
    )
    assert len(inc_edges) > 0
    edge_types = {e.edge_type for e in inc_edges}
    assert "REFERENCES" in edge_types
    assert "CALLS" not in edge_types, f"Incremental indexing created false CALLS edge: {edge_types}"


def test_fix2_deterministic_safe_file_resolution(db_session, tmp_path):
    """Verifies that duplicate basenames in separate subdirectories:

    1. Sibling references resolve strictly to the sibling.
    2. Ambiguous references (duplicate basenames) resolve to None (no arbitrary selection).
    3. Unambiguous unique basenames still resolve correctly.
    """
    repo_dir = tmp_path / "multi_dir_repo"
    repo_dir.mkdir()

    # Directory 1: services/auth
    auth_dir = repo_dir / "services" / "auth"
    auth_dir.mkdir(parents=True)
    (auth_dir / "config.json").write_text('{"service": "auth", "port": 8001}', encoding="utf-8")
    (auth_dir / "schema.yaml").write_text("service: auth\nversion: 1\n", encoding="utf-8")
    (auth_dir / "deploy.sh").write_text("#!/bin/bash\necho 'auth deploy'\n", encoding="utf-8")

    # Directory 2: services/billing (identical basenames!)
    billing_dir = repo_dir / "services" / "billing"
    billing_dir.mkdir(parents=True)
    (billing_dir / "config.json").write_text('{"service": "billing", "port": 8002}', encoding="utf-8")
    (billing_dir / "schema.yaml").write_text("service: billing\nversion: 2\n", encoding="utf-8")
    (billing_dir / "deploy.sh").write_text("#!/bin/bash\necho 'billing deploy'\n", encoding="utf-8")

    # Directory 3: shared (unique basename)
    shared_dir = repo_dir / "shared"
    shared_dir.mkdir(parents=True)
    (shared_dir / "unique_schema.json").write_text('{"title": "Unique"}', encoding="utf-8")
    (shared_dir / "unique_helper.sh").write_text("echo 'unique helper'\n", encoding="utf-8")

    ws = WorkspaceModel(name="MultiDir WS")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="multi_dir_repo",
        source_type="local",
        source_path=str(repo_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    known_files = {
        "services/auth/config.json",
        "services/auth/schema.yaml",
        "services/auth/deploy.sh",
        "services/billing/config.json",
        "services/billing/schema.yaml",
        "services/billing/deploy.sh",
        "shared/unique_schema.json",
        "shared/unique_helper.sh",
    }

    # 1. Sibling relative resolution: services/auth/client.json references ./config.json
    res_auth = indexer._resolve_import_path(
        base_file="services/auth/client.json",
        import_path="./config.json",
        known_files=known_files,
        base_language="json",
    )
    assert res_auth == "services/auth/config.json"

    # Sibling relative resolution in billing
    res_billing = indexer._resolve_import_path(
        base_file="services/billing/client.json",
        import_path="./config.json",
        known_files=known_files,
        base_language="json",
    )
    assert res_billing == "services/billing/config.json"

    # 2. Ambiguity handling: bare "config.json" imported from root without explicit path
    # Multiple candidates exist (services/auth/config.json and services/billing/config.json)
    # Must NOT arbitrarily choose one!
    res_ambiguous_json = indexer._resolve_import_path(
        base_file="root_file.json",
        import_path="config.json",
        known_files=known_files,
        base_language="json",
    )
    assert res_ambiguous_json is None, f"Ambiguous JSON resolution chose: {res_ambiguous_json}"

    res_ambiguous_yaml = indexer._resolve_import_path(
        base_file="root_file.yaml",
        import_path="schema.yaml",
        known_files=known_files,
        base_language="yaml",
    )
    assert res_ambiguous_yaml is None, f"Ambiguous YAML resolution chose: {res_ambiguous_yaml}"

    res_ambiguous_sh = indexer._resolve_import_path(
        base_file="root.sh",
        import_path="deploy.sh",
        known_files=known_files,
        base_language="shell",
    )
    assert res_ambiguous_sh is None, f"Ambiguous Shell resolution chose: {res_ambiguous_sh}"

    # 3. Unique single basename across repo resolves successfully via fallback
    res_unique_json = indexer._resolve_import_path(
        base_file="services/auth/client.json",
        import_path="unique_schema.json",
        known_files=known_files,
        base_language="json",
    )
    assert res_unique_json == "shared/unique_schema.json"

    res_unique_sh = indexer._resolve_import_path(
        base_file="services/auth/runner.sh",
        import_path="unique_helper.sh",
        known_files=known_files,
        base_language="shell",
    )
    assert res_unique_sh == "shared/unique_helper.sh"


def test_fix3_yaml_language_isolation(db_session, tmp_path):
    """Verifies that YAML references cannot resolve into TypeScript path aliases

    or match TypeScript/JavaScript files.
    """
    repo_dir = tmp_path / "polyglot_ts_yaml_repo"
    repo_dir.mkdir()

    # 1. tsconfig.json with path alias
    tsconfig = repo_dir / "tsconfig.json"
    tsconfig.write_text(
        '{\n'
        '  "compilerOptions": {\n'
        '    "baseUrl": ".",\n'
        '    "paths": {\n'
        '      "@utils": ["src/utils/index.ts"],\n'
        '      "@/*": ["src/*"]\n'
        '    }\n'
        '  }\n'
        '}\n',
        encoding="utf-8",
    )

    src_dir = repo_dir / "src" / "utils"
    src_dir.mkdir(parents=True)
    ts_file = src_dir / "index.ts"
    ts_file.write_text("export const PI = 3.14159;\n", encoding="utf-8")

    # 2. YAML file referencing @utils
    yaml_file = repo_dir / "config.yaml"
    yaml_file.write_text(
        "$schema: 'http://json-schema.org/draft-07/schema#'\n"
        "extends: '@utils'\n"
        "definitions:\n"
        "  item:\n"
        "    $ref: '@/components/Button'\n",
        encoding="utf-8",
    )

    ws = WorkspaceModel(name="Polyglot TS-YAML WS")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="polyglot_ts_yaml_repo",
        source_type="local",
        source_path=str(repo_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    # Verify that indexer._resolve_import_path strictly returns None for YAML when referencing TS alias
    known_files = {"src/utils/index.ts", "config.yaml", "tsconfig.json"}
    resolved = indexer._resolve_import_path(
        base_file="config.yaml",
        import_path="@utils",
        known_files=known_files,
        base_language="yaml",
    )
    assert resolved is None, f"YAML reference leaked into TS path alias: {resolved}"

    # Verify that no IMPORTS or REFERENCES edge exists from config.yaml to src/utils/index.ts
    yaml_file_id = f"{repo.id}:file:config.yaml"
    ts_file_id = f"{repo.id}:file:src/utils/index.ts"

    cross_edge = (
        db_session.query(GraphEdgeModel)
        .filter_by(repository_id=repo.id, source_id=yaml_file_id, target_id=ts_file_id)
        .first()
    )
    assert cross_edge is None, "False cross-language edge created between YAML and TypeScript!"


def test_sql_procedure_regex_no_keyword_false_positives():
    """Verifies that dynamic SQL keywords (IMMEDIATE, FORMAT, etc.) are never extracted

    as stored procedure invocations.
    """
    from packages.core.parsers.sql_parser import SQLParser
    parser = SQLParser()

    sql = """
    CREATE OR REPLACE PROCEDURE dynamic_worker() AS $$
    BEGIN
        EXECUTE IMMEDIATE 'UPDATE accounts SET balance = 0';
        EXECUTE format('SELECT * FROM %I', 'my_table');
        CALL valid_audit_procedure('worker finished');
    END;
    $$ LANGUAGE plpgsql;
    """
    res = parser.parse_code(sql, file_path="worker.sql")
    call_names = {c.callee_name for c in res.calls if c.edge_type == "CALLS"}

    assert "valid_audit_procedure" in call_names
    assert "IMMEDIATE" not in call_names, "Dynamic SQL keyword IMMEDIATE extracted as procedure call!"
    assert "format" not in call_names, "Builtin/format function extracted as procedure call!"


def test_path_resolution_no_cross_directory_jump_on_missing_subpaths(db_session):
    """Verifies that an explicit subpath that does not exist never falls back

    to a completely different directory matching only the basename.
    """
    indexer = RepositoryIndexer(db_session)
    known_files = {
        "services/auth/app.json",
        "archive/old_version/missing.json",
        "scripts/helpers.sh",
        "archive/tools/missing.sh",
    }

    # JSON with explicit subpath that is missing
    res_json = indexer._resolve_import_path(
        base_file="services/auth/app.json",
        import_path="services/billing/missing.json",
        known_files=known_files,
        base_language="json",
    )
    assert res_json is None, f"Explicit subpath falsely jumped to: {res_json}"

    # Shell with explicit subpath that is missing
    res_sh = indexer._resolve_import_path(
        base_file="services/auth/runner.sh",
        import_path="./deploy/missing.sh",
        known_files=known_files,
        base_language="shell",
    )
    assert res_sh is None, f"Explicit subpath falsely jumped to: {res_sh}"
