/* ===== Neural Map — 3D Knowledge Graph Visualization ===== */
/* Three.js force-directed graph in 3D space */

let neuralInitialized = false;
let scene, camera3d, renderer, controls;
let graphData = { nodes: [], edges: [] };
let nodeMeshes = new Map(); // id -> mesh
let edgeLines = [];
let simNodes = [];
let simEdges = [];
let raycaster, mouse;
let hoveredNode = null;
let selectedNode = null;
let neuralFilters = { scope: 'overview', showEvents: true, showDecisions: true, showMemories: true };
let animFrame;

const NODE_COLORS = {
  person: 0x4fc3f7,
  project: 0x81c784,
  tool: 0xffb74d,
  concept: 0xce93d8,
  agent: 0xef5350,
  organization: 0x4dd0e1,
  decision: 0xffd166,
  event: 0x7bdff2,
  memory: 0x9bdeac,
  unknown: 0x888888
};

const NODE_SIZES = {
  person: 3.0, organization: 2.8, project: 2.2, tool: 1.8,
  concept: 2.0, agent: 1.0, decision: 1.6, event: 1.2, memory: 1.0
};

// Load Three.js from CDN (r148 — last version with UMD/global THREE support)
function loadThreeJS() {
  return new Promise((resolve, reject) => {
    if (window.THREE && window.THREE.OrbitControls) { resolve(); return; }
    const s1 = document.createElement('script');
    s1.src = 'https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js';
    s1.onload = () => {
      const s2 = document.createElement('script');
      s2.src = 'https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js';
      s2.onload = () => {
        console.log('Three.js + OrbitControls loaded', THREE.REVISION);
        resolve();
      };
      s2.onerror = () => { console.error('Failed to load OrbitControls'); reject(); };
      document.head.appendChild(s2);
    };
    s1.onerror = () => { console.error('Failed to load Three.js'); reject(); };
    document.head.appendChild(s1);
  });
}

async function initNeural() {
  if (neuralInitialized) return;
  neuralInitialized = true;

  await loadThreeJS();
  const container = document.getElementById('neuralView');
  const canvas = document.getElementById('neuralCanvas');

  // Scene
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x060d17);
  scene.fog = new THREE.FogExp2(0x060d17, 0.0008);

  // Camera
  camera3d = new THREE.PerspectiveCamera(60, container.clientWidth / container.clientHeight, 1, 8000);
  camera3d.position.set(0, 0, 600);

  // Renderer
  renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
  renderer.setSize(container.clientWidth, container.clientHeight);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

  // Controls
  controls = new THREE.OrbitControls(camera3d, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.rotateSpeed = 0.6;
  controls.zoomSpeed = 1.2;
  controls.minDistance = 100;
  controls.maxDistance = 3000;

  // Ambient light
  scene.add(new THREE.AmbientLight(0x404060, 0.6));
  const dirLight = new THREE.DirectionalLight(0xffffff, 0.4);
  dirLight.position.set(200, 300, 400);
  scene.add(dirLight);

  // Starfield background
  const starGeo = new THREE.BufferGeometry();
  const starPositions = new Float32Array(3000);
  for (let i = 0; i < 3000; i++) {
    starPositions[i] = (Math.random() - 0.5) * 6000;
  }
  starGeo.setAttribute('position', new THREE.BufferAttribute(starPositions, 3));
  const starMat = new THREE.PointsMaterial({ color: 0x4fc3f7, size: 1.2, transparent: true, opacity: 0.3 });
  scene.add(new THREE.Points(starGeo, starMat));

  // Raycaster for mouse interaction
  raycaster = new THREE.Raycaster();
  mouse = new THREE.Vector2();
  raycaster.params.Points = { threshold: 5 };

  // Events
  renderer.domElement.addEventListener('mousemove', onMouseMove3D);
  renderer.domElement.addEventListener('click', onClick3D);
  window.addEventListener('resize', onResize3D);

  // Filter buttons
  document.querySelectorAll('.neural-filter-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      neuralFilters.scope = btn.dataset.filter;
      document.querySelectorAll('.neural-filter-btn').forEach(el => el.classList.toggle('active', el === btn));
      buildScene();
    });
  });
  bindToggle3D('neuralEventsToggle', 'showEvents');
  bindToggle3D('neuralDecisionsToggle', 'showDecisions');
  bindToggle3D('neuralMemoriesToggle', 'showMemories');

  // Zoom buttons
  document.getElementById('neuralZoomIn').addEventListener('click', () => {
    camera3d.position.multiplyScalar(0.75);
  });
  document.getElementById('neuralZoomOut').addEventListener('click', () => {
    camera3d.position.multiplyScalar(1.35);
  });
  document.getElementById('neuralReset').addEventListener('click', () => {
    camera3d.position.set(0, 0, 600);
    controls.target.set(0, 0, 0);
    selectedNode = null;
    hideDetail3D();
  });

  await loadGraph3D();
  animate3D();
}

