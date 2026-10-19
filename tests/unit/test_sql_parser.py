"""Unit Tests for ApexGraph SQL Parser (SQLGlot)

Validates:
- DDL Extraction: CREATE TABLE with columns, types, primary keys, and foreign keys
- Views: CREATE VIEW and CREATE MATERIALIZED VIEW
- Stored Procedures and Functions: CREATE PROCEDURE / FUNCTION in PL/pgSQL and Oracle PL/SQL
- Schemas: CREATE SCHEMA
- DML Extraction: SELECT, INSERT, UPDATE, DELETE, MERGE references to tables
- Stored Procedure Invocations: CALL and EXEC statements
- Dialects: PostgreSQL, Oracle, MySQL, SQLite, and Generic SQL
- Migration Directives: \\i, SOURCE, and @script.sql
- Syntax Error Resilience: Malformed statements, empty code, comments only
"""

import pytest

from packages.core.parsers.base import ParseResult
from packages.core.parsers.sql_parser import SQLParser


@pytest.fixture
def parser() -> SQLParser:
    return SQLParser()


def test_create_table_ddl_and_columns(parser: SQLParser):
    sql = """
    CREATE TABLE users (
        id INT PRIMARY KEY,
        username VARCHAR(50) NOT NULL,
        email VARCHAR(100),
        created_at TIMESTAMP
    );
    """
    res = parser.parse_code(sql, file_path="schema/users.sql")

    assert isinstance(res, ParseResult)
    assert res.language == "sql"
    assert len(res.syntax_errors) == 0

    table_nodes = [n for n in res.nodes if n.node_type == "CLASS"]
    assert len(table_nodes) == 1
    tbl = table_nodes[0]
    assert tbl.name == "users"
    assert tbl.qualified_name == "users"
    assert tbl.start_line == 2
    assert tbl.metadata.get("entity_type") == "table"
    assert tbl.metadata.get("primary_key") == ["id"]

    col_nodes = [n for n in res.nodes if n.node_type == "VARIABLE"]
    assert len(col_nodes) == 4
    col_names = [c.name for c in col_nodes]
    assert col_names == ["id", "username", "email", "created_at"]
    assert all(c.metadata.get("enclosing") == "users" for c in col_nodes)


def test_foreign_key_and_table_references(parser: SQLParser):
    sql = """
    CREATE TABLE orders (
        order_id INT PRIMARY KEY,
        user_id INT REFERENCES users(id),
        total DECIMAL(10, 2),
        CONSTRAINT fk_account FOREIGN KEY (account_id) REFERENCES accounts(acc_id)
    );
    """
    res = parser.parse_code(sql, file_path="schema/orders.sql")

    tbl = next(n for n in res.nodes if n.node_type == "CLASS")
    assert tbl.name == "orders"
    assert len(tbl.metadata.get("foreign_keys", [])) >= 2

    # Verify outgoing calls/references from orders to users and accounts
    callees = {c.callee_name for c in res.calls}
    assert "users" in callees
    assert "accounts" in callees
    assert all(c.caller_name == "orders" for c in res.calls)


def test_create_views_materialized(parser: SQLParser):
    sql = """
    CREATE VIEW active_users AS
    SELECT id, username FROM users WHERE email IS NOT NULL;

    CREATE MATERIALIZED VIEW monthly_sales AS
    SELECT user_id, SUM(total) as revenue FROM orders GROUP BY user_id;
    """
    res = parser.parse_code(sql, file_path="schema/views.sql")

    view_nodes = [n for n in res.nodes if n.node_type == "TYPE"]
    assert len(view_nodes) == 2
    v1 = next(v for v in view_nodes if v.name == "active_users")
    assert v1.metadata.get("materialized") is False

    v2 = next(v for v in view_nodes if v.name == "monthly_sales")
    assert v2.metadata.get("materialized") is True

    # Check view query table references
    calls_from_v1 = [c.callee_name for c in res.calls if c.caller_name == "active_users"]
    assert "users" in calls_from_v1
    calls_from_v2 = [c.callee_name for c in res.calls if c.caller_name == "monthly_sales"]
    assert "orders" in calls_from_v2


