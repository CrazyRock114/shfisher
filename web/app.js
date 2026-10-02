/* 上海钓鱼助手 MVP 前端逻辑（零构建，原生 JS） */
"use strict";

const $ = (sel) => document.querySelector(sel);
const API = {
  spots: "/api/spots",
  score: "/api/score",
  compliance: "/api/compliance",
  tide: "/api/tide",
  waterlevel: "/api/waterlevel",
  log: "/api/log",
  alerts: "/api/alerts",
  waterquality: "/api/waterquality",
};

const TYPE_META = {
  official_spots: { text: "官方垂钓点", color: "#0b6e4f" },
  river: { text: "河道野钓", color: "#2e9e5b" },
  lake: { text: "湖荡", color: "#7d5ba6" },
  paid: { text: "收费钓场", color: "#e07b00" },
  sea: { text: "海钓", color: "#1f7fb8" },
  warning: { text: "禁钓警示", color: "#c0504d" },
};
const CONF_META = {
  verified: { text: "有官方来源" },
  community: { text: "钓友经验" },
  unverified: { text: "待验证" },
};

let SPOTS = [];
let chart = null;
let map = null;
let markersLayer = null;
let currentDistrict = "全部";
let scoreAbort = null;
let lastScoreData = null;

/* ---------- 通用 ---------- */

function showError(msg) {
  const el = $("#error-banner");
  el.textContent = msg;
  el.classList.remove("hidden");
}
function clearError() {
  $("#error-banner").classList.add("hidden");
}

function scoreColor(score, gated) {
  if (gated) return "#57606a";
  if (score >= 75) return "#2e9e5b";
  if (score >= 55) return "#e0a800";
  if (score >= 35) return "#e07b00";
  return "#c0504d";
}

function scoreChip(score, gated) {
  return `<span class="score-chip" style="background:${scoreColor(score, gated)}">${gated ? "禁" : score}</span>`;
}

function verdictChip(text, score, gated) {
  return `<span class="verdict-chip" style="background:${scoreColor(score, gated)}22;color:${scoreColor(score, gated)}">${text}</span>`;
}

async function fetchJSON(url) {
  const resp = await fetch(url);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `${resp.status} ${resp.statusText}`);
  }
  return resp.json();
}

/* ---------- Tab 切换 ---------- */

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $(`#tab-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "spots") initMap();
    if (btn.dataset.tab === "tide") loadTide();
    if (btn.dataset.tab === "log") loadLog();
    if (btn.dataset.tab === "gear") renderGear();
  });
});

/* ---------- 出钓指数 ---------- */

function fillLocationSelect(spots, defaultLocation) {
  const sel = $("#loc-select");
  sel.innerHTML = "";
  const opt = document.createElement("option");
  opt.value = `${defaultLocation.lat},${defaultLocation.lon}`;
  opt.textContent = defaultLocation.label;
  opt.dataset.label = defaultLocation.label;
  sel.appendChild(opt);
  const seen = new Set([defaultLocation.label]);
  for (const s of spots) {
    if (s.type === "warning" || seen.has(s.name)) continue;
    seen.add(s.name);
    const o = document.createElement("option");
    o.value = `${s.lat},${s.lon}`;
    o.textContent = s.name + (s.tidal ? " 🌊" : "");
    o.dataset.label = s.name;
    o.dataset.spotId = s.id;
    sel.appendChild(o);
  }
  sel.addEventListener("change", () => loadScore());
}

async function loadScore() {
  const sel = $("#loc-select");
  const [lat, lon] = sel.value.split(",").map(Number);
  const label = sel.selectedOptions[0].dataset.label || "";
  if (scoreAbort) scoreAbort.abort();
  scoreAbort = new AbortController();
  try {
    const data = await fetchJSON(`${API.score}?lat=${lat}&lon=${lon}&label=${encodeURIComponent(label)}`);
    clearError();
    lastScoreData = data;
    renderScore(data);
    updateTideHint();
  } catch (err) {
    if (err.name !== "AbortError") {
      showError(`出钓指数获取失败：${err.message}（请确认后端已启动，且能访问 api.open-meteo.com）`);
    }
  }
}

function renderScore(data) {
  $("#generated-at").textContent =
    `更新于 ${data.generated_at.slice(11, 16)} · ${data.location.label}`;
  const cur = data.hourly[0];
  const color = scoreColor(cur.score, cur.gate);
  $("#score-ring").style.background = color;
  $("#score-num").textContent = cur.gate ? "✕" : cur.score;
  $("#score-verdict").textContent = cur.verdict;
  $("#score-verdict").style.color = color;
  const nowInfo = [
    `${cur.temp}℃`,
    `${cur.wind_dir}风${cur.wind_level}级`,
    `气压 ${Math.round(cur.pressure)} hPa（24h ${fmtDelta(cur.pressure_trend_24h)}）`,
    cur.precip > 0 ? `雨 ${cur.precip} mm/h` : "无雨",
  ].join(" · ");
  const reasons = cur.gate
    ? cur.gate_reasons
    : [...cur.gate_reasons, ...cur.reasons].slice(0, 4);
  $("#score-now-detail").innerHTML =
    `${nowInfo}<br/>${reasons.map((r) => `· ${r}`).join("<br/>")}`;

  // 最佳窗口
  const winBox = $("#best-windows");
  if (!data.best_windows.length) {
    winBox.innerHTML = `<div class="muted">未来 48 小时没有 ≥55 分的连续窗口，建议改天再钓。</div>`;
  } else {
    winBox.innerHTML = data.best_windows
      .map((w) => {
        const c = scoreColor(w.avg_score, false);
        return `<div class="window-item">
          <span class="win-badge" style="background:${c}">${w.verdict} ${w.avg_score}分</span>
          <span>${w.start.slice(5, 16)} – ${w.end.slice(11, 16)}（${w.hours} 小时）</span>
        </div>`;
      })
      .join("");
  }

  // 按日小结
  const dayText = data.days
    .map((d) => `${d.label || d.date.slice(5)} 最高 ${d.max_score} 分（${d.best_time} 前后${d.gated_hours ? `，${d.gated_hours} 小时不可钓` : ""}）`)
    .join(" · ");
  $("#day-summary").textContent = dayText;

  const moon = moonPhase();
  $("#moon-info").textContent = `🌙 今晚月相：${moon.name}（照亮 ${moon.illum}%）· ${moon.advice}`;

  renderChart(data);
  renderHourlyTable(data);
}

function fmtDelta(v) {
  if (v === null || v === undefined) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)} hPa`;
}

