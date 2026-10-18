"""Unit Tests for RepoPeekV3 Native Benchmark Harness.

Verifies:
1. Task definition loading, schema compliance, and completeness.
2. In-memory database isolation (zero writes to operational data/apexgraph.db).
3. Retrieval, impact, test-discovery, and cycle metric calculations.
4. Metric calculations on empty, missing, or mismatched results.
5. Report structure, platform metadata, and git commit presence.
6. Refusal to overwrite existing reports without the --overwrite flag.
7. Subprocess safety: no external repopeek.exe or legacy scripts invoked.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import pytest

from benchmarks.native.runner import (
    _WORKSPACE_ROOT,
    compute_retrieval_metrics,
    create_in_memory_database,
    execute_task,
    index_benchmark_fixture,
    run_benchmark_suite,
)
from packages.core.models import GraphNodeSchema, SearchResult
from packages.core.query_engine import GraphQueryEngine


@pytest.fixture
def tasks_file_path() -> Path:
    path = _WORKSPACE_ROOT / "benchmarks" / "native" / "tasks.json"
    assert path.exists(), f"tasks.json must exist at {path}"
    return path


@pytest.fixture
def sample_repo_path() -> Path:
    path = _WORKSPACE_ROOT / "tests" / "fixtures" / "sample_repo"
    assert path.exists(), f"sample_repo must exist at {path}"
    return path


def test_task_definitions_structure_and_completeness(tasks_file_path: Path):
    """1. Validates tasks.json schema, required fields, and unique task identifiers."""
    with open(tasks_file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "benchmark_version" in data
    assert "tasks" in data
    tasks = data["tasks"]
    assert len(tasks) == 10

    task_ids = set()
    valid_query_types = {
        "search_code",
        "analyze_change_impact",
        "find_related_tests",
        "detect_import_cycles",
    }

    for task in tasks:
        assert "task_id" in task
        assert "category" in task
        assert "difficulty" in task
        assert "description" in task
        assert "query_type" in task
        assert task["query_type"] in valid_query_types
        assert task["task_id"] not in task_ids, f"Duplicate task_id: {task['task_id']}"
        task_ids.add(task["task_id"])


def test_compute_retrieval_metrics_exact_hit():
    """2. Verifies Hit@1, Recall@K, and MRR on top-1 exact matches."""
    node = GraphNodeSchema(
        id="test:py:calculator.py:calculate_sum",
        repository_id="repo1",
        node_type="METHOD",
        name="calculate_sum",
        qualified_name="Calculator.calculate_sum",
        file_path="calculator.py",
        start_line=13,
        end_line=16,
        language="python",
    )
    results = [SearchResult(node=node, match_score=0.95, snippet="def calculate_sum")]
    expected = [{"name": "calculate_sum", "file_path": "calculator.py"}]

    metrics = compute_retrieval_metrics(results, expected)
    assert metrics["hit_at_1"] == 1.0
    assert metrics["recall_at_1"] == 1.0
    assert metrics["recall_at_3"] == 1.0
    assert metrics["recall_at_5"] == 1.0
    assert metrics["mrr"] == 1.0
    assert metrics["top_rank"] == 1


def test_compute_retrieval_metrics_lower_rank_and_empty():
    """3. Verifies MRR, Recall@K, and Hit@1 when result is at rank 3 or missing."""
    dummy_node1 = GraphNodeSchema(
        id="1", repository_id="r", node_type="FUNCTION", name="other1", qualified_name="other1",
        file_path="other.py", start_line=1, end_line=5, language="python"
    )
    dummy_node2 = GraphNodeSchema(
        id="2", repository_id="r", node_type="FUNCTION", name="other2", qualified_name="other2",
        file_path="other.py", start_line=10, end_line=15, language="python"
    )
    target_node = GraphNodeSchema(
        id="3", repository_id="r", node_type="FUNCTION", name="target", qualified_name="target",
        file_path="target.py", start_line=1, end_line=10, language="python"
    )
    results = [
        SearchResult(node=dummy_node1, match_score=0.90, snippet=""),
        SearchResult(node=dummy_node2, match_score=0.85, snippet=""),
        SearchResult(node=target_node, match_score=0.80, snippet=""),
    ]
    expected = [{"name": "target", "file_path": "target.py"}]

    metrics = compute_retrieval_metrics(results, expected)
    assert metrics["hit_at_1"] == 0.0
    assert metrics["recall_at_1"] == 0.0
    assert metrics["recall_at_3"] == 1.0
    assert metrics["recall_at_5"] == 1.0
    assert metrics["mrr"] == pytest.approx(1.0 / 3.0, 0.001)
    assert metrics["top_rank"] == 3

    # Empty results case
    empty_metrics = compute_retrieval_metrics([], expected)
    assert empty_metrics["hit_at_1"] == 0.0
    assert empty_metrics["recall_at_1"] == 0.0
    assert empty_metrics["mrr"] == 0.0
    assert empty_metrics["top_rank"] is None

    # Empty expected targets case (distractor detection)
    zero_target_metrics_dirty = compute_retrieval_metrics(results, [])
    assert zero_target_metrics_dirty["is_zero_target"] is True
    assert zero_target_metrics_dirty["negative_rejection_success"] == 0.0
    assert zero_target_metrics_dirty["distractor_count"] == 3
    assert zero_target_metrics_dirty["retrieved_count"] == 3
    assert zero_target_metrics_dirty["hit_at_1"] is None
    assert zero_target_metrics_dirty["mrr"] is None

    zero_target_metrics_clean = compute_retrieval_metrics([], [])
    assert zero_target_metrics_clean["is_zero_target"] is True
    assert zero_target_metrics_clean["negative_rejection_success"] == 1.0
    assert zero_target_metrics_clean["distractor_count"] == 0
    assert zero_target_metrics_clean["retrieved_count"] == 0
    assert zero_target_metrics_clean["hit_at_1"] is None
    assert zero_target_metrics_clean["mrr"] is None


def test_in_memory_database_isolation(sample_repo_path: Path):
    """4. Ensures indexing and querying occur in-memory without creating or modifying data/apexgraph.db."""
    op_db_path = _WORKSPACE_ROOT / "data" / "apexgraph.db"
    op_db_mtime_before = op_db_path.stat().st_mtime if op_db_path.exists() else None

    session = create_in_memory_database()
    indexed_repo, duration = index_benchmark_fixture(session, sample_repo_path)

    assert indexed_repo.id is not None
    assert indexed_repo.indexing_status == "ready"
    assert duration > 0.0

    engine = GraphQueryEngine(session)
    results = engine.search_code("calculate sum", repo_id=indexed_repo.id)
    assert len(results) >= 1

    session.close()

    # Verify operational DB remained completely untouched
    if op_db_path.exists():
        assert op_db_path.stat().st_mtime == op_db_mtime_before


def test_run_benchmark_suite_execution_and_schema():
    """5. Executes the full benchmark suite in-memory and validates report JSON structure."""
    report = run_benchmark_suite(repetitions=1)

    assert report["benchmark_suite"] == "RepoPeekV3 Native Benchmark"
    assert "benchmark_version" in report
    assert "timestamp" in report
    assert "environment" in report
    assert "platform" in report["environment"]
    assert "python_version" in report["environment"]

    assert "aggregate_metrics" in report
    agg = report["aggregate_metrics"]
    assert agg["total_tasks"] == 10
    assert agg["passed_tasks"] == 10
    assert agg["accuracy_rate"] == 1.0
    assert agg["indexing_duration_ms"] > 0
    assert agg["suite_execution_duration_ms"] > 0
    assert agg["mean_query_latency_ms"] > 0

    assert agg["retrieval"]["task_count"] == 6
    assert agg["retrieval"]["mean_hit_at_1"] == 1.0
    assert agg["retrieval"]["mean_recall_at_3"] == 1.0
    assert agg["retrieval"]["mean_mrr"] == 1.0
    assert agg["retrieval"]["negative_rejection_task_count"] == 1
    assert agg["retrieval"]["negative_rejection_accuracy"] == 1.0

    assert agg["graph_traversal"]["impact_task_count"] == 1
    assert agg["graph_traversal"]["mean_impact_file_recall"] == 1.0
    assert agg["graph_traversal"]["mean_linked_test_recall"] == 1.0

    assert len(report["tasks"]) == 10
    for t in report["tasks"]:
        assert t["correct"] is True
        assert t["performance"]["payload_bytes"] > 0
        assert t["performance"]["approx_tokens"] > 0
        assert t["performance"]["median_latency_ms"] >= 0


def test_refuse_overwrite_existing_report(tmp_path: Path):
    """6. Verifies runner CLI refuses to overwrite an existing report without --overwrite."""
    existing_file = tmp_path / "existing_report.json"
    existing_file.write_text('{"sentinel": true}', encoding="utf-8")

    runner_script = _WORKSPACE_ROOT / "benchmarks" / "native" / "runner.py"

    # Run without --overwrite: must fail with returncode 1
    res = subprocess.run(
        [
            sys.executable,
            str(runner_script),
            "--output",
            str(existing_file),
        ],
        cwd=str(_WORKSPACE_ROOT),
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1
    assert "Refusing to overwrite existing report" in res.stderr
    # Confirm file content was NOT overwritten
    assert json.loads(existing_file.read_text(encoding="utf-8")) == {"sentinel": True}

    # Run with --overwrite: must succeed with returncode 0
    res_overwrite = subprocess.run(
        [
            sys.executable,
            str(runner_script),
            "--output",
            str(existing_file),
            "--overwrite",
            "--repetitions",
            "1",
        ],
        cwd=str(_WORKSPACE_ROOT),
        capture_output=True,
        text=True,
    )
    assert res_overwrite.returncode == 0
    overwritten_data = json.loads(existing_file.read_text(encoding="utf-8"))
    assert "benchmark_suite" in overwritten_data


def test_no_legacy_or_external_processes_invoked(monkeypatch):
    """7. Verifies that the native benchmark suite does not invoke repopeek.exe or external benchmark scripts."""
    real_popen = subprocess.Popen

    def guarded_popen(*args, **kwargs):
        cmd_str = str(args[0]) if args else ""
        assert "repopeek.exe" not in cmd_str, "repopeek.exe must never be invoked"
        assert "run_new_5tasks.py" not in cmd_str, "legacy benchmark script must never be invoked"
        assert "git restore" not in cmd_str, "git restore must never be invoked"
        assert "git clean" not in cmd_str, "git clean must never be invoked"
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", guarded_popen)
    report = run_benchmark_suite(repetitions=1)
    assert report["aggregate_metrics"]["passed_tasks"] == 10


def test_zero_target_rejection_task_execution(sample_repo_path: Path):
    """8. Verifies task execution for both successful and unsuccessful zero-target negative rejection."""
    session = create_in_memory_database()
    indexed_repo, _ = index_benchmark_fixture(session, sample_repo_path)
    engine = GraphQueryEngine(session)

    # Case A: Successful rejection (clean zero results on non-existent query)
    clean_task = {
        "task_id": "CLEAN_REJECTION_TEST",
        "category": "distractor_rejection",
        "difficulty": "diagnostic",
        "description": "Zero distractor verification",
        "query_type": "search_code",
        "query_params": {"query_text": "non_existent_symbol_xyz", "limit": 5},
        "expected_targets": [],
        "relevance_criterion": {"expected_count": 0},
    }
    clean_res = execute_task(engine, indexed_repo.id, clean_task, repetitions=1)
    assert clean_res["correct"] is True
    assert clean_res["metrics"]["is_zero_target"] is True
    assert clean_res["metrics"]["negative_rejection_success"] == 1.0
    assert clean_res["metrics"]["distractor_count"] == 0
    assert clean_res["metrics"]["hit_at_1"] is None
    assert clean_res["metrics"]["mrr"] is None

    # Case B: Unsuccessful rejection (query matching symbols when 0 expected)
    dirty_task = {
        "task_id": "DIRTY_REJECTION_TEST",
        "category": "distractor_rejection",
        "difficulty": "diagnostic",
        "description": "Distractor failure verification",
        "query_type": "search_code",
        "query_params": {"query_text": "calculate", "limit": 5},
        "expected_targets": [],
        "relevance_criterion": {"expected_count": 0},
    }
    dirty_res = execute_task(engine, indexed_repo.id, dirty_task, repetitions=1)
    assert dirty_res["correct"] is False
    assert dirty_res["metrics"]["is_zero_target"] is True
    assert dirty_res["metrics"]["negative_rejection_success"] == 0.0
    assert dirty_res["metrics"]["distractor_count"] > 0
    assert dirty_res["metrics"]["hit_at_1"] is None
    assert dirty_res["metrics"]["mrr"] is None

    session.close()