function bindToggle3D(id, key) {
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener('change', (e) => { neuralFilters[key] = e.target.checked; buildScene(); });
}

async function loadGraph3D() {
  try {
    const res = await fetch('/api/graph');
    graphData = await res.json();
    buildScene();
  } catch (err) {
    console.error('Graph load error:', err);
  }
}

function filterNode(node) {
  if (node.kind === 'event' && !neuralFilters.showEvents) return false;
  if (node.kind === 'decision' && !neuralFilters.showDecisions) return false;
  if (node.kind === 'memory' && !neuralFilters.showMemories) return false;
  return true;
}

function buildScene() {
  // Clear old meshes
  nodeMeshes.forEach(m => scene.remove(m));
  nodeMeshes.clear();
  edgeLines.forEach(l => scene.remove(l));
  edgeLines = [];
  simNodes = [];
  simEdges = [];

  const allNodes = (graphData.nodes || []).filter(filterNode);
  const allEdges = graphData.edges || [];

  // Apply scope filtering
  let visibleNodes, visibleEdges;
  if (neuralFilters.scope === 'all') {
    visibleNodes = allNodes;
  } else if (neuralFilters.scope === 'thinking') {
    visibleNodes = allNodes;
  } else {
    // Overview: entities + limited narrative
    const entities = allNodes.filter(n => !isNarrative(n));
    const narratives = allNodes.filter(n => isNarrative(n));
    // Take top narratives by confidence/importance
    const topNarratives = narratives
      .sort((a, b) => (b.confidence || 0) - (a.confidence || 0))
      .slice(0, 80);
    visibleNodes = [...entities, ...topNarratives];
  }

  const visibleIds = new Set(visibleNodes.map(n => n.id));
  visibleEdges = allEdges.filter(e => visibleIds.has(e.source) && visibleIds.has(e.target));

  // Create simulation nodes with random 3D positions
  const nodeMap = {};
  visibleNodes.forEach(node => {
    const spread = 400;
    const sn = {
      ...node,
      x: (Math.random() - 0.5) * spread,
      y: (Math.random() - 0.5) * spread,
      z: (Math.random() - 0.5) * spread,
      vx: 0, vy: 0, vz: 0,
      size: NODE_SIZES[node.kind] || NODE_SIZES[node.type] || 1.5,
    };
    // Scale by confidence
    if (node.confidence) sn.size *= (0.6 + node.confidence * 0.6);
    simNodes.push(sn);
    nodeMap[node.id] = sn;
  });

  visibleEdges.forEach(edge => {
    const src = nodeMap[edge.source];
    const tgt = nodeMap[edge.target];
    if (src && tgt) simEdges.push({ ...edge, src, tgt });
  });

  // Run force simulation
  for (let i = 0; i < 200; i++) {
    stepSimulation3D();
  }

  // Create Three.js objects
  simNodes.forEach(sn => {
    const color = NODE_COLORS[sn.kind] || NODE_COLORS[sn.type] || NODE_COLORS.unknown;
    const geo = new THREE.SphereGeometry(sn.size, 16, 12);
    const mat = new THREE.MeshPhongMaterial({
      color,
      emissive: color,
      emissiveIntensity: 0.3,
      transparent: true,
      opacity: isNarrative(sn) ? 0.75 : 0.95,
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.position.set(sn.x, sn.y, sn.z);
    mesh.userData = sn;
    scene.add(mesh);
    nodeMeshes.set(sn.id, mesh);

    // Add glow sprite
    const spriteMat = new THREE.SpriteMaterial({
      map: createGlowTexture(color),
      blending: THREE.AdditiveBlending,
      transparent: true,
      opacity: 0.35,
    });
    const sprite = new THREE.Sprite(spriteMat);
    sprite.scale.set(sn.size * 6, sn.size * 6, 1);
    mesh.add(sprite);

    // Label (only for larger nodes)
    if (sn.size > 1.3 || !isNarrative(sn)) {
      const label = createTextSprite(sn.label || sn.id, color);
      label.position.set(0, -(sn.size + 3), 0);
      mesh.add(label);
    }
  });

  // Create edges as lines
  simEdges.forEach(edge => {
    const isEntityEdge = edge.kind === 'entity';
    const points = [
      new THREE.Vector3(edge.src.x, edge.src.y, edge.src.z),
      new THREE.Vector3(edge.tgt.x, edge.tgt.y, edge.tgt.z),
    ];
    const geo = new THREE.BufferGeometry().setFromPoints(points);
    const mat = new THREE.LineBasicMaterial({
      color: isEntityEdge ? 0x4fc3f7 : 0x334455,
      transparent: true,
      opacity: isEntityEdge ? 0.5 : 0.15,
      linewidth: 1,
    });
    const line = new THREE.Line(geo, mat);
    line.userData = edge;
    scene.add(line);
    edgeLines.push(line);
  });
}

function isNarrative(node) {
  return node.kind === 'event' || node.kind === 'decision' || node.kind === 'memory';
}

function stepSimulation3D() {
  const repulsion = 80;
  const attraction = 0.008;
  const damping = 0.85;
  const centerPull = 0.001;

  // Repulsion (all pairs, use spatial hashing for perf)
  for (let i = 0; i < simNodes.length; i++) {
    const a = simNodes[i];
    // Center pull
    a.vx -= a.x * centerPull;
    a.vy -= a.y * centerPull;
    a.vz -= a.z * centerPull;

    for (let j = i + 1; j < simNodes.length; j++) {
      const b = simNodes[j];
      let dx = b.x - a.x, dy = b.y - a.y, dz = b.z - a.z;
      let dist = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
      if (dist > 500) continue; // skip distant pairs
      const force = repulsion / (dist * dist);
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      const fz = (dz / dist) * force;
      a.vx -= fx; a.vy -= fy; a.vz -= fz;
      b.vx += fx; b.vy += fy; b.vz += fz;
    }
  }

  // Attraction along edges
  simEdges.forEach(edge => {
    const dx = edge.tgt.x - edge.src.x;
    const dy = edge.tgt.y - edge.src.y;
    const dz = edge.tgt.z - edge.src.z;
    const dist = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
    const ideal = edge.kind === 'entity' ? 60 : 100;
    const force = (dist - ideal) * attraction;
    const fx = (dx / dist) * force;
    const fy = (dy / dist) * force;
    const fz = (dz / dist) * force;
    edge.src.vx += fx; edge.src.vy += fy; edge.src.vz += fz;
    edge.tgt.vx -= fx; edge.tgt.vy -= fy; edge.tgt.vz -= fz;
  });

  // Apply velocities with damping
  simNodes.forEach(n => {
    n.vx *= damping; n.vy *= damping; n.vz *= damping;
    n.x += n.vx; n.y += n.vy; n.z += n.vz;
  });
}

function createGlowTexture(color) {
  const size = 64;
  const c = document.createElement('canvas');
  c.width = size; c.height = size;
  const ctx = c.getContext('2d');
  const hex = '#' + new THREE.Color(color).getHexString();
  const gradient = ctx.createRadialGradient(size/2, size/2, 0, size/2, size/2, size/2);
  gradient.addColorStop(0, hex);
  gradient.addColorStop(0.3, hex + '88');
  gradient.addColorStop(1, hex + '00');
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, size, size);
  const tex = new THREE.CanvasTexture(c);
  return tex;
}

function createTextSprite(text, color) {
  const c = document.createElement('canvas');
  const ctx = c.getContext('2d');
  const label = (text || '').substring(0, 30);
  ctx.font = '600 24px system-ui, sans-serif';
  const w = Math.min(ctx.measureText(label).width + 16, 400);
  c.width = w; c.height = 36;
  ctx.font = '600 24px system-ui, sans-serif';
  ctx.fillStyle = '#' + new THREE.Color(color).getHexString();
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText(label, w / 2, 18);
  const tex = new THREE.CanvasTexture(c);
  const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, opacity: 0.85 });
  const sprite = new THREE.Sprite(mat);
  sprite.scale.set(w / 12, 3, 1);
  return sprite;
}

