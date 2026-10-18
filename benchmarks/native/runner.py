"""Native Benchmark Runner for RepoPeekV3 (APEXGP).

Executes reproducible, zero-external-binary evaluations of code intelligence capabilities
including multi-term search, polyglot retrieval, impact analysis, test discovery,
and cycle diagnostics against isolated in-memory test fixtures.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Ensure workspace root is in sys.path
_WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE_ROOT))

from packages.core.indexer import RepositoryIndexer
from packages.core.models import (
    Base,
    GraphNodeModel,
    RepositoryModel,
    SearchResult,
    WorkspaceModel,
)
from packages.core.query_engine import GraphQueryEngine


def get_git_commit(repo_root: Path) -> Optional[str]:
    """Retrieves current Git HEAD commit hash if in a Git repository."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return None


def create_in_memory_database():
    """Initializes a completely isolated in-memory SQLite database session."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def index_benchmark_fixture(
    session, fixture_dir: Path
) -> Tuple[RepositoryModel, float]:
    """Indexes the sample fixture repository once into the in-memory database."""
    ws = WorkspaceModel(name="Native Benchmark Workspace")
    session.add(ws)
    session.commit()

    repo = RepositoryModel(
        workspace_id=ws.id,
        name="Benchmark Sample Repo",
        source_type="local",
        source_path=str(fixture_dir),
    )
    session.add(repo)
    session.commit()

    start_time = time.perf_counter()
    indexer = RepositoryIndexer(session)
    indexed_repo = indexer.index_repository(repo.id)
    indexing_duration_ms = (time.perf_counter() - start_time) * 1000.0

    return indexed_repo, indexing_duration_ms


def compute_retrieval_metrics(
    results: List[SearchResult], expected_targets: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Computes Hit@1, Recall@K, and MRR for ranked search results, or negative rejection metrics for zero-target tasks."""
    if not expected_targets:
        is_clean = len(results) == 0
        return {
            "is_zero_target": True,
            "negative_rejection_success": 1.0 if is_clean else 0.0,
            "distractor_count": len(results),
            "retrieved_count": len(results),
            "hit_at_1": None,
            "recall_at_1": None,
            "recall_at_3": None,
            "recall_at_5": None,
            "mrr": None,
            "top_rank": None,
        }

    num_expected = len(expected_targets)
    expected_keys = {(t["name"].lower(), t["file_path"].lower()) for t in expected_targets}

    # Find ranks of expected items
    found_ranks: List[int] = []
    for rank_idx, res in enumerate(results, start=1):
        res_key = (res.node.name.lower(), res.node.file_path.lower())
        if res_key in expected_keys:
            found_ranks.append(rank_idx)

    hit_at_1 = 1.0 if (found_ranks and found_ranks[0] == 1) else 0.0

    # Recall at K
    recall_at_1 = sum(1 for r in found_ranks if r <= 1) / num_expected
    recall_at_3 = sum(1 for r in found_ranks if r <= 3) / num_expected
    recall_at_5 = sum(1 for r in found_ranks if r <= 5) / num_expected

    # MRR (Mean Reciprocal Rank of first relevant item)
    mrr = (1.0 / found_ranks[0]) if found_ranks else 0.0

    return {
        "is_zero_target": False,
        "hit_at_1": hit_at_1,
        "recall_at_1": recall_at_1,
        "recall_at_3": recall_at_3,
        "recall_at_5": recall_at_5,
        "mrr": mrr,
        "top_rank": found_ranks[0] if found_ranks else None,
        "retrieved_count": len(results),
    }


