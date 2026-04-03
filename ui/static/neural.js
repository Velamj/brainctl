/* ===== Neural Map — Knowledge Web Visualization ===== */

let neuralInitialized = false;
let canvas, ctx;
let graphData = { nodes: [], edges: [] };
let simNodes = [];
let simEdges = [];
let particles = [];
let camera = { x: 0, y: 0, zoom: 1 };
let neuralFilters = { scope: 'overview', showEvents: true, showDecisions: true, showMemories: true };
let hoveredNode = null;
let selectedNode = null;
let isDragging = false;
let dragStart = { x: 0, y: 0 };
let camStart = { x: 0, y: 0 };
let animFrame;

const NODE_COLORS = {
  person: '#4fc3f7',
  project: '#81c784',
  tool: '#ffb74d',
  concept: '#ce93d8',
  agent: '#ef5350',
  organization: '#4dd0e1',
  decision: '#ffd166',
  event: '#7bdff2',
  memory: '#9bdeac',
  unknown: '#888'
};

const NODE_EMOJIS = {
  person: 'P',
  project: 'J',
  tool: 'T',
  concept: 'C',
  agent: 'A',
  organization: 'O',
  decision: 'D',
  event: 'E',
  memory: 'M',
  unknown: '?'
};

function initNeural() {
  if (neuralInitialized) return;
  neuralInitialized = true;

  canvas = document.getElementById('neuralCanvas');
  ctx = canvas.getContext('2d');
  resizeCanvas();
  window.addEventListener('resize', resizeCanvas);

  document.getElementById('neuralZoomIn').addEventListener('click', () => {
    camera.zoom = Math.min(camera.zoom * 1.3, 5);
  });
  document.getElementById('neuralZoomOut').addEventListener('click', () => {
    camera.zoom = Math.max(camera.zoom / 1.3, 0.2);
  });
  document.getElementById('neuralReset').addEventListener('click', () => {
    camera = { x: 0, y: 0, zoom: 1 };
    selectedNode = null;
    hideDetail();
  });

  document.querySelectorAll('.neural-filter-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      neuralFilters.scope = btn.dataset.filter;
      document.querySelectorAll('.neural-filter-btn').forEach((el) => el.classList.toggle('active', el === btn));
      buildSimulation();
    });
  });

  bindToggle('neuralEventsToggle', 'showEvents');
  bindToggle('neuralDecisionsToggle', 'showDecisions');
  bindToggle('neuralMemoriesToggle', 'showMemories');

  canvas.addEventListener('mousemove', onMouseMove);
  canvas.addEventListener('mousedown', onMouseDown);
  canvas.addEventListener('mouseup', onMouseUp);
  canvas.addEventListener('mouseleave', onMouseUp);
  canvas.addEventListener('wheel', onWheel, { passive: false });
  canvas.addEventListener('click', onClick);

  for (let i = 0; i < 60; i++) {
    particles.push({
      x: (Math.random() - 0.5) * 2200,
      y: (Math.random() - 0.5) * 2200,
      vx: (Math.random() - 0.5) * 0.25,
      vy: (Math.random() - 0.5) * 0.25,
      size: Math.random() * 2 + 0.5,
      alpha: Math.random() * 0.22 + 0.04
    });
  }

  loadGraph();
  animate();
}

function bindToggle(id, key) {
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener('change', (event) => {
    neuralFilters[key] = event.target.checked;
    buildSimulation();
  });
}

function resizeCanvas() {
  if (!canvas) return;
  canvas.width = canvas.parentElement.clientWidth;
  canvas.height = canvas.parentElement.clientHeight;
}

async function loadGraph() {
  try {
    const res = await fetch('/api/graph');
    graphData = await res.json();
    buildSimulation();
  } catch (err) {
    console.error('Graph load error:', err);
  }
}

function isNarrativeNode(node) {
  return node.kind === 'event' || node.kind === 'decision' || node.kind === 'memory';
}

function isCoreAgent(node) {
  return node.type === 'agent' && node.attention_class === 'exec';
}

function nodeRadius(node) {
  if (node.kind === 'decision') return 18;
  if (node.kind === 'event') return 13;
  if (node.kind === 'memory') return 10;
  if (node.type === 'person') return 26;
  if (node.type === 'organization') return 24;
  if (node.type === 'agent') return 22;
  return 20;
}

