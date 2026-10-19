"""Unit Tests for Multi-Term and Tokenized Symbol Search in GraphQueryEngine
"""

import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sqlalchemy.pool import StaticPool

from packages.core.indexer import RepositoryIndexer
from packages.core.models import (
    Base,
    GraphNodeModel,
    RepositoryModel,
    SearchResult,
    WorkspaceModel,
)
from packages.core.query_engine import GraphQueryEngine, _tokenize_query_terms

FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "sample_repo")


@pytest.fixture(scope="module")
def shared_db():
    """Provides a shared in-memory SQLite database session for the module."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="module")
def indexed_sample_repo(shared_db):
    """Indexes the sample_repo fixture once into the shared in-memory database."""
    ws = WorkspaceModel(name="Search Test Workspace")
    shared_db.add(ws)
    shared_db.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="Search Sample Repo",
        source_type="local",
        source_path=FIXTURES_DIR,
    )
    shared_db.add(repo)
    shared_db.commit()

    indexer = RepositoryIndexer(shared_db)
    indexer.index_repository(repo.id)
    return repo


@pytest.fixture
def isolated_db():
    """Provides an isolated in-memory SQLite database session for custom node tests."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def _add_custom_node(
    db,
    repo_id: str,
    name: str,
    file_path: str,
    node_type: str = "FUNCTION",
    qual_suffix: str = "",
    docstring: str = "",
    start_line: int = 1,
):
    qual_name = qual_suffix or f"{file_path}:{name}:{start_line}"
    node_id = f"{repo_id}:py:{file_path}:{name}:{start_line}"
    node = GraphNodeModel(
        id=node_id,
        repository_id=repo_id,
        node_type=node_type,
        name=name,
        qualified_name=qual_name,
        file_path=file_path,
        start_line=start_line,
        end_line=start_line + 10,
        start_col=0,
        end_col=0,
        language="python",
        signature=f"def {name}()",
        docstring=docstring,
        node_metadata={},
    )
    db.add(node)
    db.commit()
    return node


def test_tokenizer_unit_cases():
    """Verifies the tokenizer correctly splits on camelCase, snake_case, spaces, and punctuation."""
    assert _tokenize_query_terms("calculate_sum") == ["calculate", "sum"]
    assert _tokenize_query_terms("formatResult") == ["format", "result"]
    assert _tokenize_query_terms("IPAddressEngine") == ["ip", "address", "engine"]
    assert _tokenize_query_terms("math.ops::add") == ["math", "ops", "add"]
    assert _tokenize_query_terms("calculate sum") == ["calculate", "sum"]
    assert _tokenize_query_terms("") == []
    assert _tokenize_query_terms("   ") == []
    assert _tokenize_query_terms("... ___ !@#") == []


def test_natural_space_separated_snake_case(indexed_sample_repo, shared_db):
    """1. Space-separated query 'calculate sum' finds 'calculate_sum' at rank 1."""
    engine = GraphQueryEngine(shared_db)
    results = engine.search_code("calculate sum", repo_id=indexed_sample_repo.id)

    assert len(results) >= 1
    top = results[0]
    assert top.node.name == "calculate_sum"
    assert top.match_score >= 0.88
    assert isinstance(top, SearchResult)


def test_natural_space_separated_camel_case(indexed_sample_repo, shared_db):
    """2. Space-separated query 'format result' finds 'formatResult' at rank 1."""
    engine = GraphQueryEngine(shared_db)
    results = engine.search_code("format result", repo_id=indexed_sample_repo.id)

    assert len(results) >= 1
    top = results[0]
    assert top.node.name == "formatResult"
    assert top.match_score >= 0.88


def test_case_insensitivity(indexed_sample_repo, shared_db):
    """3. Queries with varied casing match accurately."""
    engine = GraphQueryEngine(shared_db)

    res_upper = engine.search_code("CALCULATE SUM", repo_id=indexed_sample_repo.id)
    res_lower = engine.search_code("calculate sum", repo_id=indexed_sample_repo.id)
    res_mixed = engine.search_code("Calculate Sum", repo_id=indexed_sample_repo.id)

    assert len(res_upper) >= 1 and res_upper[0].node.name == "calculate_sum"
    assert len(res_lower) >= 1 and res_lower[0].node.name == "calculate_sum"
    assert len(res_mixed) >= 1 and res_mixed[0].node.name == "calculate_sum"
    assert res_upper[0].match_score == res_lower[0].match_score == res_mixed[0].match_score


def test_exact_identifier_priority(indexed_sample_repo, shared_db):
    """4. Exact identifier matches retain highest priority score of 1.0."""
    engine = GraphQueryEngine(shared_db)

    res_exact = engine.search_code("Calculator", repo_id=indexed_sample_repo.id)
    assert len(res_exact) >= 1
    top = res_exact[0]
    assert top.node.name == "Calculator"
    assert top.match_score == 1.0


