// Ostsee Rising: everything the page does.
// Reads data/manifest.json (format in DATA_CONTRACT.md) and never needs to know where the data came from.

const COLORS = { "Sentinel-1": "#6ee7f9", NISAR: "#ff8fd8", official: "#ffd36e", now: "#ffffff" };
const BLANK = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==";
const SPY_RADIUS = 110;
const DEFAULT_OPACITY = 0.9;
// Water history: oldest year violet, then pink, gold, newest aqua
const HISTORY_STOPS = ["#6a3fd6", "#ff6fb5", "#ffd36e", "#6ee7f9"];
// dB range each grey PNG was stretched to (0 = lo, 255 = hi); the manifest's "stretch" can override
const DEFAULT_STRETCH = { VV: [-25, 0], HH: [-25, 0], VH: [-30, -5], HV: [-30, -5] };

const state = {
  frames: [],
  official: [],
  index: 0,            // the date shown (in compare mode: the "after" date)
  before: 0,           // compare mode only
  pol: "co",           // "co" = water view (VV / HH), "cross" = plant view (VH / HV)
  showWater: true,
  opacity: DEFAULT_OPACITY, // 1 = only radar, 0 = only the satellite photo underneath
  compare: false,
  mode: "swipe",       // swipe | fade | spy
  swipe: 0.5,          // divider position, 0..1 of the map width
  fade: 0.5,           // 0 = before, 1 = after
  spy: null,           // lens centre in map container pixels
  history: false,      // water history map on/off
  tab: "story",        // story | explore (only explore views are written into the link)
};

const $ = (id) => document.getElementById(id);
const all = (selector) => document.querySelectorAll(selector);
let map, slots, chart, playTimer, historyLayer;
const waterHistory = { ready: false, first: null, width: 0, height: 0 }; // first[p] = frame index when pixel p became water


// ---------- loading ----------

async function start() {
  let manifest;
  try {
    manifest = await (await fetch("data/manifest.json", { cache: "no-cache" })).json();  // always the newest data
  } catch {
    $("panel").insertAdjacentHTML("afterbegin",
      `<p class="card">Could not load <b>data/manifest.json</b>.<br>
       Open a terminal in the <code>web/</code> folder and run <code>python -m http.server</code>,
       then go to <a href="http://localhost:8000">localhost:8000</a>.</p>`);
    return;
  }

  state.frames = manifest.frames;
  state.official = manifest.official || [];
  state.bounds = manifest.bounds;
  state.stretch = { ...DEFAULT_STRETCH, ...(manifest.stretch || {}) };

  buildMap(manifest.bounds);
  buildControls();
  buildChart();
  buildStory();

  if (readLink()) {
    showTab("explore");
    render();
  } else {
    enterChapter(document.querySelector(".chapter"));
  }
}


// ---------- map ----------

function buildMap(bounds) {
  map = L.map("map", { zoomSnap: 0.25 }).fitBounds(bounds, { padding: [24, 24] });

  const photo = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 18, attribution: "Photo: Esri, Maxar, Earthstar Geographics" }
  ).addTo(map);
  const streets = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    { maxZoom: 19, attribution: "© OpenStreetMap contributors" });
  L.control.layers({ "Satellite photo": photo, "Street map": streets }).addTo(map);

  // Two stacked panes: "before" is the normal view, "after" sits on top and is revealed by the compare mode.
  map.createPane("before").style.zIndex = 410;
  map.createPane("after").style.zIndex = 420;
  map.createPane("history").style.zIndex = 430;
  slots = { before: makeSlot(bounds, "before"), after: makeSlot(bounds, "after") };
  historyLayer = L.imageOverlay(BLANK, bounds, { pane: "history", className: "history-layer", opacity: 0 }).addTo(map);

  map.on("move zoomend resize", updateCompare);
  map.on("moveend", () => writeLink());
  map.on("click", (e) => showSpot(e.latlng));
  map.on("mousemove", (e) => {
    if (state.compare && state.mode === "spy") {
      state.spy = e.containerPoint;
      updateCompare();
    }
  });
}

