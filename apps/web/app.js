/**
 * ApexGraph Web Dashboard and Interactive Graph Canvas
 */

// Application State
const state = {
  currentWorkspaceId: null,
  currentRepoId: null,
  activeTab: "overview",
  pollInterval: null,
  graph: {
    nodes: [],
    edges: [],
    nodeMap: new Map(),
    selectedNodeId: null,
    transform: { x: 0, y: 0, scale: 1 },
    isDragging: false,
    dragStart: { x: 0, y: 0 },
    simulating: true,
  },
};

const COLOR_MAP = {
  FUNCTION: "#10b981",
  METHOD: "#14b8a6",
  CLASS: "#6366f1",
  INTERFACE: "#06b6d4",
  TYPE: "#8b5cf6",
  FILE: "#64748b",
  TEST: "#f59e0b",
  DEFAULT: "#94a3b8",
};

// ============================================================================
// Initialization & Event Wiring
// ============================================================================

document.addEventListener("DOMContentLoaded", () => {
  setupNavigation();
  setupModals();
  setupGraphControls();
  setupSearch();
  setupImpact();
  loadInitialData();
});

function setupNavigation() {
  document.querySelectorAll(".nav-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".nav-tab").forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".view-panel").forEach((p) => p.classList.remove("active"));

      tab.classList.add("active");
      const targetId = `${tab.dataset.tab}-view`;
      const targetPanel = document.getElementById(targetId);
      if (targetPanel) {
        targetPanel.classList.add("active");
        state.activeTab = tab.dataset.tab;
        if (state.activeTab === "graph") {
          resizeCanvas();
          if (state.graph.nodes.length === 0 && state.currentRepoId) {
            loadInitialGraph(state.currentRepoId);
          }
        }
      }
    });
  });

  document.getElementById("workspace-select").addEventListener("change", (e) => {
    state.currentWorkspaceId = e.target.value;
    loadRepositories(state.currentWorkspaceId);
  });

  document.getElementById("repo-select").addEventListener("change", (e) => {
    state.currentRepoId = e.target.value;
    onRepoChanged();
  });

  document.getElementById("btn-reindex-repo").addEventListener("click", () => {
    if (state.currentRepoId) {
      reindexCurrentRepo();
    }
  });
}

// ============================================================================
// Data Fetching: Workspaces & Repositories
// ============================================================================

async function loadInitialData() {
  try {
    const res = await fetch("/api/workspaces");
    const workspaces = await res.json();
    const wsSelect = document.getElementById("workspace-select");
    wsSelect.innerHTML = "";

    if (workspaces.length === 0) {
      // Create a default workspace if none exist
      const createRes = await fetch("/api/workspaces", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: "Default Workspace" }),
      });
      const newWs = await createRes.json();
      workspaces.push(newWs);
    }

    workspaces.forEach((w) => {
      const opt = document.createElement("option");
      opt.value = w.id;
      opt.textContent = w.name;
      wsSelect.appendChild(opt);
    });

    state.currentWorkspaceId = workspaces[0].id;
    await loadRepositories(state.currentWorkspaceId);
  } catch (err) {
    console.error("Failed to load initial workspaces:", err);
  }
}

async function loadRepositories(workspaceId, selectedRepoId = null) {
  try {
    const res = await fetch(`/api/repositories?workspace_id=${workspaceId}`);
    const repos = await res.json();
    const repoSelect = document.getElementById("repo-select");
    repoSelect.innerHTML = "";

    if (repos.length === 0) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "No repositories registered";
      repoSelect.appendChild(opt);
      state.currentRepoId = null;
      renderOverviewEmpty();
      return;
    }

    repos.forEach((r) => {
      const opt = document.createElement("option");
      opt.value = r.id;
      opt.textContent = `${r.name} (${r.indexing_status})`;
      repoSelect.appendChild(opt);
    });

    const targetId = selectedRepoId && repos.some(r => r.id === selectedRepoId)
      ? selectedRepoId
      : (state.currentRepoId && repos.some(r => r.id === state.currentRepoId) ? state.currentRepoId : repos[0].id);

    repoSelect.value = targetId;
    state.currentRepoId = targetId;
    onRepoChanged();
  } catch (err) {
    console.error("Failed loading repositories:", err);
  }
}