def test_all_term_outranks_partial_term(isolated_db):
    """5. All-term matches strictly outrank partial-term matches."""
    ws = WorkspaceModel(name="Test WS")
    isolated_db.add(ws)
    isolated_db.commit()
    repo = RepositoryModel(workspace_id=ws.id, name="Test Repo", source_type="local", source_path="/tmp")
    isolated_db.add(repo)
    isolated_db.commit()

    # Create target matching both terms: 'process' and 'payment'
    _add_custom_node(isolated_db, repo.id, "process_payment", "payments.py")
    # Create target matching only 1 term: 'process'
    _add_custom_node(isolated_db, repo.id, "process_data", "data.py")
    # Create target matching only 1 term: 'payment'
    _add_custom_node(isolated_db, repo.id, "payment_record", "records.py")

    engine = GraphQueryEngine(isolated_db)
    results = engine.search_code("process payment", repo_id=repo.id)

    assert len(results) == 3
    # Top result must be the all-term match
    assert results[0].node.name == "process_payment"
    assert results[0].match_score >= 0.88

    # Partial matches must rank below with lower scores
    partial_names = {results[1].node.name, results[2].node.name}
    assert partial_names == {"process_data", "payment_record"}
    assert results[1].match_score < results[0].match_score
    assert results[2].match_score < results[0].match_score


def test_qualified_name_and_file_path_multi_term(indexed_sample_repo, shared_db):
    """6. Multi-term query matching across path and symbol name (e.g. 'math add')."""
    engine = GraphQueryEngine(shared_db)
    results = engine.search_code("math add", repo_id=indexed_sample_repo.id)

    assert len(results) >= 1
    # 'add' in 'math_ops.py' matches both 'math' and 'add'
    top = results[0]
    assert top.node.name == "add"
    assert "math_ops" in top.node.file_path
    assert top.match_score >= 0.70


def test_docstring_matches_rank_below_symbol_matches(isolated_db):
    """7. Docstring-only matches rank below symbol-name matches."""
    ws = WorkspaceModel(name="Test WS")
    isolated_db.add(ws)
    isolated_db.commit()
    repo = RepositoryModel(workspace_id=ws.id, name="Test Repo", source_type="local", source_path="/tmp")
    isolated_db.add(repo)
    isolated_db.commit()

    # Symbol name match
    _add_custom_node(isolated_db, repo.id, "authentication_handler", "auth.py", docstring="Handles requests.")
    # Docstring match only
    _add_custom_node(isolated_db, repo.id, "login_view", "views.py", docstring="Performs authentication handler steps.")

    engine = GraphQueryEngine(isolated_db)
    results = engine.search_code("authentication handler", repo_id=repo.id)

    assert len(results) == 2
    assert results[0].node.name == "authentication_handler"
    assert results[1].node.name == "login_view"
    assert results[0].match_score > results[1].match_score


def test_unrelated_results_excluded(indexed_sample_repo, shared_db):
    """8. Unrelated search queries do not return false positive matches."""
    engine = GraphQueryEngine(shared_db)
    results = engine.search_code("nonexistent_quantum_teleportation_symbol", repo_id=indexed_sample_repo.id)
    assert results == []


def test_filters_preserved(indexed_sample_repo, shared_db):
    """9. node_type and file_path_filter constraints remain strictly enforced."""
    engine = GraphQueryEngine(shared_db)

    # Filter to CLASS only
    class_results = engine.search_code("Calculator", repo_id=indexed_sample_repo.id, node_type="CLASS")
    assert all(r.node.node_type == "CLASS" for r in class_results)
    assert any(r.node.name == "Calculator" for r in class_results)

    # Filter to FUNCTION only (Calculator should not match)
    func_results = engine.search_code("Calculator", repo_id=indexed_sample_repo.id, node_type="FUNCTION")
    assert not any(r.node.name == "Calculator" for r in func_results)

    # Filter by file path
    ts_results = engine.search_code("formatResult", repo_id=indexed_sample_repo.id, file_path_filter="formatter.ts")
    assert all("formatter.ts" in r.node.file_path for r in ts_results)


def test_tie_breaking_determinism(isolated_db):
    """10. Tied scores are sorted deterministically by file_path, start_line, qualified_name."""
    ws = WorkspaceModel(name="Test WS")
    isolated_db.add(ws)
    isolated_db.commit()
    repo = RepositoryModel(workspace_id=ws.id, name="Test Repo", source_type="local", source_path="/tmp")
    isolated_db.add(repo)
    isolated_db.commit()

    # Three identical names in different files or lines
    _add_custom_node(isolated_db, repo.id, "run", "z_worker.py", start_line=10)
    _add_custom_node(isolated_db, repo.id, "run", "a_worker.py", start_line=20)
    _add_custom_node(isolated_db, repo.id, "run", "a_worker.py", start_line=5)

    engine = GraphQueryEngine(isolated_db)
    results = engine.search_code("run", repo_id=repo.id)

    assert len(results) == 3
    # Same score (1.0), so tie-break by file_path, then start_line
    assert results[0].node.file_path == "a_worker.py" and results[0].node.start_line == 5
    assert results[1].node.file_path == "a_worker.py" and results[1].node.start_line == 20
    assert results[2].node.file_path == "z_worker.py" and results[2].node.start_line == 10