function filterByToggles(node) {
  if (node.kind === 'event') return neuralFilters.showEvents;
  if (node.kind === 'decision') return neuralFilters.showDecisions;
  if (node.kind === 'memory') return neuralFilters.showMemories;
  return true;
}

function getFilteredGraphData() {
  const allNodes = (graphData.nodes || []).filter(filterByToggles);
  const allEdges = graphData.edges || [];
  const byId = new Map(allNodes.map((node) => [node.id, node]));

  if (neuralFilters.scope === 'all') {
    const visibleIds = new Set(allNodes.map((node) => node.id));
    return {
      nodes: allNodes,
      edges: allEdges.filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target))
    };
  }

  const coreNodes = allNodes.filter((node) => !isNarrativeNode(node));
  const visibleIds = new Set(coreNodes.map((node) => node.id));
  const candidateEdges = allEdges.filter((edge) => byId.has(edge.source) && byId.has(edge.target));
  const narrativeNodes = allNodes.filter((node) => isNarrativeNode(node));

  if (neuralFilters.scope === 'thinking') {
    narrativeNodes.forEach((node) => visibleIds.add(node.id));
  } else {
    const eventBudget = 14;
    const decisionBudget = 10;
    const memoryBudget = 16;
    let usedEvents = 0;
    let usedDecisions = 0;
    let usedMemories = 0;

    candidateEdges.forEach((edge) => {
      const source = byId.get(edge.source);
      const target = byId.get(edge.target);
      const narrative = source && isNarrativeNode(source) ? source : target && isNarrativeNode(target) ? target : null;
      const anchor = source && !isNarrativeNode(source) ? source : target && !isNarrativeNode(target) ? target : null;
      if (!narrative || !anchor) return;
      if (!(isCoreAgent(anchor) || anchor.type === 'person' || anchor.type === 'organization')) return;

      if (narrative.kind === 'event' && usedEvents >= eventBudget) return;
      if (narrative.kind === 'decision' && usedDecisions >= decisionBudget) return;
      if (narrative.kind === 'memory' && usedMemories >= memoryBudget) return;

      visibleIds.add(narrative.id);
      if (narrative.kind === 'event') usedEvents += 1;
      if (narrative.kind === 'decision') usedDecisions += 1;
      if (narrative.kind === 'memory') usedMemories += 1;
    });
  }

  const nodes = allNodes.filter((node) => visibleIds.has(node.id));
  const nodeIds = new Set(nodes.map((node) => node.id));
  const edges = candidateEdges.filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target));
  return { nodes, edges };
}

function buildSimulation() {
  const filtered = getFilteredGraphData();
  simNodes = [];
  simEdges = [];
  hoveredNode = null;

  if (selectedNode && !filtered.nodes.some((node) => node.id === selectedNode.id)) {
    selectedNode = null;
    hideDetail();
  }

  const nodeMap = {};
  const coreNodes = filtered.nodes.filter((node) => !isNarrativeNode(node));
  const webNodes = filtered.nodes.filter((node) => isNarrativeNode(node));

  const coreRadius = Math.max(190, 36 * coreNodes.length / Math.PI);
  coreNodes.forEach((node, index) => {
    const angle = (2 * Math.PI * index) / Math.max(coreNodes.length, 1);
    const simNode = {
      ...node,
      x: coreRadius * Math.cos(angle),
      y: coreRadius * Math.sin(angle),
      vx: 0,
      vy: 0,
      radius: nodeRadius(node)
    };
    simNodes.push(simNode);
    nodeMap[node.id] = simNode;
  });

  webNodes.forEach((node, index) => {
    const attachedEdges = filtered.edges.filter((edge) => edge.source === node.id || edge.target === node.id);
    let anchor = null;
    for (const edge of attachedEdges) {
      const otherId = edge.source === node.id ? edge.target : edge.source;
      if (nodeMap[otherId]) {
        anchor = nodeMap[otherId];
        break;
      }
    }
    const orbit = node.kind === 'decision' ? 130 : node.kind === 'event' ? 170 : 210;
    const angle = (index * 0.9) % (Math.PI * 2);
    const baseX = anchor ? anchor.x : 0;
    const baseY = anchor ? anchor.y : 0;
    const simNode = {
      ...node,
      x: baseX + Math.cos(angle) * orbit,
      y: baseY + Math.sin(angle) * orbit,
      vx: 0,
      vy: 0,
      radius: nodeRadius(node)
    };
    simNodes.push(simNode);
    nodeMap[node.id] = simNode;
  });

  filtered.edges.forEach((edge) => {
    const src = nodeMap[edge.source];
    const tgt = nodeMap[edge.target];
    if (!src || !tgt) return;
    simEdges.push({ ...edge, src, tgt });
  });

  for (let i = 0; i < 140; i++) {
    stepSimulation();
  }
}