function makeSlot(bounds, pane) {
  return {
    radar: L.imageOverlay(BLANK, bounds, { pane }).addTo(map),
    water: L.imageOverlay(BLANK, bounds, { pane, className: "water-layer" }).addTo(map),
  };
}

function polKey(frame, pol) {
  const l = frame.layers;
  if (pol === "co") return l.VV ? "VV" : l.HH ? "HH" : null;
  return l.VH ? "VH" : l.HV ? "HV" : null;
}

function radarPath(frame, pol) {
  const key = polKey(frame, pol);
  return key ? frame.layers[key] : null;
}

function paint(slot, frame) {
  const radar = radarPath(frame, state.pol);
  slot.radar.setUrl(radar ? "data/" + radar : BLANK).setOpacity(radar ? state.opacity : 0);

  const water = frame.layers.water;
  slot.water.setUrl(water ? "data/" + water : BLANK).setOpacity(state.showWater && water ? 1 : 0);
}


// ---------- compare: swipe, fade, spy ----------

function updateCompare() {
  const pane = map.getPane("after");
  pane.style.clip = "";
  pane.style.clipPath = "";
  pane.style.opacity = "";
  pane.style.display = state.compare ? "" : "none";

  $("swipe").hidden = !(state.compare && state.mode === "swipe");
  $("lens").hidden = !(state.compare && state.mode === "spy");
  if (!state.compare) return;

  // Panes move with the map, so clip shapes are expressed in the pane's own pixel space.
  const size = map.getSize();
  const nw = map.containerPointToLayerPoint([0, 0]);

  if (state.mode === "fade") {
    pane.style.opacity = state.fade;
  } else if (state.mode === "swipe") {
    const se = map.containerPointToLayerPoint(size);
    const x = nw.x + size.x * state.swipe;
    pane.style.clip = `rect(${nw.y}px, ${se.x}px, ${se.y}px, ${x}px)`;
  } else {
    const centre = state.spy || size.divideBy(2);
    const p = map.containerPointToLayerPoint(centre);
    pane.style.clipPath = `circle(${SPY_RADIUS}px at ${p.x}px ${p.y}px)`;
    Object.assign($("lens").style, {
      left: `${centre.x}px`, top: `${centre.y}px`,
      width: `${SPY_RADIUS * 2}px`, height: `${SPY_RADIUS * 2}px`,
    });
  }
}

function setupSwipe() {
  const wrap = $("map-wrap");
  const handle = document.querySelector(".swipe-handle");

  handle.addEventListener("pointerdown", (e) => handle.setPointerCapture(e.pointerId));
  handle.addEventListener("pointermove", (e) => {
    if (!handle.hasPointerCapture(e.pointerId)) return;
    const box = wrap.getBoundingClientRect();
    state.swipe = Math.min(0.98, Math.max(0.02, (e.clientX - box.left) / box.width));
    $("swipe").style.left = `${state.swipe * 100}%`;
    updateCompare();
  });
}

// ---------- reading pixels out of the PNGs (used by the history map and the click time series) ----------

const pixelCache = new Map();

function loadPixels(path) {
  if (!pixelCache.has(path)) {
    pixelCache.set(path, new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const canvas = document.createElement("canvas");
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
        const ctx = canvas.getContext("2d", { willReadFrequently: true });
        ctx.drawImage(img, 0, 0);
        resolve(ctx.getImageData(0, 0, canvas.width, canvas.height));
      };
      img.onerror = () => reject(new Error(`could not load ${path}`));
      img.src = "data/" + path;
    }));
  }
  return pixelCache.get(path);
}


// ---------- water history: the year each pixel first went under water ----------

function mixColor(stops, t) {
  const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const scaled = t * (stops.length - 1);
  const i = Math.min(Math.floor(scaled), stops.length - 2);
  const [a, b] = [hex(stops[i]), hex(stops[i + 1])];
  return a.map((v, k) => Math.round(v + (b[k] - v) * (scaled - i)));
}