function renderChart(data) {
  const labels = data.hourly.map((h) => {
    const d = h.time.slice(5, 10);
    const hh = `${String(h.hh).padStart(2, "0")}时`;
    return h.hh === 0 ? [d, hh] : hh;
  });
  const scores = data.hourly.map((h) => h.score);
  const colors = data.hourly.map((h) => scoreColor(h.score, h.gate));
  const pressure = data.hourly.map((h) => h.pressure);

  const cfg = {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "出钓指数",
          data: scores,
          backgroundColor: colors,
          borderRadius: 3,
          yAxisID: "y",
          order: 2,
        },
        {
          label: "气压 hPa",
          data: pressure,
          type: "line",
          borderColor: "#1f7fb8",
          backgroundColor: "rgba(31,127,184,.15)",
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.3,
          yAxisID: "y1",
          order: 1,
        },
      ],
    },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        tooltip: {
          callbacks: {
            afterBody: (items) => {
              const rec = data.hourly[items[0].dataIndex];
              return rec.gate ? rec.gate_reasons : rec.reasons.slice(0, 3);
            },
          },
        },
      },
      scales: {
        x: { ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 16, font: { size: 10 } } },
        y: {
          min: 0,
          max: 100,
          title: { display: true, text: "评分" },
          grid: { color: "#eef1f4" },
        },
        y1: {
          position: "right",
          min: 975,
          max: 1035,
          title: { display: true, text: "气压 hPa" },
          grid: { drawOnChartArea: false },
        },
      },
    },
  };
  if (chart) {
    chart.data = cfg.data;
    chart.options = cfg.options;
    chart.update();
  } else {
    chart = new Chart($("#score-chart"), cfg);
  }
}

function renderHourlyTable(data) {
  const rows = data.hourly.slice(0, 24).map((h) => {
    const main = h.gate ? h.gate_reasons[0] : h.reasons[0];
    return `<tr>
      <td>${h.time.slice(5, 16)}</td>
      <td>${scoreChip(h.score, h.gate)}</td>
      <td>${verdictChip(h.verdict, h.score, h.gate)}</td>
      <td>${h.wind_dir}风${h.wind_level}级${h.wind_gust ? `/阵${Math.round(h.wind_gust)}m/s` : ""}</td>
      <td>${Math.round(h.pressure)} hPa（${fmtDelta(h.pressure_trend_24h)}）</td>
      <td>${h.precip > 0 ? h.precip + " mm" : "—"}</td>
      <td>${h.temp}℃</td>
      <td class="wrap muted">${main || "—"}</td>
    </tr>`;
  });
  $("#hourly-table tbody").innerHTML = rows.join("");
}