function stepSimulation() {
  const attraction = 0.012;
  for (let i = 0; i < simNodes.length; i++) {
    for (let j = i + 1; j < simNodes.length; j++) {
      const a = simNodes[i];
      const b = simNodes[j];
      let dx = b.x - a.x;
      let dy = b.y - a.y;
      let dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const minDist = a.radius + b.radius + (isNarrativeNode(a) || isNarrativeNode(b) ? 18 : 70);
      if (dist < minDist) {
        const force = (minDist - dist) * 0.06;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        a.x -= fx;
        a.y -= fy;
        b.x += fx;
        b.y += fy;
      }
    }
  }

  simEdges.forEach((edge) => {
    const dx = edge.tgt.x - edge.src.x;
    const dy = edge.tgt.y - edge.src.y;
    const dist = Math.sqrt(dx * dx + dy * dy) || 1;
    const ideal = edge.kind === 'entity' ? 220 : edge.kind === 'decision' ? 140 : edge.kind === 'authored_by' ? 145 : 165;
    const force = (dist - ideal) * attraction;
    const fx = (dx / dist) * force;
    const fy = (dy / dist) * force;
    edge.src.x += fx;
    edge.src.y += fy;
    edge.tgt.x -= fx;
    edge.tgt.y -= fy;
  });
}

function animate() {
  animFrame = requestAnimationFrame(animate);
  draw();
}