async function onRepoChanged() {
  if (!state.currentRepoId) return;
  stopPolling();
  await loadOverview(state.currentRepoId);
  if (state.activeTab === "graph") {
    loadInitialGraph(state.currentRepoId);
  }
}

async function loadOverview(repoId) {
  try {
    const res = await fetch(`/api/repositories/${repoId}`);
    const repo = await res.json();

    // Update status badge
    const statusLower = (repo.indexing_status || "").toLowerCase();
    const statusElem = document.getElementById("metric-status");
    statusElem.innerHTML = `<span class="status-badge status-${statusLower}">${repo.indexing_status}</span>`;

    if (statusLower === "indexing" || statusLower === "pending") {
      startPolling(repoId);
    } else {
      stopPolling();
    }

    const stats = repo.stats || {};
    document.getElementById("metric-files").textContent = stats.total_files || 0;
    document.getElementById("metric-lines").textContent = (stats.total_lines || 0).toLocaleString();
    document.getElementById("metric-nodes").textContent = (stats.total_nodes || 0).toLocaleString();
    document.getElementById("metric-edges").textContent = (stats.total_edges || 0).toLocaleString();
    document.getElementById("metric-duration").textContent = stats.duration_seconds ? `${stats.duration_seconds}s` : "0.00s";

    // Languages breakdown
    const langContainer = document.getElementById("languages-container");
    langContainer.innerHTML = "";
    const languages = stats.languages || {};
    const totalLangFiles = Object.values(languages).reduce((a, b) => a + b, 0);

    if (totalLangFiles === 0) {
      langContainer.innerHTML = `<p style="color: var(--text-muted);">No language statistics available.</p>`;
    } else {
      for (const [lang, count] of Object.entries(languages)) {
        const pct = Math.round((count / totalLangFiles) * 100);
        const div = document.createElement("div");
        div.className = "dist-bar-wrapper";
        div.innerHTML = `
          <div class="dist-header">
            <span><strong>${lang.toUpperCase()}</strong> (${count} files)</span>
            <span>${pct}%</span>
          </div>
          <div class="dist-bar"><div class="dist-fill" style="width: ${pct}%;"></div></div>
        `;
        langContainer.appendChild(div);
      }
    }

    // Node Types distribution
    const typesContainer = document.getElementById("nodetypes-container");
    typesContainer.innerHTML = "";
    const nodeTypes = stats.node_types || {};
    const totalNodes = Object.values(nodeTypes).reduce((a, b) => a + b, 0);

    if (totalNodes === 0) {
      typesContainer.innerHTML = `<p style="color: var(--text-muted);">No symbol nodes extracted.</p>`;
    } else {
      for (const [type, count] of Object.entries(nodeTypes)) {
        const pct = Math.round((count / totalNodes) * 100);
        const div = document.createElement("div");
        div.className = "dist-bar-wrapper";
        div.innerHTML = `
          <div class="dist-header">
            <span><strong style="color: ${COLOR_MAP[type] || COLOR_MAP.DEFAULT};">${type}</strong> (${count})</span>
            <span>${pct}%</span>
          </div>
          <div class="dist-bar"><div class="dist-fill" style="width: ${pct}%; background-color: ${COLOR_MAP[type] || COLOR_MAP.DEFAULT};"></div></div>
        `;
        typesContainer.appendChild(div);
      }
    }

    // Diagnostics & Warnings
    const diagContainer = document.getElementById("diagnostics-container");
    if (repo.error_message) {
      diagContainer.innerHTML = `<div style="color: var(--color-danger); background: rgba(239,68,68,0.1); padding: 0.75rem; border-radius: 6px;">
        <strong>Indexing Error:</strong> ${repo.error_message}
      </div>`;
    } else if (stats.warnings && stats.warnings.length > 0) {
      diagContainer.innerHTML = `<ul style="list-style: none; font-size: 0.85rem; color: var(--text-secondary); display: flex; flex-direction: column; gap: 0.35rem;">
        ${stats.warnings.map((w) => `<li>⚠️ ${w}</li>`).join("")}
      </ul>`;
    } else {
      diagContainer.innerHTML = `<p style="color: var(--text-muted);">Clean index. Zero parsing warnings reported.</p>`;
    }
  } catch (err) {
    console.error("Failed loading overview:", err);
  }
}

