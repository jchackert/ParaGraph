# write graph to HTML, JSON, GraphML, and Neo4j Cypher
from __future__ import annotations
import html as _html
import json
import math
import re
from collections import Counter
from pathlib import Path
import networkx as nx
from networkx.readwrite import json_graph
from paragraph.security import sanitize_label
from paragraph.analyze import _node_community_map
from paragraph.layers import classify_layer, is_layer_violation, ALL_LAYERS

def _strip_diacritics(text: str) -> str:
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


COMMUNITY_COLORS = [
    "#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F",
    "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC",
]

MAX_NODES_FOR_VIZ = 5_000


def _html_styles() -> str:
    return """<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0f0f1a; color: #e0e0e0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; display: flex; height: 100vh; overflow: hidden; }
  #graph { flex: 1; }
  #sidebar { width: 280px; background: #1a1a2e; border-left: 1px solid #2a2a4e; display: flex; flex-direction: column; overflow: hidden; }
  #search-wrap { padding: 12px; border-bottom: 1px solid #2a2a4e; }
  #search { width: 100%; background: #0f0f1a; border: 1px solid #3a3a5e; color: #e0e0e0; padding: 7px 10px; border-radius: 6px; font-size: 13px; outline: none; }
  #search:focus { border-color: #4E79A7; }
  #search-results { max-height: 140px; overflow-y: auto; padding: 4px 12px; border-bottom: 1px solid #2a2a4e; display: none; }
  .search-item { padding: 4px 6px; cursor: pointer; border-radius: 4px; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .search-item:hover { background: #2a2a4e; }
  #info-panel { padding: 14px; border-bottom: 1px solid #2a2a4e; min-height: 140px; }
  #info-panel h3 { font-size: 13px; color: #aaa; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.05em; }
  #info-content { font-size: 13px; color: #ccc; line-height: 1.6; }
  #info-content .field { margin-bottom: 5px; }
  #info-content .field b { color: #e0e0e0; }
  #info-content .empty { color: #555; font-style: italic; }
  .neighbor-link { display: block; padding: 2px 6px; margin: 2px 0; border-radius: 3px; cursor: pointer; font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; border-left: 3px solid #333; }
  .neighbor-link:hover { background: #2a2a4e; }
  #neighbors-list { max-height: 160px; overflow-y: auto; margin-top: 4px; }
  #legend-wrap { flex: 1; overflow-y: auto; padding: 12px; }
  #legend-wrap h3 { font-size: 13px; color: #aaa; margin-bottom: 10px; text-transform: uppercase; letter-spacing: 0.05em; }
  .legend-item { display: flex; align-items: center; gap: 8px; padding: 4px 0; cursor: pointer; border-radius: 4px; font-size: 12px; }
  .legend-item:hover { background: #2a2a4e; padding-left: 4px; }
  .legend-item.dimmed { opacity: 0.35; }
  .legend-dot { width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }
  .legend-label { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .legend-count { color: #666; font-size: 11px; }
  #stats { padding: 10px 14px; border-top: 1px solid #2a2a4e; font-size: 11px; color: #555; }
  #controls { border-bottom: 1px solid #2a2a4e; padding: 10px 12px; max-height: 38vh; overflow-y: auto; flex-shrink: 0; }
  .ctl-section { margin-bottom: 12px; }
  .ctl-section:last-child { margin-bottom: 0; }
  .ctl-section h3 { font-size: 12px; color: #aaa; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.05em; }
  .ctl-row { display: flex; align-items: center; gap: 6px; font-size: 12px; padding: 2px 0; cursor: pointer; color: #ccc; }
  .ctl-row input { accent-color: #4E79A7; }
  .lens-title { font-size: 11px; color: #777; margin: 6px 0 2px; text-transform: uppercase; letter-spacing: 0.04em; }
  #violation-count { font-size: 11px; margin-top: 4px; }
  #blast-btn { width: 100%; background: #0f0f1a; border: 1px solid #3a3a5e; color: #e0e0e0; padding: 7px 10px; border-radius: 6px; font-size: 12px; cursor: pointer; }
  #blast-btn:hover { border-color: #4E79A7; }
  #blast-btn.armed { border-color: #E15759; color: #E15759; }
</style>"""


def _hyperedge_script(hyperedges_json: str) -> str:
    return f"""<script>
// Render hyperedges as shaded regions
const hyperedges = {hyperedges_json};
// afterDrawing passes ctx already transformed to network coordinate space.
// Draw node positions raw — no manual pan/zoom/DPR math needed.
network.on('afterDrawing', function(ctx) {{
    hyperedges.forEach(h => {{
        const positions = h.nodes
            .map(nid => network.getPositions([nid])[nid])
            .filter(p => p !== undefined);
        if (positions.length < 2) return;
        ctx.save();
        ctx.globalAlpha = 0.12;
        ctx.fillStyle = '#6366f1';
        ctx.strokeStyle = '#6366f1';
        ctx.lineWidth = 2;
        ctx.beginPath();
        // Centroid and expanded hull in network coordinates
        const cx = positions.reduce((s, p) => s + p.x, 0) / positions.length;
        const cy = positions.reduce((s, p) => s + p.y, 0) / positions.length;
        const expanded = positions.map(p => ({{
            x: cx + (p.x - cx) * 1.15,
            y: cy + (p.y - cy) * 1.15
        }}));
        ctx.moveTo(expanded[0].x, expanded[0].y);
        expanded.slice(1).forEach(p => ctx.lineTo(p.x, p.y));
        ctx.closePath();
        ctx.fill();
        ctx.globalAlpha = 0.4;
        ctx.stroke();
        // Label
        ctx.globalAlpha = 0.8;
        ctx.fillStyle = '#4f46e5';
        ctx.font = 'bold 11px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(h.label, cx, cy - 5);
        ctx.restore();
    }});
}});
</script>"""


