"""ApexGraph SQL Parser using SQLGlot

Extracts:
- Table definitions (CLASS) with schema, columns, primary keys, and foreign keys
- Column definitions (VARIABLE) enclosed by their parent table
- Views and materialized views (TYPE)
- Functions and stored procedures (FUNCTION)
- Schemas (MODULE)
- Foreign key dependencies, view query references, and DDL/DML table references (ExtractedCall)
- Stored procedure calls: CALL proc(), EXEC proc(), and user-defined function calls (ExtractedCall)
- Script inclusion directives: \\i, SOURCE, @script.sql (ExtractedImport)
- Multi-dialect support: PostgreSQL, Oracle, MySQL, SQLite, and Generic SQL
- Safe syntax error reporting without crashing indexing
"""

import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

import sqlglot
from sqlglot import ErrorLevel, TokenType, exp
from sqlglot.errors import ParseError

from packages.core.parsers.base import (
    BaseParser,
    ExtractedCall,
    ExtractedImport,
    ExtractedNode,
    ExtractedReExport,
    ParseResult,
)


SQL_BUILTIN_FUNCTIONS: Set[str] = {
    "abs", "acos", "asin", "atan", "atan2", "avg", "ceil", "ceiling", "coalesce",
    "concat", "concat_ws", "cos", "cot", "count", "curdate", "current_date",
    "current_time", "current_timestamp", "current_user", "curtime", "date",
    "date_add", "date_sub", "datediff", "datepart", "day", "dense_rank", "exp",
    "extract", "first_value", "floor", "format", "greatest", "group_concat",
    "hex", "hour", "if", "ifnull", "iif", "instr", "isnull", "lag", "last_value",
    "lead", "least", "left", "len", "length", "ln", "locate", "log", "log10",
    "lower", "lpad", "ltrim", "max", "md5", "mid", "min", "minute", "mod",
    "month", "now", "nullif", "nvl", "nvl2", "pi", "position", "power", "radians",
    "rand", "random", "rank", "repeat", "replace", "reverse", "right", "round",
    "row_number", "rpad", "rtrim", "second", "sign", "sin", "soundex", "space",
    "sqrt", "std", "stddev", "string_agg", "str_to_date", "substr", "substring",
    "sum", "tan", "to_char", "to_date", "to_number", "to_timestamp", "trim",
    "truncate", "upper", "user", "variance", "year",
}