function startPolling(repoId) {
  if (state.pollInterval) return;
  state.pollInterval = setInterval(async () => {
    if (!state.currentRepoId || state.currentRepoId !== repoId) {
      stopPolling();
      return;
    }
    try {
      const res = await fetch(`/api/repositories/${repoId}`);
      if (!res.ok) return;
      const repo = await res.json();
      const st = (repo.indexing_status || "").toLowerCase();
      if (st !== "indexing" && st !== "pending") {
        stopPolling();
        await loadRepositories(state.currentWorkspaceId, repoId);
      }
    } catch (e) {
      console.warn("Polling error:", e);
    }
  }, 2000);
}

function stopPolling() {
  if (state.pollInterval) {
    clearInterval(state.pollInterval);
    state.pollInterval = null;
  }
}

function renderOverviewEmpty() {
  document.getElementById("metric-status").innerHTML = `<span class="status-badge status-pending">NO REPO</span>`;
  document.getElementById("metric-files").textContent = "0";
  document.getElementById("metric-lines").textContent = "0";
  document.getElementById("metric-nodes").textContent = "0";
  document.getElementById("metric-edges").textContent = "0";
  document.getElementById("metric-duration").textContent = "0.00s";
  document.getElementById("languages-container").innerHTML = `<p style="color: var(--text-muted);">Register a repository to index code.</p>`;
  document.getElementById("nodetypes-container").innerHTML = `<p style="color: var(--text-muted);">Register a repository to index code.</p>`;
}

async function reindexCurrentRepo() {
  const btn = document.getElementById("btn-reindex-repo");
  btn.disabled = true;
  btn.textContent = "Indexing...";
  try {
    const res = await fetch(`/api/repositories/${state.currentRepoId}/index`, { method: "POST" });
    if (!res.ok) {
      const err = await res.json();
      alert(`Indexing failed: ${err.detail}`);
    } else {
      await loadRepositories(state.currentWorkspaceId, state.currentRepoId);
    }
  } catch (err) {
    alert(`Re-indexing error: ${err}`);
  } finally {
    btn.disabled = false;
    btn.innerHTML = "<span>↻</span> Re-Index";
  }
}

// ============================================================================
// Modals Handling
// ============================================================================

