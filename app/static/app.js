"use strict";
const LINES = ["A", "B", "C", "D"];
const WEEK = "月火水木金土日";
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = {
  date: null,
  lanes: { A: [], B: [], C: [], D: [] },
  meta: { checklist: {}, memo: "", revision: 0 },
  manual: [],
  products: {},
  fields: [],
  result: null,
  rowMap: {}, // "line:seq" → lane内の位置（チェック結果と画面の行を対応付ける）
  dirty: false,
  settings: null,
  holidays: {},
};

// ---------- 共通 ----------
async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await fetch(path, opt);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
function toast(msg, ms = 2600) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), ms);
}
function fmt(m) {
  if (m === null || m === undefined || m === "") return "";
  m = Math.round(m);
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;
}
function parseTime(s) {
  s = String(s || "").trim().replace("：", ":");
  if (!s) return null;
  const m = s.match(/^(\d{1,2})(?::(\d{1,2}))?$/);
  if (!m) return undefined;
  return Number(m[1]) * 60 + Number(m[2] || 0);
}
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* 保存できなくても動く */ } },
};
function setDirty(v) {
  state.dirty = v;
  $("#dirty").hidden = !v;
}
function addDays(dateStr, n) {
  const d = new Date(dateStr + "T00:00:00");
  d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// ---------- タブ ----------
document.querySelectorAll(".tabs button").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("on", x === b));
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t.id === "tab-" + b.dataset.tab));
    if (b.dataset.tab === "master") renderMaster();
    if (b.dataset.tab === "history") loadHistory();
    if (b.dataset.tab === "settings") renderSettings();
  })
);

// ---------- 作業予定の読み込み・保存 ----------
async function loadDay(date) {
  if (state.dirty && !confirm("保存していない変更があります。破棄して移動しますか？")) {
    $("#date").value = state.date;
    return;
  }
  state.date = date;
  $("#date").value = date;
  store.set("date", date);
  const data = await api("/api/plan?date=" + date);
  state.lanes = { A: [], B: [], C: [], D: [] };
  for (const it of data.items) state.lanes[it.line].push({ ...it });
  state.meta = data.meta;
  state.manual = data.manual;
  $("#memo").value = state.meta.memo || "";
  setDirty(false);
  flatItems(); // 行の対応表を作る
  renderLanes();
  renderManual();
  applyResult(data);
}

function flatItems() {
  const items = [];
  state.rowMap = {};
  for (const ln of LINES) {
    let seq = 0;
    state.lanes[ln].forEach((it, idx) => {
      if (!it.code || !(Number(it.batches) > 0)) return;
      state.rowMap[`${ln}:${seq}`] = idx;
      items.push({ line: ln, seq: seq++, code: it.code, batches: Number(it.batches), start_min: it.start_min, gap_min: it.gap_min || 0, memo: it.memo || "" });
    });
  }
  return items;
}

let checkTimer;
function scheduleCheck() {
  clearTimeout(checkTimer);
  checkTimer = setTimeout(runCheck, 250);
}
async function runCheck() {
  const items = flatItems();
  applyResult(await api("/api/check", { date: state.date, items }));
}

async function save() {
  state.meta.memo = $("#memo").value;
  const r = await api("/api/plan", { date: state.date, user: $("#user").value, items: flatItems(), meta: state.meta });
  state.meta.revision = r.revision;
  setDirty(false);
  renderRev();
  toast(r.changes.length ? `保存しました（改-${r.revision}：${r.changes.length}件の変更）` : "保存しました（変更なし）");
}

// ---------- ライン（行） ----------
function chips(p) {
  if (!p) return "";
  const c = [];
  if (p.is_new) c.push(["新規", "red"]);
  if (p.asa_ichi) c.push(["朝一", "amber"]);
  if (p.tanitsu) c.push(["単一", "amber"]);
  if (p.last_special) c.push(["最後", "amber"]);
  if (p.rotiru) c.push([p.rotiru === "main" ? "ロティール" : "ロティール派生", ""]);
  if (p.cheese) c.push(["チーズ", ""]);
  if (p.kaseijin) c.push(["火星人", ""]);
  if (p.egg) c.push(["卵", ""]);
  if ((p.mix || "").includes("MS")) c.push(["MS", ""]);
  if (/キャリア|ｷｬﾘｱ/.test(p.mix || "")) c.push(["キャリア", ""]);
  if (p.packaging && !p.packaging.includes("包装機")) c.push([p.packaging, ""]);
  return c.map(([t, cls]) => `<span class="chip ${cls}">${esc(t)}</span>`).join("");
}