/* ---------- 预警横幅 / 水质档案 / 月相（V3） ---------- */

let WATER_PROFILES = null;

async function loadAlerts() {
  try {
    const data = await fetchJSON(API.alerts);
    const box = $("#alert-banner");
    if (!data.active.length) {
      box.classList.add("hidden");
      return;
    }
    box.classList.remove("hidden");
    const stop = data.active.some((a) => a.fishing_stop);
    box.innerHTML =
      data.active
        .map(
          (a) => `<div class="alert-item">
        <b>⚠ ${a.level ? a.level + " · " : ""}${a.name || "预警"}</b> — ${a.title}
        <span class="alert-meta">${a.published ? "发布 " + a.published : ""}${
            a.until ? " · 至 " + a.until : ""
          }（${a.unit || a.source}）</span>
      </div>`
        )
        .join("") +
      (stop
        ? `<div class="alert-stop">存在暴雨/台风/雷电/大风类预警，建议今日不出钓；水面和碳素竿导电，空旷水边是雷击高危区。</div>`
        : "");
  } catch (err) {
    /* 预警获取失败不阻塞主功能 */
  }
}

function waterProfileFor(waterBody) {
  if (!WATER_PROFILES || !waterBody) return null;
  for (const [k, p] of Object.entries(WATER_PROFILES)) {
    if (k !== "背景" && (waterBody.includes(k) || k.includes(waterBody))) {
      return { ...p, water_body: k };
    }
  }
  return { ...WATER_PROFILES["背景"], water_body: "全市背景" };
}

function moonPhase(date = new Date()) {
  const SYNODIC = 29.530588853;
  const days = (date.getTime() - Date.UTC(2000, 0, 6, 18, 14)) / 86400000;
  const phase = (((days % SYNODIC) + SYNODIC) % SYNODIC) / SYNODIC; // 0=新月
  const illum = (1 - Math.cos(2 * Math.PI * phase)) / 2;
  const names = ["新月", "娥眉月", "上弦月", "盈凸月", "满月", "亏凸月", "下弦月", "残月"];
  const name = names[Math.round(phase * 8) % 8];
  let advice;
  if (illum < 0.25) advice = "月光暗，鱼敢靠边——夜钓好窗口";
  else if (illum > 0.75) advice = "月光强，鱼警觉——夜钓一般，建议钓浑水或草区";
  else advice = "月光适中，夜钓可试";
  return { name, illum: Math.round(illum * 100), advice };
}

/* ---------- 钓点地图与列表 ---------- */

function initMap() {
  if (map) return;
  map = L.map("map", { zoomSnap: 0.25 }).setView([31.15, 121.47], 10);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18,
    attribution: "© OpenStreetMap",
  }).addTo(map);
  markersLayer = L.featureGroup().addTo(map);
  renderSpots();
}

function spotPopup(s) {
  const meta = TYPE_META[s.type] || { text: s.type, color: "#7b8794" };
  const conf = CONF_META[s.confidence] || { text: s.confidence };
  const fish = s.fish_species.length ? s.fish_species.join("、") : "—";
  const fee = typeof s.fee === "number" ? (s.fee === 0 ? "免费" : s.fee) : s.fee;
  const wp = waterProfileFor(s.water_body);
  const wqLine =
    wp && wp.grade
      ? `水质：${wp.grade}（${wp.water_body}，截至 ${wp.as_of}）<br/>`
      : "";
  const links = (s.sources || [])
    .map((u, i) => `<a href="${u}" target="_blank" rel="noreferrer">来源${i + 1}</a>`)
    .join(" · ");
  return `<div style="max-width:280px">
    <b>${s.name}</b><span class="confidence ${s.confidence}">${conf.text}</span><br/>
    <span style="color:${meta.color};font-weight:600">${meta.text}</span> · ${s.district}<br/>
    鱼种：${fish}<br/>
    费用：${fee}<br/>
    ${wqLine}<span style="color:#7b8794">合规：${s.compliance}</span><br/>
    ${links ? `<small>${links}</small>` : ""}
  </div>`;
}