function setupModals() {
  // Register Repo Modal
  const repoModal = document.getElementById("modal-register-repo");
  document.getElementById("btn-register-repo").addEventListener("click", () => {
    document.getElementById("repo-modal-error").style.display = "none";
    repoModal.classList.add("active");
  });
  document.getElementById("btn-cancel-repo").addEventListener("click", () => {
    repoModal.classList.remove("active");
  });
  document.getElementById("btn-submit-repo").addEventListener("click", async () => {
    const btnSubmit = document.getElementById("btn-submit-repo");
    const name = document.getElementById("repo-name-input").value.trim();
    const sourceType = document.getElementById("repo-source-type").value;
    const path = document.getElementById("repo-path-input").value.trim();
    const errBox = document.getElementById("repo-modal-error");

    if (!name || !path) {
      errBox.textContent = "Both name and path/URL are required.";
      errBox.style.display = "block";
      return;
    }

    btnSubmit.disabled = true;
    btnSubmit.textContent = "Registering & Indexing...";

    try {
      const res = await fetch("/api/repositories", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          workspace_id: state.currentWorkspaceId,
          name,
          source_type: sourceType,
          source_path: path,
        }),
      });

      if (!res.ok) {
        const err = await res.json();
        errBox.textContent = err.detail || "Registration failed.";
        errBox.style.display = "block";
        btnSubmit.disabled = false;
        btnSubmit.textContent = "Register & Index";
        return;
      }

      const repo = await res.json();

      // Auto-trigger indexing
      const indexRes = await fetch(`/api/repositories/${repo.id}/index`, { method: "POST" });
      if (!indexRes.ok) {
        const indexErr = await indexRes.json();
        alert(`Repository registered but indexing failed: ${indexErr.detail}`);
      }

      repoModal.classList.remove("active");
      await loadRepositories(state.currentWorkspaceId, repo.id);
    } catch (e) {
      errBox.textContent = `Error: ${e}`;
      errBox.style.display = "block";
    } finally {
      btnSubmit.disabled = false;
      btnSubmit.textContent = "Register & Index";
    }
  });

  // New Workspace Modal
  const wsModal = document.getElementById("modal-new-workspace");
  document.getElementById("btn-new-workspace").addEventListener("click", () => {
    document.getElementById("workspace-modal-error").style.display = "none";
    wsModal.classList.add("active");
  });
  document.getElementById("btn-cancel-workspace").addEventListener("click", () => {
    wsModal.classList.remove("active");
  });
  document.getElementById("btn-submit-workspace").addEventListener("click", async () => {
    const name = document.getElementById("workspace-name-input").value.trim();
    const errBox = document.getElementById("workspace-modal-error");
    if (!name) {
      errBox.textContent = "Workspace name is required.";
      errBox.style.display = "block";
      return;
    }
    try {
      const res = await fetch("/api/workspaces", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!res.ok) {
        const err = await res.json();
        errBox.textContent = err.detail || "Creation failed.";
        errBox.style.display = "block";
        return;
      }
      wsModal.classList.remove("active");
      await loadInitialData();
    } catch (e) {
      errBox.textContent = `Error: ${e}`;
      errBox.style.display = "block";
    }
  });
}

// ============================================================================
// Interactive Graph Canvas Engine
// ============================================================================

let canvas, ctx;

function setupGraphControls() {
  canvas = document.getElementById("graph-canvas");
  ctx = canvas.getContext("2d");

  window.addEventListener("resize", resizeCanvas);

  // Pan & Drag handlers
  canvas.addEventListener("mousedown", (e) => {
    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    // Check if clicked on a node
    const clickedNode = findNodeAt(mouseX, mouseY);
    if (clickedNode) {
      selectNode(clickedNode);
    } else {
      state.graph.isDragging = true;
      state.graph.dragStart = { x: mouseX - state.graph.transform.x, y: mouseY - state.graph.transform.y };
    }
  });

  canvas.addEventListener("mousemove", (e) => {
    if (state.graph.isDragging) {
      const rect = canvas.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const mouseY = e.clientY - rect.top;
      state.graph.transform.x = mouseX - state.graph.dragStart.x;
      state.graph.transform.y = mouseY - state.graph.dragStart.y;
      renderGraph();
    }
  });

  window.addEventListener("mouseup", () => {
    state.graph.isDragging = false;
  });

  // Double click expands neighbors
  canvas.addEventListener("dblclick", (e) => {
    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;
    const node = findNodeAt(mouseX, mouseY);
    if (node) {
      expandNodeNeighbors(node.id);
    }
  });

  // Zoom handlers
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const zoomFactor = e.deltaY < 0 ? 1.1 : 0.9;
    zoom(zoomFactor, e.clientX, e.clientY);
  });

  document.getElementById("btn-zoom-in").addEventListener("click", () => zoom(1.2));
  document.getElementById("btn-zoom-out").addEventListener("click", () => zoom(0.8));
  document.getElementById("btn-zoom-reset").addEventListener("click", resetGraphView);

  // Node type filters
  document.querySelectorAll(".node-filter").forEach((cb) => {
    cb.addEventListener("change", renderGraph);
  });

  // Search input in sidebar
  document.getElementById("graph-search-input").addEventListener("keydown", async (e) => {
    if (e.key === "Enter") {
      const query = e.target.value.trim();
      if (!query || !state.currentRepoId) return;
      const res = await fetch(`/api/repositories/${state.currentRepoId}/search?q=${encodeURIComponent(query)}&limit=1`);
      const results = await res.json();
      if (results.length > 0) {
        expandNodeNeighbors(results[0].node.id, true);
      }
    }
  });

  document.getElementById("btn-insp-impact").addEventListener("click", () => {
    if (state.graph.selectedNodeId) {
      const node = state.graph.nodeMap.get(state.graph.selectedNodeId);
      if (node) {
        document.querySelector("[data-tab='impact']").click();
        document.getElementById("impact-target-input").value = node.name;
        document.getElementById("btn-run-impact").click();
      }
    }
  });

  // Hop radius slider
  document.getElementById("graph-radius-slider").addEventListener("input", (e) => {
    document.getElementById("radius-val").textContent = e.target.value;
  });

  startForceSimulation();
}