def _html_script(nodes_json: str, edges_json: str, legend_json: str,
                 layers_json: str | None = None) -> str:
    if layers_json is None:
        layers_json = json.dumps(ALL_LAYERS)
    return f"""<script>
const RAW_NODES = {nodes_json};
const RAW_EDGES = {edges_json};
const LEGEND = {legend_json};

// HTML-escape helper — prevents XSS when injecting graph data into innerHTML
function esc(s) {{
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}}

const nodeById = new Map(RAW_NODES.map(n => [n.id, n]));

// Build vis datasets
const nodesDS = new vis.DataSet(RAW_NODES.map(n => ({{
  id: n.id, label: n.label, color: n.color, size: n.size,
  font: n.font, title: n.title,
  _community: n.community, _community_name: n.community_name,
  _source_file: n.source_file, _file_type: n.file_type, _degree: n.degree,
  _layer: n.layer || 'other',
  _href: n.href || null,
}})));

const edgesDS = new vis.DataSet(RAW_EDGES.map((e, i) => ({{
  id: i, from: e.from, to: e.to,
  label: '',
  title: e.title,
  dashes: e.dashes,
  width: e.width,
  color: e.color,
  arrows: {{ to: {{ enabled: true, scaleFactor: 0.5 }} }},
}})));

const container = document.getElementById('graph');
const network = new vis.Network(container, {{ nodes: nodesDS, edges: edgesDS }}, {{
  physics: {{
    enabled: true,
    solver: 'forceAtlas2Based',
    forceAtlas2Based: {{
      gravitationalConstant: -60,
      centralGravity: 0.005,
      springLength: 120,
      springConstant: 0.08,
      damping: 0.4,
      avoidOverlap: 0.8,
    }},
    stabilization: {{ iterations: 200, fit: true }},
  }},
  interaction: {{
    hover: true,
    tooltipDelay: 100,
    hideEdgesOnDrag: true,
    navigationButtons: false,
    keyboard: false,
  }},
  nodes: {{ shape: 'dot', borderWidth: 1.5 }},
  edges: {{ smooth: {{ type: 'continuous', roundness: 0.2 }}, selectionWidth: 3 }},
}});

network.once('stabilizationIterationsDone', () => {{
  network.setOptions({{ physics: {{ enabled: false }} }});
}});

// Drill-down: double-click a node that carries a page link (aggregated view)
network.on('doubleClick', params => {{
  if (!params.nodes.length) return;
  const n = nodesDS.get(params.nodes[0]);
  if (n && n._href) window.location = n._href;
}});

function showInfo(nodeId) {{
  const n = nodesDS.get(nodeId);
  if (!n) return;
  const neighborIds = network.getConnectedNodes(nodeId);
  const neighborItems = neighborIds.map(nid => {{
    const nb = nodesDS.get(nid);
    const color = nb ? nb.color.background : '#555';
    return `<span class="neighbor-link" style="border-left-color:${{esc(color)}}" onclick="focusNode(${{JSON.stringify(nid)}})">${{esc(nb ? nb.label : nid)}}</span>`;
  }}).join('');
  document.getElementById('info-content').innerHTML = `
    <div class="field"><b>${{esc(n.label)}}</b></div>
    <div class="field">Type: ${{esc(n._file_type || 'unknown')}}</div>
    <div class="field">Community: ${{esc(n._community_name)}}</div>
    <div class="field">Source: ${{esc(n._source_file || '-')}}</div>
    <div class="field">Degree: ${{n._degree}}</div>
    ${{neighborIds.length ? `<div class="field" style="margin-top:8px;color:#aaa;font-size:11px">Neighbors (${{neighborIds.length}})</div><div id="neighbors-list">${{neighborItems}}</div>` : ''}}
  `;
}}

function focusNode(nodeId) {{
  network.focus(nodeId, {{ scale: 1.4, animation: true }});
  network.selectNodes([nodeId]);
  showInfo(nodeId);
}}

// Track hovered node — hover detection is more reliable than click params
let hoveredNodeId = null;
network.on('hoverNode', params => {{
  hoveredNodeId = params.node;
  container.style.cursor = 'pointer';
}});
network.on('blurNode', () => {{
  hoveredNodeId = null;
  container.style.cursor = 'default';
}});
container.addEventListener('click', () => {{
  if (hoveredNodeId !== null) {{
    if (blastArmed) {{ runBlast(hoveredNodeId); return; }}
    if (blastClickGuard) return;
    showInfo(hoveredNodeId);
    network.selectNodes([hoveredNodeId]);
  }}
}});
network.on('click', params => {{
  if (params.nodes.length > 0) {{
    if (blastArmed) {{ runBlast(params.nodes[0]); return; }}
    if (blastClickGuard) return;
    showInfo(params.nodes[0]);
  }} else if (hoveredNodeId === null) {{
    if (blastArmed || blastActive) {{ resetBlast(); return; }}
    document.getElementById('info-content').innerHTML = '<span class="empty">Click a node to inspect it</span>';
  }}
}});

const searchInput = document.getElementById('search');
const searchResults = document.getElementById('search-results');
searchInput.addEventListener('input', () => {{
  const q = searchInput.value.toLowerCase().trim();
  searchResults.innerHTML = '';
  if (!q) {{ searchResults.style.display = 'none'; return; }}
  const matches = RAW_NODES.filter(n => n.label.toLowerCase().includes(q)).slice(0, 20);
  if (!matches.length) {{ searchResults.style.display = 'none'; return; }}
  searchResults.style.display = 'block';
  matches.forEach(n => {{
    const el = document.createElement('div');
    el.className = 'search-item';
    el.textContent = n.label;
    el.style.borderLeft = `3px solid ${{n.color.background}}`;
    el.style.paddingLeft = '8px';
    el.onclick = () => {{
      network.focus(n.id, {{ scale: 1.5, animation: true }});
      network.selectNodes([n.id]);
      showInfo(n.id);
      searchResults.style.display = 'none';
      searchInput.value = '';
    }};
    searchResults.appendChild(el);
  }});
}});
document.addEventListener('click', e => {{
  if (!searchResults.contains(e.target) && e.target !== searchInput)
    searchResults.style.display = 'none';
}});

// ---------- Filtering (communities + lenses) ----------
// One shared visibility model: legend clicks and lens checkboxes both feed
// isNodeHidden/isEdgeHidden, so every feature (search, blast radius, layer
// bands) operates on the same notion of "currently visible".
const hiddenCommunities = new Set();
const lensOff = {{ relation: new Set(), confidence: new Set(), file_type: new Set() }};

function relationGroup(rel) {{
  const r = String(rel || '').toLowerCase();
  if (r.includes('call')) return 'calls';
  if (r.includes('import')) return 'imports';
  if (r.includes('contain') || r.includes('method')) return 'contains/method';
  return 'other';
}}
function confidenceGroup(c) {{
  return (c === 'EXTRACTED' || c === 'INFERRED' || c === 'AMBIGUOUS') ? c : 'other';
}}
function fileTypeGroup(ft) {{
  return (ft === 'code' || ft === 'document' || ft === 'observation' || ft === 'rationale') ? ft : 'other';
}}
function isNodeHidden(n) {{
  return hiddenCommunities.has(n.community) || lensOff.file_type.has(fileTypeGroup(n.file_type));
}}
function isEdgeHidden(e) {{
  if (lensOff.relation.has(relationGroup(e.relation))) return true;
  if (lensOff.confidence.has(confidenceGroup(e.confidence))) return true;
  const a = nodeById.get(e.from), b = nodeById.get(e.to);
  return (a && isNodeHidden(a)) || (b && isNodeHidden(b));
}}
function applyFilters() {{
  nodesDS.update(RAW_NODES.map(n => ({{ id: n.id, hidden: isNodeHidden(n) }})));
  edgesDS.update(RAW_EDGES.map((e, i) => ({{ id: i, hidden: isEdgeHidden(e) }})));
}}

const legendEl = document.getElementById('legend');
LEGEND.forEach(c => {{
  const item = document.createElement('div');
  item.className = 'legend-item';
  item.innerHTML = `<div class="legend-dot" style="background:${{c.color}}"></div>
    <span class="legend-label">${{c.label}}</span>
    <span class="legend-count">${{c.count}}</span>`;
  item.onclick = () => {{
    if (hiddenCommunities.has(c.cid)) {{
      hiddenCommunities.delete(c.cid);
      item.classList.remove('dimmed');
    }} else {{
      hiddenCommunities.add(c.cid);
      item.classList.add('dimmed');
    }}
    if (blastActive) resetBlast();
    applyFilters();
  }};
  legendEl.appendChild(item);
}});

// Lens checkboxes (all checked by default; unchecking filters out)
document.querySelectorAll('.lens-cb').forEach(cb => {{
  cb.addEventListener('change', () => {{
    const set = lensOff[cb.dataset.kind];
    if (!set) return;
    if (cb.checked) set.delete(cb.dataset.value); else set.add(cb.dataset.value);
    if (blastActive) resetBlast();
    applyFilters();
  }});
}});

// ---------- Style refresh (composes layer mode + blast radius) ----------
let layerMode = false;
let blastArmed = false, blastActive = false, blastRoot = null, blastDepth = new Map();
let blastClickGuard = false;

function refreshEdgeStyles() {{
  edgesDS.update(RAW_EDGES.map((e, i) => {{
    let color = e.color, width = e.width;
    if (layerMode && e.violation) {{
      color = {{ color: '#E15759', opacity: 0.9 }};
      width = 3;
    }}
    if (blastActive && !(blastDepth.has(e.src) && blastDepth.has(e.tgt))) {{
      color = Object.assign({{}}, (typeof color === 'object' ? color : {{}}), {{ opacity: 0.05 }});
    }}
    return {{ id: i, color: color, width: width }};
  }}));
}}
function refreshNodeStyles() {{
  nodesDS.update(RAW_NODES.map(n => {{
    const st = {{ id: n.id, color: n.color, size: n.size, borderWidth: 1.5, opacity: 1 }};
    if (blastActive) {{
      const d = blastDepth.get(n.id);
      if (n.id === blastRoot) {{
        st.color = {{ background: n.color.background, border: '#ffffff', highlight: n.color.highlight }};
        st.borderWidth = 3;
      }} else if (d !== undefined) {{
        st.opacity = Math.max(0.45, 1 - 0.12 * d);
        st.size = n.size * Math.max(1.0, 1.35 - 0.08 * d);
      }} else {{
        st.opacity = 0.15;
      }}
    }}
    return st;
  }}));
}}

// ---------- Layered architecture view ----------
// Band order comes from paragraph/layers.py ALL_LAYERS: view (top) -> other (bottom)
const LAYER_BANDS = {layers_json};
const layersToggle = document.getElementById('layers-toggle');
let bandMeta = null, bandLabelX = 0;

if (layersToggle) {{
  const nv = RAW_EDGES.filter(e => e.violation).length;
  const vcEl = document.getElementById('violation-count');
  vcEl.textContent = nv ? nv + ' layering violation' + (nv === 1 ? '' : 's') : 'no layering violations';
  vcEl.style.color = nv ? '#E15759' : '#888';
  layersToggle.addEventListener('change', () => {{
    if (layersToggle.checked) enterLayerMode(); else exitLayerMode();
  }});
}}

function enterLayerMode() {{
  layerMode = true;
  network.setOptions({{ physics: {{ enabled: false }} }});
  const bandH = 260, spacing = 110;
  const byLayer = new Map(LAYER_BANDS.map(l => [l, []]));
  RAW_NODES.forEach(n => {{
    byLayer.get(byLayer.has(n.layer) ? n.layer : 'other').push(n);
  }});
  const present = LAYER_BANDS.filter(l => byLayer.get(l).length > 0);
  const updates = [];
  bandMeta = [];
  let minX = 0;
  present.forEach((layer, bi) => {{
    // Deterministic x spread: group by community, then stable id order
    const nodes = byLayer.get(layer).slice().sort((a, b) =>
      (a.community - b.community) || String(a.id).localeCompare(String(b.id)));
    const y = bi * bandH;
    bandMeta.push({{ label: layer, y: y }});
    nodes.forEach((n, i) => {{
      const x = (i - (nodes.length - 1) / 2) * spacing;
      if (x < minX) minX = x;
      updates.push({{ id: n.id, x: x, y: y, fixed: {{ x: true, y: true }} }});
    }});
  }});
  bandLabelX = minX - 180;
  nodesDS.update(updates);
  refreshEdgeStyles();
  network.fit({{ animation: true }});
}}
function exitLayerMode() {{
  layerMode = false;
  bandMeta = null;
  nodesDS.update(RAW_NODES.map(n => ({{ id: n.id, fixed: false }})));
  refreshEdgeStyles();
  network.setOptions({{ physics: {{ enabled: true }} }});
  network.once('stabilized', () => network.setOptions({{ physics: {{ enabled: false }} }}));
}}
// Subtle band labels at the left edge of each layer band
network.on('afterDrawing', ctx => {{
  if (!layerMode || !bandMeta) return;
  ctx.save();
  ctx.font = 'bold 12px sans-serif';
  ctx.fillStyle = 'rgba(224, 224, 224, 0.4)';
  ctx.textAlign = 'left';
  bandMeta.forEach(b => ctx.fillText(b.label.toUpperCase(), bandLabelX, b.y + 4));
  ctx.restore();
}});

// ---------- Blast radius (transitive dependents via reverse BFS) ----------
const blastBtn = document.getElementById('blast-btn');
blastBtn.addEventListener('click', () => {{
  if (blastArmed) {{
    blastArmed = false;
    blastBtn.classList.remove('armed');
    blastBtn.textContent = 'Blast radius';
    return;
  }}
  if (blastActive) resetBlast();
  blastArmed = true;
  blastBtn.classList.add('armed');
  blastBtn.textContent = 'Blast radius: click a node';
}});

function runBlast(rootId) {{
  blastArmed = false;
  blastBtn.classList.remove('armed');
  blastBtn.textContent = 'Blast radius';
  const root = nodeById.get(rootId);
  if (!root || isNodeHidden(root)) return;
  // Reverse adjacency over currently visible edges: tgt -> [src, ...]
  const rev = new Map();
  RAW_EDGES.forEach(e => {{
    if (isEdgeHidden(e)) return;
    if (!rev.has(e.tgt)) rev.set(e.tgt, []);
    rev.get(e.tgt).push(e.src);
  }});
  blastDepth = new Map([[rootId, 0]]);
  let frontier = [rootId], depth = 0, maxDepth = 0;
  while (frontier.length) {{
    depth += 1;
    const next = [];
    frontier.forEach(t => (rev.get(t) || []).forEach(s => {{
      const sn = nodeById.get(s);
      if (!blastDepth.has(s) && sn && !isNodeHidden(sn)) {{
        blastDepth.set(s, depth);
        next.push(s);
        maxDepth = depth;
      }}
    }}));
    frontier = next;
  }}
  blastRoot = rootId;
  blastActive = true;
  blastClickGuard = true;
  setTimeout(() => {{ blastClickGuard = false; }}, 0);
  refreshNodeStyles();
  refreshEdgeStyles();
  const count = blastDepth.size - 1;
  document.getElementById('info-content').innerHTML =
    `<div class="field"><b>Blast radius</b></div>
     <div class="field">${{count}} node${{count === 1 ? '' : 's'}} depend on ${{esc(root.label)}} (max depth ${{maxDepth}})</div>
     <div class="field" style="color:#888;font-size:11px">Esc or click empty space to reset</div>`;
}}
function resetBlast() {{
  const was = blastActive || blastArmed;
  blastArmed = false; blastActive = false; blastRoot = null; blastDepth = new Map();
  blastBtn.classList.remove('armed');
  blastBtn.textContent = 'Blast radius';
  if (was) {{
    refreshNodeStyles();
    refreshEdgeStyles();
  }}
}}
document.addEventListener('keydown', e => {{
  if (e.key === 'Escape') resetBlast();
}});
</script>"""


