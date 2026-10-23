import { sunPosition } from './sun.js';

const CENTER = [48.2085, 16.3731];
const STEP = 5;                      // minutes per timeline step
const STEPS = (24 * 60) / STEP;
const SUNNY = 0.5;                   // fraction of the garden that counts as "sunny"
const TYPE_LABEL = {
  cafe: 'Café', restaurant: 'Restaurant', bar: 'Bar', pub: 'Pub', biergarten: 'Beer garden',
  ice_cream: 'Ice cream', fast_food: 'Snack bar',
};
const TYPE_GROUP = { cafe: 'cafe', ice_cream: 'cafe', bar: 'bar', pub: 'bar', biergarten: 'bar', restaurant: 'restaurant', fast_food: 'restaurant' };

const $ = (s) => document.querySelector(s);
const state = {
  places: [], H: null, bins: 0, azMin: 0,
  live: true, date: todayISO(), minute: nowMinute(),
  filter: 'sun', type: null, query: '',
  you: null, selected: null,
  weather: null,
};

// ------------------------------------------------------------------ time ---

function todayISO(d = new Date()) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
function nowMinute() { const d = new Date(); return d.getHours() * 60 + d.getMinutes(); }
function dateAt(iso, minute) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d, Math.floor(minute / 60), minute % 60);
}
const hhmm = (minute) => `${String(Math.floor(minute / 60) % 24).padStart(2, '0')}:${String(minute % 60).padStart(2, '0')}`;

// Sun positions for every 5-minute step of a day (one position for all of
// Vienna: the city is small enough that the error stays below 0.2°).
const dayCache = new Map();
function daySun(iso) {
  if (!dayCache.has(iso)) {
    const arr = [];
    for (let i = 0; i < STEPS; i++) arr.push(sunPosition(dateAt(iso, i * STEP), CENTER[0], CENTER[1]));
    dayCache.set(iso, arr);
    if (dayCache.size > 4) dayCache.delete(dayCache.keys().next().value);
  }
  return dayCache.get(iso);
}

// How much light passes deciduous crowns: bare in early spring, dense in summer.
function treeTransmission(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  const doy = (Date.UTC(y, m - 1, d) - Date.UTC(y, 0, 0)) / 864e5;
  const bare = 0.6, leafy = 0.15;
  const ramp = (x, a, b) => Math.min(1, Math.max(0, (x - a) / (b - a)));
  if (doy < 180) return bare + (leafy - bare) * ramp(doy, 95, 130);   // leaf-out ~Apr 5 → May 10
  return leafy + (bare - leafy) * ramp(doy, 275, 320);                 // leaf-fall ~Oct 2 → Nov 16
}

// ------------------------------------------------------------- sun model ---

// 1 = sun, T = through tree crowns, 0 = shade
function pointLight(p, sun, T) {
  if (sun.elevation <= 0) return 0;
  const i = Math.round(sun.azimuth) - state.azMin;
  if (i < 0 || i >= state.bins) return 0;
  const o = (p * state.bins + i) * 3;
  const H = state.H;
  if (sun.elevation <= H[o] / 2) return 0;
  const hi = H[o + 2] / 2;
  if (hi > 0 && sun.elevation >= H[o + 1] / 2 && sun.elevation <= hi) return T;
  return 1;
}

function placeLight(pl, sun, T) {
  let s = 0;
  for (let k = 0; k < pl.n; k++) s += pointLight(pl.p0 + k, sun, T);
  return s / pl.n;
}

function placeDay(pl, iso) {
  if (pl._dayIso === iso) return pl._day;
  const sun = daySun(iso), T = treeTransmission(iso);
  const out = new Float32Array(STEPS);
  for (let i = 0; i < STEPS; i++) out[i] = placeLight(pl, sun[i], T);
  pl._day = out; pl._dayIso = iso;
  return out;
}

function sunriseSunset(iso) {
  const sun = daySun(iso);
  let rise = null, set = null;
  for (let i = 0; i < STEPS; i++) {
    if (sun[i].elevation > 0) { if (rise === null) rise = i; set = i; }
  }
  return { rise, set };
}

// "sun until 15:40" / "sun from 11:20"
function outlook(pl, iso, step) {
  const day = placeDay(pl, iso);
  const { set } = sunriseSunset(iso);
  const sunny = (pl.f ?? day[step]) >= SUNNY;
  for (let i = step + 1; i < STEPS; i++) {
    if ((day[i] >= SUNNY) !== sunny) return { sunny, change: i * STEP };
  }
  return { sunny, change: null, sunset: set !== null ? (set + 1) * STEP : null };
}

