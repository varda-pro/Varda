// Varda's shape model viewer: a plate model (a triangle mesh in body-fixed
// km) with one color per plate. The same page runs inside Varda (Qt
// WebEngine) and as an exported web page. Its data is window.VARDA_SCENE
// (see varda/terrain/shape_scene.py); Varda drives it through window.viewer
// and hears of clicks through pick messages on the console.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const scene = window.VARDA_SCENE;
const settings = Object.assign(
  { light: "headlight", sun: [1, 0, 0], background: "#101418", ambient: 0.3, colorBar: true },
  scene.settings || {}
);

const container = document.getElementById("view");
let renderer;
try {
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
} catch (error) {
  console.log("varda:error " + error.message);
  document.body.insertAdjacentHTML("beforeend",
    "<p style='color:#ccc;padding:2em'>This view needs WebGL 2, which isn't available here.</p>");
  throw error;
}
renderer.setPixelRatio(window.devicePixelRatio || 1);
renderer.outputColorSpace = THREE.SRGBColorSpace;
container.appendChild(renderer.domElement);

const world = new THREE.Scene();
const radius = scene.radiusKm || 1;
const camera = new THREE.PerspectiveCamera(30, 1, radius * 1e-3, radius * 100);
camera.up.set(0, 0, 1); // the body's +Z (north pole) up

// --- the model: one vertex per plate corner, so each plate has its own color

function bytes(base64) {
  return Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
}
const quantized = new Uint16Array(bytes(scene.vertices).buffer);
const plates = new Int32Array(bytes(scene.plates).buffer);
const plateCount = plates.length / 3;
const positions = new Float32Array(plateCount * 9);
for (let p = 0; p < plateCount; p++) {
  for (let k = 0; k < 3; k++) {
    const v = plates[3 * p + k];
    for (let axis = 0; axis < 3; axis++) {
      positions[9 * p + 3 * k + axis] = scene.low[axis] + quantized[3 * v + axis] * scene.step[axis];
    }
  }
}
const geometry = new THREE.BufferGeometry();
geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
geometry.computeVertexNormals(); // flat: no corner is shared
const colorArray = new Float32Array(plateCount * 9);
geometry.setAttribute("color", new THREE.BufferAttribute(colorArray, 3));
const material = new THREE.MeshLambertMaterial({ vertexColors: true });
const mesh = new THREE.Mesh(geometry, material);
world.add(mesh);

let legend = null;
function setColors(payload) {
  const rgb = bytes(payload.colors);
  const linear = new THREE.Color();
  for (let p = 0; p < plateCount; p++) {
    linear.setRGB(rgb[3 * p] / 255, rgb[3 * p + 1] / 255, rgb[3 * p + 2] / 255, THREE.SRGBColorSpace);
    for (let k = 0; k < 3; k++) colorArray.set([linear.r, linear.g, linear.b], 9 * p + 3 * k);
  }
  geometry.attributes.color.needsUpdate = true;
  legend = payload.legend;
}
setColors(scene);

// --- light: a headlight that follows the camera, or the sun

const sky = new THREE.AmbientLight(0xffffff, settings.ambient);
const light = new THREE.DirectionalLight(0xffffff, 2.2);
world.add(sky, light, light.target);
function placeLight() {
  if (settings.light === "sun") {
    light.position.fromArray(settings.sun).normalize().multiplyScalar(radius * 10);
  } else {
    light.position.copy(camera.position);
  }
}

// --- the picked plate's marker

const marker = new THREE.Mesh(
  new THREE.SphereGeometry(1, 12, 8),
  new THREE.MeshBasicMaterial({ color: 0xff3b30 })
);
marker.visible = false;
world.add(marker);

// --- camera

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.12;
controls.screenSpacePanning = true;

function home() {
  camera.position.set(radius * 3.2, -radius * 3.2, radius * 1.6);
  controls.target.set(0, 0, 0);
  controls.update();
}

function resize() {
  const w = container.clientWidth, h = container.clientHeight;
  renderer.setSize(w, h);
  camera.aspect = w / Math.max(h, 1);
  camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize);

// --- picking: a click (not a drag) reports the plate and the view ray in km

const raycaster = new THREE.Raycaster();
let pressedAt = null;
renderer.domElement.addEventListener("pointerdown", (event) => { pressedAt = [event.clientX, event.clientY]; });
renderer.domElement.addEventListener("pointerup", (event) => {
  if (!pressedAt || Math.hypot(event.clientX - pressedAt[0], event.clientY - pressedAt[1]) > 4) return;
  const bounds = renderer.domElement.getBoundingClientRect();
  const ndc = new THREE.Vector2(
    ((event.clientX - bounds.left) / bounds.width) * 2 - 1,
    -((event.clientY - bounds.top) / bounds.height) * 2 + 1
  );
  raycaster.setFromCamera(ndc, camera);
  const hit = raycaster.intersectObject(mesh, false)[0];
  if (!hit) return;
  marker.position.copy(hit.point);
  marker.scale.setScalar(camera.position.distanceTo(hit.point) * 0.006);
  marker.visible = true;
  console.log("varda:pick " + JSON.stringify({
    plate: hit.faceIndex,
    xyz: hit.point.toArray(),
    origin: raycaster.ray.origin.toArray(),
    direction: raycaster.ray.direction.toArray(),
  }));
});