function draw() {
  if (!ctx || !canvas) return;
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = '#08111b';
  ctx.fillRect(0, 0, w, h);

  ctx.save();
  ctx.translate(w / 2 + camera.x, h / 2 + camera.y);
  ctx.scale(camera.zoom, camera.zoom);

  particles.forEach((particle) => {
    particle.x += particle.vx;
    particle.y += particle.vy;
    if (Math.abs(particle.x) > 1100) particle.vx *= -1;
    if (Math.abs(particle.y) > 1100) particle.vy *= -1;
    ctx.beginPath();
    ctx.arc(particle.x, particle.y, particle.size, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(79,195,247,${particle.alpha})`;
    ctx.fill();
  });

  simEdges.forEach((edge) => drawEdge(edge));
  simNodes.forEach((node) => drawNode(node));

  ctx.restore();
}

function drawEdge(edge) {
  const highlighted = selectedNode && (edge.src === selectedNode || edge.tgt === selectedNode);
  const mx = (edge.src.x + edge.tgt.x) / 2;
  const my = (edge.src.y + edge.tgt.y) / 2;
  const dx = edge.tgt.x - edge.src.x;
  const dy = edge.tgt.y - edge.src.y;
  const cx = mx - dy * 0.12;
  const cy = my + dx * 0.12;

  ctx.beginPath();
  ctx.moveTo(edge.src.x, edge.src.y);
  ctx.quadraticCurveTo(cx, cy, edge.tgt.x, edge.tgt.y);
  ctx.strokeStyle = highlighted ? 'rgba(255,255,255,0.52)' : edge.kind === 'entity' ? 'rgba(255,255,255,0.2)' : 'rgba(123,223,242,0.14)';
  ctx.lineWidth = highlighted ? 2 : edge.kind === 'entity' ? 1.3 : 0.9;
  ctx.stroke();

  if (highlighted && edge.label) {
    ctx.save();
    ctx.font = '11px system-ui';
    ctx.fillStyle = 'rgba(232,232,232,0.7)';
    ctx.textAlign = 'center';
    ctx.fillText(edge.label, cx, cy - 8);
    ctx.restore();
  }
}

function drawNode(node) {
  const color = NODE_COLORS[node.kind] || NODE_COLORS[node.type] || NODE_COLORS.unknown;
  const isSelected = node === selectedNode;
  const isHovered = node === hoveredNode;
  const isConnected = selectedNode && simEdges.some((edge) =>
    (edge.src === selectedNode && edge.tgt === node) || (edge.tgt === selectedNode && edge.src === node)
  );
  const dimmed = selectedNode && !isSelected && !isConnected;
  const alpha = dimmed ? 0.16 : 1;
  const r = node.radius;

  const glow = ctx.createRadialGradient(node.x, node.y, r * 0.2, node.x, node.y, r * (isHovered ? 3 : 2.3));
  glow.addColorStop(0, hexToRgba(color, 0.22 * alpha));
  glow.addColorStop(1, hexToRgba(color, 0));
  ctx.beginPath();
  ctx.arc(node.x, node.y, r * (isHovered ? 3 : 2.3), 0, Math.PI * 2);
  ctx.fillStyle = glow;
  ctx.fill();

  ctx.beginPath();
  ctx.arc(node.x, node.y, r, 0, Math.PI * 2);
  ctx.fillStyle = hexToRgba(node.kind === 'memory' ? '#10221a' : '#0f1b2a', alpha);
  ctx.fill();
  ctx.strokeStyle = hexToRgba(color, (isSelected ? 0.95 : isHovered ? 0.78 : 0.45) * alpha);
  ctx.lineWidth = isSelected ? 2.8 : isHovered ? 2.1 : 1.2;
  ctx.stroke();

  ctx.font = `${Math.max(9, r * 0.62)}px ui-monospace, SFMono-Regular, Menlo, monospace`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = hexToRgba(color, 0.9 * alpha);
  ctx.fillText(NODE_EMOJIS[node.kind] || NODE_EMOJIS[node.type] || '?', node.x, node.y);

  if (node.kind !== 'memory' || isHovered || isSelected) {
    ctx.font = `600 ${node.kind === 'memory' ? 10 : 12}px system-ui`;
    ctx.fillStyle = hexToRgba('#e8e8e8', 0.82 * alpha);
    ctx.textAlign = 'center';
    ctx.fillText(trimLabel(node.label, node.kind === 'memory' ? 24 : 28), node.x, node.y + r + 15);
  }
}

function screenToWorld(sx, sy) {
  const w = canvas.width;
  const h = canvas.height;
  return {
    x: (sx - w / 2 - camera.x) / camera.zoom,
    y: (sy - h / 2 - camera.y) / camera.zoom
  };
}

function getNodeAt(mx, my) {
  const point = screenToWorld(mx, my);
  for (let i = simNodes.length - 1; i >= 0; i--) {
    const node = simNodes[i];
    const dx = point.x - node.x;
    const dy = point.y - node.y;
    const hit = node.radius + 8;
    if (dx * dx + dy * dy < hit * hit) return node;
  }
  return null;
}

function onMouseMove(event) {
  const rect = canvas.getBoundingClientRect();
  const mx = event.clientX - rect.left;
  const my = event.clientY - rect.top;

  if (isDragging) {
    camera.x = camStart.x + (event.clientX - dragStart.x);
    camera.y = camStart.y + (event.clientY - dragStart.y);
    return;
  }

  hoveredNode = getNodeAt(mx, my);
  canvas.style.cursor = hoveredNode ? 'pointer' : 'grab';
  renderTooltip(event.clientX, event.clientY, hoveredNode);
}

function onMouseDown(event) {
  isDragging = true;
  dragStart = { x: event.clientX, y: event.clientY };
  camStart = { x: camera.x, y: camera.y };
  canvas.style.cursor = 'grabbing';
}

function onMouseUp() {
  isDragging = false;
  canvas.style.cursor = hoveredNode ? 'pointer' : 'grab';
}

function onWheel(event) {
  event.preventDefault();
  const factor = event.deltaY > 0 ? 0.9 : 1.1;
  camera.zoom = Math.min(Math.max(camera.zoom * factor, 0.24), 5);
}

function onClick(event) {
  if (Math.abs(event.clientX - dragStart.x) > 5 || Math.abs(event.clientY - dragStart.y) > 5) return;
  const rect = canvas.getBoundingClientRect();
  const node = getNodeAt(event.clientX - rect.left, event.clientY - rect.top);
  if (node) {
    selectedNode = node;
    showDetail(node);
  } else {
    selectedNode = null;
    hideDetail();
  }
}

function renderTooltip(clientX, clientY, node) {
  const tooltip = document.getElementById('neuralTooltip');
  if (!node) {
    tooltip.classList.add('hidden');
    return;
  }

  const color = NODE_COLORS[node.kind] || NODE_COLORS[node.type] || '#888';
  const kind = node.kind || node.type || 'node';
  const meta = [];
  if (node.agent_id) meta.push(node.agent_id);
  if (node.project) meta.push(node.project);
  if (node.category) meta.push(node.category);
  if (node.event_type) meta.push(node.event_type);

  tooltip.innerHTML = `
    <div class="tooltip-type" style="color:${color}">${escHtml(kind)}</div>
    <div class="tooltip-name">${escHtml(node.label)}</div>
    ${meta.length ? `<div class="tooltip-meta">${escHtml(meta.join(' · '))}</div>` : ''}
  `;
  tooltip.classList.remove('hidden');
  tooltip.style.left = `${clientX + 16}px`;
  tooltip.style.top = `${clientY - 20}px`;
}

function showDetail(node) {
  const panel = document.getElementById('neuralDetail');
  const color = NODE_COLORS[node.kind] || NODE_COLORS[node.type] || '#888';
  const kind = node.kind || node.type || 'node';
  const relations = simEdges
    .filter((edge) => edge.src === node || edge.tgt === node)
    .slice(0, 18)
    .map((edge) => {
      const other = edge.src === node ? edge.tgt : edge.src;
      return `<li>${escHtml(edge.label || edge.kind || 'linked to')}: ${escHtml(other.label)}</li>`;
    })
    .join('');

  const facts = [];
  if (node.agent_id) facts.push(`Agent: ${node.agent_id}`);
  if (node.agent_type) facts.push(`Runtime label: ${node.agent_type}`);
  if (node.attention_class) facts.push(`Priority tier: ${node.attention_class}`);
  if (node.project) facts.push(`Project: ${node.project}`);
  if (node.category) facts.push(`Category: ${node.category}`);
  if (node.event_type) facts.push(`Event: ${node.event_type}`);
  if (node.created_at) facts.push(`Created: ${node.created_at}`);
  if (node.reversible !== undefined) facts.push(`Reversible: ${node.reversible ? 'yes' : 'no'}`);

  let detailBody = '';
  if (node.detail) {
    detailBody = `<div class="detail-section-title">Content</div><div class="detail-text">${escHtml(node.detail)}</div>`;
  } else if (node.observations && node.observations.length) {
    detailBody = `<div class="detail-section-title">Observations</div><ul class="detail-obs-list">${node.observations.map((item) => `<li>${escHtml(item)}</li>`).join('')}</ul>`;
  }

  panel.innerHTML = `
    <button class="detail-close" onclick="hideDetail();selectedNode=null">x</button>
    <div class="detail-emoji" style="color:${color}">${escHtml(NODE_EMOJIS[node.kind] || NODE_EMOJIS[node.type] || '?')}</div>
    <div class="detail-name" style="color:${color}">${escHtml(node.label)}</div>
    <div class="detail-type">${escHtml(kind)}</div>
    ${facts.length ? `<ul class="detail-obs-list">${facts.map((item) => `<li>${escHtml(item)}</li>`).join('')}</ul>` : ''}
    ${detailBody}
    ${relations ? `<div class="detail-section-title">Connections</div><ul class="detail-obs-list">${relations}</ul>` : ''}
  `;
  panel.classList.remove('hidden');
}

function hideDetail() {
  document.getElementById('neuralDetail').classList.add('hidden');
}

function trimLabel(label, maxLen) {
  if (!label) return '';
  return label.length > maxLen ? `${label.slice(0, maxLen - 1)}...` : label;
}

function hexToRgba(hex, alpha) {
  if (hex.startsWith('rgba')) return hex;
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

function escHtml(value) {
  if (!value) return '';
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}