function outlookText(o, el) {
  if (el <= 0) return 'Sun is down';
  if (o.sunny) return o.change !== null ? `until ${hhmm(o.change)}` : `until sunset ${hhmm(o.sunset)}`;
  return o.change !== null ? `sun from ${hhmm(o.change)}` : 'no more sun today';
}

function color(f, el) {
  if (el <= 0) return getCss('--night');
  if (f >= 0.75) return getCss('--sun');
  if (f >= 0.35) return getCss('--part');
  if (f > 0.05) return mix(getCss('--part'), getCss('--shade'), 0.55);
  return getCss('--shade');
}
const cssCache = {};
function getCss(v) { return cssCache[v] ??= getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }
function mix(a, b, t) {
  const pa = a.match(/\w\w/g).map((h) => parseInt(h, 16)), pb = b.match(/\w\w/g).map((h) => parseInt(h, 16));
  return '#' + pa.map((v, i) => Math.round(v + (pb[i] - v) * t).toString(16).padStart(2, '0')).join('');
}

// ------------------------------------------------------------------- map ---

const map = L.map('map', { zoomControl: false, preferCanvas: true, attributionControl: true })
  .setView(CENTER, 15);
L.tileLayer('https://mapsneu.wien.gv.at/basemap/bmapgrau/normal/google3857/{z}/{y}/{x}.png', {
  maxNativeZoom: 19, maxZoom: 20, subdomains: '',
  attribution: '<a href="https://basemap.at">basemap.at</a> · Stadt Wien OGD · © OSM',
}).addTo(map);
const renderer = L.canvas({ padding: 0.3 });
const markers = [];
let youMarker = null, pointLayer = L.layerGroup().addTo(map);

function markerRadius() {
  const z = map.getZoom();
  return z >= 18 ? 9 : z >= 16 ? 7 : z >= 14 ? 5 : 3.5;
}
map.on('zoomend', () => { const r = markerRadius(); markers.forEach((m) => m.setRadius(r)); });

// ------------------------------------------------------------------ data ---

async function load() {
  const [pj, hb] = await Promise.all([
    fetch('data/places.json').then((r) => r.json()),
    fetch('data/horizons.bin').then((r) => r.arrayBuffer()),
  ]);
  state.places = pj.places;
  state.H = new Uint8Array(hb);
  state.bins = pj.meta.azBins;
  state.azMin = pj.meta.azMin;
  for (const pl of state.places) {
    pl.label = pl.name || `Schanigarten ${pl.addr}`;
    pl.group = TYPE_GROUP[pl.type] || null;
    pl.hay = norm([pl.name, pl.addr, pl.plz, TYPE_LABEL[pl.type], pl.cuisine].filter(Boolean).join(' '));
    const m = L.circleMarker([pl.lat, pl.lon], {
      renderer, radius: markerRadius(), weight: 1.5, color: '#ffffff', fillOpacity: 0.95,
    });
    m.on('click', () => openDetail(pl));
    m.addTo(map);
    pl.marker = m;
    markers.push(m);
  }
}

const norm = (s) => s.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/ß/g, 'ss');

// --------------------------------------------------------------- weather ---

async function loadWeather() {
  try {
    const url = 'https://api.open-meteo.com/v1/forecast?latitude=48.21&longitude=16.37'
      + '&hourly=cloud_cover,temperature_2m&minutely_15=sunshine_duration'
      + '&timezone=Europe%2FVienna&forecast_days=3&past_days=1';
    const w = await (await fetch(url)).json();
    state.weather = {
      hourly: Object.fromEntries(w.hourly.time.map((t, i) => [t, { cloud: w.hourly.cloud_cover[i], temp: w.hourly.temperature_2m[i] }])),
      q: Object.fromEntries((w.minutely_15?.time || []).map((t, i) => [t, w.minutely_15.sunshine_duration[i]])),
    };
  } catch { state.weather = null; }
  render();
}

function weatherAt(iso, minute) {
  if (!state.weather) return null;
  const h = state.weather.hourly[`${iso}T${hhmm(Math.floor(minute / 60) * 60)}`];
  if (!h) return null;
  const q = state.weather.q[`${iso}T${hhmm(Math.floor(minute / 15) * 15)}`];
  return { ...h, sunshine: q == null ? null : q / 900 };
}