_CONFIDENCE_SCORE_DEFAULTS = {"EXTRACTED": 1.0, "INFERRED": 0.5, "AMBIGUOUS": 0.2}

def to_json(G: nx.Graph, communities: dict[int, list[str]], output_path: str, *,
            community_labels: dict[int, str] | None = None, force: bool = False) -> None:
    # Safety check: refuse to silently shrink an existing graph (#479)
    existing_path = Path(output_path)
    if not force and existing_path.exists():
        try:
            existing_data = json.loads(existing_path.read_text(encoding="utf-8"))
            existing_n = len(existing_data.get("nodes", []))
            new_n = G.number_of_nodes()
            if new_n < existing_n:
                import sys as _sys
                print(
                    f"[paragraph] WARNING: new graph has {new_n} nodes but existing "
                    f"graph.json has {existing_n}. Refusing to overwrite — you may be "
                    f"missing chunk files from a previous session. "
                    f"Pass force=True to override.",
                    file=_sys.stderr,
                )
                return
        except Exception:
            pass  # unreadable existing file — proceed with write

    node_community = _node_community_map(communities)
    try:
        data = json_graph.node_link_data(G, edges="links")
    except TypeError:
        data = json_graph.node_link_data(G)
    for node in data["nodes"]:
        node["community"] = node_community.get(node["id"])
        node["norm_label"] = _strip_diacritics(node.get("label", "")).lower()
    for link in data["links"]:
        if "confidence_score" not in link:
            conf = link.get("confidence", "EXTRACTED")
            link["confidence_score"] = _CONFIDENCE_SCORE_DEFAULTS.get(conf, 1.0)
    data["hyperedges"] = getattr(G, "graph", {}).get("hyperedges", [])
    if community_labels:
        # graph.json is the canonical store for community labels — semantic
        # (Claude-written) labels passed here survive the skill's temp-file
        # cleanup and are recovered by LLM-free rebuilds (watch, cluster-only).
        data.setdefault("graph", {})["community_labels"] = {
            str(cid): label for cid, label in community_labels.items()
        }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def _cypher_escape(s: str) -> str:
    """Escape a string for safe embedding in a Cypher single-quoted literal."""
    return s.replace("\\", "\\\\").replace("'", "\\'")