def execute_task(
    engine: GraphQueryEngine, repo_id: str, task: Dict[str, Any], repetitions: int = 5
) -> Dict[str, Any]:
    """Executes a single benchmark task with warm-up, timing repetitions, and correctness verification."""
    query_type = task["query_type"]
    query_params = task.get("query_params", {})
    task_id = task["task_id"]

    def _invoke():
        if query_type == "search_code":
            return engine.search_code(
                query_text=query_params["query_text"],
                repo_id=repo_id,
                limit=query_params.get("limit", 20),
            )
        elif query_type == "analyze_change_impact":
            return engine.analyze_change_impact(
                target_node_or_file=query_params["target_node_or_file"],
                repo_id=repo_id,
                max_depth=query_params.get("max_depth", 5),
            )
        elif query_type == "find_related_tests":
            sym_name = query_params["symbol_name"]
            file_path = query_params.get("file_path")
            nodes = engine.find_symbol(sym_name, repo_id=repo_id, file_path=file_path)
            if not nodes:
                return []
            return engine.find_related_tests(nodes[0].id)
        elif query_type == "detect_import_cycles":
            return engine.detect_import_cycles(repo_id=repo_id)
        else:
            raise ValueError(f"Unsupported query_type '{query_type}' for task {task_id}")

    # 1. Warm-up invocation (excluded from timing)
    warmup_res = _invoke()

    # 2. Repeated timed invocations
    durations_ms: List[float] = []
    raw_result = warmup_res
    for _ in range(repetitions):
        t0 = time.perf_counter()
        raw_result = _invoke()
        durations_ms.append((time.perf_counter() - t0) * 1000.0)

    median_lat_ms = statistics.median(durations_ms)
    p95_lat_ms = (
        durations_ms[0]
        if len(durations_ms) == 1
        else statistics.quantiles(durations_ms, n=20)[18]  # approx 95th percentile
    )

    # 3. Payload size calculation
    if hasattr(raw_result, "model_dump"):
        serialized_json = json.dumps(raw_result.model_dump(), default=str)
    elif isinstance(raw_result, list) and raw_result and hasattr(raw_result[0], "model_dump"):
        serialized_json = json.dumps([r.model_dump() for r in raw_result], default=str)
    elif isinstance(raw_result, list) and raw_result and hasattr(raw_result[0], "__dict__"):
        serialized_json = json.dumps(
            [{"name": getattr(r, "name", ""), "file_path": getattr(r, "file_path", "")} for r in raw_result],
            default=str,
        )
    else:
        serialized_json = json.dumps(raw_result, default=str)

    payload_bytes = len(serialized_json.encode("utf-8"))
    approx_tokens = max(1, round(len(serialized_json) / 4.0)) if payload_bytes > 0 else 0

    # 4. Correctness & Metric Evaluation
    correct = False
    task_metrics: Dict[str, Any] = {}

    if query_type == "search_code":
        expected_targets = task.get("expected_targets", [])
        crit = task.get("relevance_criterion", {})
        task_metrics = compute_retrieval_metrics(raw_result, expected_targets)
        min_rank = crit.get("min_rank", 1)
        min_score = crit.get("min_score", 0.70)
        expected_count = crit.get("expected_count")

        if expected_count is not None:
            correct = len(raw_result) == expected_count
            task_metrics["distractor_count"] = len(raw_result)
        else:
            # Check if top expected item matches within min_rank and score
            top_rank = task_metrics.get("top_rank")
            if top_rank is not None and top_rank <= min_rank:
                # Check score of top result
                top_result = raw_result[top_rank - 1]
                if top_result.match_score >= min_score:
                    correct = True

    elif query_type == "analyze_change_impact":
        affected_files = set(raw_result.affected_files)
        affected_symbols = {n.name for n in raw_result.affected_nodes}
        linked_test_names = {t.name for t in raw_result.linked_tests}

        exp_files = set(task.get("expected_affected_files", []))
        exp_symbols = set(task.get("expected_affected_symbols", []))
        exp_tests = set(task.get("expected_linked_tests", []))

        file_recall = (
            len(affected_files.intersection(exp_files)) / len(exp_files)
            if exp_files
            else 1.0
        )
        test_recall = (
            len(linked_test_names.intersection(exp_tests)) / len(exp_tests)
            if exp_tests
            else 1.0
        )

        task_metrics = {
            "impact_file_recall": file_recall,
            "linked_test_recall": test_recall,
            "total_affected": raw_result.total_affected,
            "affected_files_count": len(raw_result.affected_files),
            "linked_tests_count": len(raw_result.linked_tests),
        }
        correct = file_recall == 1.0 and test_recall == 1.0

    elif query_type == "find_related_tests":
        exp_tests = set(task.get("expected_test_names", []))
        found_test_names = {getattr(t, "name", "") for t in raw_result}

        test_recall = (
            len(found_test_names.intersection(exp_tests)) / len(exp_tests)
            if exp_tests
            else 0.0
        )
        task_metrics = {
            "test_recall": test_recall,
            "found_tests_count": len(raw_result),
            "found_test_names": sorted(list(found_test_names)),
        }
        min_tests = task.get("relevance_criterion", {}).get("min_tests_found", 1)
        correct = len(found_test_names.intersection(exp_tests)) >= min_tests

    elif query_type == "detect_import_cycles":
        # raw_result is List[List[str]]
        total_cycles = len(raw_result)
        exp_total = task.get("expected_total_cycles", 0)
        is_acyclic = total_cycles == 0
        task_metrics = {
            "total_cycles": total_cycles,
            "is_acyclic": is_acyclic,
            "cycles": raw_result,
        }
        correct = total_cycles == exp_total

    return {
        "task_id": task_id,
        "category": task.get("category"),
        "difficulty": task.get("difficulty"),
        "description": task.get("description"),
        "query_type": query_type,
        "correct": correct,
        "metrics": task_metrics,
        "performance": {
            "median_latency_ms": round(median_lat_ms, 3),
            "p95_latency_ms": round(p95_lat_ms, 3),
            "repetitions": repetitions,
            "payload_bytes": payload_bytes,
            "approx_tokens": approx_tokens,
        },
    }