function renderLanes() {
  const wrap = $("#lanes");
  wrap.innerHTML = "";
  for (const ln of LINES) {
    const lane = document.createElement("div");
    lane.className = "lane";
    lane.dataset.line = ln;
    lane.innerHTML = `<div class="lane-head"><b>${ln}ライン</b><span class="sub" data-sum></span></div>
      <div class="item colhead"><span></span><span>#</span><span>コード</span><span>製品名</span><span>バッチ</span><span>開始</span><span>間隔分</span><span>時刻</span><span>印</span><span></span></div>`;
    state.lanes[ln].forEach((it, idx) => lane.appendChild(rowEl(ln, idx, it)));
    const add = document.createElement("button");
    add.className = "add ghost";
    add.textContent = "＋ 製品を追加";
    add.onclick = () => {
      state.lanes[ln].push({ code: "", batches: "", start_min: null, gap_min: 0, memo: "" });
      setDirty(true);
      renderLanes();
      const rows = document.querySelectorAll(`.lane[data-line="${ln}"] .item[data-idx]`);
      rows[rows.length - 1]?.querySelector(".code")?.focus();
    };
    lane.appendChild(add);
    // 空いている所へのドロップ＝末尾に追加
    lane.addEventListener("dragover", (e) => { e.preventDefault(); lane.classList.add("drop"); });
    lane.addEventListener("dragleave", () => lane.classList.remove("drop"));
    lane.addEventListener("drop", (e) => {
      e.preventDefault();
      lane.classList.remove("drop");
      if (drag) moveItem(drag.line, drag.idx, ln, state.lanes[ln].length);
    });
    wrap.appendChild(lane);
  }
  updateComputed();
}

let drag = null;
function rowEl(ln, idx, it) {
  const row = document.createElement("div");
  row.className = "item";
  row.dataset.line = ln;
  row.dataset.idx = idx;
  row.innerHTML = `
    <span class="handle" title="ドラッグで移動">⋮⋮</span>
    <span class="seq">${idx + 1}</span>
    <input class="code" list="productList" value="${esc(it.code)}" placeholder="コード">
    <span class="pname"></span>
    <input class="batches" type="number" step="0.5" min="0" value="${esc(it.batches)}">
    <input class="start" value="${esc(fmt(it.start_min))}" placeholder="自動" title="空欄なら前の製品の終了から自動。例 7:10">
    <input class="gap" type="number" step="5" value="${it.gap_min ? esc(it.gap_min) : ""}" placeholder="0" title="前の製品との間隔（洗浄・切替など）">
    <span class="time"></span>
    <span class="chips"></span>
    <button class="del" title="削除">✕</button>`;
  const h = row.querySelector(".handle");
  h.addEventListener("mousedown", () => (row.draggable = true));
  row.addEventListener("dragstart", (e) => {
    drag = { line: ln, idx };
    row.classList.add("dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", `${ln}:${idx}`);
  });
  row.addEventListener("dragend", () => { row.draggable = false; row.classList.remove("dragging"); drag = null; });
  row.addEventListener("dragover", (e) => { e.preventDefault(); e.stopPropagation(); row.classList.add("over"); });
  row.addEventListener("dragleave", () => row.classList.remove("over"));
  row.addEventListener("drop", (e) => {
    e.preventDefault();
    e.stopPropagation();
    row.classList.remove("over");
    if (drag) moveItem(drag.line, drag.idx, ln, idx);
  });

  const onChange = () => { setDirty(true); updateRowStatic(row, state.lanes[ln][idx]); scheduleCheck(); };
  row.querySelector(".code").addEventListener("input", (e) => {
    state.lanes[ln][idx].code = e.target.value.trim().split(/\s+/)[0];
    onChange();
  });
  row.querySelector(".batches").addEventListener("input", (e) => { state.lanes[ln][idx].batches = e.target.value; onChange(); });
  row.querySelector(".gap").addEventListener("input", (e) => { state.lanes[ln][idx].gap_min = Number(e.target.value) || 0; onChange(); });
  row.querySelector(".start").addEventListener("change", (e) => {
    const t = parseTime(e.target.value);
    if (t === undefined) { toast("時刻は 7:10 や 25:30 の形で入力してください"); e.target.value = fmt(state.lanes[ln][idx].start_min); return; }
    state.lanes[ln][idx].start_min = t;
    e.target.value = fmt(t);
    onChange();
  });
  row.querySelector(".del").onclick = () => {
    state.lanes[ln].splice(idx, 1);
    setDirty(true);
    renderLanes();
    scheduleCheck();
  };
  updateRowStatic(row, it);
  return row;
}