function animate3D() {
  animFrame = requestAnimationFrame(animate3D);
  controls.update();

  // Slow continuous simulation for organic movement
  stepSimulation3D();
  simNodes.forEach(sn => {
    const mesh = nodeMeshes.get(sn.id);
    if (mesh) {
      mesh.position.lerp(new THREE.Vector3(sn.x, sn.y, sn.z), 0.05);
    }
  });
  // Update edge positions
  edgeLines.forEach(line => {
    const edge = line.userData;
    const positions = line.geometry.attributes.position.array;
    positions[0] = edge.src.x; positions[1] = edge.src.y; positions[2] = edge.src.z;
    positions[3] = edge.tgt.x; positions[4] = edge.tgt.y; positions[5] = edge.tgt.z;
    line.geometry.attributes.position.needsUpdate = true;
  });

  // Highlight hovered/selected
  nodeMeshes.forEach((mesh, id) => {
    const isSelected = selectedNode && selectedNode.id === id;
    const isHovered = hoveredNode && hoveredNode.id === id;
    const baseOpacity = isNarrative(mesh.userData) ? 0.75 : 0.95;
    mesh.material.opacity = isSelected ? 1.0 : isHovered ? 0.95 :
      (selectedNode ? 0.15 : baseOpacity);
    mesh.material.emissiveIntensity = isSelected ? 0.8 : isHovered ? 0.5 : 0.3;
  });

  renderer.render(scene, camera3d);
}