// Rough "is the sun out" from the forecast: sunshine duration when the sun is
// high enough for it to be meaningful, otherwise cloud cover.
function skyClear(w, el) {
  if (!w) return null;
  if (w.sunshine != null && el > 10) return w.sunshine;
  return 1 - w.cloud / 100;
}

function renderWeather(el) {
  const box = $('#weather');
  const w = weatherAt(state.date, state.minute);
  if (!w || el <= 0) { box.hidden = true; return; }
  const c = skyClear(w, el);
  const t = `${Math.round(w.temp)}°`;
  box.hidden = false;
  box.classList.toggle('cloudy', c < 0.35);
  box.textContent = c >= 0.65 ? `☀︎ Clear sky forecast · ${t}`
    : c >= 0.35 ? `⛅ Sun and clouds · ${t} — sun spots below may be clouded over at times`
      : `☁︎ Mostly cloudy · ${t} — the map shows where the sun would reach if it came out`;
}

// ---------------------------------------------------------------- render ---

function currentStep() { return Math.min(STEPS - 1, Math.floor(state.minute / STEP)); }

function render() {
  if (!state.H) return;
  const iso = state.date, step = currentStep();
  const sun = sunPosition(dateAt(iso, state.minute), CENTER[0], CENTER[1]);
  const T = treeTransmission(iso);
  state.sun = sun;

  for (const pl of state.places) {
    pl.f = placeLight(pl, sun, T);
    pl.marker.setStyle({ fillColor: color(pl.f, sun.elevation) });
  }
  $('#timelabel').textContent = hhmm(state.minute);
  $('#slider').value = step;
  $('#date').value = iso;
  $('#now').classList.toggle('active', state.live);
  renderWeather(sun.elevation);
  renderList(iso, step, sun);
  if (state.selected) renderDetail(state.selected);
}

function distance(a, b) {
  const R = 6371000, toR = Math.PI / 180;
  const dLat = (b[0] - a[0]) * toR, dLon = (b[1] - a[1]) * toR;
  const x = Math.sin(dLat / 2) ** 2 + Math.cos(a[0] * toR) * Math.cos(b[0] * toR) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
}

function renderList(iso, step, sun) {
  const q = norm(state.query.trim());
  const ref = state.you || (q ? null : [map.getCenter().lat, map.getCenter().lng]);
  let items = state.places.filter((pl) =>
    (!q || q.split(/\s+/).every((w) => pl.hay.includes(w))) &&
    (!state.type || pl.group === state.type) &&
    (state.filter !== 'sun' || pl.f >= SUNNY));
  for (const pl of items) pl.dist = ref ? distance(ref, [pl.lat, pl.lon]) : null;
  items.sort((a, b) => ref ? a.dist - b.dist : (b.f - a.f) || a.label.localeCompare(b.label));

  const sunnyCount = state.places.filter((p) => p.f >= SUNNY).length;
  const summary = sun.elevation <= 0 ? 'The sun is down — move the slider to see daytime sun.'
    : `${sunnyCount} of ${state.places.length} Schanigärten in sun at ${hhmm(state.minute)}`
      + (state.you ? ' · nearest first' : ref ? ' · nearest to map centre' : '');
  $('#summary').textContent = summary;

  const ul = $('#list');
  ul.innerHTML = '';
  if (!items.length) {
    ul.innerHTML = `<li class="empty">${sun.elevation <= 0 ? 'No sun right now.' : 'Nothing found — try “All” or another search.'}</li>`;
    return;
  }
  const frag = document.createDocumentFragment();
  for (const pl of items.slice(0, 120)) {
    const li = document.createElement('li');
    const o = outlook(pl, iso, step);
    const pct = sun.elevation > 0 ? `${Math.round(pl.f * 100)}%` : '–';
    const walk = pl.dist != null ? (pl.dist < 1500 ? `${Math.max(1, Math.round(pl.dist / 80))} min walk` : `${(pl.dist / 1000).toFixed(1)} km`) : '';
    li.innerHTML = `
      <div class="badge" style="background:${color(pl.f, sun.elevation)}">${pct}</div>
      <div style="min-width:0">
        <div class="name"></div>
        <div class="sub"></div>
      </div>
      <div class="right"><b>${outlookText(o, sun.elevation)}</b><br>${walk}</div>`;
    li.querySelector('.name').textContent = pl.label;
    li.querySelector('.sub').textContent = [TYPE_LABEL[pl.type], pl.addr].filter(Boolean).join(' · ');
    li.addEventListener('click', () => openDetail(pl, true));
    frag.appendChild(li);
  }
  ul.appendChild(frag);
}