// --- the color bar

const annotations = document.createElement("canvas");
annotations.style.cssText = "position:absolute;inset:0;width:100%;height:100%;pointer-events:none";
container.appendChild(annotations);

function drawLegend(ctx, w, h, k) {
  ctx.clearRect(0, 0, w, h);
  if (!legend || !settings.colorBar) return;
  const barW = 14 * k, barH = Math.min(220 * k, h * 0.5);
  const x = w - 70 * k, y = h - barH - 34 * k;
  legend.colors.forEach((color, i) => {
    const n = legend.colors.length;
    ctx.fillStyle = color;
    ctx.fillRect(x, y + barH * (1 - (i + 1) / n), barW, barH / n + 1);
  });
  ctx.fillStyle = "#e8e8e8";
  ctx.font = `${11 * k}px sans-serif`;
  ctx.textAlign = "right";
  const label = (v) => (Math.abs(v) >= 1000 || (Math.abs(v) < 0.01 && v !== 0) ? v.toExponential(2) : v.toPrecision(3));
  ctx.fillText(label(legend.high), x - 4 * k, y + 8 * k);
  ctx.fillText(label(legend.low), x - 4 * k, y + barH);
  ctx.textAlign = "center";
  ctx.fillText(legend.label, x + barW / 2 - 20 * k, y - 8 * k);
}

function frame() {
  controls.update();
  placeLight();
  renderer.render(world, camera);
  const w = container.clientWidth, h = container.clientHeight, ratio = renderer.getPixelRatio();
  if (annotations.width !== w * ratio || annotations.height !== h * ratio) {
    annotations.width = w * ratio;
    annotations.height = h * ratio;
  }
  drawLegend(annotations.getContext("2d"), annotations.width, annotations.height, ratio);
  requestAnimationFrame(frame);
}

function apply() {
  sky.intensity = settings.ambient;
  renderer.setClearColor(new THREE.Color(settings.background), 1);
  placeLight();
}

window.viewer = {
  ready: false,
  set(changes) {
    Object.assign(settings, changes);
    apply();
    syncPanel();
  },
  settings() {
    return Object.assign({}, settings);
  },
  setColors,
  getCamera() {
    return { position: camera.position.toArray(), target: controls.target.toArray(), fov: camera.fov };
  },
  setCamera(view) {
    camera.position.fromArray(view.position);
    controls.target.fromArray(view.target);
    if (view.fov) { camera.fov = view.fov; camera.updateProjectionMatrix(); }
    controls.update();
  },
  home,
  still(width, height, transparent) {
    const size = new THREE.Vector2();
    renderer.getSize(size);
    const ratio = renderer.getPixelRatio();
    renderer.setPixelRatio(1);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    if (transparent) renderer.setClearColor(0x000000, 0);
    placeLight();
    renderer.render(world, camera);
    const still = document.createElement("canvas");
    still.width = width;
    still.height = height;
    const ctx = still.getContext("2d");
    ctx.drawImage(renderer.domElement, 0, 0);
    const overlay = document.createElement("canvas");
    overlay.width = width;
    overlay.height = height;
    drawLegend(overlay.getContext("2d"), width, height, width / Math.max(container.clientWidth, 1));
    ctx.drawImage(overlay, 0, 0);
    const url = still.toDataURL("image/png");
    renderer.setClearColor(new THREE.Color(settings.background), 1);
    renderer.setPixelRatio(ratio);
    renderer.setSize(size.x, size.y);
    resize();
    return url;
  },
  info() {
    return { plates: plateCount, note: scene.note };
  },
};

// --- the page's own controls (in exported pages)

const panel = document.getElementById("panel");
function syncPanel() {
  if (!panel) return;
  const sun = panel.querySelector("input[data-setting=sunLight]");
  if (sun) sun.checked = settings.light === "sun";
}
if (panel) {
  const sun = panel.querySelector("input[data-setting=sunLight]");
  if (sun) sun.addEventListener("input", () => window.viewer.set({ light: sun.checked ? "sun" : "headlight" }));
  const homeButton = document.getElementById("home");
  if (homeButton) homeButton.addEventListener("click", home);
}

resize();
apply();
if (scene.camera) window.viewer.setCamera(scene.camera);
else home();
syncPanel();
window.viewer.ready = true;
frame();
console.log("varda:ready");