function historyYears() {
  return [...new Set(state.frames.map((f) => +f.date.slice(0, 4)))];
}

function yearColor(year) {
  const years = historyYears();
  return mixColor(HISTORY_STOPS, years.length > 1 ? years.indexOf(year) / (years.length - 1) : 0);
}

async function buildHistory() {
  const masks = await Promise.all(state.frames.map((f) => (f.layers.water ? loadPixels(f.layers.water) : null)));
  const { width, height } = masks.find(Boolean);
  const isWater = (i, p) => !masks[i] || masks[i].data[p * 4 + 3] > 0;  // missing mask = don't veto
  const first = new Int16Array(width * height).fill(-1);

  for (let p = 0; p < first.length; p++) {
    for (let i = 0; i < masks.length; i++) {
      if (!masks[i] || !isWater(i, p)) continue;
      // must still be water on the next pass, so a single noisy speckle doesn't count
      if (i === masks.length - 1 || isWater(i + 1, p)) { first[p] = i; break; }
    }
  }

  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  const image = ctx.createImageData(width, height);
  first.forEach((i, p) => {
    if (i < 0) return;
    const [r, g, b] = yearColor(+state.frames[i].date.slice(0, 4));
    image.data.set([r, g, b, 235], p * 4);
  });
  ctx.putImageData(image, 0, 0);

  Object.assign(waterHistory, { ready: true, first, width, height });
  historyLayer.setUrl(canvas.toDataURL());
  buildLegend();
}

function buildLegend() {
  const used = new Set([...waterHistory.first].filter((i) => i >= 0).map((i) => +state.frames[i].date.slice(0, 4)));
  $("history-legend").innerHTML = `<b>First year under water</b><div class="swatches">${
    historyYears().filter((y) => used.has(y)).map((y) => `<span><i style="background:rgb(${yearColor(y)})"></i>${y}</span>`).join("")
  }</div>`;
}

const MODE_HINTS = {
  swipe: "Drag the star on the map: before on the left, after on the right.",
  fade: "Slide to melt one date into the other.",
  spy: "Move your mouse over the map: the lens shows the after date.",
};


// ---------- controls ----------

const isBeta = (f) => f.maturity === "BETA";   // pre-calibration NISAR product

function frameLabel(f) {
  return `${f.date} · ${f.sensor === "NISAR" ? "NISAR" : "S-1"}${isBeta(f) ? " (beta)" : ""}`;
}

function buildControls() {
  $("time").max = state.frames.length - 1;
  $("time").addEventListener("input", (e) => setIndex(+e.target.value));
  $("play").addEventListener("click", () => (playTimer ? stopPlay() : play(state.frames.length - 1)));

  all("button[data-pol]").forEach((btn) =>
    btn.addEventListener("click", () => update({ pol: btn.dataset.pol })));
  all("button[data-mode]").forEach((btn) =>
    btn.addEventListener("click", () => update({ mode: btn.dataset.mode })));

  $("show-water").addEventListener("change", (e) => update({ showWater: e.target.checked }));
  $("show-history").addEventListener("change", (e) => update({ history: e.target.checked }));
  $("opacity").addEventListener("input", (e) => update({ opacity: +e.target.value }));
  $("fade").addEventListener("input", (e) => update({ fade: +e.target.value }));

  const options = state.frames.map((f, i) => `<option value="${i}">${frameLabel(f)}</option>`).join("");
  $("before").innerHTML = options;
  $("after").innerHTML = options;
  $("before").addEventListener("change", (e) => update({ before: +e.target.value }));
  $("after").addEventListener("change", (e) => setIndex(+e.target.value));

  $("compare").addEventListener("change", (e) => {
    const on = e.target.checked;
    update({ compare: on, before: on && state.before === state.index ? 0 : state.before });
  });

  all("[data-tab]").forEach((btn) => btn.addEventListener("click", () => showTab(btn.dataset.tab)));
  all("[data-goto]").forEach((btn) => btn.addEventListener("click", (e) => {
    e.stopPropagation();
    showTab(btn.dataset.goto);
  }));

  $("copy-link").addEventListener("click", copyLink);
  $("export-gif").addEventListener("click", exportTimelapse);
  setupSwipe();
}

