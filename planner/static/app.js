"use strict";
const LINES = ["A", "B", "C", "D"];
const WEEK = "月火水木金土日";
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const CIRCLED = "⓪①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳";
const revLabel = (r) => (r === 0 ? "初版" : "改-" + (CIRCLED[r] || r));

const state = {
  products: {},
  weeks: [],
  today: "",
  editable: false,
  week: null,     // { monday, title, dates, rev, snapshot }
  cells: {},      // "日付|ライン" → [{ code, batches }]
  tareFixed: {},  // "日付" → タレ欄の手入力の行 [{ code, name, kg }]（自動の行は毎回計算する）
  betten: {},     // "日付" → 別添タレ [{ code, kg, bags, bags_auto }]
  dirty: false,
};

// ---------- 共通 ----------
async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const res = await fetch(path, opt);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
function toast(msg, ms = 3000) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (t.hidden = true), ms);
}
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* 保存できなくても動く */ } },
};
const userName = () => $("#user").value.trim();
const md = (d) => `${Number(d.slice(5, 7))}/${Number(d.slice(8))}`;
function setDirty(v) {
  state.dirty = v;
  $("#dirty").hidden = !v;
}
function csOf(code, b) {
  const y = state.products[code]?.yield_per_b;
  return y && b ? Math.round(Number(b) * y) : null;
}

// ---------- タブ ----------
document.querySelectorAll(".tabs button").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("on", x === b));
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("on", t.id === "tab-" + b.dataset.tab));
    if (b.dataset.tab === "day") openDayTab().catch((e) => toast("作業予定を開けませんでした：" + e.message));
    if (b.dataset.tab === "master") renderMaster();
    if (b.dataset.tab === "history") loadHistory();
  })
);

// ---------- 編集の鍵 ----------
function applyLock(lock) {
  state.editable = lock.editable;
  document.body.classList.toggle("readonly", !lock.editable);
  const bar = $("#lockbar");
  if (lock.editable) {
    bar.hidden = true;
  } else {
    const h = lock.holder;
    bar.hidden = false;
    bar.innerHTML = h
      ? `<b>閲覧のみ</b> ${esc(h.name || h.user)}さん（${esc(h.host)}）が編集中です。<button id="take">編集を引き継ぐ</button>`
      : `<b>閲覧のみ</b> 編集の鍵を取れていません。<button id="take">編集を始める</button>`;
    $("#take").onclick = async () => {
      if (h && !confirm(`${h.name || h.user}さんが編集中です。引き継ぐと、相手は保存できなくなります。よろしいですか？`)) return;
      const s = await api("/api/lock/take", { user: userName() });
      applyLock(s);
      if (s.editable) {
        toast("編集できるようになりました");
        await loadWeek(state.week.monday, true);
        if (dayState.date) await loadDay(dayState.date, true);
      }
    };
  }
  document.querySelectorAll("#grid input, #mTable input, #dayLines input").forEach((i) => (i.disabled = !state.editable));
}