def run_benchmark_suite(
    tasks_path: Optional[Path] = None,
    fixture_dir: Optional[Path] = None,
    repetitions: int = 5,
) -> Dict[str, Any]:
    """Executes the full native benchmark suite and returns structured report data."""
    if tasks_path is None:
        tasks_path = Path(__file__).resolve().parent / "tasks.json"
    if fixture_dir is None:
        fixture_dir = _WORKSPACE_ROOT / "tests" / "fixtures" / "sample_repo"

    if not tasks_path.exists():
        raise FileNotFoundError(f"Benchmark tasks file not found: {tasks_path}")
    if not fixture_dir.exists():
        raise FileNotFoundError(f"Fixture directory not found: {fixture_dir}")

    with open(tasks_path, "r", encoding="utf-8") as f:
        spec = json.load(f)

    tasks: List[Dict[str, Any]] = spec.get("tasks", [])
    benchmark_version = spec.get("benchmark_version", "1.0.0")

    # Initialize isolated in-memory DB and index fixture once
    session = create_in_memory_database()
    indexed_repo, indexing_duration_ms = index_benchmark_fixture(session, fixture_dir)
    engine = GraphQueryEngine(session)

    start_suite_time = time.perf_counter()
    task_results: List[Dict[str, Any]] = []

    for task in tasks:
        res = execute_task(engine, indexed_repo.id, task, repetitions=repetitions)
        task_results.append(res)

    total_duration_ms = (time.perf_counter() - start_suite_time) * 1000.0

    # Aggregate metrics
    total_tasks = len(task_results)
    passed_tasks = sum(1 for t in task_results if t["correct"])
    accuracy_rate = (passed_tasks / total_tasks) if total_tasks > 0 else 0.0

    retrieval_tasks = [t for t in task_results if t["query_type"] == "search_code"]
    pos_retrieval_tasks = [
        t for t in retrieval_tasks if not t["metrics"].get("is_zero_target", False)
    ]
    neg_retrieval_tasks = [
        t for t in retrieval_tasks if t["metrics"].get("is_zero_target", False)
    ]

    pos_count = len(pos_retrieval_tasks)
    mean_hit_at_1 = (
        sum(t["metrics"]["hit_at_1"] for t in pos_retrieval_tasks) / pos_count
        if pos_count > 0
        else 0.0
    )
    mean_recall_at_3 = (
        sum(t["metrics"]["recall_at_3"] for t in pos_retrieval_tasks) / pos_count
        if pos_count > 0
        else 0.0
    )
    mean_recall_at_5 = (
        sum(t["metrics"]["recall_at_5"] for t in pos_retrieval_tasks) / pos_count
        if pos_count > 0
        else 0.0
    )
    mean_mrr = (
        sum(t["metrics"]["mrr"] for t in pos_retrieval_tasks) / pos_count
        if pos_count > 0
        else 0.0
    )

    neg_count = len(neg_retrieval_tasks)
    negative_rejection_accuracy = (
        sum(t["metrics"]["negative_rejection_success"] for t in neg_retrieval_tasks) / neg_count
        if neg_count > 0
        else 1.0
    )

    impact_tasks = [t for t in task_results if t["query_type"] == "analyze_change_impact"]
    mean_impact_file_recall = (
        sum(t["metrics"]["impact_file_recall"] for t in impact_tasks) / len(impact_tasks)
        if impact_tasks
        else 0.0
    )
    mean_linked_test_recall = (
        sum(t["metrics"]["linked_test_recall"] for t in impact_tasks) / len(impact_tasks)
        if impact_tasks
        else 0.0
    )

    mean_query_latency_ms = (
        statistics.mean(t["performance"]["median_latency_ms"] for t in task_results)
        if task_results
        else 0.0
    )

    git_commit = get_git_commit(_WORKSPACE_ROOT)

    report: Dict[str, Any] = {
        "benchmark_suite": "RepoPeekV3 Native Benchmark",
        "benchmark_version": benchmark_version,
        "git_commit": git_commit,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "os": platform.system(),
            "machine": platform.machine(),
        },
        "fixture": {
            "source_path": str(fixture_dir),
            "total_files": indexed_repo.stats.get("total_files"),
            "total_nodes": indexed_repo.stats.get("total_nodes"),
            "total_edges": indexed_repo.stats.get("total_edges"),
            "languages": indexed_repo.stats.get("languages"),
        },
        "aggregate_metrics": {
            "total_tasks": total_tasks,
            "passed_tasks": passed_tasks,
            "accuracy_rate": round(accuracy_rate, 4),
            "indexing_duration_ms": round(indexing_duration_ms, 2),
            "suite_execution_duration_ms": round(total_duration_ms, 2),
            "mean_query_latency_ms": round(mean_query_latency_ms, 3),
            "retrieval": {
                "task_count": pos_count,
                "mean_hit_at_1": round(mean_hit_at_1, 4),
                "mean_recall_at_3": round(mean_recall_at_3, 4),
                "mean_recall_at_5": round(mean_recall_at_5, 4),
                "mean_mrr": round(mean_mrr, 4),
                "negative_rejection_task_count": neg_count,
                "negative_rejection_accuracy": round(negative_rejection_accuracy, 4),
            },
            "graph_traversal": {
                "impact_task_count": len(impact_tasks),
                "mean_impact_file_recall": round(mean_impact_file_recall, 4),
                "mean_linked_test_recall": round(mean_linked_test_recall, 4),
            },
        },
        "tasks": task_results,
    }

    session.close()
    return report