function resizeCanvas() {
  if (!canvas) return;
  const parent = canvas.parentElement;
  canvas.width = parent.clientWidth;
  canvas.height = parent.clientHeight;
  renderGraph();
}

function zoom(factor, clientX, clientY) {
  state.graph.transform.scale = Math.max(0.2, Math.min(4.0, state.graph.transform.scale * factor));
  renderGraph();
}

function resetGraphView() {
  state.graph.transform = { x: canvas.width / 2, y: canvas.height / 2, scale: 1 };
  renderGraph();
}

async function loadInitialGraph(repoId) {
  try {
    // Find key classes and functions to seed initial graph view
    const res = await fetch(`/api/repositories/${repoId}/search?q=a&limit=25`);
    const results = await res.json();
    if (results.length === 0) return;

    const seedId = results[0].node.id;
    await expandNodeNeighbors(seedId, true);
  } catch (err) {
    console.error("Failed loading initial graph seed:", err);
  }
}

async function expandNodeNeighbors(nodeId, centerOnNode = false) {
  try {
    const radius = document.getElementById("graph-radius-slider").value || 1;
    const res = await fetch(`/api/nodes/${encodeURIComponent(nodeId)}/neighborhood?radius=${radius}&limit=100`);
    const data = await res.json();

    data.nodes.forEach((n) => {
      if (!state.graph.nodeMap.has(n.id)) {
        const angle = Math.random() * Math.PI * 2;
        const dist = 50 + Math.random() * 150;
        const newNode = {
          ...n,
          x: (canvas.width / 2) + Math.cos(angle) * dist,
          y: (canvas.height / 2) + Math.sin(angle) * dist,
          vx: 0,
          vy: 0,
          radius: n.node_type === "CLASS" ? 18 : (n.node_type === "FILE" ? 22 : 14),
        };
        state.graph.nodes.push(newNode);
        state.graph.nodeMap.set(n.id, newNode);
      }
    });

    data.edges.forEach((e) => {
      const exists = state.graph.edges.some((ex) => ex.id === e.id);
      if (!exists && state.graph.nodeMap.has(e.source) && state.graph.nodeMap.has(e.target)) {
        state.graph.edges.push(e);
      }
    });

    if (centerOnNode && state.graph.nodeMap.has(nodeId)) {
      const targetNode = state.graph.nodeMap.get(nodeId);
      selectNode(targetNode);
      state.graph.transform.x = canvas.width / 2 - targetNode.x * state.graph.transform.scale;
      state.graph.transform.y = canvas.height / 2 - targetNode.y * state.graph.transform.scale;
    }

    state.graph.simulating = true;
  } catch (err) {
    console.error("Failed expanding neighbors:", err);
  }
}

function findNodeAt(clientX, clientY) {
  const { x: tx, y: ty, scale } = state.graph.transform;
  const worldX = (clientX - tx) / scale;
  const worldY = (clientY - ty) / scale;

  for (let i = state.graph.nodes.length - 1; i >= 0; i--) {
    const n = state.graph.nodes[i];
    const dx = worldX - n.x;
    const dy = worldY - n.y;
    if (Math.sqrt(dx * dx + dy * dy) <= n.radius) {
      return n;
    }
  }
  return null;
}