def test_empty_and_punctuation_queries(indexed_sample_repo, shared_db):
    """11. Empty strings and punctuation-only queries return an empty list without error."""
    engine = GraphQueryEngine(shared_db)

    assert engine.search_code("", repo_id=indexed_sample_repo.id) == []
    assert engine.search_code("   ", repo_id=indexed_sample_repo.id) == []
    assert engine.search_code("...", repo_id=indexed_sample_repo.id) == []
    assert engine.search_code("!@#$%^&*()", repo_id=indexed_sample_repo.id) == []
    assert engine.search_code(";;; --- :::", repo_id=indexed_sample_repo.id) == []


def test_limit_constraint(indexed_sample_repo, shared_db):
    """12. Result count strictly respects the limit argument."""
    engine = GraphQueryEngine(shared_db)
    results_limit_1 = engine.search_code("e", repo_id=indexed_sample_repo.id, limit=1)
    results_limit_2 = engine.search_code("e", repo_id=indexed_sample_repo.id, limit=2)

    assert len(results_limit_1) == 1
    assert len(results_limit_2) == 2
    assert results_limit_1[0].node.id == results_limit_2[0].node.id


def test_exact_qualified_name_prioritization(indexed_sample_repo, shared_db):
    """13. Exact qualified name matches (e.g. Calculator.calculate_sum) rank at top with high score."""
    engine = GraphQueryEngine(shared_db)
    results = engine.search_code("Calculator.calculate_sum", repo_id=indexed_sample_repo.id)
    assert len(results) >= 1
    top = results[0]
    assert top.node.name == "calculate_sum"
    assert top.node.qualified_name.endswith("Calculator.calculate_sum")
    assert top.match_score >= 0.95


def test_symbol_precedence_over_file_containers(indexed_sample_repo, shared_db):
    """14. Functional symbols outrank FILE containers on symbol queries, while explicit file searches preserve FILE at top."""
    engine = GraphQueryEngine(shared_db)

    # Symbol-directed query: FUNCTION should outrank FILE container
    results = engine.search_code("format", repo_id=indexed_sample_repo.id)
    assert len(results) >= 2
    assert results[0].node.node_type == "FUNCTION"
    assert results[0].node.name == "formatResult"

    # Explicit file query: FILE container should rank at top
    file_results = engine.search_code("formatter.ts", repo_id=indexed_sample_repo.id)
    assert len(file_results) >= 1
    assert file_results[0].node.node_type == "FILE"
    assert file_results[0].node.file_path == "formatter.ts"
    assert file_results[0].match_score == 1.0


def test_stopwords_resilience(indexed_sample_repo, shared_db):
    """15. Natural-language stop words do not dilute the score or precision of substantive query tokens."""
    engine = GraphQueryEngine(shared_db)
    results = engine.search_code("how to calculate sum", repo_id=indexed_sample_repo.id)
    assert len(results) >= 1
    top = results[0]
    assert top.node.name == "calculate_sum"
    assert top.match_score >= 0.85
    # calculate_product should be ranked lower with noticeable separation
    product_res = next((r for r in results if r.node.name == "calculate_product"), None)
    if product_res:
        assert top.match_score > product_res.match_score + 0.20


def test_candidate_truncation_exact_symbol_preservation(isolated_db):
    """16. SQL candidate retrieval ordering prevents candidate truncation when over 100 partial matches exist."""
    ws = WorkspaceModel(name="Truncation WS")
    isolated_db.add(ws)
    isolated_db.commit()
    repo = RepositoryModel(workspace_id=ws.id, name="Truncation Repo", source_type="local", source_path="/tmp")
    isolated_db.add(repo)
    isolated_db.commit()

    # Add 105 distractor nodes whose docstrings contain 'add' (e.g. 'address validation')
    for i in range(105):
        _add_custom_node(
            isolated_db,
            repo.id,
            name=f"worker_{i:03d}",
            file_path=f"workers/worker_{i:03d}.py",
            docstring="Performs address resolution and memory addition padding routines.",
            start_line=1,
        )

    # Add the exact target node 'add'
    _add_custom_node(
        isolated_db,
        repo.id,
        name="add",
        file_path="math_utils.py",
        docstring="Adds two numbers together.",
        start_line=5,
    )

    engine = GraphQueryEngine(isolated_db)
    # Search for 'add' with default candidate pool (100)
    results = engine.search_code("add", repo_id=repo.id, limit=10)

    assert len(results) >= 1
    assert results[0].node.name == "add"
    assert results[0].node.file_path == "math_utils.py"
    assert results[0].match_score == 1.0