function updateRowStatic(row, it) {
  const p = state.products[it.code];
  const nm = row.querySelector(".pname");
  nm.textContent = p ? p.name : it.code ? "マスタに無いコード" : "";
  nm.title = p ? `${p.name}\n${p.spec || ""}` : "";
  nm.classList.toggle("unknown", !!it.code && !p);
  row.querySelector(".chips").innerHTML = chips(p);
}

function moveItem(fromLine, fromIdx, toLine, toIdx) {
  const [it] = state.lanes[fromLine].splice(fromIdx, 1);
  if (fromLine === toLine && fromIdx < toIdx) toIdx--;
  // 時刻を固定していた製品は、移動後は自動計算に戻す
  if (fromLine !== toLine || fromIdx !== toIdx) it.start_min = null;
  state.lanes[toLine].splice(toIdx, 0, it);
  setDirty(true);
  renderLanes();
  scheduleCheck();
}

// ---------- チェック結果の反映 ----------
function applyResult(res) {
  state.result = res;
  renderDayInfo();
  renderRev();
  updateComputed();
  renderGauges();
  renderAlerts();
  renderTimeline();
}

function worstLevels() {
  const order = { NG: 0, 注意: 1, 情報: 2 };
  const map = {};
  for (const a of state.result?.alerts || []) {
    if (a.line == null || a.level === "情報") continue;
    const idx = state.rowMap[`${a.line}:${a.seq}`];
    const key = `${a.line}:${idx}`;
    if (!map[key] || order[a.level] < order[map[key]]) map[key] = a.level;
  }
  return map;
}

function updateComputed() {
  if (!state.result) return;
  const levels = worstLevels();
  const byKey = {};
  for (const s of state.result.schedule) byKey[`${s.line}:${state.rowMap[`${s.line}:${s.seq}`]}`] = s;
  document.querySelectorAll(".item[data-idx]").forEach((row) => {
    const key = `${row.dataset.line}:${row.dataset.idx}`;
    const s = byKey[key];
    row.querySelector(".time").textContent = s ? `${s.start_text}〜${s.end_text}` : "";
    row.classList.remove("NG", "注意");
    if (levels[key]) row.classList.add(levels[key]);
  });
  for (const ln of LINES) {
    const rows = state.result.schedule.filter((s) => s.line === ln);
    const sum = $(`.lane[data-line="${ln}"] [data-sum]`);
    if (!sum) continue;
    const b = rows.reduce((a, s) => a + s.batches, 0);
    sum.textContent = rows.length ? `${rows.length}品／${b}B／${rows[0].start_text}〜${rows[rows.length - 1].end_text}` : "稼働なし";
  }
}

function renderDayInfo() {
  const d = new Date(state.date + "T00:00:00");
  const info = state.result?.day || {};
  const tags = [];
  if (info.holiday_name) tags.push(info.holiday_name);
  if (info.is_holiday) tags.push("休日");
  if (info.is_saturday) tags.push("土曜");
  if (info.pre_long) tags.push("連休前");
  else if (info.pre_holiday) tags.push("休日前");
  if (info.post_long) tags.push("連休明け");
  $("#dayinfo").innerHTML = `<b>${d.getMonth() + 1}月${d.getDate()}日（${WEEK[(d.getDay() + 6) % 7]}）</b>` + tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("");
}
function renderRev() { $("#rev").textContent = `改-${state.meta.revision || 0}`; }