async function selectNode(node) {
  state.graph.selectedNodeId = node.id;
  renderGraph();

  // Populate Inspector
  const badge = document.getElementById("insp-badge");
  badge.textContent = node.node_type;
  badge.style.backgroundColor = COLOR_MAP[node.node_type] || COLOR_MAP.DEFAULT;
  badge.style.color = "#fff";

  document.getElementById("insp-name").textContent = node.name;
  document.getElementById("insp-file").textContent = `${node.file_path}:${node.line || 1}`;

  try {
    const res = await fetch(`/api/nodes/${encodeURIComponent(node.id)}/details`);
    if (res.ok) {
      const details = await res.json();
      document.getElementById("insp-signature").textContent = details.node.signature || "No signature";
      document.getElementById("insp-doc").textContent = details.node.docstring || "No docstring provided.";

      const relContainer = document.getElementById("insp-relations");
      relContainer.innerHTML = "";

      const incoming = details.incoming_edges || [];
      const outgoing = details.outgoing_edges || [];

      if (incoming.length === 0 && outgoing.length === 0) {
        relContainer.innerHTML = `<p style="color: var(--text-muted);">No edges linked.</p>`;
      } else {
        incoming.forEach((e) => {
          const row = document.createElement("div");
          row.style.fontSize = "0.75rem";
          row.style.color = "var(--text-secondary)";
          row.innerHTML = `<span style="color: var(--color-accent);">← ${e.edge_type}</span> from <span style="font-family: var(--font-mono);">${e.source_id.split(':').pop()}</span>`;
          relContainer.appendChild(row);
        });
        outgoing.forEach((e) => {
          const row = document.createElement("div");
          row.style.fontSize = "0.75rem";
          row.style.color = "var(--text-secondary)";
          row.innerHTML = `<span style="color: var(--color-function);">→ ${e.edge_type}</span> to <span style="font-family: var(--font-mono);">${e.target_id.split(':').pop()}</span>`;
          relContainer.appendChild(row);
        });
      }
    }
  } catch (err) {
    console.error("Failed fetching node details:", err);
  }
}

// Simple Physics Simulation
function startForceSimulation() {
  function step() {
    if (state.graph.simulating && state.graph.nodes.length > 0) {
      const nodes = state.graph.nodes;
      const edges = state.graph.edges;

      // Node repulsion
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const n1 = nodes[i];
          const n2 = nodes[j];
          const dx = n2.x - n1.x;
          const dy = n2.y - n1.y;
          const dist = Math.sqrt(dx * dx + dy * dy) || 1;
          if (dist < 200) {
            const force = (200 - dist) / dist * 0.5;
            n1.vx -= dx * force * 0.05;
            n1.vy -= dy * force * 0.05;
            n2.vx += dx * force * 0.05;
            n2.vy += dy * force * 0.05;
          }
        }
      }

      // Edge springs
      edges.forEach((e) => {
        const source = state.graph.nodeMap.get(e.source);
        const target = state.graph.nodeMap.get(e.target);
        if (source && target) {
          const dx = target.x - source.x;
          const dy = target.y - source.y;
          const dist = Math.sqrt(dx * dx + dy * dy) || 1;
          const targetDist = 90;
          const force = (dist - targetDist) * 0.02;
          source.vx += dx * force * 0.05;
          source.vy += dy * force * 0.05;
          target.vx -= dx * force * 0.05;
          target.vy -= dy * force * 0.05;
        }
      });

      // Position update with damping
      let totalMotion = 0;
      nodes.forEach((n) => {
        n.x += n.vx;
        n.y += n.vy;
        n.vx *= 0.85;
        n.vy *= 0.85;
        totalMotion += Math.abs(n.vx) + Math.abs(n.vy);
      });

      if (totalMotion < 0.1) {
        state.graph.simulating = false;
      }
      renderGraph();
    }
    requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