class SQLParser(BaseParser):
    """AST parser for SQL source files supporting PostgreSQL, Oracle, MySQL, SQLite, and Generic dialects."""

    def __init__(self):
        super().__init__()

    def detect_dialect(self, code: str) -> str:
        """Detects the most appropriate SQLGlot dialect for the given SQL source."""
        # Check explicit dialect hint in comments: -- dialect: oracle / -- dialect: postgres
        hint_match = re.search(r"--\s*dialect\s*:\s*([a-zA-Z0-9_]+)", code, re.IGNORECASE)
        if hint_match:
            hint = hint_match.group(1).lower()
            if hint in sqlglot.dialects.Dialect.classes:
                return hint

        code_upper = code.upper()

        # Oracle heuristics: VARCHAR2, NUMBER(, PL/SQL block terminators, SYSDATE
        if (
            "VARCHAR2" in code_upper
            or "NUMBER(" in code_upper
            or "PLS_INTEGER" in code_upper
            or re.search(r"\bROWNUM\b", code_upper)
            or re.search(r"\bSYSDATE\b", code_upper)
            or re.search(r"/\s*$", code, re.MULTILINE)
        ):
            return "oracle"

        # PostgreSQL heuristics: $$ delimiters, plpgsql, SERIAL, BIGSERIAL, TIMESTAMPTZ, BYTEA
        if (
            "PLPGSQL" in code_upper
            or "$$" in code
            or re.search(r"\bSERIAL\b", code_upper)
            or re.search(r"\bBIGSERIAL\b", code_upper)
            or re.search(r"\bTIMESTAMPTZ\b", code_upper)
            or re.search(r"\bCITEXT\b", code_upper)
        ):
            return "postgres"

        # MySQL heuristics: AUTO_INCREMENT, ENGINE=
        if "AUTO_INCREMENT" in code_upper or "ENGINE=" in code_upper:
            return "mysql"

        # SQLite heuristics: AUTOINCREMENT
        if "AUTOINCREMENT" in code_upper:
            return "sqlite"

        # Default to postgres as general-purpose standard SQL parser
        return "postgres"

    def parse_code(self, code: str, file_path: str = "") -> ParseResult:
        """Standardized BaseParser implementation parsing SQL code into a ParseResult."""
        if not code or not code.strip():
            return ParseResult(
                file_path=file_path,
                language="sql",
                nodes=[],
                imports=[],
                calls=[],
                exports=[],
                re_exports=[],
                syntax_errors=[],
            )

        dialect = self.detect_dialect(code)

        nodes: List[ExtractedNode] = []
        imports: List[ExtractedImport] = []
        calls: List[ExtractedCall] = []
        syntax_errors: List[str] = []

        # 1. Extract script inclusion directives (e.g. \i file.sql, SOURCE file.sql, @script.sql)
        self._extract_include_imports(code, imports)

        # 2. Tokenize and segment statements with line number tracking
        statement_blocks = self._segment_statements(code, dialect)

        # 3. Process each statement block
        for block in statement_blocks:
            raw_sql = block["raw_sql"]
            start_line = block["start_line"]
            end_line = block["end_line"]
            start_col = block["start_col"]
            end_col = block["end_col"]
            tokens_in_block = block["tokens"]

            # If statement only consists of comments or whitespace, skip it
            stripped_code = re.sub(r"--.*?$|/\*.*?\*/", "", raw_sql, flags=re.MULTILINE | re.DOTALL).strip()
            if not stripped_code:
                continue

            # Try parsing with detected dialect, falling back to postgres / generic if needed
            stmt = None
            parse_err = None
            dialects_to_try = [dialect]
            for fallback in ["postgres", "oracle", "mysql", "sqlite", None]:
                if fallback not in dialects_to_try:
                    dialects_to_try.append(fallback)

            # First attempt strict parsing to detect any syntax errors
            for d in dialects_to_try:
                try:
                    stmt = sqlglot.parse_one(raw_sql, read=d)
                    if stmt:
                        parse_err = None
                        break
                except ParseError as pe:
                    if parse_err is None:
                        parse_err = pe
                except Exception as ex:
                    if parse_err is None:
                        parse_err = ex

            # If strict parsing failed, attempt lenient parsing for partial AST recovery
            if stmt is None:
                for d in dialects_to_try:
                    try:
                        recovered = sqlglot.parse_one(raw_sql, read=d, error_level=ErrorLevel.IGNORE)
                        if recovered and isinstance(recovered, (exp.Create, exp.Alter, exp.Select, exp.Insert, exp.Update, exp.Delete, exp.Command)):
                            stmt = recovered
                            break
                    except Exception:
                        pass

            if parse_err is not None:
                err_msg = f"SQL parse error at line {start_line}: {parse_err}"
                syntax_errors.append(err_msg)

            if stmt is None:
                # If statement only consists of comments or whitespace, ignore it
                stripped_code = re.sub(r"--.*?$|/\*.*?\*/", "", raw_sql, flags=re.MULTILINE | re.DOTALL).strip()
                if not stripped_code:
                    continue
                # Check for procedure calls like CALL / EXEC in raw text even if parser failed
                self._fallback_extract_calls(raw_sql, start_line, calls)
                continue

            # 4. Extract entities and relationships from the parsed statement
            try:
                self._extract_from_statement(
                    stmt=stmt,
                    raw_sql=raw_sql,
                    start_line=start_line,
                    end_line=end_line,
                    start_col=start_col,
                    end_col=end_col,
                    tokens=tokens_in_block,
                    nodes=nodes,
                    calls=calls,
                )
            except Exception as ex:
                syntax_errors.append(f"Statement processing error at line {start_line}: {ex}")

        # Deterministic sorting of extracted entities
        nodes.sort(key=lambda n: (n.start_line, n.qualified_name))
        calls.sort(key=lambda c: (c.line_number, c.callee_name))
        imports.sort(key=lambda i: (i.line_number, i.module_path))

        return ParseResult(
            file_path=file_path,
            language="sql",
            nodes=nodes,
            imports=imports,
            calls=calls,
            exports=[n.name for n in nodes if n.node_type in ("CLASS", "TYPE", "FUNCTION")],
            re_exports=[],
            syntax_errors=syntax_errors,
        )

    def _extract_include_imports(self, code: str, imports: List[ExtractedImport]) -> None:
        """Extracts script inclusion directives (e.g., \\i, SOURCE, @script.sql)."""
        lines = code.splitlines()
        for idx, line in enumerate(lines):
            line_no = idx + 1
            stripped = line.strip()

            # PostgreSQL psql: \i path/to/file.sql
            m_psql = re.match(r"^\\i\s+['\"]?([^'\";\s]+)['\"]?", stripped)
            if m_psql:
                target = m_psql.group(1)
                imports.append(
                    ExtractedImport(
                        module_path=target,
                        imported_symbols=["*"],
                        line_number=line_no,
                        is_relative=target.startswith(("./", "../")),
                        raw_statement=stripped,
                    )
                )
                continue

            # MySQL: SOURCE path/to/file.sql
            m_mysql = re.match(r"^SOURCE\s+['\"]?([^'\";\s]+)['\"]?", stripped, re.IGNORECASE)
            if m_mysql:
                target = m_mysql.group(1)
                imports.append(
                    ExtractedImport(
                        module_path=target,
                        imported_symbols=["*"],
                        line_number=line_no,
                        is_relative=target.startswith(("./", "../")),
                        raw_statement=stripped,
                    )
                )
                continue

            # Oracle SQL*Plus: @path/to/file.sql or @@path/to/file.sql
            m_ora = re.match(r"^@{1,2}\s*['\"]?([^'\";\s]+)['\"]?", stripped)
            if m_ora:
                target = m_ora.group(1)
                imports.append(
                    ExtractedImport(
                        module_path=target,
                        imported_symbols=["*"],
                        line_number=line_no,
                        is_relative=target.startswith(("./", "../")),
                        raw_statement=stripped,
                    )
                )

    def _segment_statements(self, code: str, dialect: str) -> List[Dict[str, Any]]:
        """Segments code into individual statement blocks with line numbers and character offsets."""
        try:
            toks = sqlglot.tokenize(code, read=dialect)
        except Exception:
            try:
                toks = sqlglot.tokenize(code)
            except Exception:
                toks = []

        if not toks:
            # Fallback to simple split by semicolon
            return self._regex_segment_statements(code)

        blocks: List[Dict[str, Any]] = []
        current_tokens: List[sqlglot.tokens.Token] = []

        for token in toks:
            current_tokens.append(token)
            if token.token_type == TokenType.SEMICOLON:
                meaningful = [t for t in current_tokens if t.token_type != TokenType.SEMICOLON]
                if meaningful:
                    start_char = meaningful[0].start
                    end_char = meaningful[-1].end + 1
                    blocks.append(
                        {
                            "start_line": meaningful[0].line,
                            "end_line": meaningful[-1].line,
                            "start_col": meaningful[0].col,
                            "end_col": meaningful[-1].col,
                            "raw_sql": code[start_char:end_char].strip(),
                            "tokens": meaningful,
                        }
                    )
                current_tokens = []

        if current_tokens:
            meaningful = [t for t in current_tokens if t.token_type != TokenType.SEMICOLON]
            if meaningful:
                start_char = meaningful[0].start
                end_char = meaningful[-1].end + 1
                blocks.append(
                    {
                        "start_line": meaningful[0].line,
                        "end_line": meaningful[-1].line,
                        "start_col": meaningful[0].col,
                        "end_col": meaningful[-1].col,
                        "raw_sql": code[start_char:end_char].strip(),
                        "tokens": meaningful,
                    }
                )

        return blocks

    def _regex_segment_statements(self, code: str) -> List[Dict[str, Any]]:
        """Fallback statement segmentation when tokenization fails."""
        blocks: List[Dict[str, Any]] = []
        raw_stmts = re.split(r";|\n/\s*\n", code)
        current_line = 1
        for stmt in raw_stmts:
            stmt_lines = stmt.count("\n")
            stripped = stmt.strip()
            if stripped:
                blocks.append(
                    {
                        "start_line": current_line,
                        "end_line": current_line + stmt_lines,
                        "start_col": 1,
                        "end_col": 1,
                        "raw_sql": stripped,
                        "tokens": [],
                    }
                )
            current_line += stmt_lines + 1
        return blocks

    def _extract_from_statement(
        self,
        stmt: exp.Expression,
        raw_sql: str,
        start_line: int,
        end_line: int,
        start_col: int,
        end_col: int,
        tokens: List[sqlglot.tokens.Token],
        nodes: List[ExtractedNode],
        calls: List[ExtractedCall],
    ) -> None:
        """Extracts definitions and relational references from an individual SQL statement."""
        # 1. CREATE statements (TABLE, VIEW, FUNCTION, PROCEDURE, SCHEMA)
        if isinstance(stmt, exp.Create):
            self._handle_create_statement(
                stmt=stmt,
                raw_sql=raw_sql,
                start_line=start_line,
                end_line=end_line,
                start_col=start_col,
                end_col=end_col,
                tokens=tokens,
                nodes=nodes,
                calls=calls,
            )
            return

        # 2. ALTER statements (ALTER TABLE ... ADD CONSTRAINT / FOREIGN KEY)
        if isinstance(stmt, exp.Alter):
            self._handle_alter_statement(stmt=stmt, raw_sql=raw_sql, line_number=start_line, calls=calls)
            return

        # 3. DML / Query statements (SELECT, INSERT, UPDATE, DELETE, MERGE)
        if isinstance(stmt, (exp.Select, exp.Insert, exp.Update, exp.Delete, exp.Merge)):
            self._handle_query_statement(stmt=stmt, raw_sql=raw_sql, line_number=start_line, calls=calls)
            return

        # 4. Procedure invocations via Command (e.g. CALL proc(), EXEC proc())
        if isinstance(stmt, exp.Command):
            cmd_name = str(stmt.this).upper()
            if cmd_name in ("CALL", "EXEC", "EXECUTE"):
                expr_text = str(stmt.args.get("expression") or "")
                callee = self._parse_callee_from_command(expr_text)
                if callee:
                    calls.append(
                        ExtractedCall(
                            caller_name="<module>",
                            callee_name=callee,
                            line_number=start_line,
                            raw_call=raw_sql[:200],
                        )
                    )
            return

        # 5. Generic statement: scan for procedure calls and referenced tables
        self._fallback_extract_calls(raw_sql, start_line, calls)

    def _handle_create_statement(
        self,
        stmt: exp.Create,
        raw_sql: str,
        start_line: int,
        end_line: int,
        start_col: int,
        end_col: int,
        tokens: List[sqlglot.tokens.Token],
        nodes: List[ExtractedNode],
        calls: List[ExtractedCall],
    ) -> None:
        """Processes a CREATE statement (TABLE, VIEW, FUNCTION, PROCEDURE, SCHEMA)."""
        kind = str(stmt.args.get("kind") or "").upper()

        # A. CREATE TABLE
        if kind == "TABLE" or (not kind and isinstance(stmt.this, exp.Schema)):
            table_name = ""
            schema_name = ""
            if isinstance(stmt.this, exp.Schema) and isinstance(stmt.this.this, exp.Table):
                table_name = stmt.this.this.name
                schema_name = stmt.this.this.db or ""
            elif isinstance(stmt.this, exp.Table):
                table_name = stmt.this.name
                schema_name = stmt.this.db or ""
            elif hasattr(stmt.this, "name"):
                table_name = stmt.this.name

            if not table_name:
                return

            qualified_name = f"{schema_name}.{table_name}" if schema_name else table_name
            columns_meta: List[Dict[str, Any]] = []
            primary_keys: List[str] = []
            foreign_keys: List[Dict[str, Any]] = []

            # Extract columns and constraints if schema is available
            schema_expr = stmt.this if isinstance(stmt.this, exp.Schema) else stmt.find(exp.Schema)
            if schema_expr:
                for col_def in schema_expr.expressions:
                    if isinstance(col_def, exp.ColumnDef):
                        cname = col_def.this.name if hasattr(col_def.this, "name") else str(col_def.this)
                        ctype = str(col_def.kind) if col_def.kind else "UNKNOWN"
                        is_pk = any(isinstance(c.kind, exp.PrimaryKeyColumnConstraint) for c in col_def.constraints)
                        if is_pk:
                            primary_keys.append(cname)

                        # Check column-level foreign key reference: REFERENCES target_table(col)
                        for ref in col_def.find_all(exp.Reference):
                            target_tbl = ref.find(exp.Table)
                            target_col = ref.find(exp.Identifier)
                            target_tbl_name = target_tbl.name if target_tbl else ""
                            target_col_name = target_col.name if target_col else ""
                            if target_tbl_name:
                                foreign_keys.append(
                                    {
                                        "column": cname,
                                        "referenced_table": target_tbl_name,
                                        "referenced_column": target_col_name,
                                    }
                                )
                                calls.append(
                                    ExtractedCall(
                                        caller_name=table_name,
                                        callee_name=target_tbl_name,
                                        line_number=start_line,
                                        raw_call=f"FOREIGN KEY ({cname}) REFERENCES {target_tbl_name}",
                                    )
                                )

                        columns_meta.append(
                            {
                                "name": cname,
                                "type": ctype,
                                "primary_key": is_pk,
                                "nullable": not any(isinstance(c.kind, exp.NotNullColumnConstraint) for c in col_def.constraints),
                            }
                        )

                        # Extract column as child VARIABLE node
                        col_line = self._find_token_line(tokens, cname, fallback=start_line)
                        nodes.append(
                            ExtractedNode(
                                name=cname,
                                node_type="VARIABLE",
                                qualified_name=f"{qualified_name}.{cname}",
                                start_line=col_line,
                                end_line=col_line,
                                start_col=0,
                                end_col=0,
                                language="sql",
                                signature=f"{cname} {ctype}",
                                docstring="",
                                metadata={
                                    "enclosing": qualified_name,
                                    "entity_type": "column",
                                    "data_type": ctype,
                                    "primary_key": is_pk,
                                },
                            )
                        )

                    # Table-level constraints: CONSTRAINT fk FOREIGN KEY (col) REFERENCES target(col)
                    elif isinstance(col_def, (exp.Constraint, exp.ForeignKey)):
                        for fk in col_def.find_all(exp.ForeignKey):
                            source_cols = [id_node.name for id_node in fk.expressions if hasattr(id_node, "name")]
                            ref = fk.find(exp.Reference)
                            if ref:
                                target_tbl = ref.find(exp.Table)
                                target_tbl_name = target_tbl.name if target_tbl else ""
                                target_cols = [id_node.name for id_node in ref.expressions if hasattr(id_node, "name")]
                                if target_tbl_name:
                                    foreign_keys.append(
                                        {
                                            "columns": source_cols,
                                            "referenced_table": target_tbl_name,
                                            "referenced_columns": target_cols,
                                        }
                                    )
                                    calls.append(
                                        ExtractedCall(
                                            caller_name=table_name,
                                            callee_name=target_tbl_name,
                                            line_number=start_line,
                                            raw_call=f"FOREIGN KEY REFERENCES {target_tbl_name}",
                                        )
                                    )

            # Create Table Node (CLASS)
            nodes.append(
                ExtractedNode(
                    name=table_name,
                    node_type="CLASS",
                    qualified_name=qualified_name,
                    start_line=start_line,
                    end_line=end_line,
                    start_col=start_col,
                    end_col=end_col,
                    language="sql",
                    signature=f"CREATE TABLE {qualified_name}",
                    docstring="",
                    metadata={
                        "entity_type": "table",
                        "schema": schema_name or None,
                        "columns": columns_meta,
                        "primary_key": primary_keys,
                        "foreign_keys": foreign_keys,
                    },
                )
            )

        # B. CREATE VIEW / MATERIALIZED VIEW
        elif "VIEW" in kind:
            view_name = ""
            schema_name = ""
            if isinstance(stmt.this, exp.Table):
                view_name = stmt.this.name
                schema_name = stmt.this.db or ""
            elif hasattr(stmt.this, "name"):
                view_name = stmt.this.name

            if not view_name:
                return

            qualified_name = f"{schema_name}.{view_name}" if schema_name else view_name
            is_mat = "MATERIALIZED" in kind or "MATERIALIZED" in raw_sql.upper()

            # View node (TYPE)
            nodes.append(
                ExtractedNode(
                    name=view_name,
                    node_type="TYPE",
                    qualified_name=qualified_name,
                    start_line=start_line,
                    end_line=end_line,
                    start_col=start_col,
                    end_col=end_col,
                    language="sql",
                    signature=f"CREATE {'MATERIALIZED ' if is_mat else ''}VIEW {qualified_name}",
                    docstring="",
                    metadata={
                        "entity_type": "view",
                        "materialized": is_mat,
                        "schema": schema_name or None,
                    },
                )
            )

            # Extract referenced tables from the view query
            for tbl in stmt.find_all(exp.Table):
                if tbl.name and tbl.name.lower() != view_name.lower():
                    calls.append(
                        ExtractedCall(
                            caller_name=view_name,
                            callee_name=tbl.name,
                            line_number=start_line,
                            raw_call=f"VIEW {view_name} REFERENCES {tbl.name}",
                        )
                    )

        # C. CREATE FUNCTION / PROCEDURE
        elif "FUNCTION" in kind or "PROCEDURE" in kind or isinstance(stmt.this, exp.UserDefinedFunction):
            proc_name = ""
            if isinstance(stmt.this, exp.UserDefinedFunction):
                proc_name = stmt.this.this.name if hasattr(stmt.this.this, "name") else str(stmt.this.this)
            elif isinstance(stmt.this, exp.Table):
                proc_name = stmt.this.name
            elif hasattr(stmt.this, "name"):
                proc_name = stmt.this.name

            # Fallback regex extraction if sqlglot UserDefinedFunction lacks name
            if not proc_name:
                m_proc = re.search(r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:PROCEDURE|FUNCTION)\s+([a-zA-Z0-9_\.]+)", raw_sql, re.IGNORECASE)
                if m_proc:
                    proc_name = m_proc.group(1).split(".")[-1]

            if not proc_name:
                return

            entity_type = "procedure" if "PROCEDURE" in kind or "PROCEDURE" in raw_sql.upper() else "function"

            nodes.append(
                ExtractedNode(
                    name=proc_name,
                    node_type="FUNCTION",
                    qualified_name=proc_name,
                    start_line=start_line,
                    end_line=end_line,
                    start_col=start_col,
                    end_col=end_col,
                    language="sql",
                    signature=f"CREATE {entity_type.upper()} {proc_name}",
                    docstring="",
                    metadata={
                        "entity_type": entity_type,
                    },
                )
            )

            # Extract tables modified or queried inside the procedure body
            body_tables: Set[str] = set()
            for tbl in stmt.find_all(exp.Table):
                if tbl.name and tbl.name.lower() != proc_name.lower():
                    body_tables.add(tbl.name)

            # Regex scan over body text to catch procedural blocks
            for m_tbl in re.finditer(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([a-zA-Z0-9_]+)", raw_sql, re.IGNORECASE):
                t_name = m_tbl.group(1)
                if t_name.lower() != proc_name.lower() and t_name.upper() not in ("SELECT", "SET", "WHERE", "VALUES", "DEFAULT"):
                    body_tables.add(t_name)

            for b_tbl in sorted(body_tables):
                calls.append(
                    ExtractedCall(
                        caller_name=proc_name,
                        callee_name=b_tbl,
                        line_number=start_line,
                        raw_call=f"PROCEDURE {proc_name} ACCESSES {b_tbl}",
                    )
                )

        # D. CREATE SCHEMA
        elif "SCHEMA" in kind:
            schema_name = stmt.this.name if hasattr(stmt.this, "name") else str(stmt.this)
            if schema_name:
                nodes.append(
                    ExtractedNode(
                        name=schema_name,
                        node_type="MODULE",
                        qualified_name=schema_name,
                        start_line=start_line,
                        end_line=end_line,
                        start_col=start_col,
                        end_col=end_col,
                        language="sql",
                        signature=f"CREATE SCHEMA {schema_name}",
                        docstring="",
                        metadata={"entity_type": "schema"},
                    )
                )

    def _handle_alter_statement(self, stmt: exp.Alter, raw_sql: str, line_number: int, calls: List[ExtractedCall]) -> None:
        """Processes an ALTER TABLE statement extracting foreign key and target table references."""
        target_tbl = stmt.this.name if hasattr(stmt.this, "name") else str(stmt.this)
        if target_tbl:
            for ref in stmt.find_all(exp.Reference):
                tbl = ref.find(exp.Table)
                ref_tbl = tbl.name if tbl else ""
                if ref_tbl and ref_tbl.lower() != target_tbl.lower():
                    calls.append(
                        ExtractedCall(
                            caller_name=target_tbl,
                            callee_name=ref_tbl,
                            line_number=line_number,
                            raw_call=f"ALTER TABLE {target_tbl} REFERENCES {ref_tbl}",
                        )
                    )

    def _handle_query_statement(self, stmt: exp.Expression, raw_sql: str, line_number: int, calls: List[ExtractedCall]) -> None:
        """Processes DML and query statements (SELECT, INSERT, UPDATE, DELETE, MERGE)."""
        seen_tables: Set[str] = set()
        for tbl in stmt.find_all(exp.Table):
            tname = tbl.name
            if tname and tname.lower() not in seen_tables:
                seen_tables.add(tname.lower())
                calls.append(
                    ExtractedCall(
                        caller_name="<module>",
                        callee_name=tname,
                        line_number=line_number,
                        raw_call=raw_sql[:200],
                    )
                )

        # Check for user-defined function calls within query
        for func in stmt.find_all((exp.Anonymous, exp.Func)):
            fname = func.name if hasattr(func, "name") else ""
            if fname and fname.lower() not in SQL_BUILTIN_FUNCTIONS and fname.lower() not in seen_tables:
                calls.append(
                    ExtractedCall(
                        caller_name="<module>",
                        callee_name=fname,
                        line_number=line_number,
                        raw_call=f"SELECT {fname}(...)",
                    )
                )

    def _parse_callee_from_command(self, expr_text: str) -> Optional[str]:
        """Parses procedure name from CALL/EXEC command expression."""
        clean = expr_text.strip().strip("'\"")
        m = re.match(r"^([a-zA-Z0-9_\.]+)", clean)
        if m:
            full = m.group(1)
            return full.split(".")[-1]
        return None

    def _fallback_extract_calls(self, raw_sql: str, line_number: int, calls: List[ExtractedCall]) -> None:
        """Regex-based fallback call extraction for procedure calls and queries."""
        # CALL proc(...) or EXEC proc
        m_call = re.search(r"\b(?:CALL|EXEC|EXECUTE)\s+([a-zA-Z0-9_\.]+)", raw_sql, re.IGNORECASE)
        if m_call:
            callee = m_call.group(1).split(".")[-1]
            calls.append(
                ExtractedCall(
                    caller_name="<module>",
                    callee_name=callee,
                    line_number=line_number,
                    raw_call=raw_sql[:200],
                )
            )

        # FROM / JOIN table references
        for m_table in re.finditer(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([a-zA-Z0-9_]+)", raw_sql, re.IGNORECASE):
            tbl = m_table.group(1)
            if tbl.upper() not in ("SELECT", "SET", "WHERE", "VALUES", "DEFAULT"):
                calls.append(
                    ExtractedCall(
                        caller_name="<module>",
                        callee_name=tbl,
                        line_number=line_number,
                        raw_call=raw_sql[:200],
                    )
                )

    def _find_token_line(self, tokens: List[sqlglot.tokens.Token], text: str, fallback: int) -> int:
        """Finds the 1-indexed line number of a token matching the given text."""
        for t in tokens:
            if t.text.lower() == text.lower():
                return t.line
        return fallback