def to_cypher(G: nx.Graph, output_path: str) -> None:
    lines = ["// Neo4j Cypher import - generated by /paragraph", ""]
    for node_id, data in G.nodes(data=True):
        label = _cypher_escape(data.get("label", node_id))
        node_id_esc = _cypher_escape(node_id)
        _ft = re.sub(r"[^A-Za-z0-9_]", "", data.get("file_type", "unknown").capitalize())
        ftype = (_ft if _ft and _ft[0].isalpha() else "Entity")
        lines.append(f"MERGE (n:{ftype} {{id: '{node_id_esc}', label: '{label}'}});")
    lines.append("")
    for u, v, data in G.edges(data=True):
        rel = re.sub(r"[^A-Za-z0-9_]", "_", data.get("relation", "RELATES_TO").upper())
        conf = _cypher_escape(data.get("confidence", "EXTRACTED"))
        u_esc = _cypher_escape(u)
        v_esc = _cypher_escape(v)
        lines.append(
            f"MATCH (a {{id: '{u_esc}'}}), (b {{id: '{v_esc}'}}) "
            f"MERGE (a)-[:{rel} {{confidence: '{conf}'}}]->(b);"
        )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def to_html(
    G: nx.Graph,
    communities: dict[int, list[str]],
    output_path: str,
    community_labels: dict[int, str] | None = None,
    member_counts: dict[int, int] | None = None,
    back_href: str | None = None,
) -> None:
    """Generate an interactive vis.js HTML visualization of the graph.

    Features: node size by degree, click-to-inspect panel, search box,
    community filter, physics clustering by community, confidence-styled edges.
    Raises ValueError if graph exceeds MAX_NODES_FOR_VIZ.

    If member_counts is provided (aggregated community view), node sizes are
    based on community member counts rather than graph degree.
    """
    if G.number_of_nodes() > MAX_NODES_FOR_VIZ:
        raise ValueError(
            f"Graph has {G.number_of_nodes()} nodes - too large for HTML viz. "
            f"Use --no-viz or reduce input size."
        )

    node_community = _node_community_map(communities)
    degree = dict(G.degree())
    max_deg = max(degree.values(), default=1) or 1
    max_mc = (max(member_counts.values(), default=1) or 1) if member_counts else 1

    # Architectural layer per node (shared classifier — see paragraph/layers.py).
    # Aggregated meta-graphs lack source_file, so every node lands in "other"
    # and the Layers toggle is suppressed below (needs >1 distinct layer).
    node_layer = {
        node_id: classify_layer({
            "label": data.get("label", node_id),
            "source_file": data.get("source_file"),
            "file_type": data.get("file_type"),
        })
        for node_id, data in G.nodes(data=True)
    }

    # Build nodes list for vis.js
    vis_nodes = []
    for node_id, data in G.nodes(data=True):
        cid = node_community.get(node_id, 0)
        color = COMMUNITY_COLORS[cid % len(COMMUNITY_COLORS)]
        label = sanitize_label(data.get("label", node_id))
        deg = degree.get(node_id, 1)
        if member_counts:
            mc = member_counts.get(cid, 1)
            size = 10 + 30 * (mc / max_mc)
            font_size = 12
        else:
            size = 10 + 30 * (deg / max_deg)
            # Only show label for high-degree nodes by default; others show on hover
            font_size = 12 if deg >= max_deg * 0.15 else 0
        vis_node = {
            "id": node_id,
            "label": label,
            "color": {"background": color, "border": color, "highlight": {"background": "#ffffff", "border": color}},
            "size": round(size, 1),
            "font": {"size": font_size, "color": "#ffffff"},
            "title": _html.escape(label),
            "community": cid,
            "community_name": sanitize_label((community_labels or {}).get(cid, f"Community {cid}")),
            "source_file": sanitize_label(str(data.get("source_file") or "")),
            "file_type": data.get("file_type", ""),
            "degree": deg,
            "layer": node_layer.get(node_id, "other"),
        }
        if data.get("href"):
            vis_node["href"] = str(data["href"])
        vis_nodes.append(vis_node)

    # Build edges list
    vis_edges = []
    for u, v, data in G.edges(data=True):
        confidence = data.get("confidence", "EXTRACTED")
        relation = data.get("relation", "")
        # Preserved direction (_src/_tgt) — fall back to storage order (u, v)
        src = data.get("_src", u)
        tgt = data.get("_tgt", v)
        if src not in node_layer or tgt not in node_layer:
            src, tgt = u, v
        violation = is_layer_violation(
            node_layer.get(src, "other"), node_layer.get(tgt, "other"))
        vis_edges.append({
            "from": u,
            "to": v,
            "label": relation,
            "title": _html.escape(f"{relation} [{confidence}]"),
            "dashes": confidence != "EXTRACTED",
            "width": 2 if confidence == "EXTRACTED" else 1,
            "color": {"opacity": 0.7 if confidence == "EXTRACTED" else 0.35},
            "relation": relation,
            "confidence": confidence,
            "src": src,
            "tgt": tgt,
            "violation": violation,
        })

    # Build community legend data
    legend_data = []
    for cid in sorted((community_labels or {}).keys()):
        color = COMMUNITY_COLORS[cid % len(COMMUNITY_COLORS)]
        lbl = _html.escape(sanitize_label((community_labels or {}).get(cid, f"Community {cid}")))
        n = member_counts.get(cid, len(communities.get(cid, []))) if member_counts else len(communities.get(cid, []))
        legend_data.append({"cid": cid, "color": color, "label": lbl, "count": n})

    # Escape </script> sequences so embedded JSON cannot break out of the script tag
    def _js_safe(obj) -> str:
        return json.dumps(obj).replace("</", "<\\/")

    nodes_json = _js_safe(vis_nodes)
    edges_json = _js_safe(vis_edges)
    legend_json = _js_safe(legend_data)
    layers_json = _js_safe(ALL_LAYERS)
    hyperedges_json = _js_safe(getattr(G, "graph", {}).get("hyperedges", []))

    # Layers toggle only makes sense when the graph spans >1 layer (aggregated
    # meta-graph nodes all classify as "other", so overview pages skip it).
    show_layers = len({n["layer"] for n in vis_nodes}) > 1
    layers_section = ("""
  <div class="ctl-section" id="layers-section">
    <h3>Layers</h3>
    <label class="ctl-row"><input type="checkbox" id="layers-toggle"> Layered view</label>
    <div id="violation-count"></div>
  </div>""" if show_layers else "")

    def _lens_cb(kind: str, value: str, text: str) -> str:
        return (f'<label class="ctl-row"><input type="checkbox" class="lens-cb" checked '
                f'data-kind="{kind}" data-value="{_html.escape(value)}"> {_html.escape(text)}</label>')

    lenses_section = (
        '\n  <div class="ctl-section" id="lenses-section">\n    <h3>Lenses</h3>'
        '\n    <div class="lens-title">Relations</div>\n    '
        + "".join(_lens_cb("relation", v, v) for v in
                  ("calls", "imports", "contains/method", "other"))
        + '\n    <div class="lens-title">Confidence</div>\n    '
        + "".join(_lens_cb("confidence", v, v) for v in
                  ("EXTRACTED", "INFERRED", "AMBIGUOUS", "other"))
        + '\n    <div class="lens-title">Node types</div>\n    '
        + "".join(_lens_cb("file_type", v, v) for v in
                  ("code", "document", "observation", "rationale", "other"))
        + '\n  </div>'
    )
    blast_section = (
        '\n  <div class="ctl-section" id="blast-section">'
        '\n    <button id="blast-btn" type="button">Blast radius</button>'
        '\n  </div>'
    )
    controls_html = f'<div id="controls">{layers_section}{lenses_section}{blast_section}\n</div>'
    title = _html.escape(sanitize_label(str(output_path)))
    stats = f"{G.number_of_nodes()} nodes &middot; {G.number_of_edges()} edges &middot; {len(communities)} communities"

    back_link = (

        f'<a href="{_html.escape(back_href)}" style="position:absolute;top:10px;left:10px;z-index:10;color:#8ab4f8;font-size:13px;text-decoration:none;background:#1a1a2e;padding:6px 10px;border-radius:6px;border:1px solid #2a2a4e">&#8592; Overview</a>\n'

        if back_href else "")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>paragraph - {title}</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
{_html_styles()}
</head>
<body>
{back_link}<div id="graph"></div>
<div id="sidebar">
  <div id="search-wrap">
    <input id="search" type="text" placeholder="Search nodes..." autocomplete="off">
    <div id="search-results"></div>
  </div>
  <div id="info-panel">
    <h3>Node Info</h3>
    <div id="info-content"><span class="empty">Click a node to inspect it</span></div>
  </div>
  {controls_html}
  <div id="legend-wrap">
    <h3>Communities</h3>
    <div id="legend"></div>
  </div>
  <div id="stats">{stats}</div>