function renderGraph() {
  if (!ctx || !canvas) return;

  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.save();

  // Apply camera transform
  ctx.translate(state.graph.transform.x, state.graph.transform.y);
  ctx.scale(state.graph.transform.scale, state.graph.transform.scale);

  const activeFilters = new Set(
    Array.from(document.querySelectorAll(".node-filter:checked")).map((cb) => cb.value)
  );

  // Render Edges
  state.graph.edges.forEach((edge) => {
    const src = state.graph.nodeMap.get(edge.source);
    const dst = state.graph.nodeMap.get(edge.target);
    if (!src || !dst) return;
    if (!activeFilters.has(src.node_type) || !activeFilters.has(dst.node_type)) return;

    const isConnected = state.graph.selectedNodeId === src.id || state.graph.selectedNodeId === dst.id;

    ctx.beginPath();
    ctx.moveTo(src.x, src.y);
    ctx.lineTo(dst.x, dst.y);
    ctx.strokeStyle = isConnected ? "#60a5fa" : "rgba(100, 116, 139, 0.35)";
    ctx.lineWidth = isConnected ? 2.5 : 1.2;
    ctx.stroke();

    // Render edge label if selected
    if (isConnected) {
      const midX = (src.x + dst.x) / 2;
      const midY = (src.y + dst.y) / 2;
      ctx.fillStyle = "#94a3b8";
      ctx.font = "9px Inter, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText(edge.type, midX, midY - 3);
    }
  });

  // Render Nodes
  state.graph.nodes.forEach((node) => {
    if (!activeFilters.has(node.node_type)) return;

    const isSelected = state.graph.selectedNodeId === node.id;
    const color = COLOR_MAP[node.node_type] || COLOR_MAP.DEFAULT;

    // Outer glow for selected
    if (isSelected) {
      ctx.beginPath();
      ctx.arc(node.x, node.y, node.radius + 6, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(59, 130, 246, 0.35)";
      ctx.fill();
    }

    // Node body
    ctx.beginPath();
    ctx.arc(node.x, node.y, node.radius, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.strokeStyle = isSelected ? "#ffffff" : "rgba(255, 255, 255, 0.4)";
    ctx.lineWidth = isSelected ? 2.5 : 1;
    ctx.stroke();

    // Label
    ctx.fillStyle = "#f8fafc";
    ctx.font = "11px Inter, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(node.name, node.x, node.y + node.radius + 14);
  });

  ctx.restore();
}

// ============================================================================
// Symbol Search View
// ============================================================================

let searchDebounceTimer = null;
let activeSearchType = "";

function setupSearch() {
  const searchInput = document.getElementById("main-search-input");
  searchInput.addEventListener("input", () => {
    clearTimeout(searchDebounceTimer);
    searchDebounceTimer = setTimeout(executeSearch, 250);
  });

  document.getElementById("btn-search-exec").addEventListener("click", executeSearch);

  // Type filter chips
  document.querySelectorAll(".type-filter-group .filter-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      document.querySelectorAll(".type-filter-group .filter-chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      activeSearchType = chip.dataset.type;
      executeSearch();
    });
  });
}

async function executeSearch() {
  const query = document.getElementById("main-search-input").value.trim();
  const tbody = document.getElementById("search-results-tbody");

  if (!query || !state.currentRepoId) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 2rem;">Type a search query above.</td></tr>`;
    return;
  }

  tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 2rem;">Searching repository...</td></tr>`;

  try {
    let url = `/api/repositories/${state.currentRepoId}/search?q=${encodeURIComponent(query)}&limit=50`;
    if (activeSearchType) {
      url += `&type=${activeSearchType}`;
    }

    const res = await fetch(url);
    const results = await res.json();

    if (results.length === 0) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 2rem;">No matching symbols found.</td></tr>`;
      return;
    }

    tbody.innerHTML = "";
    results.forEach((r) => {
      const n = r.node;
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td style="font-weight: 600;">${n.name}</td>
        <td><span class="node-badge" style="background: ${COLOR_MAP[n.node_type] || COLOR_MAP.DEFAULT}; color: #fff;">${n.node_type}</span></td>
        <td style="font-family: var(--font-mono); font-size: 0.8rem;">${n.file_path}</td>
        <td>${n.start_line} - ${n.end_line}</td>
        <td style="font-family: var(--font-mono); font-size: 0.75rem; max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${r.snippet || n.signature}</td>
        <td><button class="btn btn-inspect" data-node-id="${n.id}">Inspect</button></td>
      `;
      tbody.appendChild(tr);
    });

    tbody.querySelectorAll(".btn-inspect").forEach((btn) => {
      btn.addEventListener("click", () => {
        const nodeId = btn.dataset.nodeId;
        document.querySelector("[data-tab='graph']").click();
        expandNodeNeighbors(nodeId, true);
      });
    });
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6" style="color: var(--color-danger); text-align: center; padding: 2rem;">Search failed: ${err}</td></tr>`;
  }
}