// ---------------------------------------------------------------- detail ---

function openDetail(pl, pan) {
  state.selected = pl;
  $('#listview').hidden = true;
  $('#detail').hidden = false;
  $('#detail').scrollTop = 0;
  if (pan) map.setView([pl.lat, pl.lon], Math.max(map.getZoom(), 18), { animate: true });
  renderDetail(pl);
}

function closeDetail() {
  state.selected = null;
  pointLayer.clearLayers();
  $('#detail').hidden = true;
  $('#listview').hidden = false;
}

function timelineSvg(pl, iso, step) {
  const day = placeDay(pl, iso);
  const { rise, set } = sunriseSunset(iso);
  const s0 = Math.max(0, Math.floor(((rise ?? 72) * STEP - 30) / 60) * 12);
  const s1 = Math.min(STEPS, Math.ceil(((set ?? 216) * STEP + 30) / 60) * 12);
  const W = 340, H = 64, n = s1 - s0, w = W / n;
  const sun = daySun(iso);
  let bars = '';
  for (let i = s0; i < s1; i++) {
    const f = day[i], x = (i - s0) * w;
    bars += `<rect x="${x.toFixed(2)}" y="8" width="${(w + 0.4).toFixed(2)}" height="32" fill="${color(f, sun[i].elevation)}"/>`;
  }
  // clouds row
  let clouds = '';
  if (state.weather) {
    for (let i = s0; i < s1; i += 3) {
      const wv = weatherAt(iso, i * STEP);
      if (!wv) continue;
      const c = 1 - (skyClear(wv, sun[i].elevation) ?? 1);
      clouds += `<rect x="${((i - s0) * w).toFixed(2)}" y="44" width="${(w * 3 + 0.4).toFixed(2)}" height="5" style="fill:var(--shade)" opacity="${(0.1 + c * 0.9).toFixed(2)}"/>`;
    }
  }
  let ticks = '';
  for (let i = s0; i <= s1; i += 24) {
    const x = (i - s0) * w;
    ticks += `<line x1="${x}" x2="${x}" y1="40" y2="43" style="stroke:var(--muted)"/><text x="${x}" y="61" font-size="10" style="fill:var(--muted)" text-anchor="middle">${hhmm(i * STEP).slice(0, 2)}</text>`;
  }
  const nx = (Math.min(Math.max(step, s0), s1) - s0) * w;
  return `<svg class="timeline" viewBox="-8 0 ${W + 16} ${H}" role="img" aria-label="Sun over the day">
    ${bars}<line x1="0" x2="${W}" y1="40" y2="40" style="stroke:var(--line)"/>${clouds}${ticks}
    <line x1="${nx}" x2="${nx}" y1="0" y2="49" style="stroke:var(--ink)" stroke-width="1.5"/>
  </svg>`;
}