function renderSpots() {
  const chips = $("#district-chips");
  const districts = ["全部", ...new Set(SPOTS.map((s) => s.district))];
  chips.innerHTML = districts
    .map((d) => `<button class="chip ${d === currentDistrict ? "active" : ""}" data-d="${d}">${d}</button>`)
    .join("");
  chips.querySelectorAll(".chip").forEach((c) =>
    c.addEventListener("click", () => {
      currentDistrict = c.dataset.d;
      renderSpots();
    })
  );

  markersLayer.clearLayers();
  const shown = SPOTS.filter((s) => currentDistrict === "全部" || s.district === currentDistrict);

  const list = $("#spot-list");
  list.innerHTML = "";
  for (const s of shown) {
    const meta = TYPE_META[s.type] || { text: s.type, color: "#7b8794" };
    const marker = L.circleMarker([s.lat, s.lon], {
      radius: s.type === "warning" ? 10 : 8,
      color: "#fff",
      weight: 2,
      fillColor: meta.color,
      fillOpacity: 0.95,
    }).bindPopup(spotPopup(s));
    marker.addTo(markersLayer);
    marker.on("click", () => showSpotDetail(s));

    const item = document.createElement("div");
    item.className = "spot-item";
    item.innerHTML = `<div class="spot-name" style="border-left:4px solid ${meta.color};padding-left:8px">${s.name}
        <span class="confidence ${s.confidence}">${(CONF_META[s.confidence] || {}).text || ""}</span></div>
      <div class="spot-sub">${meta.text} · ${s.district} · ${s.fish_species.slice(0, 3).join("/") || "—"}</div>`;
    item.addEventListener("click", () => {
      map.setView([s.lat, s.lon], 13);
      marker.fire("click");
      showSpotDetail(s);
    });
    list.appendChild(item);
  }
  if (shown.length) {
    // 自动缩放到当前筛选结果（含崇明等远郊钓点）
    map.fitBounds(markersLayer.getBounds().pad(0.15), { maxZoom: 12 });
  }
  $("#spot-detail").classList.add("hidden");
}

