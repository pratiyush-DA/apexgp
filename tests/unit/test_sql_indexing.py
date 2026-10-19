"""Integration and Indexing Tests for Phase 9.6: SQL Language Support.

Validates:
- Multi-file SQL database schema indexing (tables, views, columns, foreign keys)
- Graph node extraction (CLASS for tables, TYPE for views, FUNCTION for procedures, VARIABLE for columns)
- Relationship building: DEFINES, CONTAINS, CALLS, and REFERENCES edges
- Cross-file script inclusion (\\i, SOURCE, @script) and import-cycle detection
- Stored procedure calls across files and caller/callee query engine operations
- God-Node architectural hub analysis on database tables
- Incremental indexing via sync_repository (add, modify, delete, rename)
- Polyglot repository isolation across SQL, Python, TypeScript, Go, and Rust
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
def sql_repo_dir(tmp_path):
    """Creates a temporary multi-file SQL repository with schemas, views, and migrations."""
    repo_dir = tmp_path / "sql_test_repo"
    repo_dir.mkdir()

    schema_dir = repo_dir / "schema"
    schema_dir.mkdir()

    # 1. schema/01_users.sql
    users_sql = schema_dir / "01_users.sql"
    users_sql.write_text(
        "-- dialect: postgres\n"
        "CREATE TABLE users (\n"
        "    id INT PRIMARY KEY,\n"
        "    name VARCHAR(100) NOT NULL,\n"
        "    email VARCHAR(100)\n"
        ");\n",
        encoding="utf-8",
    )

    # 2. schema/02_orders.sql
    orders_sql = schema_dir / "02_orders.sql"
    orders_sql.write_text(
        "-- dialect: postgres\n"
        "CREATE TABLE orders (\n"
        "    order_id INT PRIMARY KEY,\n"
        "    user_id INT REFERENCES users(id),\n"
        "    total DECIMAL(10, 2)\n"
        ");\n",
        encoding="utf-8",
    )

    # 3. schema/03_views.sql
    views_sql = schema_dir / "03_views.sql"
    views_sql.write_text(
        "CREATE VIEW active_users AS\n"
        "SELECT id, name FROM users WHERE email IS NOT NULL;\n\n"
        "CREATE MATERIALIZED VIEW top_orders AS\n"
        "SELECT order_id, total FROM orders WHERE total > 100;\n",
        encoding="utf-8",
    )

    # 4. queries/reports.sql
    queries_dir = repo_dir / "queries"
    queries_dir.mkdir()
    reports_sql = queries_dir / "reports.sql"
    reports_sql.write_text(
        "SELECT u.name, o.total\n"
        "FROM users u\n"
        "JOIN orders o ON u.id = o.user_id;\n",
        encoding="utf-8",
    )

    return repo_dir


def test_sql_repository_full_indexing(db_session, sql_repo_dir):
    """Verifies complete indexing of a multi-file SQL repository into property graph nodes and edges."""
    ws = WorkspaceModel(name="SQL Test Workspace")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="sql_test_repo",
        source_type="local",
        source_path=str(sql_repo_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexed_repo = indexer.index_repository(repo.id)
    stats = indexed_repo.stats

    assert stats["total_files"] == 4
    assert stats["languages"].get("sql") == 4
    assert stats["total_nodes"] > 0
    assert stats["total_edges"] > 0

    # 1. Verify Table nodes
    users_node = (
        db_session.query(GraphNodeModel)
        .filter_by(repository_id=repo.id, name="users", node_type="CLASS")
        .first()
    )
    assert users_node is not None
    assert users_node.language == "sql"
    assert users_node.file_path == "schema/01_users.sql"
    assert users_node.id.startswith(f"{repo.id}:sql:")

    orders_node = (
        db_session.query(GraphNodeModel)
        .filter_by(repository_id=repo.id, name="orders", node_type="CLASS")
        .first()
    )
    assert orders_node is not None

    # 2. Verify Column nodes and CONTAINS relationships
    col_nodes = (
        db_session.query(GraphNodeModel)
        .filter(
            GraphNodeModel.repository_id == repo.id,
            GraphNodeModel.file_path == "schema/01_users.sql",
            GraphNodeModel.node_type == "VARIABLE",
        )
        .all()
    )
    assert len(col_nodes) == 3
    col_names = {c.name for c in col_nodes}
    assert col_names == {"id", "name", "email"}

    contains_edges = (
        db_session.query(GraphEdgeModel)
        .filter_by(
            repository_id=repo.id,
            source_id=users_node.id,
            edge_type="CONTAINS",
        )
        .all()
    )
    assert len(contains_edges) == 3

    # 3. Verify Foreign Key CALLS / REFERENCES edge from orders to users
    fk_edges = (
        db_session.query(GraphEdgeModel)
        .filter_by(
            repository_id=repo.id,
            source_id=orders_node.id,
            target_id=users_node.id,
        )
        .all()
    )
    assert len(fk_edges) > 0
    edge_types = {e.edge_type for e in fk_edges}
    assert "CALLS" in edge_types or "REFERENCES" in edge_types

    # 4. Verify View node and dependency on orders/users
    view_node = (
        db_session.query(GraphNodeModel)
        .filter_by(repository_id=repo.id, name="active_users", node_type="TYPE")
        .first()
    )
    assert view_node is not None
    view_edges = (
        db_session.query(GraphEdgeModel)
        .filter_by(
            repository_id=repo.id,
            source_id=view_node.id,
            target_id=users_node.id,
        )
        .all()
    )
    assert len(view_edges) > 0


def test_sql_cross_file_script_inclusion_and_cycles(db_session, tmp_path):
    """Verifies \\i script inclusion creating IMPORTS edges and detection of circular includes."""
    repo_dir = tmp_path / "sql_migrations_repo"
    repo_dir.mkdir()

    # Cyclic migration scripts: a.sql includes b.sql and b.sql includes a.sql
    a_sql = repo_dir / "a.sql"
    a_sql.write_text("\\i b.sql\nCREATE TABLE a_tbl (id INT);\n", encoding="utf-8")

    b_sql = repo_dir / "b.sql"
    b_sql.write_text("\\i a.sql\nCREATE TABLE b_tbl (id INT);\n", encoding="utf-8")

    ws = WorkspaceModel(name="Migrations WS")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="migrations_repo",
        source_type="local",
        source_path=str(repo_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    # Verify IMPORTS edges
    a_file_id = f"{repo.id}:file:a.sql"
    b_file_id = f"{repo.id}:file:b.sql"

    edge_a_to_b = (
        db_session.query(GraphEdgeModel)
        .filter_by(repository_id=repo.id, source_id=a_file_id, target_id=b_file_id, edge_type="IMPORTS")
        .first()
    )
    assert edge_a_to_b is not None

    edge_b_to_a = (
        db_session.query(GraphEdgeModel)
        .filter_by(repository_id=repo.id, source_id=b_file_id, target_id=a_file_id, edge_type="IMPORTS")
        .first()
    )
    assert edge_b_to_a is not None

    # Detect cycle via GraphQueryEngine
    query_engine = GraphQueryEngine(db_session)
    cycles = query_engine.detect_import_cycles(repo.id)
    assert len(cycles) > 0
    cycle_files = set(cycles[0])
    assert "a.sql" in cycle_files and "b.sql" in cycle_files


def test_sql_stored_procedure_calls_and_callers(db_session, tmp_path):
    """Verifies that CALL/EXEC statements resolve to stored procedures across files."""
    repo_dir = tmp_path / "sql_procs_repo"
    repo_dir.mkdir()

    # File 1: defines procedure
    proc_sql = repo_dir / "procedures.sql"
    proc_sql.write_text(
        "-- dialect: postgres\n"
        "CREATE OR REPLACE PROCEDURE sync_accounts(p_id INT) AS $$\n"
        "BEGIN\n"
        "    UPDATE accounts SET synced = 1 WHERE id = p_id;\n"
        "END;\n"
        "$$ LANGUAGE plpgsql;\n",
        encoding="utf-8",
    )

    # File 2: calls procedure
    cron_sql = repo_dir / "cron.sql"
    cron_sql.write_text(
        "CALL sync_accounts(42);\n",
        encoding="utf-8",
    )

    ws = WorkspaceModel(name="Procs WS")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="procs_repo",
        source_type="local",
        source_path=str(repo_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    proc_node = (
        db_session.query(GraphNodeModel)
        .filter_by(repository_id=repo.id, name="sync_accounts", node_type="FUNCTION")
        .first()
    )
    assert proc_node is not None

    # Verify CALLS edge exists from cron.sql to sync_accounts
    cron_file_id = f"{repo.id}:file:cron.sql"
    call_edge = (
        db_session.query(GraphEdgeModel)
        .filter_by(
            repository_id=repo.id,
            source_id=cron_file_id,
            target_id=proc_node.id,
            edge_type="CALLS",
        )
        .first()
    )
    assert call_edge is not None

    # Query engine callers
    query_engine = GraphQueryEngine(db_session)
    callers = query_engine.get_callers(proc_node.id)
    assert len(callers) > 0
    caller_files = [c["node"]["file_path"] for c in callers]
    assert "cron.sql" in caller_files


def test_sql_god_node_analysis(db_session, sql_repo_dir):
    """Verifies that central tables in SQL schemas are correctly identified by God-Node centrality."""
    ws = WorkspaceModel(name="God Node WS")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="god_sql_repo",
        source_type="local",
        source_path=str(sql_repo_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    query_engine = GraphQueryEngine(db_session)
    god_nodes = query_engine.get_god_nodes(repo.id, limit=10, scope="symbol")

    assert len(god_nodes) > 0
    node_names = [g.node.name for g in god_nodes]
    # users is referenced by orders FK, active_users view, and reports query
    assert "users" in node_names


def test_sql_incremental_indexing(db_session, sql_repo_dir):
    """Verifies incremental sync_repository handles added, modified, deleted, and renamed SQL files."""
    ws = WorkspaceModel(name="Inc WS")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="inc_sql_repo",
        source_type="local",
        source_path=str(sql_repo_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    # 1. ADD a new table payments.sql
    payments_sql = sql_repo_dir / "schema" / "05_payments.sql"
    payments_sql.write_text(
        "CREATE TABLE payments (\n"
        "    pay_id INT PRIMARY KEY,\n"
        "    order_id INT REFERENCES orders(order_id)\n"
        ");\n",
        encoding="utf-8",
    )

    sync_res = indexer.sync_repository(repo.id)
    assert "schema/05_payments.sql" in sync_res["files_added"]

    pay_node = (
        db_session.query(GraphNodeModel)
        .filter_by(repository_id=repo.id, name="payments", node_type="CLASS")
        .first()
    )
    assert pay_node is not None

    # 2. MODIFY payments.sql to add an amount column
    payments_sql.write_text(
        "CREATE TABLE payments (\n"
        "    pay_id INT PRIMARY KEY,\n"
        "    amount DECIMAL(10, 2),\n"
        "    order_id INT REFERENCES orders(order_id)\n"
        ");\n",
        encoding="utf-8",
    )
    sync_res2 = indexer.sync_repository(repo.id)
    assert "schema/05_payments.sql" in sync_res2["files_modified"]

    amt_col = (
        db_session.query(GraphNodeModel)
        .filter_by(repository_id=repo.id, name="amount", node_type="VARIABLE")
        .first()
    )
    assert amt_col is not None

    # 3. RENAME payments.sql -> billing.sql
    billing_sql = sql_repo_dir / "schema" / "05_billing.sql"
    os.rename(payments_sql, billing_sql)

    sync_res3 = indexer.sync_repository(repo.id)
    renamed_pairs = [(r["from"], r["to"]) for r in sync_res3.get("files_renamed", [])]
    assert ("schema/05_payments.sql", "schema/05_billing.sql") in renamed_pairs

    # 4. DELETE billing.sql
    os.remove(billing_sql)
    sync_res4 = indexer.sync_repository(repo.id)
    assert "schema/05_billing.sql" in sync_res4["files_deleted"]

    # Verify no dangling payments or billing nodes
    dead_nodes = (
        db_session.query(GraphNodeModel)
        .filter(
            GraphNodeModel.repository_id == repo.id,
            GraphNodeModel.file_path.in_(["schema/05_payments.sql", "schema/05_billing.sql"]),
        )
        .all()
    )
    assert len(dead_nodes) == 0


def test_sql_polyglot_and_repository_isolation(db_session, tmp_path):
    """Verifies that SQL tables do not collide with Python, TS, Go, or Rust symbols of the same name."""
    repo_dir = tmp_path / "polyglot_repo"
    repo_dir.mkdir()

    # 1. SQL table
    (repo_dir / "schema.sql").write_text("CREATE TABLE account (id INT PRIMARY KEY);\n", encoding="utf-8")

    # 2. Python class
    (repo_dir / "model.py").write_text("class Account:\n    pass\n", encoding="utf-8")

    # 3. TypeScript class
    (repo_dir / "service.ts").write_text("export class Account {}\n", encoding="utf-8")

    # 4. Go struct
    (repo_dir / "account.go").write_text("package main\ntype Account struct{}\n", encoding="utf-8")

    # 5. Rust struct
    (repo_dir / "lib.rs").write_text("pub struct Account;\n", encoding="utf-8")

    ws = WorkspaceModel(name="Polyglot WS")
    db_session.add(ws)
    db_session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="poly_repo",
        source_type="local",
        source_path=str(repo_dir),
    )
    db_session.add(repo)
    db_session.commit()

    indexer = RepositoryIndexer(db_session)
    indexer.index_repository(repo.id)

    # Verify each has distinct runtime prefix and language
    sql_node = db_session.query(GraphNodeModel).filter_by(repository_id=repo.id, file_path="schema.sql", name="account").first()
    py_node = db_session.query(GraphNodeModel).filter_by(repository_id=repo.id, file_path="model.py", name="Account").first()
    ts_node = db_session.query(GraphNodeModel).filter_by(repository_id=repo.id, file_path="service.ts", name="Account").first()
    go_node = db_session.query(GraphNodeModel).filter_by(repository_id=repo.id, file_path="account.go", name="Account").first()
    rs_node = db_session.query(GraphNodeModel).filter_by(repository_id=repo.id, file_path="lib.rs", name="Account").first()

    assert sql_node.id.startswith(f"{repo.id}:sql:")
    assert py_node.id.startswith(f"{repo.id}:py:")
    assert ts_node.id.startswith(f"{repo.id}:ts:")
    assert go_node.id.startswith(f"{repo.id}:go:")
    assert rs_node.id.startswith(f"{repo.id}:rs:")