function renderDetail(pl) {
  const iso = state.date, step = currentStep(), sun = state.sun;
  const o = outlook(pl, iso, step);
  const T = treeTransmission(iso);
  const day = placeDay(pl, iso);
  const total = day.reduce((a, f) => a + (f >= SUNNY ? STEP : 0), 0);
  const label = sun.elevation <= 0 ? 'Sun is down' : pl.f >= 0.75 ? 'Sunny' : pl.f >= 0.35 ? 'Partly sunny' : pl.f > 0.05 ? 'A little sun' : 'In the shade';
  const nav = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(pl.name ? `${pl.name}, ${pl.addr}, Wien` : `${pl.lat},${pl.lon}`)}`;

  const el = $('#detail');
  el.innerHTML = `
    <button class="back">‹ Back to list</button>
    <h2 class="d-title"></h2>
    <p class="d-sub"></p>
    <div class="d-now">
      <div class="big" style="color:${color(pl.f, sun.elevation)}">${sun.elevation > 0 ? Math.round(pl.f * 100) + '%' : '☾'}</div>
      <div class="txt"><b>${label} at ${hhmm(state.minute)}</b>${outlookText(o, sun.elevation)}</div>
    </div>
    <div class="d-h">Sun over the day · ${Math.floor(total / 60)} h ${total % 60} min sunny</div>
    ${timelineSvg(pl, iso, step)}
    <div class="legend">
      <span><i style="background:var(--sun)"></i>sun</span>
      <span><i style="background:var(--part)"></i>partly / through trees</span>
      <span><i style="background:var(--shade)"></i>shade</span>
      ${state.weather ? '<span><i style="background:var(--shade);opacity:.6"></i>clouds (forecast)</span>' : ''}
    </div>
    <div class="d-h">Details</div>
    <dl class="facts"></dl>
    <div class="actions">
      <a href="${nav}" target="_blank" rel="noopener">Open in Maps</a>
      ${pl.web ? `<a href="${encodeURI(pl.web)}" target="_blank" rel="noopener">Website</a>` : ''}
    </div>
    <p class="note">Shadows are computed from the city's 3D building model and tree register.
      Umbrellas, awnings and private trees aren't included. The dots on the map show where we assume the tables are.</p>`;
  el.querySelector('.d-title').textContent = pl.label;
  el.querySelector('.d-sub').textContent = [TYPE_LABEL[pl.type], `${pl.addr}, ${pl.plz} Wien`].filter(Boolean).join(' · ');
  const facts = [
    ['Garden', `${pl.area} m² · ${pl.kinds.join(', ')}`],
    pl.cuisine && ['Cuisine', pl.cuisine.replace(/;/g, ', ').replace(/_/g, ' ')],
    pl.hours && ['Hours', pl.hours],
  ].filter(Boolean);
  const dl = el.querySelector('.facts');
  for (const [k, v] of facts) {
    const dt = document.createElement('dt'); dt.textContent = k;
    const dd = document.createElement('dd'); dd.textContent = v;
    dl.append(dt, dd);
  }
  el.querySelector('.back').addEventListener('click', closeDetail);

  pointLayer.clearLayers();
  pl.pts.forEach(([lon, lat], k) => {
    const f = pointLight(pl.p0 + k, sun, T);
    L.circleMarker([lat, lon], { radius: 4, weight: 1, color: '#fff', fillColor: color(f, sun.elevation), fillOpacity: 1, interactive: false })
      .addTo(pointLayer);
  });
}

// ------------------------------------------------------------- controls ---

$('#slider').addEventListener('input', (e) => {
  state.live = false;
  state.minute = Number(e.target.value) * STEP;
  render();
});
$('#now').addEventListener('click', () => {
  state.live = true; state.date = todayISO(); state.minute = nowMinute(); render();
});
$('#date').addEventListener('change', (e) => {
  if (!e.target.value) return;
  state.live = false; state.date = e.target.value; render();
});
let searchTimer;
$('#search').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.query = e.target.value;
    if (state.query) { state.filter = 'all'; syncChips(); }
    closeDetail(); render();
  }, 150);
});
document.querySelectorAll('.filters .chip').forEach((b) => b.addEventListener('click', () => {
  if (b.dataset.filter) state.filter = b.dataset.filter;
  if (b.dataset.type) state.type = state.type === b.dataset.type ? null : b.dataset.type;
  syncChips(); render();
}));
function syncChips() {
  document.querySelectorAll('.filters .chip').forEach((b) => {
    b.classList.toggle('active', b.dataset.filter ? b.dataset.filter === state.filter : b.dataset.type === state.type);
  });
}
$('#handle').addEventListener('click', () => {
  const p = $('#panel');
  if (p.classList.contains('tall')) { p.classList.remove('tall'); p.classList.add('short'); }
  else if (p.classList.contains('short')) p.classList.remove('short');
  else p.classList.add('tall');
  setTimeout(() => map.invalidateSize(), 300);
});
map.on('moveend', () => { if (!state.you && !state.query && !state.selected) renderList(state.date, currentStep(), state.sun); });

$('#locate').addEventListener('click', () => {
  if (!navigator.geolocation) return;
  $('#locate').classList.add('on');
  let first = true;
  navigator.geolocation.watchPosition((pos) => {
    state.you = [pos.coords.latitude, pos.coords.longitude];
    if (!youMarker) youMarker = L.marker(state.you, { icon: L.divIcon({ className: '', html: '<div class="you-dot"></div>', iconSize: [16, 16] }), interactive: false }).addTo(map);
    youMarker.setLatLng(state.you);
    if (first) { map.setView(state.you, 17); first = false; }
    render();
  }, () => { $('#locate').classList.remove('on'); }, { enableHighAccuracy: true, maximumAge: 30000 });
});

setInterval(() => {
  if (!state.live) return;
  const d = todayISO();
  if (d !== state.date || nowMinute() !== state.minute) { state.date = d; state.minute = nowMinute(); render(); }
}, 30000);
setInterval(loadWeather, 30 * 60000);

if ('serviceWorker' in navigator && location.protocol === 'https:') navigator.serviceWorker.register('sw.js');

load().then(() => { render(); loadWeather(); });