function showSpotDetail(s) {
  const box = $("#spot-detail");
  const conf = CONF_META[s.confidence] || { text: s.confidence };
  const wp = waterProfileFor(s.water_body);
  const links = (s.sources || [])
    .map((u) => `<a href="${u}" target="_blank" rel="noreferrer">${u}</a>`)
    .join("<br/>");
  box.innerHTML = `<h3>${s.name}<span class="confidence ${s.confidence}">${conf.text}</span></h3>
    <dl>
      <dt>类型</dt><dd>${(TYPE_META[s.type] || {}).text || s.type}</dd>
      <dt>区域</dt><dd>${s.district} · ${s.water_body}</dd>
      <dt>对象鱼</dt><dd>${s.fish_species.join("、") || "—"}</dd>
      <dt>费用</dt><dd>${typeof s.fee === "number" ? (s.fee === 0 ? "免费" : s.fee) : s.fee}</dd>
      <dt>水质档案</dt><dd>${wp ? `${wp.grade || "未定级"}（${wp.water_body}，截至 ${wp.as_of}）· ${wp.note} <a href="${wp.source}" target="_blank" rel="noreferrer">月报来源</a>` : "—"}</dd>
      <dt>合规要点</dt><dd>${s.compliance}</dd>
      <dt>坐标</dt><dd>${s.lat.toFixed(4)}, ${s.lon.toFixed(4)}（概略，出发前现场确认）</dd>
      <dt>来源</dt><dd>${links || "钓友社区口口相传"}</dd>
    </dl>`;
  box.classList.remove("hidden");
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* ---------- 合规速查 ---------- */

function renderCompliance(items) {
  const rows = items
    .map(
      (r) => `<tr>
        <td>${r.category}</td>
        <td><b>${r.item}</b></td>
        <td class="wrap">${r.rule}</td>
        <td>${r.source ? `<a href="${r.source}" target="_blank" rel="noreferrer">原文</a>` : "—"}</td>
      </tr>`
    )
    .join("");
  $("#compliance-table tbody").innerHTML = rows;
}

/* ---------- 潮汐水文 ---------- */

let tideChart = null;
let tideDataCache = null;
let tideCacheAt = 0;

const PHASE_COLORS = { 1: "#c0504d", 2: "#e07b00", 3: "#e0a800", 4: "#8fbf6f", 5: "#2e9e5b" };

function todayStr() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

async function fetchTide(force = false) {
  const station = $("#tide-station").value || "吴淞";
  if (!force && tideDataCache && Date.now() - tideCacheAt < 10 * 60 * 1000 && tideDataCache.station === station) {
    return tideDataCache;
  }
  const data = await fetchJSON(`${API.tide}?station=${encodeURIComponent(station)}`);
  tideDataCache = data;
  tideCacheAt = Date.now();
  return data;
}

async function loadTide(force = false) {
  try {
    const data = await fetchTide(force);
    clearError();
    const sel = $("#tide-station");
    if (!sel.options.length) {
      sel.innerHTML = data.stations.map((s) => `<option>${s}</option>`).join("");
      sel.value = data.station;
      sel.addEventListener("change", () => loadTide(true));
    }
    $("#tide-updated").textContent = `更新于 ${data.generated_at.slice(11, 16)} · ${data.station}站`;

    const cur = data.current;
    $("#tide-current").innerHTML = cur
      ? `<div class="tide-phase" style="background:${PHASE_COLORS[cur.quality]}">${cur.label}</div>
         <div class="tide-advice">至 ${cur.until}${cur.date !== todayStr() ? "（" + cur.date.slice(5) + "）" : ""}</div>
         <div class="muted small">${cur.advice}</div>`
      : `<div class="muted">暂无当前阶段数据</div>`;

    const nt = data.next_turn;
    $("#tide-next-turn").innerHTML = nt
      ? `<div class="turn-time">${nt.date === todayStr() ? "今天" : nt.date.slice(5)} ${nt.start} – ${nt.end}</div>
         <div class="muted small">转流初期是感潮河道全天最佳作钓窗口：急流转缓、饵料鱼被潮流搬运、掠食鱼最活跃。建议提前 30 分钟到位打窝。</div>`
      : `<div class="muted">48 小时内没有转流窗口</div>`;

    renderTideChart(data);
    renderTideStands(data);
    loadWaterLevel();
  } catch (err) {
    showError(`潮汐数据获取失败：${err.message}`);
  }
}

function renderTideChart(data) {
  const labels = [];
  const heights = [];
  const quality = [];
  const colors = [];
  data.days.forEach((day, di) => {
    for (let h = 0; h < 24; h++) {
      labels.push(di === 0 ? `${h}时` : `明${h}时`);
      heights.push(day.hourly[h]);
      const ph = day.phases.find((p) => p.start_min <= h * 60 && h * 60 < p.end_min);
      const q = ph ? ph.quality : 0;
      quality.push(q);
      colors.push(PHASE_COLORS[q] || "#dfe3e8");
    }
  });
  const cfg = {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "潮高 cm",
          data: heights,
          type: "line",
          borderColor: "#1f7fb8",
          borderWidth: 1.5,
          pointRadius: 0,
          tension: 0.35,
          yAxisID: "y",
          order: 1,
        },
        {
          label: "钓况阶段",
          data: quality,
          backgroundColor: colors,
          yAxisID: "y2",
          order: 2,
          barPercentage: 1,
          categoryPercentage: 1,
        },
      ],
    },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        tooltip: {
          callbacks: {
            afterBody: (items) => {
              const idx = items[0].dataIndex;
              const day = data.days[Math.floor(idx / 24)];
              const h = idx % 24;
              const ph = day.phases.find((p) => p.start_min <= h * 60 && h * 60 < p.end_min);
              return ph ? [ph.label, ph.advice] : [];
            },
          },
        },
      },
      scales: {
        x: { ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 24, font: { size: 10 } } },
        y: { title: { display: true, text: "潮高 cm" } },
        y2: {
          position: "right",
          min: 0,
          max: 5,
          title: { display: true, text: "钓况" },
          grid: { drawOnChartArea: false },
          ticks: { stepSize: 1 },
        },
      },
    },
  };
  if (tideChart) {
    tideChart.data = cfg.data;
    tideChart.options = cfg.options;
    tideChart.update();
  } else {
    tideChart = new Chart($("#tide-chart"), cfg);
  }
}

function renderTideStands(data) {
  $("#tide-stands").innerHTML = data.days
    .map(
      (day) => `<div class="stand-day">
        <div class="stand-date">${day.date === todayStr() ? "今天" : day.date.slice(5)}（${day.station}）</div>
        <div class="stand-chips">${day.stands
          .map(
            (s) =>
              `<span class="stand-chip ${s.type}">${s.time} ${s.type === "high" ? "高潮" : "低潮"} ${s.height}cm</span>`
          )
          .join("")}</div>
      </div>`
    )
    .join("");
}