function onResize3D() {
  const container = document.getElementById('neuralView');
  if (!container || !renderer) return;
  camera3d.aspect = container.clientWidth / container.clientHeight;
  camera3d.updateProjectionMatrix();
  renderer.setSize(container.clientWidth, container.clientHeight);
}

function onMouseMove3D(event) {
  const rect = renderer.domElement.getBoundingClientRect();
  mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
  mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;

  raycaster.setFromCamera(mouse, camera3d);
  const meshes = Array.from(nodeMeshes.values());
  const intersects = raycaster.intersectObjects(meshes);

  const prev = hoveredNode;
  hoveredNode = intersects.length > 0 ? intersects[0].object.userData : null;
  renderer.domElement.style.cursor = hoveredNode ? 'pointer' : 'grab';

  if (hoveredNode) {
    renderTooltip3D(event.clientX, event.clientY, hoveredNode);
  } else {
    document.getElementById('neuralTooltip').classList.add('hidden');
  }
}

function onClick3D(event) {
  if (!hoveredNode) {
    selectedNode = null;
    hideDetail3D();
    return;
  }
  selectedNode = hoveredNode;
  showDetail3D(selectedNode);

  // Fly camera toward selected node
  const target = new THREE.Vector3(selectedNode.x, selectedNode.y, selectedNode.z);
  controls.target.lerp(target, 0.5);
}

function renderTooltip3D(clientX, clientY, node) {
  const tooltip = document.getElementById('neuralTooltip');
  const hex = '#' + new THREE.Color(NODE_COLORS[node.kind] || NODE_COLORS[node.type] || 0x888888).getHexString();
  const kind = node.kind || node.type || 'node';
  const meta = [node.agent_id, node.project, node.category, node.event_type].filter(Boolean);
  tooltip.innerHTML = `
    <div class="tooltip-type" style="color:${hex}">${escHtml(kind)}</div>
    <div class="tooltip-name">${escHtml(node.label)}</div>
    ${meta.length ? `<div class="tooltip-meta">${escHtml(meta.join(' · '))}</div>` : ''}
  `;
  tooltip.classList.remove('hidden');
  tooltip.style.left = `${clientX + 16}px`;
  tooltip.style.top = `${clientY - 20}px`;
}

function showDetail3D(node) {
  const panel = document.getElementById('neuralDetail');
  const hex = '#' + new THREE.Color(NODE_COLORS[node.kind] || NODE_COLORS[node.type] || 0x888888).getHexString();
  const kind = node.kind || node.type || 'node';
  const connections = simEdges
    .filter(e => e.src.id === node.id || e.tgt.id === node.id)
    .slice(0, 18)
    .map(e => {
      const other = e.src.id === node.id ? e.tgt : e.src;
      return `<li>${escHtml(e.label || e.kind || 'linked')}: ${escHtml(other.label)}</li>`;
    }).join('');

  const facts = [];
  if (node.agent_id) facts.push(`Agent: ${node.agent_id}`);
  if (node.project) facts.push(`Project: ${node.project}`);
  if (node.event_type) facts.push(`Event: ${node.event_type}`);
  if (node.created_at) facts.push(`Created: ${node.created_at}`);

  let body = '';
  if (node.detail) body = `<div class="detail-section-title">Content</div><div class="detail-text">${escHtml(node.detail)}</div>`;
  else if (node.observations && node.observations.length) body = `<div class="detail-section-title">Observations</div><ul class="detail-obs-list">${node.observations.map(o => `<li>${escHtml(o)}</li>`).join('')}</ul>`;

  panel.innerHTML = `
    <button class="detail-close" onclick="hideDetail3D();selectedNode=null">×</button>
    <div class="detail-name" style="color:${hex}">${escHtml(node.label)}</div>
    <div class="detail-type">${escHtml(kind)}</div>
    ${facts.length ? `<ul class="detail-obs-list">${facts.map(f => `<li>${escHtml(f)}</li>`).join('')}</ul>` : ''}
    ${body}
    ${connections ? `<div class="detail-section-title">Connections (${simEdges.filter(e => e.src.id === node.id || e.tgt.id === node.id).length})</div><ul class="detail-obs-list">${connections}</ul>` : ''}
  `;
  panel.classList.remove('hidden');
}

function hideDetail3D() {
  document.getElementById('neuralDetail').classList.add('hidden');
}

function escHtml(v) {
  if (!v) return '';
  return String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