function renderGauges() {
  const g = state.result.gauges;
  $("#gauges").innerHTML = Object.entries(g).map(([k, v]) => {
    const pct = v.limit ? Math.min(100, (v.value / v.limit) * 100) : 0;
    const over = v.limit && v.value > v.limit;
    const note = v.missing ? `<div class="note">解凍kg 未設定 ${v.missing}品は含まれていません</div>` : "";
    return `<div class="gauge ${over ? "over" : ""}"><div class="lbl"><span>${esc(k)}</span><b>${Number(v.value).toLocaleString()} / ${Number(v.limit).toLocaleString()} ${esc(v.unit)}</b></div>
      <div class="track"><div class="fill" style="width:${pct}%"></div></div>${note}</div>`;
  }).join("");
}

function renderAlerts() {
  const a = state.result.alerts;
  const ng = a.filter((x) => x.level === "NG").length;
  const warn = a.filter((x) => x.level === "注意").length;
  $("#alertCount").textContent = `NG ${ng}／注意 ${warn}`;
  $("#alerts").innerHTML = a.length
    ? a.map((x) => `<li class="${x.level}"><span class="rule">${esc(x.rule)}</span>${esc(x.message)}</li>`).join("")
    : `<li class="情報">問題は見つかりませんでした</li>`;
}

function renderTimeline() {
  const t0 = 6 * 60, t1 = 30 * 60;
  const pct = (m) => Math.max(0, Math.min(100, ((m - t0) / (t1 - t0)) * 100));
  const levels = worstLevels();
  let html = `<div class="tl-row"><div class="tl-label"></div><div class="tl-track tl-ticks">`;
  for (let h = 6; h <= 30; h += 2) html += `<div class="tick" style="left:${pct(h * 60)}%">${h % 24}</div>`;
  html += `</div></div>`;
  const night = state.settings ? state.settings.night_start : 1020;
  for (const ln of LINES) {
    html += `<div class="tl-row"><div class="tl-label">${ln}</div><div class="tl-track"><div class="night" style="left:${pct(night)}%"></div>`;
    for (const s of state.result.schedule.filter((x) => x.line === ln)) {
      const p = state.products[s.code];
      const nm = p ? p.name : s.code;
      const lv = levels[`${ln}:${state.rowMap[`${ln}:${s.seq}`]}`] || "";
      html += `<div class="bar ${lv}" style="left:${pct(s.start)}%;width:${Math.max(0.5, pct(s.end) - pct(s.start))}%" title="${esc(nm)} ${s.batches}B ${s.start_text}〜${s.end_text}">${esc(nm)}</div>`;
    }
    html += `</div></div>`;
  }
  $("#timeline").innerHTML = html;
}

function renderManual() {
  $("#manual").innerHTML = state.manual.map(([k, label]) =>
    `<label><input type="checkbox" data-k="${esc(k)}" ${state.meta.checklist[k] ? "checked" : ""}> ${esc(label)}</label>`).join("");
  $("#manual").querySelectorAll("input").forEach((cb) => cb.addEventListener("change", () => {
    state.meta.checklist[cb.dataset.k] = cb.checked;
    setDirty(true);
  }));
}

// ---------- 製品マスタ ----------
const FLAG_COLS = [["asa_ichi", "朝一"], ["tanitsu", "単一"], ["last_special", "特殊(最後)"], ["cheese", "チーズ"], ["kaseijin", "火星人"], ["is_new", "新規"], ["wheat", "小麦"], ["milk", "乳"], ["egg", "卵"]];
const SELECT_COLS = {
  default_line: ["", ...LINES],
  rotiru: ["", "main", "derived"],
  drain_group: ["", "ロティール", "フジ食品"],
  dango_type: ["", "団子", "HB"],
};
const SELECT_LABEL = { main: "ロティール", derived: "派生(MB/パティ)" };

