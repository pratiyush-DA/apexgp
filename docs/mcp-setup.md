# ApexGraph MCP Setup & Client Integration Guide

## 1. Overview
ApexGraph exposes a Model Context Protocol (MCP) server conforming to the official MCP specification. Coding assistants and agents can query repository structures, search symbols, trace dependency chains, and evaluate change impacts directly through standard MCP tools.

Two transports are supported:
1. **Stdio Transport:** Ideal for local coding agents (Antigravity, Claude Code, Cursor, Windsurf) executing the server process locally.
2. **Streamable HTTP (SSE) Transport:** For network-accessible, multi-user, or containerized deployments.

---

## 2. Available MCP Tools Catalog

| Tool Name | Parameters | Description |
| :--- | :--- | :--- |
| `list_workspaces` | `{}` | Lists all registered workspaces. |
| `list_repositories` | `{"workspace_id"?: str}` | Lists repositories with language stats and indexing status. |
| `get_repository_status` | `{"repo_id": str}` | Returns indexing status, last indexed revision, and statistics. |
| `search_code` | `{"query": str, "repo_id"?: str, "node_type"?: str, "limit"?: int}` | Exact & structural code search across symbols and files. |
| `find_symbol` | `{"name": str, "repo_id"?: str, "file_path"?: str}` | Locates a specific symbol definition with exact line ranges. |
| `get_symbol_details` | `{"node_id": str}` | Returns complete definition, signature, docstring, and metadata. |
| `get_callers` | `{"node_id": str, "max_depth"?: int}` | Finds all callers and incoming call paths with evidence. |
| `get_callees` | `{"node_id": str, "max_depth"?: int}` | Finds all functions/methods called by the given symbol. |
| `get_dependencies` | `{"node_id": str}` | Returns imports and structural dependencies of a file or symbol. |
| `trace_relationships` | `{"source_id": str, "target_id": str, "max_depth"?: int}` | Calculates shortest path connecting two symbols or files. |
| `analyze_change_impact`| `{"target_node_or_file": str, "repo_id": str, "max_depth"?: int}` | Calculates blast radius: affected symbols, files, and linked tests. |
| `find_related_tests` | `{"node_id": str}` | Discovers unit and integration tests covering the target symbol or file. |
| `get_graph_neighborhood` | `{"node_id": str, "radius"?: int, "limit"?: int}` | Returns immediate N-hop graph neighborhood for visualization. |

---

## 3. Client Configuration Examples

### 3.1 Google Antigravity Configuration (`mcp_config.json`)
Add the following entry to your workspace `.agents/mcp_config.json` or user `~/.gemini/antigravity-ide/mcp/` config:

```json
{
  "mcpServers": {
    "apexgraph": {
      "command": "python",
      "args": ["-m", "packages.mcp.server", "--transport", "stdio"],
      "cwd": "C:/Users/pkumar2/OneDrive - Data Axle/Documents/AI_INIT/RepopeekV3",
      "env": {
        "PYTHONPATH": "C:/Users/pkumar2/OneDrive - Data Axle/Documents/AI_INIT/RepopeekV3"
      }
    }
  }
}
```

### 3.2 Claude Code / Desktop Configuration (`claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "apexgraph": {
      "command": "python",
      "args": ["-m", "packages.mcp.server", "--transport", "stdio"],
      "cwd": "/path/to/RepopeekV3",
      "env": {
        "PYTHONPATH": "/path/to/RepopeekV3"
      }
    }
  }
}
```

### 3.3 Cursor / Windsurf Configuration
```json
{
  "mcp": {
    "servers": {
      "apexgraph": {
        "command": "python",
        "args": ["-m", "packages.mcp.server", "--transport", "stdio"],
        "cwd": "/path/to/RepopeekV3"
      }
    }
  }
}
```

---

## 4. Testing the MCP Server Directly

Run the interactive stdio inspector or test client:
```powershell
.\.venv\Scripts\python.exe -m packages.mcp.server --test
```
Or execute the automated MCP integration test suite:
```powershell
.\.venv\Scripts\pytest -v tests/integration/test_mcp_server.py
```