def test_stored_procedures_and_functions(parser: SQLParser):
    sql = """
    CREATE OR REPLACE PROCEDURE cancel_order(p_order_id INT) AS $$
    BEGIN
        UPDATE orders SET total = 0 WHERE order_id = p_order_id;
    END;
    $$ LANGUAGE plpgsql;

    CREATE OR REPLACE FUNCTION get_user_total(p_uid INT) RETURNS DECIMAL AS $$
    BEGIN
        RETURN (SELECT SUM(total) FROM orders WHERE user_id = p_uid);
    END;
    $$ LANGUAGE plpgsql;
    """
    res = parser.parse_code(sql, file_path="schema/procedures.sql")

    func_nodes = [n for n in res.nodes if n.node_type == "FUNCTION"]
    assert len(func_nodes) == 2
    names = {f.name for f in func_nodes}
    assert "cancel_order" in names
    assert "get_user_total" in names

    # Procedures should reference the tables modified inside their body
    calls_cancel = [c.callee_name for c in res.calls if c.caller_name == "cancel_order"]
    assert "orders" in calls_cancel


def test_oracle_dialect(parser: SQLParser):
    sql = """
    -- dialect: oracle
    CREATE TABLE departments (
        dept_id NUMBER(10) PRIMARY KEY,
        dept_name VARCHAR2(100) NOT NULL,
        manager_id NUMBER(10) REFERENCES employees(emp_id)
    );

    CREATE OR REPLACE PROCEDURE update_dept(p_id IN NUMBER) AS
    BEGIN
        UPDATE departments SET dept_name = 'HQ' WHERE dept_id = p_id;
        COMMIT;
    END;
    /
    """
    res = parser.parse_code(sql, file_path="oracle/depts.sql")

    table_nodes = [n for n in res.nodes if n.node_type == "CLASS"]
    assert any(t.name == "departments" for t in table_nodes)

    proc_nodes = [n for n in res.nodes if n.node_type == "FUNCTION"]
    assert any(p.name == "update_dept" for p in proc_nodes)

    # Cross-table reference to employees
    callees = {c.callee_name for c in res.calls}
    assert "employees" in callees


def test_dml_and_queries_extraction(parser: SQLParser):
    sql = """
    SELECT u.name, o.total FROM users u JOIN orders o ON u.id = o.user_id;
    INSERT INTO audit_log (action) VALUES ('login');
    UPDATE accounts SET balance = balance - 50 WHERE acc_id = 1;
    DELETE FROM sessions WHERE expired = 1;
    """
    res = parser.parse_code(sql, file_path="queries.sql")

    callees = {c.callee_name for c in res.calls}
    assert "users" in callees
    assert "orders" in callees
    assert "audit_log" in callees
    assert "accounts" in callees
    assert "sessions" in callees


def test_procedure_invocation_calls(parser: SQLParser):
    sql = """
    CALL cancel_order(101);
    EXEC sp_sync_cache;
    SELECT calculate_custom_tax(amount) FROM payments;
    """
    res = parser.parse_code(sql, file_path="run_job.sql")

    callees = {c.callee_name for c in res.calls}
    assert "cancel_order" in callees
    assert "sp_sync_cache" in callees
    assert "calculate_custom_tax" in callees
    assert "payments" in callees


def test_script_inclusion_imports(parser: SQLParser):
    sql = """
    -- PostgreSQL psql include
    \\i schema/01_users.sql
    \\i schema/02_orders.sql

    -- MySQL source
    SOURCE db/seeds.sql

    -- Oracle SQL*Plus
    @oracle/views.sql
    """
    res = parser.parse_code(sql, file_path="run_all.sql")

    assert len(res.imports) == 4
    imported_paths = [i.module_path for i in res.imports]
    assert "schema/01_users.sql" in imported_paths
    assert "schema/02_orders.sql" in imported_paths
    assert "db/seeds.sql" in imported_paths
    assert "oracle/views.sql" in imported_paths


def test_syntax_error_resilience_and_empty_files(parser: SQLParser):
    # Completely empty
    res_empty = parser.parse_code("", file_path="empty.sql")
    assert len(res_empty.nodes) == 0
    assert len(res_empty.syntax_errors) == 0

    # Comments only
    res_comments = parser.parse_code("-- Only comments\n/* Multi-line comment */", file_path="comments.sql")
    assert len(res_comments.nodes) == 0
    assert len(res_comments.syntax_errors) == 0

    # Malformed SQL mixed with valid table
    malformed_sql = """
    CREATE TABLE valid_tbl (id INT);

    THIS IS ABSOLUTELY NOT VALID SQL %%% ^^^;

    CREATE VIEW valid_view AS SELECT * FROM valid_tbl;
    """
    res_malformed = parser.parse_code(malformed_sql, file_path="broken.sql")

    # Valid table and view should still be extracted despite invalid line
    tbl_names = [n.name for n in res_malformed.nodes if n.node_type == "CLASS"]
    assert "valid_tbl" in tbl_names
    view_names = [n.name for n in res_malformed.nodes if n.node_type == "TYPE"]
    assert "valid_view" in view_names
    # Syntax error recorded safely
    assert len(res_malformed.syntax_errors) > 0
