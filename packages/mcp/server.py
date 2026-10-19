"""ApexGraph Model Context Protocol (MCP) Server

Exposes code knowledge graph queries, symbol lookups, caller/callee traces,
change-impact blast-radius analyses, and test discovery to AI coding agents.
Supports stdio and Streamable HTTP transports.
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure package root is in sys.path when executed directly as a script
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mcp.server.mcpserver import MCPServer
from packages.core.database import get_db_session, init_db
from packages.core.indexer import RepositoryIndexer
from packages.core.models import RepositoryModel, WorkspaceModel
from packages.core.query_engine import GraphQueryEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("apexgraph.mcp")

mcp_server = MCPServer(
    name="ApexGraph Code Intelligence Server",
    version="1.0.0",
    instructions="ApexGraph provides code knowledge graph queries, symbol navigation, change impact analysis, and test discovery for AI coding agents.",
)


@mcp_server.tool()
def list_workspaces() -> str:
    """Lists all registered workspaces in ApexGraph."""
    with get_db_session() as db:
        workspaces = db.query(WorkspaceModel).all()
        result = [
            {"id": w.id, "name": w.name, "created_at": w.created_at.isoformat()}
            for w in workspaces
        ]
        return json.dumps(result, indent=2)


@mcp_server.tool()
def list_repositories(workspace_id: str = "") -> str:
    """Lists repositories in ApexGraph with indexing status and language statistics.
    
    Args:
        workspace_id: Optional workspace UUID to filter repositories.
    """
    with get_db_session() as db:
        query = db.query(RepositoryModel)
        if workspace_id:
            query = query.filter_by(workspace_id=workspace_id)
        repos = query.all()
        result = [
            {
                "id": r.id,
                "workspace_id": r.workspace_id,
                "name": r.name,
                "source_type": r.source_type,
                "source_path": r.source_path,
                "indexing_status": r.indexing_status,
                "last_indexed_at": r.last_indexed_at.isoformat() if r.last_indexed_at else None,
                "stats": r.stats,
            }
            for r in repos
        ]
        return json.dumps(result, indent=2)


@mcp_server.tool()
def get_repository_status(repo_id: str) -> str:
    """Returns indexing status, last indexed revision, and statistics for a repository.
    
    Args:
        repo_id: The UUID of the repository.
    """
    with get_db_session() as db:
        repo = db.query(RepositoryModel).filter_by(id=repo_id).first()
        if not repo:
            return json.dumps({"error": f"Repository '{repo_id}' not found"})
        return json.dumps(
            {
                "id": repo.id,
                "name": repo.name,
                "indexing_status": repo.indexing_status,
                "current_revision": repo.current_revision,
                "last_indexed_at": repo.last_indexed_at.isoformat() if repo.last_indexed_at else None,
                "error_message": repo.error_message,
                "stats": repo.stats,
            },
            indent=2,
        )


@mcp_server.tool()
def search_code(
    query: str,
    repo_id: str = "",
    node_type: str = "",
    limit: int = 20,
) -> str:
    """Performs structural and lexical search across code symbols and files.
    
    Args:
        query: Search term (e.g., function name, class name, docstring keyword).
        repo_id: Optional repository UUID to scope search.
        node_type: Optional node category filter (FUNCTION, METHOD, CLASS, INTERFACE, TYPE, FILE, TEST).
        limit: Maximum number of search results to return (default 20).
    """
    with get_db_session() as db:
        engine = GraphQueryEngine(db)
        results = engine.search_code(
            query_text=query,
            repo_id=repo_id or None,
            node_type=node_type or None,
            limit=limit,
        )
        return json.dumps([r.model_dump() for r in results], indent=2)


@mcp_server.tool()
def find_symbol(name: str, repo_id: str = "", file_path: str = "") -> str:
    """Locates symbol definitions matching the given name with exact line numbers.
    
    Args:
        name: Symbol identifier (e.g., 'Calculator', 'add', 'formatResult').
        repo_id: Optional repository UUID.
        file_path: Optional relative file path to disambiguate.
    """
    with get_db_session() as db:
        engine = GraphQueryEngine(db)
        nodes = engine.find_symbol(name=name, repo_id=repo_id or None, file_path=file_path or None)
        return json.dumps(
            [
                {
                    "id": n.id,
                    "name": n.name,
                    "qualified_name": n.qualified_name,
                    "node_type": n.node_type,
                    "file_path": n.file_path,
                    "line_range": [n.start_line, n.end_line],
                    "signature": n.signature,
                    "docstring": n.docstring,
                    "language": n.language,
                }
                for n in nodes
            ],
            indent=2,
        )


@mcp_server.tool()
def get_symbol_details(node_id: str) -> str:
    """Returns complete symbol definition, incoming calls, and outgoing dependencies.
    
    Args:
        node_id: Full namespaced node ID (as returned from search_code or find_symbol).
    """
    with get_db_session() as db:
        engine = GraphQueryEngine(db)
        details = engine.get_symbol_details(node_id)
        if not details:
            return json.dumps({"error": f"Node '{node_id}' not found"})
        return json.dumps(details, indent=2, default=str)


@mcp_server.tool()
def get_callers(node_id: str, max_depth: int = 5) -> str:
    """Finds all functions/methods that call this symbol directly or transitively.
    
    Args:
        node_id: Target symbol node ID.
        max_depth: Maximum call-graph traversal depth (default 5).
    """
    with get_db_session() as db:
        engine = GraphQueryEngine(db)
        callers = engine.get_callers(node_id=node_id, max_depth=max_depth)
        return json.dumps(callers, indent=2, default=str)


@mcp_server.tool()
def get_callees(node_id: str, max_depth: int = 5) -> str:
    """Finds all functions/methods called by the given symbol directly or transitively.
    
    Args:
        node_id: Source symbol node ID.
        max_depth: Maximum call-graph traversal depth (default 5).
    """
    with get_db_session() as db:
        engine = GraphQueryEngine(db)
        callees = engine.get_callees(node_id=node_id, max_depth=max_depth)
        return json.dumps(callees, indent=2, default=str)


@mcp_server.tool()
def get_dependencies(node_id: str) -> str:
    """Returns file imports and structural dependencies of a file or symbol.
    
    Args:
        node_id: Node ID or file node ID.
    """
    with get_db_session() as db:
        engine = GraphQueryEngine(db)
        deps = engine.get_dependencies(node_id)
        return json.dumps(deps, indent=2, default=str)


@mcp_server.tool()
def trace_relationships(source_id: str, target_id: str, max_depth: int = 6) -> str:
    """Calculates the shortest relational path connecting two symbols or files.
    
    Args:
        source_id: Origin node ID.
        target_id: Destination node ID.
        max_depth: Maximum path search depth (default 6).
    """
    with get_db_session() as db:
        engine = GraphQueryEngine(db)
        path = engine.trace_relationships(source_id=source_id, target_id=target_id, max_depth=max_depth)
        if path is None:
            return json.dumps({"message": f"No relationship path found within {max_depth} hops", "path": []})
        return json.dumps({"path": path}, indent=2)


@mcp_server.tool()
def analyze_change_impact(target_node_or_file: str, repo_id: str, max_depth: int = 5) -> str:
    """Calculates blast radius: all affected symbols, files, and linked tests.
    
    Args:
        target_node_or_file: Symbol identifier, relative file path, or node ID.
        repo_id: Repository UUID.
        max_depth: Traversal depth for upstream consumers (default 5).
    """
    with get_db_session() as db:
        engine = GraphQueryEngine(db)
        try:
            impact = engine.analyze_change_impact(
                target_node_or_file=target_node_or_file,
                repo_id=repo_id,
                max_depth=max_depth,
            )
            return json.dumps(impact.model_dump(), indent=2, default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})


@mcp_server.tool()
def find_related_tests(node_id: str) -> str:
    """Discovers unit and integration tests covering the target symbol or file.
    
    Args:
        node_id: Node ID of the symbol or file.
    """
    with get_db_session() as db:
        engine = GraphQueryEngine(db)
        tests = engine.find_related_tests(node_id)
        return json.dumps(
            [
                {
                    "id": t.id,
                    "name": t.name,
                    "node_type": t.node_type,
                    "file_path": t.file_path,
                    "line": t.start_line,
                    "signature": t.signature,
                }
                for t in tests
            ],
            indent=2,
        )


@mcp_server.tool()
def get_graph_neighborhood(node_id: str, radius: int = 1, limit: int = 50) -> str:
    """Returns immediate N-hop graph neighborhood for visualization and context expansion.
    
    Args:
        node_id: Center node ID.
        radius: Number of hops (1 or 2).
        limit: Maximum nodes to return.
    """
    with get_db_session() as db:
        engine = GraphQueryEngine(db)
        neighborhood = engine.get_graph_neighborhood(node_id=node_id, radius=radius, limit=limit)
        return json.dumps(neighborhood, indent=2)


@mcp_server.tool()
def detect_import_cycles(repo_id: str) -> str:
    """Detects cyclic import dependencies across internal files in a repository.
    
    Returns cyclic strongly connected components (groups of files where each file
    directly or transitively depends on all others in the group, or self-loops).
    
    Args:
        repo_id: Repository UUID.
    """
    with get_db_session() as db:
        repo = db.query(RepositoryModel).filter_by(id=repo_id).first()
        if not repo:
            return json.dumps({"error": f"Repository '{repo_id}' not found"})
        engine = GraphQueryEngine(db)
        try:
            cycles = engine.detect_import_cycles(repo_id=repo_id)
            return json.dumps(
                {
                    "repository_id": repo_id,
                    "cyclic_components": cycles,
                    "total_cyclic_components": len(cycles),
                },
                indent=2,
            )
        except Exception as e:
            return json.dumps({"error": str(e)})


@mcp_server.tool()
def god_nodes(
    repo_id: str,
    limit: int = 20,
    scope: str = "all",
) -> str:
    """Identifies architectural hub nodes (god nodes) with high connectivity and call centrality.

    Args:
        repo_id: Repository UUID.
        limit: Maximum number of god nodes to return (default 20).
        scope: Analysis scope filter - 'all' (files and symbols), 'file' (only FILE nodes), or 'symbol' (only non-file symbols).
    """
    with get_db_session() as db:
        repo = db.query(RepositoryModel).filter_by(id=repo_id).first()
        if not repo:
            return json.dumps({"error": f"Repository '{repo_id}' not found"})
        if scope not in ("all", "file", "symbol"):
            return json.dumps({"error": f"Invalid scope '{scope}'. Must be 'all', 'file', or 'symbol'"})
        if limit <= 0:
            return json.dumps(
                {
                    "repository_id": repo_id,
                    "scope": scope,
                    "total_results": 0,
                    "god_nodes": [],
                },
                indent=2,
            )

        engine = GraphQueryEngine(db)
        results = engine.get_god_nodes(repo_id=repo_id, limit=limit, scope=scope)
        payload = {
            "repository_id": repo_id,
            "scope": scope,
            "total_results": len(results),
            "god_nodes": [r.model_dump() for r in results],
        }
        return json.dumps(payload, indent=2, default=str)


@mcp_server.tool()
def sync_repository(repo_id: str) -> str:
    """Incrementally synchronizes a repository with disk, updating modified, added, or deleted files.
    
    Args:
        repo_id: The UUID of the repository to synchronize.
    """
    with get_db_session() as db:
        repo = db.query(RepositoryModel).filter_by(id=repo_id).first()
        if not repo:
            return json.dumps({"error": f"Repository '{repo_id}' not found"})
        indexer = RepositoryIndexer(db)
        sync_result = indexer.sync_repository(repo_id)
        return json.dumps(sync_result, indent=2, default=str)


def main():
    parser = argparse.ArgumentParser(description="ApexGraph MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable_http"],
        default="stdio",
        help="Transport protocol to use",
    )
    parser.add_argument("--port", type=int, default=8001, help="Port for SSE / HTTP transport")
    parser.add_argument("--host", default="127.0.0.1", help="Host address for HTTP transport")
    parser.add_argument("--test", action="store_true", help="Run self-test inspection")
    args = parser.parse_args()

    init_db()

    if args.test:
        async def run_test():
            tools = await mcp_server.list_tools()
            print(f"ApexGraph MCP Server initialized with {len(tools)} tools:")
            for t in tools:
                print(f" - {t.name}: {t.description[:60]}...")
            print("Self-test check passed successfully.")

        asyncio.run(run_test())
        return

    if args.transport == "stdio":
        logger.info("Starting ApexGraph MCP Server over Stdio transport...")
        asyncio.run(mcp_server.run_stdio_async())
    else:
        logger.info(f"Starting ApexGraph MCP Server over SSE transport on {args.host}:{args.port}...")
        mcp_server.settings.port = args.port
        mcp_server.settings.host = args.host
        asyncio.run(mcp_server.run_sse_async())


if __name__ == "__main__":
    main()