async function loadWaterLevel() {
  try {
    const data = await fetchJSON(API.waterlevel);
    const rt = data.realtime
      .slice(0, 3)
      .map(
        (r) => `<div class="wl-card">
          <div class="wl-station">${r.station}</div>
          <div class="wl-level">${r.level}<span>米</span></div>
          <div class="muted small">${(r.time || "").slice(5, 16)} · ${r.river || ""}</div>
        </div>`
      )
      .join("");
    const fc = data.forecast
      .filter((f) => ["吴淞口", "黄浦公园", "米市渡"].includes(f.station))
      .map(
        (f) => `<div class="wl-fc"><b>${f.station}</b> 预报潮位：${f.items
          .slice(0, 4)
          .map((i) => `${(i.time || "").slice(5, 16)} ${i.height}m`)
          .join(" · ")}</div>`
      )
      .join("");
    $("#waterlevel").innerHTML = `<div class="wl-row">${rt}</div>${fc}<div class="muted small">上海市水务局水文数据 · 更新于 ${data.generated_at.slice(11, 16)}</div>`;
  } catch (err) {
    $("#waterlevel").innerHTML = `<div class="muted">水位数据获取失败：${err.message}</div>`;
  }
}

async function updateTideHint() {
  const sel = $("#loc-select");
  const spotId = sel.selectedOptions[0]?.dataset.spotId;
  const spot = SPOTS.find((s) => s.id === spotId);
  const box = $("#tide-hint");
  if (!spot || !spot.tidal) {
    box.classList.add("hidden");
    return;
  }
  try {
    const data = await fetchTide();
    const cur = data.current;
    const nt = data.next_turn;
    box.classList.remove("hidden");
    box.innerHTML = `<h3>🌊 感潮点位提示 · ${spot.name}</h3>
      <div>${cur ? `当前 <b>${cur.label}</b>（至 ${cur.until}）` : ""}${
      nt ? ` · 下次转流窗口 <b>${nt.date === todayStr() ? "今天" : nt.date.slice(5)} ${nt.start} – ${nt.end}</b>` : ""
    }</div>
      <div class="muted small">${cur ? cur.advice : ""}。详见"潮汐水文"页（当前为${data.station}站，上游江段略有滞后）。</div>`;
  } catch (err) {
    box.classList.add("hidden");
  }
}

/* ---------- 钓鱼日志 ---------- */

async function loadLog() {
  fillLogSpots();
  if (!$("#log-date").value) $("#log-date").value = todayStr();
  try {
    const data = await fetchJSON(API.log);
    renderLogStats(data.stats);
    renderLogTable(data.trips);
  } catch (err) {
    showError(`日志获取失败：${err.message}`);
  }
}

function fillLogSpots() {
  const sel = $("#log-spot");
  if (sel.options.length) return;
  sel.innerHTML =
    `<option value="">（未指定/自定义）</option>` +
    SPOTS.filter((s) => s.type !== "warning")
      .map((s) => `<option>${s.name}</option>`)
      .join("");
}