function renderMaster() {
  const q = $("#mSearch").value.trim().normalize("NFKC");
  const noThaw = $("#mNoThaw").checked;
  const list = Object.values(state.products).filter((p) =>
    (!q || (p.code + " " + p.name).normalize("NFKC").includes(q)) && (!noThaw || p.thaw_kg === null));
  const head = `<tr><th>コード</th><th>製品名</th><th>分/B</th><th>標準ライン</th><th>解凍kg/B</th>${FLAG_COLS.map((c) => `<th>${c[1]}</th>`).join("")}
    <th>ロティール区分</th><th>排水区分</th><th>形状</th><th>配合</th><th>包装形態</th><th>メモ</th></tr>`;
  const sel = (p, k) => `<select data-k="${k}">${SELECT_COLS[k].map((v) => `<option value="${v}" ${p[k] === v ? "selected" : ""}>${esc(SELECT_LABEL[v] || v || "－")}</option>`).join("")}</select>`;
  const body = list.map((p) => `<tr data-code="${esc(p.code)}">
    <td>${esc(p.code)}</td>
    <td><input class="name" data-k="name" value="${esc(p.name)}" title="${esc(p.spec || "")}"></td>
    <td><input type="number" data-k="batch_min" value="${p.batch_min ?? ""}"></td>
    <td>${sel(p, "default_line")}</td>
    <td><input type="number" data-k="thaw_kg" value="${p.thaw_kg ?? ""}" placeholder="未設定"></td>
    ${FLAG_COLS.map(([k]) => `<td><input type="checkbox" data-k="${k}" ${p[k] ? "checked" : ""}></td>`).join("")}
    <td>${sel(p, "rotiru")}</td><td>${sel(p, "drain_group")}</td><td>${sel(p, "dango_type")}</td>
    <td><input data-k="mix" value="${esc(p.mix)}" size="10"></td>
    <td><input data-k="packaging" value="${esc(p.packaging)}" size="8"></td>
    <td><input data-k="memo" value="${esc(p.memo)}" size="16"></td></tr>`).join("");
  $("#mTable").innerHTML = `<thead>${head}</thead><tbody>${body}</tbody>`;
}

$("#mTable").addEventListener("change", async (e) => {
  const el = e.target;
  const tr = el.closest("tr[data-code]");
  if (!tr || !el.dataset.k) return;
  const k = el.dataset.k, code = tr.dataset.code;
  let v = el.type === "checkbox" ? (el.checked ? 1 : 0) : el.value;
  if (el.type === "number") v = el.value === "" ? null : Number(el.value);
  try {
    await api("/api/products", { code, [k]: v });
    state.products[code][k] = v;
    refreshProductList();
    toast(`${code} を更新しました`, 1200);
    renderLanes();
    scheduleCheck();
  } catch (err) { toast("保存できませんでした：" + err.message); }
});
$("#mSearch").addEventListener("input", renderMaster);
$("#mNoThaw").addEventListener("change", renderMaster);
$("#mAdd").onclick = async () => {
  const code = prompt("製品コード");
  if (!code) return;
  if (state.products[code.trim()]) return toast("そのコードはすでにあります");
  const name = prompt("製品名") || "";
  const bm = Number(prompt("1バッチの時間（分）") || 0) || null;
  await api("/api/products", { code: code.trim(), name, batch_min: bm, is_new: 1 });
  await loadProducts();
  $("#mSearch").value = code.trim();
  renderMaster();
};

async function loadProducts() {
  const data = await api("/api/products");
  state.fields = data.fields;
  state.products = Object.fromEntries(data.products.map((p) => [p.code, p]));
  refreshProductList();
}
function refreshProductList() {
  $("#productList").innerHTML = Object.values(state.products)
    .map((p) => `<option value="${esc(p.code)}">${esc(p.name)}${p.default_line ? `（${p.default_line}）` : ""}</option>`).join("");
}

// ---------- 変更履歴 ----------
async function loadHistory() {
  const rows = await api("/api/changelog" + ($("#hOnlyDay").checked ? "?date=" + state.date : ""));
  $("#hTable").innerHTML = `<thead><tr><th>日時</th><th>対象日</th><th>担当</th><th>内容</th></tr></thead><tbody>` +
    rows.map((r) => `<tr><td>${esc(r.ts)}</td><td>${esc(r.date)}</td><td>${esc(r.user)}</td><td class="wrap">${esc(r.message)}</td></tr>`).join("") + "</tbody>";
}
$("#hOnlyDay").addEventListener("change", loadHistory);