</div>
{_html_script(nodes_json, edges_json, legend_json, layers_json)}
{_hyperedge_script(hyperedges_json)}
</body>
</html>"""

    Path(output_path).write_text(html, encoding="utf-8")


# Keep backward-compatible alias - skill.md calls generate_html
generate_html = to_html


def _clear_drilldown_pages(out_dir: Path) -> None:
    """Remove community drill-down pages left by a previous aggregated build."""
    pages_dir = Path(out_dir) / "graph_communities"
    if not pages_dir.is_dir():
        return
    for page in pages_dir.glob("community_*.html"):
        page.unlink()
    try:
        pages_dir.rmdir()  # only removes if now empty
    except OSError:
        pass


def build_meta_graph(G: nx.Graph, communities: dict[int, list[str]],
                     community_labels: dict[int, str] | None = None) -> nx.Graph:
    """Collapse G to one node per community, edges weighted by cross-community edge counts."""
    labels = community_labels or {}
    node_to_community = {nid: cid for cid, members in communities.items() for nid in members}
    meta = nx.Graph()
    for cid in communities:
        meta.add_node(str(cid), label=labels.get(cid, f"Community {cid}"))
    edge_counts: Counter = Counter()
    for u, v in G.edges():
        cu, cv = node_to_community.get(u), node_to_community.get(v)
        if cu is not None and cv is not None and cu != cv:
            edge_counts[(min(cu, cv), max(cu, cv))] += 1
    for (cu, cv), w in edge_counts.items():
        meta.add_edge(str(cu), str(cv), weight=w,
                      relation=f"{w} cross-community edges", confidence="AGGREGATED")
    return meta


def to_html_auto(
    G: nx.Graph,
    communities: dict[int, list[str]],
    output_path: str,
    community_labels: dict[int, str] | None = None,
) -> str:
    """Write an HTML viz, aggregating to a community-level view when the graph
    exceeds MAX_NODES_FOR_VIZ instead of raising like to_html.

    Returns "full", "aggregated", or "skipped" (a single community would
    aggregate to one dot; more communities than MAX_NODES_FOR_VIZ cannot be
    drawn either). A skip removes any stale file at output_path so downstream
    tooling never serves an outdated viz.
    """
    if G.number_of_nodes() <= MAX_NODES_FOR_VIZ:
        to_html(G, communities, output_path, community_labels=community_labels)
        # The full viz owns the page; drill-down pages from a previous
        # aggregated build would go stale and mislead.
        _clear_drilldown_pages(Path(output_path).parent)
        return "full"
    # Communities whose members are all disconnected (orphan content) render
    # as one collapsed meta-node, not a ring of identical dots.
    degree = dict(G.degree())
    connected = {cid: m for cid, m in communities.items()
                 if any(degree.get(n, 0) > 0 for n in m)}
    unconnected_count = sum(len(m) for cid, m in communities.items()
                            if cid not in connected)
    if not connected:
        connected = communities
        unconnected_count = 0

    meta = build_meta_graph(G, connected, community_labels)
    if 1 < meta.number_of_nodes() + (1 if unconnected_count else 0) <= MAX_NODES_FOR_VIZ:
        # Drill-down pages: one full-featured viz per community, linked from
        # the overview (double-click a community node to open it).
        out_file = Path(output_path)
        pages_dir = out_file.parent / "graph_communities"
        pages_dir.mkdir(parents=True, exist_ok=True)
        for stale in pages_dir.glob("community_*.html"):
            stale.unlink()
        labels = dict(community_labels or {})
        pages = 0
        for cid, members in connected.items():
            if not 1 < len(members) <= MAX_NODES_FOR_VIZ:
                continue
            page = pages_dir / f"community_{cid}.html"
            to_html(G.subgraph(members).copy(), {cid: list(members)}, str(page),
                    community_labels={cid: labels.get(cid, f"Community {cid}")},
                    back_href=f"../{out_file.name}")
            meta.nodes[str(cid)]["href"] = f"graph_communities/{page.name}"
            pages += 1

        meta_communities = {cid: [str(cid)] for cid in connected}
        member_counts = {cid: len(members) for cid, members in connected.items()}
        if unconnected_count:
            # Synthetic cid chosen so cid % len(palette) lands on the grey swatch
            top = max(connected, default=0)
            syn_cid = ((top // len(COMMUNITY_COLORS)) + 1) * len(COMMUNITY_COLORS) - 1
            syn_label = f"Unconnected content ({unconnected_count} nodes)"
            meta.add_node("__unconnected__", label=syn_label)
            meta_communities[syn_cid] = ["__unconnected__"]
            member_counts[syn_cid] = unconnected_count
            labels[syn_cid] = syn_label
        to_html(meta, meta_communities, output_path,
                community_labels=labels or None, member_counts=member_counts)
        if pages:
            print(f"[paragraph] {pages} community drill-down pages in {pages_dir}/ "
                  "(double-click a community in the overview)")
        return "aggregated"
    stale = Path(output_path)
    if stale.exists():
        stale.unlink()
    _clear_drilldown_pages(stale.parent)
    return "skipped"

def push_to_neo4j(
    G: nx.Graph,
    uri: str,
    user: str,
    password: str,
    communities: dict[int, list[str]] | None = None,
) -> dict[str, int]:
    """Push graph directly to a running Neo4j instance via the Python driver.

    Requires: pip install neo4j

    Uses MERGE so re-running is safe - nodes and edges are upserted, not duplicated.
    Returns a dict with counts of nodes and edges pushed.
    """
    try:
        from neo4j import GraphDatabase
    except ImportError as e:
        raise ImportError(
            "neo4j driver not installed. Run: pip install neo4j"
        ) from e

    node_community = _node_community_map(communities) if communities else {}

    def _safe_rel(relation: str) -> str:
        return re.sub(r"[^A-Z0-9_]", "_", relation.upper().replace(" ", "_").replace("-", "_")) or "RELATED_TO"

    def _safe_label(label: str) -> str:
        """Sanitize a Neo4j node label to prevent Cypher injection."""
        sanitized = re.sub(r"[^A-Za-z0-9_]", "", label)
        return sanitized if sanitized else "Entity"

    driver = GraphDatabase.driver(uri, auth=(user, password))
    nodes_pushed = 0
    edges_pushed = 0

    with driver.session() as session:
        for node_id, data in G.nodes(data=True):
            props = {k: v for k, v in data.items() if isinstance(v, (str, int, float, bool))}
            props["id"] = node_id
            cid = node_community.get(node_id)
            if cid is not None:
                props["community"] = cid
            ftype = _safe_label(data.get("file_type", "Entity").capitalize())
            session.run(
                f"MERGE (n:{ftype} {{id: $id}}) SET n += $props",
                id=node_id,
                props=props,
            )
            nodes_pushed += 1

        for u, v, data in G.edges(data=True):
            rel = _safe_rel(data.get("relation", "RELATED_TO"))
            props = {k: v for k, v in data.items() if isinstance(v, (str, int, float, bool))}
            session.run(
                f"MATCH (a {{id: $src}}), (b {{id: $tgt}}) "
                f"MERGE (a)-[r:{rel}]->(b) SET r += $props",
                src=u,
                tgt=v,
                props=props,
            )
            edges_pushed += 1

    driver.close()
    return {"nodes": nodes_pushed, "edges": edges_pushed}


def to_graphml(
    G: nx.Graph,
    communities: dict[int, list[str]],
    output_path: str,
) -> None:
    """Export graph as GraphML - opens in Gephi, yEd, and any GraphML-compatible tool.

    Community IDs are written as a node attribute so Gephi can colour by community.
    Edge confidence (EXTRACTED/INFERRED/AMBIGUOUS) is preserved as an edge attribute.

    GraphML only supports scalar attribute values (str/int/float/bool), so
    non-scalar graph/node/edge attributes (dicts, lists — e.g. hyperedges,
    community_labels) are JSON-stringified rather than dropped: the data
    survives the round-trip and consumers can json.loads() it back. None
    values are dropped (GraphML has no null representation).
    """
    import json as _json

    def _sanitize(attrs: dict) -> None:
        for key in list(attrs.keys()):
            value = attrs[key]
            if value is None:
                del attrs[key]
            elif not isinstance(value, (str, int, float, bool)):
                attrs[key] = _json.dumps(value, default=str)

    H = G.copy()
    node_community = _node_community_map(communities)
    for node_id in H.nodes():
        H.nodes[node_id]["community"] = node_community.get(node_id, -1)

    _sanitize(H.graph)
    for _, node_attrs in H.nodes(data=True):
        _sanitize(node_attrs)
    for _, _, edge_attrs in H.edges(data=True):
        _sanitize(edge_attrs)
    nx.write_graphml(H, output_path)