function renderLogStats(st) {
  const speciesChips = Object.entries(st.species_counts || {})
    .slice(0, 8)
    .map(([sp, n]) => `<span class="verdict-chip" style="background:#e3f2ec;color:#0b6e4f">${sp}×${n}</span>`)
    .join(" ");
  const cal = st.calibration || { sample_size: 0, ready: false, buckets: {} };
  const calBody = cal.ready
    ? `<table class="cal-table"><tr><th>预测评分</th><th>条数</th><th>实际满意度</th></tr>${Object.entries(
        cal.buckets
      )
        .filter(([, v]) => v.count)
        .map(
          ([k, v]) =>
            `<tr><td>${k} 分</td><td>${v.count}</td><td>${"★".repeat(Math.round(v.avg_rating))} ${v.avg_rating}</td></tr>`
        )
        .join("")}</table>
       <div class="muted small">若"高分桶"的实际满意度并不更高，就该调权重了（回填 scoring.py 常量）。</div>`
    : `<div class="muted small">已积累 <b>${cal.sample_size}</b> 条有效样本（有评分+有快照）；满 10 条后这里会给出"预测评分 vs 实际满意度"的分桶对比，用于校准评分权重。多钓多记哦。</div>`;
  $("#log-stats").innerHTML = `
    <div class="stat-card"><div class="stat-num">${st.trips}</div><div class="muted">出钓次数</div></div>
    <div class="stat-card"><div class="stat-num">${st.total_fish}</div><div class="muted">累计渔获</div></div>
    <div class="stat-card"><div class="stat-num">${st.avg_rating ?? "—"}</div><div class="muted">平均满意度</div></div>
    <div class="stat-card stat-wide"><div class="muted">鱼种分布</div><div>${speciesChips || "—"}</div>
      ${st.top_spots?.length ? `<div class="muted small" style="margin-top:6px">口碑钓点：${st.top_spots.map((t) => `${t.spot}（${t.avg_rating}分/${t.trips}次）`).join("、")}</div>` : ""}
    </div>
    <div class="stat-card stat-wide cal-card"><div class="muted">📈 出钓指数校准（预测 vs 实际）</div>${calBody}</div>`;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function renderLogTable(trips) {
  const rows = trips
    .map((t) => {
      let weather = "—";
      try {
        const w = JSON.parse(t.weather || "null");
        if (w) weather = [w.temp ? `${w.temp}℃` : "", w.wind || "", w.pressure ? `${w.pressure} hPa` : ""].filter(Boolean).join(" · ") || "—";
      } catch (e) { /* 忽略坏快照 */ }
      return `<tr>
        <td>${esc(t.date)}</td>
        <td class="wrap">${esc(t.spot_name)}</td>
        <td>${esc(t.start_time)}–${esc(t.end_time)}</td>
        <td>${esc(t.score ?? "—")}</td>
        <td>${esc(weather)}</td>
        <td class="wrap">${esc(t.fish_text || "—")}</td>
        <td>${esc(t.bait || "—")}</td>
        <td>${t.rating ? "★".repeat(Math.min(5, Math.max(1, Number(t.rating) || 0))) : "—"}</td>
        <td><button class="link-danger" data-del="${esc(t.id)}">删除</button></td>
      </tr>`;
    })
    .join("");
  $("#log-table tbody").innerHTML = rows || `<tr><td colspan="9" class="muted">还没有记录，钓完记得回来记一笔。</td></tr>`;
}

$("#log-snapshot").addEventListener("click", () => {
  if (!lastScoreData) {
    alert("请先在“出钓指数”页加载一次评分，再点快照。");
    return;
  }
  const h = lastScoreData.hourly[0];
  $("#log-score").value = h.score;
  $("#log-weather").value = JSON.stringify({
    temp: h.temp,
    wind: `${h.wind_dir}风${h.wind_level}级`,
    pressure: Math.round(h.pressure),
  });
  alert(`已填入快照：${h.score} 分 · ${h.temp}℃ ${h.wind_dir}风${h.wind_level}级`);
});

$("#log-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const payload = {
    date: $("#log-date").value,
    spot_name: $("#log-spot").value || "自定义钓点",
    start_time: $("#log-start").value || null,
    end_time: $("#log-end").value || null,
    score: $("#log-score").value ? Number($("#log-score").value) : null,
    weather: $("#log-weather").value || null,
    fish_text: $("#log-fish").value.trim() || null,
    bait: $("#log-bait").value.trim() || null,
    rating: Number($("#log-rating").value),
    notes: $("#log-notes").value.trim() || null,
  };
  try {
    await fetch(API.log, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    $("#log-fish").value = "";
    $("#log-notes").value = "";
    loadLog();
  } catch (err) {
    showError(`保存失败：${err.message}`);
  }
});

$("#log-table").addEventListener("click", async (ev) => {
  const id = ev.target.dataset?.del;
  if (!id) return;
  if (!confirm("删除这条记录？")) return;
  await fetch(`${API.log}/${id}`, { method: "DELETE" });
  loadLog();
});

/* ---------- 装备清单 ---------- */

const GEAR_ITEMS = [
  { name: "4.5 米 28 调综合竿（汉鼎一号）", price: 120, note: "新手黄金长度，百元档口碑首选" },
  { name: "成品线组 1.5+0.8 ×5 副", price: 50, note: "钓鲫经典组，断线保竿" },
  { name: "纳米漂 1.5g + 2g 各一支", price: 30, note: "选加粗醒目尾" },
  { name: "成品金袖 3-4 号 + 伊势尼 6 号", price: 20, note: "买绑好的，别贪杂牌" },
  { name: "野战蓝鲫 / 九一八 / 速攻 各一包", price: 30, note: "老三样通杀" },
  { name: "维他米酒米一袋", price: 10, note: "打窝宁少勿多" },
  { name: "蚯蚓一盒", price: 5, note: "渔具店现场买，野钓万能饵" },
  { name: "竿架 + 炮台", price: 40, note: "" },
  { name: "抄网（可伸缩）", price: 50, note: "" },
  { name: "鱼护", price: 40, note: "" },
  { name: "失手绳", price: 30, note: "防大鱼拖竿，实测真香" },
  { name: "饵料盆 + 拉饵盘", price: 30, note: "" },
  { name: "剪刀 / 毛巾 / 塑料袋", price: 10, note: "垃圾随手带走" },
  { name: "帽子 + 冰袖", price: 40, note: "防晒必备" },
  { name: "驱蚊液", price: 15, note: "夏秋傍晚刚需" },
];