// ============================================================================
// Change Impact Blast-Radius View
// ============================================================================

function setupImpact() {
  const depthSlider = document.getElementById("impact-depth-slider");
  depthSlider.addEventListener("input", (e) => {
    document.getElementById("impact-depth-val").textContent = e.target.value;
  });

  document.getElementById("btn-run-impact").addEventListener("click", async () => {
    const target = document.getElementById("impact-target-input").value.trim();
    const depth = depthSlider.value || 5;
    const resBox = document.getElementById("impact-results-container");

    if (!target || !state.currentRepoId) {
      alert("Please enter a target symbol or file name.");
      return;
    }

    const btn = document.getElementById("btn-run-impact");
    btn.disabled = true;
    btn.textContent = "Analyzing...";

    try {
      const res = await fetch(`/api/repositories/${state.currentRepoId}/impact?target=${encodeURIComponent(target)}&depth=${depth}`);
      if (!res.ok) {
        const err = await res.json();
        alert(`Impact analysis failed: ${err.detail}`);
        return;
      }

      const impact = await res.json();
      resBox.style.display = "block";

      document.getElementById("impact-metric-target").textContent = impact.target;
      document.getElementById("impact-metric-symbols").textContent = impact.total_affected;
      document.getElementById("impact-metric-files").textContent = impact.affected_files.length;
      document.getElementById("impact-metric-tests").textContent = impact.linked_tests.length;

      // Affected Files
      const filesList = document.getElementById("impact-files-list");
      filesList.innerHTML = "";
      impact.affected_files.forEach((f) => {
        const li = document.createElement("li");
        li.innerHTML = `📄 ${f}`;
        filesList.appendChild(li);
      });

      // Linked Tests
      const testsList = document.getElementById("impact-tests-list");
      testsList.innerHTML = "";
      if (impact.linked_tests.length === 0) {
        testsList.innerHTML = `<li style="color: var(--text-muted);">No linked unit tests discovered.</li>`;
      } else {
        impact.linked_tests.forEach((t) => {
          const li = document.createElement("li");
          li.innerHTML = `🧪 <strong>${t.name}</strong> <span style="color: var(--text-muted);">(${t.file_path}:${t.start_line})</span>`;
          testsList.appendChild(li);
        });
      }

      // Evidence Propagation Trace
      const tbody = document.getElementById("impact-evidence-tbody");
      tbody.innerHTML = "";
      const traces = impact.evidence_paths || [];
      if (traces.length === 0) {
        tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 1.5rem;">Direct modification — no upstream consumers identified.</td></tr>`;
      } else {
        traces.forEach((tr) => {
          const trElem = document.createElement("tr");
          trElem.innerHTML = `
            <td><strong>${tr.source}</strong></td>
            <td><span class="status-badge status-ready">${tr.edge_type}</span></td>
            <td style="font-family: var(--font-mono); font-size: 0.8rem;">${tr.file}</td>
            <td>${tr.line}</td>
            <td style="font-family: var(--font-mono); font-size: 0.75rem;"><code>${tr.evidence}</code></td>
          `;
          tbody.appendChild(trElem);
        });
      }
    } catch (err) {
      alert(`Impact calculation error: ${err}`);
    } finally {
      btn.disabled = false;
      btn.textContent = "Calculate Blast Radius";
    }
  });
}