// ---------- 週の一覧 ----------
async function loadState() {
  const s = await api("/api/state");
  state.weeks = s.weeks;
  state.today = s.today;
  state.outputDir = s.output_dir;
  state.distribution = s.distribution;
  applyLock(s.lock);
  renderWeekList();
}
function renderWeekList() {
  const thisMonday = mondayOf(state.today);
  $("#weekList").innerHTML = state.weeks.map((w) => {
    let st = w.count ? `${w.count}件` : "空";
    if (w.rev === null) st += "・未発行";
    else st += "・" + revLabel(w.rev);
    const warn = w.changed_since_issue || (w.rev === null && w.count);
    return `<li data-m="${w.monday}" class="${w.monday === state.week?.monday ? "on" : ""} ${w.monday < thisMonday ? "past" : ""}">
      <span class="wt">${esc(w.title)}<span class="wd">${md(w.monday)}〜</span></span><span class="st ${warn ? "warn" : ""}">${esc(st)}${w.changed_since_issue ? "・変更あり" : ""}</span></li>`;
  }).join("");
  $("#weekList").querySelectorAll("li").forEach((li) => (li.onclick = () => loadWeek(li.dataset.m)));
}
function mondayOf(d) {
  const x = new Date(d + "T00:00:00");
  x.setDate(x.getDate() - ((x.getDay() + 6) % 7));
  return iso(x);
}
function iso(x) {
  return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`;
}
function addWeeks(monday, n) {
  const x = new Date(monday + "T00:00:00");
  x.setDate(x.getDate() + 7 * n);
  return iso(x);
}

// ---------- 週間予定 ----------
async function loadWeek(monday, force = false) {
  if (!force && state.dirty && !confirm("保存していない変更があります。破棄して移動しますか？")) return;
  const w = await api("/api/week?monday=" + monday);
  state.week = w;
  state.cells = {};
  for (const d of w.dates) for (const ln of LINES) state.cells[`${d}|${ln}`] = [];
  for (const it of w.items) state.cells[`${it.date}|${it.line}`].push({ code: it.code, batches: it.batches });
  state.tareFixed = {};
  for (const d of w.dates) state.tareFixed[d] = [];
  for (const t of w.tare) if (!t.auto) state.tareFixed[t.date].push({ code: t.code, name: t.name, kg: t.kg });
  state.betten = {};
  for (const d of w.dates) state.betten[d] = [];
  for (const b of w.betten) state.betten[b.date].push({ code: b.code, kg: b.kg, bags: b.bags, bags_auto: b.bags_auto });
  store.set("week", monday);
  setDirty(false);
  $("#weekTitle").textContent = `${w.title}（${md(w.dates[0])}〜${md(w.dates[w.dates.length - 1])}）`;
  renderRevInfo();
  renderGrid();
  renderWeekList();
}

function renderRevInfo() {
  const r = state.week.rev;
  $("#revInfo").textContent = r ? `${revLabel(r.rev)}（${r.issued_at.slice(5, 10).replace("-", "/")} ${esc(r.user || "")} 発行）` : "未発行";
}

function flatItems() {
  const items = [];
  for (const d of state.week.dates)
    for (const ln of LINES)
      for (const e of state.cells[`${d}|${ln}`]) items.push({ date: d, line: ln, code: e.code, batches: Number(e.batches) });
  return items.filter((i) => i.code && i.batches > 0);
}

// ---------- タレ欄 ----------
// その日のライン明細のうち「内添タレ kg/B」がある製品は B × kg/B を自動の行にする（サーバーの build_tare と同じ考え方）
function buildTare(date) {
  const fixed = state.tareFixed[date] || [];
  const fixedCodes = new Set(fixed.filter((t) => t.code).map((t) => t.code));
  const auto = new Map();
  for (const ln of LINES) {
    for (const e of state.cells[`${date}|${ln}`]) {
      const p = state.products[e.code];
      if (!p?.tare_kg_per_b || fixedCodes.has(e.code) || !(Number(e.batches) > 0)) continue;
      if (!auto.has(e.code)) auto.set(e.code, { date, code: e.code, name: p.name, kg: 0, auto: true });
      auto.get(e.code).kg += Number(e.batches) * p.tare_kg_per_b;
    }
  }
  for (const t of auto.values()) t.kg = Math.round(t.kg * 10) / 10;
  return [...auto.values(), ...fixed.map((t, i) => ({ ...t, date, auto: false, idx: i }))];
}
const tareKey = (t) => `${t.date}|タレ|${t.code || t.name}`;
const tareName = (t) => state.products[t.code]?.name || t.name || t.code;
const fmtKg = (kg) => `${Number(kg).toLocaleString()}kg`;

function renderTareCell(date) {
  const cellEl = $(`.tcell[data-date="${date}"]`);
  if (!cellEl) return;
  const dis = state.editable ? "" : "disabled";
  const rows = buildTare(date);
  cellEl.innerHTML = rows.map((t) => t.auto
    ? `<div class="tentry auto" data-key="${esc(tareKey(t))}" data-code="${esc(t.code)}">
         <span class="tname" title="${esc(t.code)} ${esc(t.name)}：B × ${state.products[t.code].tare_kg_per_b}kg/B">${esc(t.name)}</span>
         <span class="chip" title="ライン明細から自動計算">自動</span>
         <input class="kg" type="number" step="1" min="0" value="${t.kg}" title="直すと手入力に変わります" ${dis}>
       </div>`
    : `<div class="tentry manual" data-key="${esc(tareKey(t))}" data-idx="${t.idx}">
         <input class="tcode" list="productList" value="${esc(t.code ? `${t.code} ${tareName(t)}` : t.name)}" title="${esc(t.code ? `${t.code} ${tareName(t)}` : t.name)}" placeholder="品名かコード" ${dis}>
         <span class="chip manual" title="${t.code && state.products[t.code] ? esc(tareName(t)) : "手入力"}">手</span>
         <input class="kg" type="number" step="1" min="0" value="${esc(t.kg)}" placeholder="kg" ${dis}>
         <button class="del edit-only" title="削除（製品があれば自動の行に戻ります）">✕</button>
       </div>`).join("") +
    `<div class="cellfoot"><button class="add edit-only">＋ 追加</button><span class="sum">${rows.length ? "計 " + fmtKg(rows.reduce((a, t) => a + (Number(t.kg) || 0), 0)) : ""}</span></div>`;

  const fixed = state.tareFixed[date];
  cellEl.querySelectorAll(".tentry.auto .kg").forEach((inp) => inp.addEventListener("change", () => {
    const code = inp.closest(".tentry").dataset.code;
    fixed.push({ code, name: state.products[code]?.name || "", kg: Number(inp.value) || 0 });
    tareChanged(date);
  }));
  cellEl.querySelectorAll(".tentry:not(.auto)").forEach((row) => {
    const t = fixed[row.dataset.idx];
    row.querySelector(".tcode").addEventListener("change", (ev) => {
      const v = ev.target.value.trim();
      const code = v.split(/\s+/)[0];
      if (state.products[code]) { t.code = code; t.name = state.products[code].name; }
      else { t.code = ""; t.name = v; }
      tareChanged(date);
    });
    row.querySelector(".kg").addEventListener("input", (ev) => { t.kg = ev.target.value; setDirty(true); updateTareSum(cellEl, date); markChanges(); });
    row.querySelector(".del").onclick = () => { fixed.splice(row.dataset.idx, 1); tareChanged(date); };
  });
  cellEl.querySelector(".add").onclick = () => {
    fixed.push({ code: "", name: "", kg: "" });
    tareChanged(date);
    const inputs = cellEl.querySelectorAll(".tcode");
    inputs[inputs.length - 1]?.focus();
  };
}
function updateTareSum(cellEl, date) {
  const rows = buildTare(date);
  cellEl.querySelector(".sum").textContent = rows.length ? "計 " + fmtKg(rows.reduce((a, t) => a + (Number(t.kg) || 0), 0)) : "";
}
function tareChanged(date) {
  setDirty(true);
  renderTareCell(date);
  markChanges();
}
function flatTare() {
  return state.week.dates.flatMap((d) => (state.tareFixed[d] || [])
    .filter((t) => (t.code || t.name) && Number(t.kg) > 0)
    .map((t) => ({ date: d, code: t.code, name: t.name, kg: Number(t.kg) })));
}

// ---------- 別添タレ ----------
// 半製品コード（89xxx）と kg を入れると、使う製品名と袋数（kg × 袋/kg）を出す。袋数は手で直せる
const betKey = (date, b) => `${date}|別添|${b.code}`;
function betBags(b) {
  if (!b.bags_auto) return b.bags;
  const r = state.products[b.code]?.bags_per_kg;
  return r && Number(b.kg) > 0 ? Math.round(Number(b.kg) * r) : null;
}
function betTarget(code) {
  const p = state.products[code];
  if (!p) return code ? "マスタに無いコード" : "";
  return state.products[p.betten_for]?.name || p.name;
}
function renderBettenCell(date) {
  const cellEl = $(`.bcell[data-date="${date}"]`);
  if (!cellEl) return;
  const dis = state.editable ? "" : "disabled";
  const list = state.betten[date];
  cellEl.innerHTML = list.map((b, i) => {
    const bags = betBags(b);
    return `<div class="bentry" data-key="${esc(betKey(date, b))}" data-idx="${i}">
      <input class="bcode" list="bettenList" value="${esc(b.code)}" placeholder="89xxx" ${dis}>
      <span class="tname" title="${esc(state.products[b.code]?.name || "")}">→${esc(betTarget(b.code))}</span>
      <button class="del edit-only" title="削除">✕</button>
      <span class="qty"><input class="kg" type="number" step="10" min="0" value="${esc(b.kg)}" title="タレ kg" ${dis}><small>kg</small>
      <input class="bags ${b.bags_auto ? "auto" : ""}" type="number" step="1" min="0" value="${bags ?? ""}"
        title="${b.bags_auto ? "kg × 袋/kg で自動計算（直すと手入力）" : "手入力（空にすると自動に戻る）"}" ${dis}><small>袋${b.bags_auto ? "" : "（手）"}</small></span>
      </div>`;
  }).join("") + `<div class="cellfoot"><button class="add edit-only">＋ 追加</button><span class="sum"></span></div>`;
  cellEl.querySelectorAll(".bentry").forEach((row) => {
    const b = list[row.dataset.idx];
    const refresh = () => {
      row.querySelector(".tname").textContent = "→" + betTarget(b.code);
      if (b.bags_auto) row.querySelector(".bags").value = betBags(b) ?? "";
      setDirty(true);
      markChanges();
    };
    row.querySelector(".bcode").addEventListener("input", (ev) => { b.code = ev.target.value.trim().split(/\s+/)[0]; refresh(); });
    row.querySelector(".kg").addEventListener("input", (ev) => { b.kg = ev.target.value; refresh(); });
    row.querySelector(".bags").addEventListener("change", (ev) => {
      b.bags_auto = ev.target.value === "";
      b.bags = b.bags_auto ? null : Number(ev.target.value);
      renderBettenCell(date);
      setDirty(true);
      markChanges();
    });
    row.querySelector(".del").onclick = () => { list.splice(row.dataset.idx, 1); renderBettenCell(date); setDirty(true); markChanges(); };
  });
  cellEl.querySelector(".add").onclick = () => {
    list.push({ code: "", kg: "", bags: null, bags_auto: true });
    renderBettenCell(date);
    const inputs = cellEl.querySelectorAll(".bcode");
    inputs[inputs.length - 1]?.focus();
  };
}
function flatBetten() {
  return state.week.dates.flatMap((d) => state.betten[d]
    .filter((b) => b.code && Number(b.kg) > 0)
    .map((b) => ({ date: d, code: b.code, kg: Number(b.kg), bags: b.bags_auto ? null : b.bags, bags_auto: b.bags_auto })));
}

// 前回の発行からの差分（赤字にするキーと、削除された明細）
function changes() {
  const snap = state.week.snapshot;
  if (!snap) return { keys: new Set(), removed: [] };
  const legacy = Array.isArray(snap); // タレ欄を作る前の控え（タレは比べない）
  const oldItems = legacy ? snap : snap.items;
  const tot = (list) => list.reduce((m, i) => m.set(`${i.date}|${i.line}|${i.code}`, (m.get(`${i.date}|${i.line}|${i.code}`) || 0) + Number(i.batches)), new Map());
  const o = tot(oldItems), n = tot(flatItems());
  const keys = new Set([...n].filter(([k, v]) => o.get(k) !== v).map(([k]) => k));
  const removed = [...o].filter(([k]) => !n.has(k)).map(([k, v]) => {
    const [d, ln, code] = k.split("|");
    return `${md(d)} ${ln}ライン ${state.products[code]?.name || code} ${v}B`;
  });
  if (!legacy) {
    const ot = new Map(snap.tare.map((t) => [tareKey(t), t]));
    const nt = new Map(state.week.dates.flatMap((d) => buildTare(d)).map((t) => [tareKey(t), t]));
    for (const [k, t] of nt) if (!ot.has(k) || Number(ot.get(k).kg) !== Number(t.kg)) keys.add(k);
    for (const [k, t] of ot) if (!nt.has(k)) removed.push(`${md(t.date)} タレ ${tareName(t)} ${fmtKg(t.kg)}`);
  }
  if (!legacy && snap.betten) {
    const ob = new Map(snap.betten.map((b) => [betKey(b.date, b), b]));
    const nb = new Map(flatBetten().map((b) => [betKey(b.date, b), { ...b, bags: betBags(b) }]));
    for (const [k, b] of nb) {
      const o = ob.get(k);
      if (!o || Number(o.kg) !== Number(b.kg) || Number(o.bags || 0) !== Number(b.bags || 0)) keys.add(k);
    }
    for (const [k, b] of ob) if (!nb.has(k)) removed.push(`${md(b.date)} 別添タレ ${b.code} →${betTarget(b.code)} ${fmtKg(b.kg)}`);
  }
  return { keys, removed };
}

function renderGrid() {
  const { dates } = state.week;
  const head = `<thead><tr><th class="day">日付</th>${LINES.map((l) => `<th>${l}ライン</th>`).join("")}<th class="tarecol">タレ</th><th class="betcol">別添タレ</th></tr></thead>`;
  const rows = dates.map((d, i) => {
    const dd = new Date(d + "T00:00:00");
    const cells = LINES.map((ln) => `<td><div class="cell" data-key="${d}|${ln}"></div></td>`).join("");
    return `<tr class="${i === 5 ? "sat" : ""}"><th class="day"><div class="d">${dd.getDate()}</div><div>（${WEEK[i]}）</div></th>${cells}<td><div class="tcell" data-date="${d}"></div></td><td><div class="bcell" data-date="${d}"></div></td></tr>`;
  }).join("");
  $("#grid").innerHTML = head + "<tbody>" + rows + "</tbody>";
  document.querySelectorAll("#grid .cell").forEach(renderCell);
  dates.forEach(renderTareCell);
  dates.forEach(renderBettenCell);
  markChanges();
}

function renderCell(cellEl) {
  const key = cellEl.dataset.key;
  const list = state.cells[key];
  cellEl.innerHTML = "";
  list.forEach((e, idx) => cellEl.appendChild(entryEl(key, idx, e)));
  const foot = document.createElement("div");
  foot.className = "cellfoot";
  foot.innerHTML = `<button class="add edit-only">＋ 追加</button><span class="sum"></span>`;
  foot.querySelector(".add").onclick = () => {
    list.push({ code: "", batches: "" });
    setDirty(true);
    renderCell(cellEl);
    cellEl.querySelectorAll(".entry .code")[list.length - 1].focus();
  };
  cellEl.appendChild(foot);
  updateSum(cellEl);
  // マスの空き部分へのドロップ＝末尾に移動
  cellEl.ondragover = (ev) => { if (drag) { ev.preventDefault(); cellEl.classList.add("drop"); } };
  cellEl.ondragleave = () => cellEl.classList.remove("drop");
  cellEl.ondrop = (ev) => { ev.preventDefault(); cellEl.classList.remove("drop"); if (drag) moveEntry(drag, key, list.length); };
}

function updateSum(cellEl) {
  const list = state.cells[cellEl.dataset.key];
  const b = list.reduce((a, e) => a + (Number(e.batches) || 0), 0);
  const cs = list.reduce((a, e) => a + (csOf(e.code, e.batches) || 0), 0);
  cellEl.querySelector(".sum").textContent = list.length ? `計 ${b}B／${cs.toLocaleString()}cs` : "";
}

let drag = null;
function entryEl(key, idx, e) {
  const el = document.createElement("div");
  el.className = "entry";
  el.dataset.key = key;
  el.dataset.idx = idx;
  const dis = state.editable ? "" : "disabled";
  el.innerHTML = `<span class="handle edit-only" title="ドラッグで移動">⋮⋮</span>
    <input class="code" list="productList" value="${esc(e.code)}" placeholder="コード" ${dis}>
    <span class="name"></span>
    <input class="b" type="number" step="0.25" min="0" value="${esc(e.batches)}" placeholder="B" ${dis}>
    <span class="cs"></span>
    <button class="del edit-only" title="削除">✕</button>`;
  const refresh = () => {
    const p = state.products[e.code];
    const nm = el.querySelector(".name");
    nm.textContent = p ? p.name : e.code ? "マスタに無いコード" : "";
    nm.title = p ? `${p.code} ${p.name}（${p.packaging || ""}）出来高 ${p.yield_per_b ?? "未設定"}cs/B` : "";
    nm.classList.toggle("unknown", !!e.code && !p);
    const cs = csOf(e.code, e.batches);
    el.querySelector(".cs").textContent = cs === null ? (e.code && e.batches ? "?" : "") : `${cs}cs`;
  };
  el.querySelector(".code").addEventListener("input", (ev) => {
    e.code = ev.target.value.trim().split(/\s+/)[0];
    refresh(); changed(el);
  });
  el.querySelector(".b").addEventListener("input", (ev) => { e.batches = ev.target.value; refresh(); changed(el); });
  el.querySelector(".del").onclick = () => {
    state.cells[key].splice(idx, 1);
    setDirty(true);
    renderCell(el.closest(".cell"));
    renderTareCell(key.split("|")[0]);
    markChanges();
  };
  const h = el.querySelector(".handle");
  h.addEventListener("mousedown", () => (el.draggable = true));
  el.addEventListener("dragstart", (ev) => { drag = { key, idx }; el.classList.add("dragging"); ev.dataTransfer.setData("text/plain", key); });
  el.addEventListener("dragend", () => { el.draggable = false; el.classList.remove("dragging"); drag = null; });
  el.addEventListener("dragover", (ev) => { if (drag) { ev.preventDefault(); ev.stopPropagation(); el.classList.add("over"); } });
  el.addEventListener("dragleave", () => el.classList.remove("over"));
  el.addEventListener("drop", (ev) => { ev.preventDefault(); ev.stopPropagation(); el.classList.remove("over"); if (drag) moveEntry(drag, key, idx); });
  refresh();
  return el;
}

function changed(entry) {
  setDirty(true);
  updateSum(entry.closest(".cell"));
  renderTareCell(entry.dataset.key.split("|")[0]); // B数・製品が変わるとタレの自動の行も変わる
  markChanges();
}

function moveEntry(from, toKey, toIdx) {
  const src = state.cells[from.key];
  const [e] = src.splice(from.idx, 1);
  if (from.key === toKey && from.idx < toIdx) toIdx--;
  state.cells[toKey].splice(toIdx, 0, e);
  setDirty(true);
  renderCell($(`.cell[data-key="${from.key}"]`));
  if (toKey !== from.key) renderCell($(`.cell[data-key="${toKey}"]`));
  renderTareCell(from.key.split("|")[0]);
  renderTareCell(toKey.split("|")[0]);
  markChanges();
}

function markChanges() {
  const { keys, removed } = changes();
  document.querySelectorAll("#grid .entry").forEach((el) => {
    const e = state.cells[el.dataset.key][el.dataset.idx];
    el.classList.toggle("changed", !!e && keys.has(`${el.dataset.key}|${e.code}`));
  });
  document.querySelectorAll("#grid .tentry, #grid .bentry").forEach((el) => el.classList.toggle("changed", keys.has(el.dataset.key)));
  const box = $("#removed");
  box.hidden = !removed.length;
  box.innerHTML = removed.length
    ? `<b>前回の発行から削除した明細</b><ul>${removed.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>`
    : "";
}

// ---------- 保存・発行 ----------
async function save() {
  const bad = Object.values(state.cells).flat().filter((e) => (e.code && !(Number(e.batches) > 0)) || (!e.code && e.batches));
  if (bad.length && !confirm(`コードかB数が空の明細が ${bad.length} 件あります。この明細は保存されません。続けますか？`)) return false;
  const unknown = flatItems().filter((i) => !state.products[i.code]);
  if (unknown.length && !confirm(`製品マスタに無いコードがあります（${[...new Set(unknown.map((i) => i.code))].join("、")}）。このまま保存しますか？`)) return false;
  const r = await api("/api/week", { monday: state.week.monday, user: userName(), items: flatItems(), tare: flatTare(), betten: flatBetten() });
  await loadWeek(state.week.monday, true);
  await refreshWeeks();
  toast(r.changes.length ? `保存しました（${r.changes.length}件の変更）` : "保存しました（変更なし）");
  return true;
}
async function refreshWeeks() {
  const s = await api("/api/state");
  state.weeks = s.weeks;
  applyLock(s.lock);
  renderWeekList();
}
async function issue() {
  if (state.dirty && !(await save())) return;
  const r0 = state.week.rev;
  const msg = r0 ? `${revLabel(r0.rev)} からの変更を発行して、配布用Excelを出力します。` : "初版として発行して、配布用Excelを出力します。";
  if (!confirm(msg + "\n出力先：" + state.outputDir)) return;
  const r = await api("/api/issue", { monday: state.week.monday, user: userName() });
  await loadWeek(state.week.monday, true);
  await refreshWeeks();
  toast((r.note ? r.note + "\n" : `${revLabel(r.rev)} を発行しました（変更 ${r.changes.length}件）\n`) + r.path, 7000);
}
// ---------- 印刷（A3縦） ----------
// 配布用Excelと同じ並び（日付 × A〜Dライン［B・品名・コード・数量］＋タレ＋別添タレ）で、今の画面の内容を印刷する。
// 赤字＝前回の発行から変わった明細（画面と同じ）
function printWeek() {
  const w = state.week;
  const { keys, removed } = changes();
  // 数字の列（n）は折り返さない。赤字＝前回の発行から変わった明細
  const td = (v, k, num) => `<td class="${num ? "n" : ""} ${k && keys.has(k) ? "red" : ""}">${v}</td>`;
  const m = new Date(w.monday + "T00:00:00");
  const rev = w.rev ? `${revLabel(w.rev.rev)}（${md(w.rev.issued_at.slice(0, 10))} 発行）` : "未発行";
  const now = new Date();
  const body = w.dates.map((d, di) => {
    const per = Object.fromEntries(LINES.map((ln) => [ln, state.cells[`${d}|${ln}`].filter((e) => e.code && Number(e.batches) > 0)]));
    const tare = buildTare(d).filter((t) => (t.code || t.name) && Number(t.kg) > 0);
    const bet = state.betten[d].filter((b) => b.code && Number(b.kg) > 0);
    const n = Math.max(2, tare.length + 1, bet.length, ...LINES.map((ln) => per[ln].length + 1)); // 最後の1行は合計
    const dd = new Date(d + "T00:00:00");
    const rows = [];
    for (let i = 0; i < n; i++) {
      const last = i === n - 1;
      let tr = i === 0 ? `<th class="pdate" rowspan="${n}">${dd.getDate()}<br><small>${WEEK[di]}</small></th>` : "";
      for (const ln of LINES) {
        const e = per[ln][i];
        if (e) {
          const k = `${d}|${ln}|${e.code}`;
          tr += td(fmtB(e.batches), k, 1) + td(esc(state.products[e.code]?.name || e.code), k) + td(esc(e.code), k, 1) + td(csOf(e.code, e.batches)?.toLocaleString() ?? "", k, 1);
        } else if (last && per[ln].length) {
          tr += `<td class="sum" colspan="4">計 ${fmtB(per[ln].reduce((a, x) => a + Number(x.batches), 0))}B</td>`;
        } else tr += "<td></td><td></td><td></td><td></td>";
      }
      const t = tare[i];
      if (t) tr += td(esc(tareName(t)), tareKey(t)) + td(fmtKg(t.kg), tareKey(t), 1);
      else if (last && tare.length) tr += `<td class="sum" colspan="2">計 ${fmtKg(tare.reduce((a, x) => a + Number(x.kg), 0))}</td>`;
      else tr += "<td></td><td></td>";
      const b = bet[i];
      if (b) {
        const k = betKey(d, b), bags = betBags(b);
        tr += td(`${esc(b.code)} →${esc(betTarget(b.code))}`, k) + td(fmtKg(b.kg), k, 1) + td(bags ? bags.toLocaleString() + "袋" : "", k, 1);
      } else tr += "<td></td><td></td><td></td>";
      rows.push(`<tr>${tr}</tr>`);
    }
    return `<tbody class="${di === 5 ? "sat" : ""}">${rows.join("")}</tbody>`;
  }).join("");
  // 列の幅（mm の目安。A3縦の印刷幅 約281mm に合わせる）
  const widths = [8, ...LINES.flatMap(() => [9, 23, 9, 9]), 18, 11, 19, 12, 12];
  const total = widths.reduce((a, x) => a + x, 0);
  printHtml("week", `
    <div class="ptop"><span>${esc(state.distribution || "")}</span><span>印刷 ${now.getMonth() + 1}/${now.getDate()} ${now.getHours()}:${String(now.getMinutes()).padStart(2, "0")}${state.dirty ? "（未保存の変更を含む）" : ""}</span></div>
    <h1>週　間　製　造　予　定　表<small>（変更箇所　赤色）</small></h1>
    <div class="psub"><b>${m.getMonth() + 1}月　『　第　${(m.getDate() - 1) / 7 + 1 | 0}　週　』</b><span>${md(w.dates[0])}　～　${md(w.dates[w.dates.length - 1])}</span><b>${esc(rev)}</b></div>
    <table class="ptable">
      <colgroup>${widths.map((x) => `<col style="width:${(x / total * 100).toFixed(2)}%">`).join("")}</colgroup>
      <thead>
        <tr><th rowspan="2">日付</th>${LINES.map((ln) => `<th colspan="4">${ln}ライン</th>`).join("")}<th colspan="2">タレ</th><th colspan="3">別添タレ</th></tr>
        <tr>${LINES.map(() => "<th>B</th><th>品　名</th><th>コード</th><th>数量</th>").join("")}<th>品　名</th><th>数量</th><th>品名（→製品）</th><th>タレ</th><th>包装機</th></tr>
      </thead>
      ${body}
    </table>
    ${removed.length ? `<div class="premoved"><b>■ 前回の発行から削除した明細</b>${removed.map((r) => `<div>${esc(r)}</div>`).join("")}</div>` : ""}`);
}
// 印刷用の内容を #printArea に入れて印刷する。kind で用紙を変える（week＝A3縦、zensho＝A4縦、seikei＝A3横。style.css の @page）
function printHtml(kind, html) {
  $("#printArea").className = kind;
  $("#printArea").innerHTML = html;
  document.body.classList.add("printing");
  window.print();
}
window.addEventListener("afterprint", () => document.body.classList.remove("printing"));
$("#print").onclick = printWeek;
$("#save").onclick = () => save().catch((e) => toast("保存できませんでした：" + e.message));
$("#issue").onclick = () => issue().catch((e) => toast("発行できませんでした：" + e.message, 7000));
$("#prev").onclick = () => loadWeek(addWeeks(state.week.monday, -1));
$("#next").onclick = () => loadWeek(addWeeks(state.week.monday, 1));
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "s") {
    e.preventDefault();
    if (!state.editable) return;
    if ($("#tab-day").classList.contains("on")) $("#dSave").click();
    else if ($("#tab-week").classList.contains("on")) $("#save").click();
  }
});
window.addEventListener("beforeunload", (e) => { if (state.dirty || dayState.dirty) { e.preventDefault(); e.returnValue = ""; } });

// ---------- 製品マスタ ----------
async function loadProducts() {
  const list = await api("/api/products");
  state.products = Object.fromEntries(list.map((p) => [p.code, p]));
  $("#productList").innerHTML = list.map((p) => `<option value="${esc(p.code)}">${esc(p.name)}</option>`).join("");
  $("#bettenList").innerHTML = list.filter((p) => p.betten_for)
    .map((p) => `<option value="${esc(p.code)}">→${esc(state.products[p.betten_for]?.name || p.name)}</option>`).join("");
}
function renderMaster() {
  const q = $("#mSearch").value.trim().normalize("NFKC");
  const only = $("#mNoYield").checked;
  const dis = state.editable ? "" : "disabled";
  const rows = Object.values(state.products)
    .filter((p) => (!q || `${p.code} ${p.name}`.normalize("NFKC").includes(q)) && (!only || p.yield_per_b == null))
    .map((p) => `<tr data-code="${esc(p.code)}"><td>${esc(p.code)}</td>
      <td><input class="name" data-k="name" value="${esc(p.name)}" ${dis}></td>
      <td><input class="num" type="number" data-k="yield_per_b" value="${p.yield_per_b ?? ""}" placeholder="未設定" ${dis}></td>
      <td><input class="num" type="number" data-k="tare_kg_per_b" value="${p.tare_kg_per_b ?? ""}" placeholder="なし" ${dis}></td>
      <td><input class="num" data-k="betten_for" value="${esc(p.betten_for ?? "")}" placeholder="－" title="別添タレの半製品なら、使う製品のコード" ${dis}></td>
      <td><input class="num" type="number" step="0.01" data-k="bags_per_kg" value="${p.bags_per_kg ?? ""}" placeholder="－" ${dis}></td>
      <td><input data-k="packaging" value="${esc(p.packaging)}" size="10" ${dis}></td>
      <td><input class="num" type="number" data-k="batch_min" value="${p.batch_min ?? ""}" placeholder="未設定" ${dis}></td>
      <td><input class="num" type="number" data-k="shot" value="${p.shot ?? ""}" ${dis}></td>
      <td><input data-k="mix" value="${esc(p.mix ?? "")}" size="12" ${dis}></td>
      <td><input data-k="mold" value="${esc(p.mold ?? "")}" size="18" ${dis}></td>
      <td><input data-k="memo" value="${esc(p.memo)}" size="30" ${dis}></td></tr>`).join("");
  $("#mTable").innerHTML = `<thead><tr><th>コード</th><th>製品名</th><th>出来高/B (cs)</th><th title="ラインに入れると、タレ欄に B × この値 を自動で出します">内添タレ kg/B</th><th title="別添タレの半製品（89xxx）が使われる製品">別添→製品</th><th title="別添タレ 1kgあたりの袋数">袋/kg</th><th>包装形態</th><th title="作業予定の終了時刻の計算に使います">分/B</th><th>ショット</th><th>配合</th><th>モールド板</th><th>メモ</th></tr></thead><tbody>${rows}</tbody>`;
}
$("#mTable").addEventListener("change", async (ev) => {
  const el = ev.target, tr = el.closest("tr[data-code]");
  if (!tr || !el.dataset.k) return;
  const code = tr.dataset.code, k = el.dataset.k;
  const v = el.type === "number" ? (el.value === "" ? null : Number(el.value)) : el.value;
  try {
    await api("/api/products", { code, [k]: v });
    state.products[code][k] = v;
    toast(`${code} を更新しました`, 1200);
    if (dayState.date && dayState.data) renderLines();
    if (state.week) {
      document.querySelectorAll("#grid .cell").forEach(renderCell);
      state.week.dates.forEach(renderTareCell);
      state.week.dates.forEach(renderBettenCell);
      markChanges();
    }
  } catch (e) { toast("保存できませんでした：" + e.message); }
});
$("#mSearch").addEventListener("input", renderMaster);
$("#mNoYield").addEventListener("change", renderMaster);
$("#mAdd").onclick = async () => {
  const code = (prompt("製品コード") || "").trim();
  if (!code) return;
  if (state.products[code]) return toast("そのコードはすでにあります");
  const name = prompt("製品名") || code;
  const y = Number(prompt("出来高/B（cs）") || "") || null;
  await api("/api/products", { code, name, yield_per_b: y });
  await loadProducts();
  $("#mSearch").value = code;
  renderMaster();
};

// ---------- 変更履歴 ----------
async function loadHistory() {
  const q = $("#hOnlyWeek").checked && state.week ? "?week=" + state.week.monday : "";
  const rows = await api("/api/changelog" + q);
  $("#hTable").innerHTML = `<thead><tr><th>日時</th><th>週</th><th>担当</th><th>内容</th></tr></thead><tbody>` +
    rows.map((r) => `<tr><td>${esc(r.ts)}</td><td>${md(r.week)}〜</td><td>${esc(r.user)}</td><td class="wrap">${esc(r.message)}</td></tr>`).join("") + "</tbody>";
}
$("#hOnlyWeek").addEventListener("change", loadHistory);

// ---------- 起動 ----------
$("#user").value = store.get("user") || "";
$("#user").addEventListener("change", () => { store.set("user", userName()); api("/api/lock/name", { user: userName() }); });
(async function init() {
  await Promise.all([loadProducts(), loadState()]);
  if (userName()) api("/api/lock/name", { user: userName() });
  const q = new URLSearchParams(location.search).get("week");
  const thisMonday = mondayOf(state.today);
  await loadWeek(q || store.get("week") || thisMonday, true);
})().catch((e) => toast("起動に失敗しました：" + e.message, 8000));