function showTab(name) {
  state.tab = name;
  all("[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $("tab-story").hidden = name !== "story";
  $("tab-explore").hidden = name !== "explore";
  if (name === "story") enterChapter(document.querySelector(".chapter.active") || document.querySelector(".chapter"));
  writeLink();
}

function update(changes) {
  Object.assign(state, changes);
  render();
}

function setIndex(i) {
  update({ index: i });
}

function play(target, from = null) {
  stopPlay();
  if (from !== null) setIndex(from);
  else if (state.index >= target) setIndex(0);
  $("play").textContent = "❚❚";
  playTimer = setInterval(() => {
    if (state.index >= target) return stopPlay();
    setIndex(state.index + 1);
  }, 700);
}

function stopPlay() {
  clearInterval(playTimer);
  playTimer = null;
  $("play").textContent = "▶";
}


// ---------- click anywhere: one spot's radar history ----------

let spotChart, spotMarker;

function toPixel(latlng, width, height) {
  const [[south, west], [north, east]] = state.bounds;
  const x = Math.floor(((latlng.lng - west) / (east - west)) * width);
  const y = Math.floor(((north - latlng.lat) / (north - south)) * height);
  return x >= 0 && y >= 0 && x < width && y < height ? { x, y } : null;
}

// Mean backscatter of the 3x3 window around (x, y), in dB.
// Averaged as linear power, because averaging logarithmic dB values directly would skew the result.
function windowDb(pixels, x, y, [lo, hi]) {
  let sum = 0, n = 0;
  for (let dy = -1; dy <= 1; dy++) {
    for (let dx = -1; dx <= 1; dx++) {
      const px = x + dx, py = y + dy;
      if (px < 0 || py < 0 || px >= pixels.width || py >= pixels.height) continue;
      const grey = pixels.data[(py * pixels.width + px) * 4];
      sum += 10 ** ((lo + (grey / 255) * (hi - lo)) / 10);
      n++;
    }
  }
  return +(10 * Math.log10(sum / n)).toFixed(1);
}

async function spotSeries(latlng) {
  return Promise.all(state.frames.map(async (frame) => {
    const keys = { co: polKey(frame, "co"), cross: polKey(frame, "cross") };
    const [co, cross, water] = await Promise.all([
      keys.co && loadPixels(frame.layers[keys.co]),
      keys.cross && loadPixels(frame.layers[keys.cross]),
      frame.layers.water && loadPixels(frame.layers.water),
    ]);
    const ref = co || cross || water;
    const at = toPixel(latlng, ref.width, ref.height);
    if (!at) return null;
    return {
      time: Date.parse(frame.date),
      date: frame.date,
      co: co ? windowDb(co, at.x, at.y, state.stretch[keys.co]) : null,
      cross: cross ? windowDb(cross, at.x, at.y, state.stretch[keys.cross]) : null,
      water: water ? water.data[(at.y * water.width + at.x) * 4 + 3] > 0 : null,
    };
  }));
}

async function showSpot(latlng) {
  if (!toPixel(latlng, 1000, 1000)) return;           // outside the radar images
  $("map-hint").hidden = true;
  const series = await spotSeries(latlng);
  if (series.includes(null)) return;

  // same rule as the history map: water now and still water on the next pass
  const firstWet = series.findIndex((s, i) => s.water && (i === series.length - 1 || series[i + 1].water !== false));
  const verdict = firstWet < 0
    ? "Never under water: dry land the whole time."
    : `First under water: <b>${series[firstWet].date}</b>`;

  spotMarker?.remove();
  spotMarker = L.circleMarker(latlng, { radius: 6, color: "#fff", weight: 2, fillColor: "#ff8fd8", fillOpacity: 1 }).addTo(map);

  L.popup({ className: "spot-popup", minWidth: 300, maxWidth: 320 })
    .setLatLng(latlng)
    .setContent(`<div class="spot">
        <b class="spot-title">This spot over time ✦</b>
        <small>${latlng.lat.toFixed(4)}° N, ${latlng.lng.toFixed(4)}° E</small>
        <div class="spot-chart"><canvas id="spot-canvas"></canvas></div>
        <p>${verdict}</p>
        <p class="hint">Filled dots: our water mask says <i>water</i> on that date. Water view drops sharply when the lake arrives.</p>
      </div>`)
    .on("remove", () => { spotChart?.destroy(); spotMarker?.remove(); })
    .openOn(map);

  spotChart?.destroy();
  const line = (label, key, color) => ({
    label,
    data: series.map((s) => ({ x: s.time, y: s[key] })),
    borderColor: color,
    pointBackgroundColor: series.map((s) => (s.water ? color : "transparent")),
    pointBorderColor: color,
    pointRadius: 3,
    showLine: true,
    borderWidth: 1.5,
  });
  spotChart = new Chart($("spot-canvas"), {
    type: "scatter",
    data: { datasets: [line("Water view", "co", COLORS["Sentinel-1"]), line("Plant view", "cross", COLORS.NISAR)] },
    options: {
      maintainAspectRatio: false,
      animation: false,
      scales: {
        x: { ...yearAxis(), grid: { color: "rgba(255,255,255,0.07)" } },
        y: { grid: { color: "rgba(255,255,255,0.07)" }, ticks: { font: { size: 10 } }, title: { display: true, text: "dB", font: { size: 10 } } },
      },
      plugins: {
        legend: { labels: { boxWidth: 8, boxHeight: 8, font: { size: 10 } } },
        tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.raw.y} dB (${new Date(ctx.raw.x).toISOString().slice(0, 10)})` } },
      },
    },
  });
}


// ---------- shareable links: the view lives in the URL after the # ----------

function linkFor() {
  const f = state.frames;
  const p = new URLSearchParams({ d: f[state.index].date });
  if (state.pol === "cross") p.set("v", "plant");
  if (!state.showWater) p.set("w", "0");
  if (state.opacity !== DEFAULT_OPACITY) p.set("o", state.opacity);
  if (state.history) p.set("h", "1");
  if (state.compare) {
    p.set("c", state.mode);
    p.set("b", f[state.before].date);
    if (state.mode === "fade") p.set("f", state.fade);
  }
  const c = map.getCenter();
  p.set("m", `${c.lat.toFixed(4)},${c.lng.toFixed(4)},${map.getZoom()}`);
  return p.toString();
}

function writeLink() {
  if (!map) return;
  const hash = state.tab === "explore" ? "#" + linkFor() : "";
  window.history.replaceState(null, "", location.pathname + location.search + hash);
}

// A number from the link, kept between 0 and 1; anything broken falls back to the default
function unit(value, fallback) {
  const n = Number(value);
  return value !== null && Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : fallback;
}

function readLink() {
  const p = new URLSearchParams(location.hash.slice(1));
  if (!p.has("d")) return false;
  const at = (date) => nearestIndex(Date.parse(date));
  Object.assign(state, {
    index: at(p.get("d")),
    pol: p.get("v") === "plant" ? "cross" : "co",
    showWater: p.get("w") !== "0",
    opacity: unit(p.get("o"), DEFAULT_OPACITY),
    history: p.get("h") === "1",
    compare: p.has("c"),
    mode: ["swipe", "fade", "spy"].includes(p.get("c")) ? p.get("c") : "swipe",
    before: p.has("b") ? at(p.get("b")) : 0,
    fade: unit(p.get("f"), 0.5),
  });
  const [lat, lng, zoom] = (p.get("m") || "").split(",").map(Number);
  if ([lat, lng, zoom].every(Number.isFinite)) map.setView([lat, lng], zoom);
  return true;
}

async function copyLink() {
  writeLink();
  const button = $("copy-link");
  try {
    await navigator.clipboard.writeText(location.href);
    button.textContent = "Copied! ✦";
  } catch {
    window.prompt("Copy this link:", location.href);   // clipboard blocked (e.g. plain http)
  }
  setTimeout(() => (button.textContent = "Copy link to this view"), 1800);
}


// ---------- timelapse GIF: every pass drawn on a canvas, encoded in the browser ----------

const GIF_WIDTH = 600;
const GIF_DELAY = 650;       // ms per pass
const GIF_HOLD = 2200;       // ms on the last pass

function loadImage(path) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`could not load ${path}`));
    img.src = "data/" + path;
  });
}

function drawTimelapseFrame(ctx, frame, i, images, layout) {
  const { width, height, mapTop, mapH, scale } = layout;

  const sky = ctx.createLinearGradient(0, 0, width, height);
  sky.addColorStop(0, "#0c0a26");
  sky.addColorStop(1, "#1a1446");
  ctx.fillStyle = sky;
  ctx.fillRect(0, 0, width, height);

  ctx.imageSmoothingEnabled = false;
  if (images.radar) ctx.drawImage(images.radar, 0, mapTop, images.radar.width * scale, mapH);
  if (images.water && state.showWater) {
    ctx.filter = "hue-rotate(-28deg) saturate(1.4) brightness(1.25)";
    ctx.drawImage(images.water, 0, mapTop, images.water.width * scale, mapH);
    ctx.filter = "none";
  }

  ctx.textBaseline = "alphabetic";
  ctx.fillStyle = "#ff8fd8";
  ctx.font = "600 24px Fredoka, Nunito, sans-serif";
  ctx.fillText("Ostsee Rising ✦", 18, 34);
  ctx.fillStyle = "#f2eeff";
  ctx.font = "600 20px Fredoka, Nunito, sans-serif";
  ctx.fillText(`${frame.date}  ·  ${frame.sensor}`, 18, 62);

  ctx.textAlign = "right";
  ctx.fillStyle = "#6ee7f9";
  ctx.font = "600 34px Fredoka, Nunito, sans-serif";
  ctx.fillText(`${frame.area_km2.toFixed(1)} km²`, width - 18, 52);
  ctx.textAlign = "left";

  const barY = mapTop + mapH + 14;
  ctx.fillStyle = "rgba(255,255,255,0.15)";
  ctx.fillRect(18, barY, width - 36, 6);
  ctx.fillStyle = "#ff8fd8";
  ctx.fillRect(18, barY, (width - 36) * ((i + 1) / state.frames.length), 6);
  ctx.fillStyle = "#a9a2cf";
  ctx.font = "13px Nunito, sans-serif";
  ctx.fillText("NASA Space Apps 2026 · radar: ESA Sentinel-1 + NASA-ISRO NISAR", 18, barY + 26);
}

async function exportTimelapse() {
  const button = $("export-gif");
  button.disabled = true;
  try {
    const { GIFEncoder, quantize, applyPalette } = await import("./lib/gifenc.esm.js");
    await document.fonts.ready;

    const first = await loadImage(radarPath(state.frames[0], state.pol) || state.frames[0].layers.water);
    const scale = GIF_WIDTH / first.width;
    const mapTop = 80;
    const mapH = Math.round(first.height * scale);
    const layout = { width: GIF_WIDTH, height: mapTop + mapH + 50, mapTop, mapH, scale };

    const canvas = document.createElement("canvas");
    canvas.width = layout.width;
    canvas.height = layout.height;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    const gif = GIFEncoder();

    for (const [i, frame] of state.frames.entries()) {
      button.textContent = `Drawing pass ${i + 1} of ${state.frames.length}…`;
      const radar = radarPath(frame, state.pol);
      const images = {
        radar: radar ? await loadImage(radar) : null,
        water: frame.layers.water ? await loadImage(frame.layers.water) : null,
      };
      drawTimelapseFrame(ctx, frame, i, images, layout);

      const { data } = ctx.getImageData(0, 0, layout.width, layout.height);
      const palette = quantize(data, 256);
      const last = i === state.frames.length - 1;
      gif.writeFrame(applyPalette(data, palette), layout.width, layout.height,
        { palette, delay: last ? GIF_HOLD : GIF_DELAY });
      await new Promise((r) => setTimeout(r));   // let the button text repaint
    }
    gif.finish();

    const url = URL.createObjectURL(new Blob([gif.bytes()], { type: "image/gif" }));
    const a = Object.assign(document.createElement("a"), {
      href: url, download: `ostsee-rising-${state.pol === "co" ? "water" : "plant"}-view.gif`,
    });
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    button.textContent = "Downloaded ✦";
  } catch (err) {
    console.error(err);
    button.textContent = "Export failed, see console";
  }
  setTimeout(() => {
    button.textContent = "Download timelapse GIF";
    button.disabled = false;
  }, 2500);
}


// ---------- story ----------

// "first", "last" or a date: picks the satellite pass closest to that date
function resolveDate(value) {
  if (value === "first") return 0;
  if (value === "last") return state.frames.length - 1;
  return nearestIndex(Date.parse(value));
}

function nearestIndex(time) {
  let best = 0;
  state.frames.forEach((f, i) => {
    if (Math.abs(Date.parse(f.date) - time) < Math.abs(Date.parse(state.frames[best].date) - time)) best = i;
  });
  return best;
}

function enterChapter(chapter) {
  all(".chapter").forEach((c) => c.classList.toggle("active", c === chapter));
  const d = chapter.dataset;
  const target = resolveDate(d.date);

  stopPlay();
  update({
    pol: d.pol || "co",
    opacity: d.opacity ? +d.opacity : DEFAULT_OPACITY,
    compare: Boolean(d.compare),
    history: Boolean(d.history),
    mode: d.compare || state.mode,
    before: d.before ? resolveDate(d.before) : state.before,
    index: d.play ? state.index : target,
  });
  if (d.play) play(target, 0);
}

function buildStory() {
  const chapters = [...all(".chapter")];
  // Desktop: the story panel scrolls by itself. Phone: the whole page scrolls, so watch the viewport.
  const panelScrolls = getComputedStyle($("tab-story")).overflowY === "auto";
  const observer = new IntersectionObserver(
    (entries) => entries.forEach((entry) => entry.isIntersecting && state.tab === "story" && enterChapter(entry.target)),
    { root: panelScrolls ? $("tab-story") : null, rootMargin: panelScrolls ? "-40% 0px -55% 0px" : "-62% 0px -30% 0px" }
  );
  chapters.forEach((c) => {
    observer.observe(c);
    c.addEventListener("click", () => enterChapter(c));
  });
}


// ---------- chart ----------

const asPoint = (f) => ({ x: Date.parse(f.date), y: f.area_km2 });

// x axis with one tick per 1 January, labelled '19, '20, ...
function yearAxis() {
  const years = state.frames.map((f) => +f.date.slice(0, 4));
  const firstYear = Math.min(...years);
  const lastYear = Math.max(...years) + 1;
  return {
    min: Date.UTC(firstYear, 0, 1),
    max: Date.UTC(lastYear, 0, 1),
    afterBuildTicks: (axis) => {
      axis.ticks = [];
      for (let y = firstYear; y <= lastYear; y++) axis.ticks.push({ value: Date.UTC(y, 0, 1) });
    },
    ticks: { maxRotation: 0, font: { size: 10 }, callback: (v) => `'${String(new Date(v).getUTCFullYear()).slice(2)}` },
  };
}

function buildChart() {
  Chart.defaults.color = "rgba(233, 228, 255, 0.7)";
  Chart.defaults.font.family = "Nunito, system-ui, sans-serif";

  const sensors = [...new Set(state.frames.map((f) => f.sensor))];
  const datasets = sensors.map((sensor) => ({
    label: sensor,
    data: state.frames.filter((f) => f.sensor === sensor).map(asPoint),
    borderColor: COLORS[sensor] || "#ccc",
    backgroundColor: COLORS[sensor] || "#ccc",
    showLine: true,
    borderWidth: 2,
    pointRadius: state.frames.length > 40 ? 1.5 : 2.5,
  }));
  datasets.push({
    label: "Official (LEAG)",
    data: state.official.map(asPoint),
    pointStyle: "rectRot",
    pointRadius: 7,
    backgroundColor: COLORS.official,
    borderColor: "#1a1446",
  });
  datasets.push({
    label: "now",
    data: [],
    pointRadius: 9,
    borderWidth: 2.5,
    borderColor: COLORS.now,
    backgroundColor: "transparent",
  });

  const grid = { color: "rgba(255, 255, 255, 0.07)" };

  chart = new Chart($("chart"), {
    type: "scatter",
    data: { datasets },
    options: {
      maintainAspectRatio: false,
      animation: false,
      scales: {
        x: { ...yearAxis(), grid },
        y: { beginAtZero: true, grid, ticks: { font: { size: 10 } }, title: { display: true, text: "km²", font: { size: 10 } } },
      },
      plugins: {
        legend: {
          position: "bottom",
          labels: { boxWidth: 8, boxHeight: 8, font: { size: 10 }, filter: (item) => item.text !== "now" },
        },
        tooltip: {
          callbacks: {
            label: (ctx) => `${ctx.dataset.label}: ${ctx.raw.y} km² (${new Date(ctx.raw.x).toISOString().slice(0, 10)})`,
          },
        },
      },
      onClick: (_, hits) => {
        if (!hits.length) return;
        const { datasetIndex, index } = hits[0];
        setIndex(nearestIndex(chart.data.datasets[datasetIndex].data[index].x));
      },
    },
  });
}


// ---------- draw everything for the current state ----------

function render() {
  const now = state.frames[state.index];
  const before = state.frames[state.before];

  if (state.compare) {
    paint(slots.before, before);
    paint(slots.after, now);
    $("label-before").textContent = `◀ ${frameLabel(before)}`;
    $("label-after").textContent = `${frameLabel(now)} ▶`;
  } else {
    paint(slots.before, now);
  }
  updateCompare();

  if (state.history && !waterHistory.ready && !waterHistory.building) {
    waterHistory.building = buildHistory().then(render);
  }
  historyLayer.setOpacity(state.history && waterHistory.ready ? 1 : 0);
  $("history-legend").hidden = !(state.history && waterHistory.ready);

  // readout
  $("date").textContent = now.date;
  $("area").textContent = now.area_km2.toFixed(1);
  $("sensor").textContent = isBeta(now) ? `${now.sensor} · beta` : now.sensor;
  $("sensor").title = isBeta(now) ? "Pre-calibration (BETA) NISAR product: less certain" : "";
  $("sensor").className = `badge ${now.sensor === "NISAR" ? "nisar" : "s1"}`;
  $("co-name").textContent = now.layers.VV ? "VV" : "HH";
  $("cross-name").textContent = now.layers.VH ? "VH" : "HV";

  // keep every control in sync, whether the change came from the story or from a click
  $("time").value = state.index;
  $("after").value = state.index;
  $("before").value = state.before;
  $("show-water").checked = state.showWater;
  $("show-history").checked = state.history;
  $("opacity").value = state.opacity;
  $("fade").value = state.fade;
  $("compare").checked = state.compare;
  $("compare-options").hidden = !state.compare;
  $("fade-row").hidden = state.mode !== "fade";
  $("mode-hint").textContent = MODE_HINTS[state.mode];
  $("label-before").hidden = $("label-after").hidden = !state.compare;
  all("button[data-pol]").forEach((b) => b.classList.toggle("active", b.dataset.pol === state.pol));
  all("button[data-mode]").forEach((b) => b.classList.toggle("active", b.dataset.mode === state.mode));

  chart.data.datasets.at(-1).data = [asPoint(now)];
  chart.update("none");
  writeLink();
}

start();