def print_summary_table(report: Dict[str, Any]) -> None:
    """Prints a clear, compact terminal summary table of benchmark results."""
    agg = report["aggregate_metrics"]
    print("\n" + "=" * 80)
    print(f" REPOPEEK V3 NATIVE BENCHMARK REPORT (v{report['benchmark_version']})")
    print(f" Commit: {report.get('git_commit', 'unknown')} | Platform: {report['environment']['platform']}")
    print(f" Fixture: {report['fixture']['total_files']} files, {report['fixture']['total_nodes']} nodes, {report['fixture']['total_edges']} edges")
    print("=" * 80)
    print(f"{'Task ID':<28} {'Category':<20} {'Status':<8} {'Median(ms)':<12} {'Tokens':<8}")
    print("-" * 80)
    for t in report["tasks"]:
        status = "PASS" if t["correct"] else "FAIL"
        lat = f"{t['performance']['median_latency_ms']:.2f}ms"
        tokens = str(t["performance"]["approx_tokens"])
        print(f"{t['task_id']:<28} {t['category']:<20} {status:<8} {lat:<12} {tokens:<8}")
    print("-" * 80)
    print(f"Overall Accuracy:            {agg['passed_tasks']}/{agg['total_tasks']} ({agg['accuracy_rate'] * 100:.1f}%)")
    print(f"Retrieval Hit@1:             {agg['retrieval']['mean_hit_at_1'] * 100:.1f}% (N={agg['retrieval']['task_count']})")
    print(f"Retrieval Recall@3:          {agg['retrieval']['mean_recall_at_3'] * 100:.1f}%")
    print(f"Retrieval MRR:               {agg['retrieval']['mean_mrr']:.3f}")
    if agg["retrieval"].get("negative_rejection_task_count", 0) > 0:
        print(f"Negative Rejection Accuracy: {agg['retrieval']['negative_rejection_accuracy'] * 100:.1f}% (N={agg['retrieval']['negative_rejection_task_count']})")
    print(f"Impact File Recall:          {agg['graph_traversal']['mean_impact_file_recall'] * 100:.1f}%")
    print(f"Linked Test Recall:          {agg['graph_traversal']['mean_linked_test_recall'] * 100:.1f}%")
    print(f"Indexing Duration:      {agg['indexing_duration_ms']:.2f}ms")
    print(f"Mean Query Latency:     {agg['mean_query_latency_ms']:.2f}ms")
    print(f"Suite Total Runtime:    {agg['suite_execution_duration_ms']:.2f}ms")
    print("=" * 80 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="RepoPeekV3 Native Benchmark Runner")
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Path to write the benchmark result JSON report.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output report file if it already exists.",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=5,
        help="Number of measured timing repetitions per task (default: 5).",
    )
    args = parser.parse_args()

    output_path: Optional[Path] = None
    if args.output:
        output_path = Path(args.output).resolve()
        if output_path.exists() and not args.overwrite:
            sys.stderr.write(
                f"Error: Refusing to overwrite existing report at '{output_path}'. "
                f"Use --overwrite to allow overwriting.\n"
            )
            sys.exit(1)

    try:
        report = run_benchmark_suite(repetitions=args.repetitions)
    except Exception as e:
        sys.stderr.write(f"Benchmark run failed: {e}\n")
        sys.exit(2)

    print_summary_table(report)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"Benchmark report written to: {output_path}")


if __name__ == "__main__":
    main()