const GEAR_TIERS = [
  {
    name: "300 元入门档",
    desc: "验证兴趣用，够钓",
    items: "汉鼎一号 4.5 米（120）+ 成品线组×5（50）+ 纳米漂 2 支（30）+ 成品钩（20）+ 老三样、酒米（50）+ 简易竿架抄网（30）",
  },
  {
    name: "600–800 元舒适档",
    desc: "覆盖第一年",
    items: "竿升级 200–300 元档（钓鱼王白刺/佳钓尼）+ 钓椅（150）+ 抄网鱼护失手绳全套（150）+ 线组钩漂备足（100）+ 饵料窝料（80）+ 防晒驱蚊（100）",
  },
  {
    name: "1500 元进阶档",
    desc: "确定长期玩",
    items: "竿 500–800（达瓦入门/国产高端综合竿）+ 精品配件（300）+ 第二支 3.6 米鲫竿（300）或 500 元路亚套装",
  },
];

const BAIT_GUIDE = `
  <table>
    <thead><tr><th>季节</th><th>配比（野战蓝鲫 : 九一八 : 速攻）</th><th>说明</th></tr></thead>
    <tbody>
      <tr><td>春秋</td><td>4 : 3 : 3</td><td>腥香各半，通杀配比</td></tr>
      <tr><td>冬季</td><td>8 : 1 : 1</td><td>偏腥，钓深钓暖</td></tr>
      <tr><td>夏季</td><td>0.3 : 0.6 : 0.1</td><td>清淡防小鱼闹窝</td></tr>
    </tbody>
  </table>
  <p class="small" style="margin:10px 0 4px"><b>开饵四步</b>：① 按当季比例倒饵 → ② 饵水比 1:1 <b>一次性</b>量好水倒入 → ③ 快速搅匀，静置醒饵 5 分钟 → ④ 收拢成团挤空气；钓鲫捏小团搓饵即可，想拉饵确认含拉丝粉（蓝鲫自带）。</p>
  <p class="small muted">野钓不要碰小药（加多死窝）；酒米打窝一把即可，口好约 20 分钟补半份；鱼不进窝等一等，别狂补窝。</p>
`;

let gearRendered = false;

function renderGear() {
  if (gearRendered) return;
  gearRendered = true;

  let checked = [];
  try {
    checked = JSON.parse(localStorage.getItem("gearChecked") || "[]");
  } catch (e) { /* 首次使用 */ }

  const save = () => localStorage.setItem("gearChecked", JSON.stringify(checked));

  const renderProgress = () => {
    const total = GEAR_ITEMS.reduce((a, b) => a + b.price, 0);
    const remain = GEAR_ITEMS.filter((g) => !checked.includes(g.name)).reduce((a, b) => a + b.price, 0);
    $("#gear-progress").innerHTML = `<b>${checked.length}</b> / ${GEAR_ITEMS.length} 件已备齐 · 还需约 <b>${remain}</b> 元（全套 ${total} 元）`;
  };

  $("#gear-list").innerHTML = GEAR_ITEMS.map(
    (g) => `<label class="gear-item">
      <input type="checkbox" value="${g.name}" ${checked.includes(g.name) ? "checked" : ""} />
      <span class="gear-name">${g.name}</span>
      <span class="gear-price">¥${g.price}</span>
      <span class="gear-note muted small">${g.note}</span>
    </label>`
  ).join("");

  $("#gear-list").addEventListener("change", (ev) => {
    const name = ev.target.value;
    if (ev.target.checked && !checked.includes(name)) checked.push(name);
    if (!ev.target.checked) checked = checked.filter((n) => n !== name);
    save();
    renderProgress();
  });
  renderProgress();

  $("#gear-tiers").innerHTML = GEAR_TIERS.map(
    (t) => `<div class="tier-card"><div class="tier-name">${t.name}</div>
      <div class="muted small">${t.desc}</div><div class="tier-items">${t.items}</div></div>`
  ).join("");

  $("#gear-bait").innerHTML = BAIT_GUIDE;
}

/* ---------- 启动 ---------- */

(async function init() {
  try {
    const [spotsData, complianceData, waterqualityData] = await Promise.all([
      fetchJSON(API.spots),
      fetchJSON(API.compliance),
      fetchJSON(API.waterquality),
    ]);
    clearError();
    SPOTS = spotsData.spots;
    WATER_PROFILES = waterqualityData.profiles;
    fillLocationSelect(SPOTS, spotsData.default_location);
    renderCompliance(complianceData.items);
    loadScore();
    loadAlerts();
  } catch (err) {
    showError(`初始化失败：${err.message}（请确认后端已启动：./run.sh，地址 http://127.0.0.1:8787）`);
  }
})();