// ---------- 設定 ----------
async function loadSettings() {
  const data = await api("/api/settings");
  state.settings = data.settings;
  state.holidays = data.holidays;
}
function renderSettings() {
  const s = state.settings;
  $("#sDayStart").value = fmt(s.day_start);
  $("#sNightStart").value = fmt(s.night_start);
  $("#sThaw").innerHTML = [...WEEK].map((w, i) => `<label>${w} <input data-wd="${i}" size="5" value="${s.thaw_caps[i] ?? 0}"></label>`).join("");
  $("#sMachine").value = s.machine_limit;
  $("#sDrainR").value = s.drain_rotiru;
  $("#sDrainRPre").value = s.drain_rotiru_pre_holiday;
  $("#sDrainRNight").value = s.drain_rotiru_night;
  $("#sDrainF").value = s.drain_fuji;
  $("#sExport").value = s.export_dir;
  renderHolidays();
}
function renderHolidays() {
  $("#hList").innerHTML = Object.entries(state.holidays).sort().map(([d, n]) =>
    `<li>${esc(d)} ${esc(n)}<button data-d="${esc(d)}">削除</button></li>`).join("") || "<li>なし</li>";
  $("#hList").querySelectorAll("button").forEach((b) => (b.onclick = () => { delete state.holidays[b.dataset.d]; renderHolidays(); }));
}
$("#hAdd").onclick = () => {
  const d = $("#hDate").value;
  if (!d) return;
  state.holidays[d] = $("#hName").value || "休日";
  $("#hName").value = "";
  renderHolidays();
};
$("#sSave").onclick = async () => {
  const ds = parseTime($("#sDayStart").value), ns = parseTime($("#sNightStart").value);
  if (ds == null || ns == null) return toast("時刻は 7:00 の形で入力してください");
  const caps = {};
  document.querySelectorAll("#sThaw input").forEach((i) => (caps[i.dataset.wd] = Number(i.value) || 0));
  const settings = {
    day_start: ds, night_start: ns, thaw_caps: caps,
    machine_limit: Number($("#sMachine").value), drain_rotiru: Number($("#sDrainR").value),
    drain_rotiru_pre_holiday: Number($("#sDrainRPre").value), drain_rotiru_night: Number($("#sDrainRNight").value),
    drain_fuji: Number($("#sDrainF").value), export_dir: $("#sExport").value.trim(),
  };
  await api("/api/settings", { settings, holidays: state.holidays });
  await loadSettings();
  toast("設定を保存しました");
  runCheck();
};

// ---------- 上部のボタン ----------
$("#date").addEventListener("change", (e) => e.target.value && loadDay(e.target.value));
$("#prev").onclick = () => loadDay(addDays(state.date, -1));
$("#next").onclick = () => loadDay(addDays(state.date, 1));
$("#save").onclick = () => save().catch((e) => toast("保存できませんでした：" + e.message));
$("#export").onclick = async () => {
  try {
    if (state.dirty) await save();
    const r = await api("/api/export", { date: state.date });
    toast("出力しました：" + r.path, 5000);
  } catch (e) { toast("出力できませんでした：" + e.message, 5000); }
};
$("#copyFrom").onclick = async () => {
  const from = prompt("コピー元の日付（例 2026-11-12）", addDays(state.date, -7));
  if (!from) return;
  const data = await api("/api/plan?date=" + from);
  if (!data.items.length) return toast("その日の予定はありません");
  if (flatItems().length && !confirm("今の予定を置き換えます。よろしいですか？")) return;
  state.lanes = { A: [], B: [], C: [], D: [] };
  for (const it of data.items) state.lanes[it.line].push({ ...it });
  setDirty(true);
  renderLanes();
  runCheck();
};
$("#memo").addEventListener("input", () => setDirty(true));
$("#user").value = store.get("user") || "";
$("#user").addEventListener("change", (e) => store.set("user", e.target.value));
window.addEventListener("beforeunload", (e) => { if (state.dirty) { e.preventDefault(); e.returnValue = ""; } });
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); $("#save").click(); }
});

// ---------- 起動 ----------
(async function init() {
  await Promise.all([loadProducts(), loadSettings()]);
  const dates = await api("/api/dates");
  const saved = new URLSearchParams(location.search).get("date") || store.get("date");
  await loadDay(saved || dates[dates.length - 1] || new Date().toISOString().slice(0, 10));
})().catch((e) => toast("起動に失敗しました：" + e.message, 8000));
