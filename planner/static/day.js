"use strict";
// ---------- 作業予定（日ごと・ライン別の製造順と時刻） ----------
// 時刻の計算はサーバーの day.compute と同じ：
//   開始 = 前の製品の終了を5分単位に切り上げ ＋ 切替（分）… 朝一は各ラインの開始時刻。固定した行はその時刻
//   終了 = 開始 ＋ B数 × 分/B
const KIND_TITLE = { seikei: "成型予定表", zensho: "前処理製造予定表" };
const dayState = {
  date: null,
  lines: {},        // ライン → [{ code, batches, gap, start }]
  data: null,       // サーバーから来た内容（weekly・revs・snapshots など）
  settings: null,   // { line_start, gap, night, night_gap }
  days: [],         // 表示中の週の各日の状態
  outputDir: "",
  columns: [],      // 項目の一覧 [{ key, label, default }]
  printCols: [],    // 成型予定表に印刷する項目のキー
  dirty: false,
};

const ceil5 = (m) => Math.ceil(m / 5 - 1e-9) * 5;
const hm = (m) => (m == null ? "" : `${Math.floor(Math.round(m) / 60)}:${String(Math.round(m) % 60).padStart(2, "0")}`);
function parseHm(s) {
  const t = String(s ?? "").trim().replace("：", ":");
  if (!t) return null;
  const m = t.match(/^(\d{1,2})(?::?(\d{2}))?$/);
  return m ? Number(m[1]) * 60 + Number(m[2] || 0) : NaN;
}
const fmtB = (b) => Number(Number(b).toFixed(2));

function setDayDirty(v) {
  dayState.dirty = v;
  $("#dDirty").hidden = !v;
}

function computeLine(ln) {
  const st = dayState.settings;
  let prev = null;
  return dayState.lines[ln].map((r) => {
    const mpb = state.products[r.code]?.batch_min || 0;
    let s;
    if (r.start != null) s = r.start;
    else if (prev == null) s = st.line_start[ln];
    else s = ceil5(prev) + (r.gap == null ? st.gap : Number(r.gap));
    const e = s + (Number(r.batches) || 0) * mpb;
    prev = e;
    return { ...r, line: ln, start_at: s, end_at: e, end5: ceil5(e), mpb };
  });
}
const allTimed = () => LINES.flatMap(computeLine);

// (ライン, コード, 何回目) のキー。分けた行を区別する（サーバーの day.keyed と同じ）
function keyedRows(rows) {
  const n = new Map(), out = new Map();
  for (const r of rows) {
    const k0 = `${r.line}|${r.code}`;
    const i = n.get(k0) || 0;
    n.set(k0, i + 1);
    out.set(`${k0}|${i}`, r);
  }
  return out;
}

// 成型予定表の控えから変わった行（B数・開始時刻）
function dayChanges() {
  const snap = dayState.data?.snapshots?.seikei;
  if (!snap) return { keys: new Set(), removed: [] };
  const o = keyedRows(snap), n = keyedRows(allTimed());
  const keys = new Set();
  for (const [k, r] of n) {
    const p = o.get(k);
    if (!p || Math.abs(p.batches - r.batches) > 1e-6 || Math.round(p.start_at) !== Math.round(r.start_at)) keys.add(k);
  }
  const removed = [...o].filter(([k]) => !n.has(k))
    .map(([, r]) => `${r.line}ライン ${state.products[r.code]?.name || r.code} ${fmtB(r.batches)}B`);
  return { keys, removed };
}

// 週間予定と B数が合わない製品
function dayMismatch() {
  const w = new Map(), d = new Map();
  for (const r of dayState.data.weekly) w.set(`${r.line}|${r.code}`, (w.get(`${r.line}|${r.code}`) || 0) + r.batches);
  for (const ln of LINES) for (const r of dayState.lines[ln]) d.set(`${ln}|${r.code}`, (d.get(`${ln}|${r.code}`) || 0) + (Number(r.batches) || 0));
  return [...new Set([...w.keys(), ...d.keys()])]
    .filter((k) => Math.abs((w.get(k) || 0) - (d.get(k) || 0)) > 1e-6)
    .map((k) => { const [line, code] = k.split("|"); return { line, code, weekly: w.get(k) || 0, day: d.get(k) || 0 }; });
}

// ---------- 読み込み ----------
function defaultDate() {
  const d = new Date(state.today + "T00:00:00");
  d.setDate(d.getDate() + (d.getDay() === 6 ? 2 : 1)); // 翌日（土曜なら月曜）
  return iso(d);
}
async function openDayTab() {
  if (!dayState.date) await loadDay(store.get("day") || defaultDate(), true);
  else await loadDay(dayState.date, true, true);
}
async function loadDay(date, force = false, keepIfDirty = false) {
  if (keepIfDirty && dayState.dirty) return;
  if (!force && dayState.dirty && !confirm("保存していない変更があります。破棄して移動しますか？")) return;
  if (new Date(date + "T00:00:00").getDay() === 0) date = addDays(date, 1); // 日曜は飛ばす
  const [d, list] = await Promise.all([api("/api/day?date=" + date), api("/api/days?monday=" + mondayOf(date))]);
  dayState.date = date;
  dayState.data = d;
  dayState.settings = list.settings;
  dayState.days = list.days;
  dayState.outputDir = list.day_output_dir;
  dayState.columns = list.columns;
  dayState.printCols = list.print_cols;
  setRows(d.rows);
  store.set("day", date);
  setDayDirty(false);
  renderDay();
}
function setRows(rows) {
  dayState.lines = Object.fromEntries(LINES.map((ln) => [ln, []]));
  for (const r of rows) dayState.lines[r.line].push({ code: r.code, batches: r.batches, gap: r.gap, start: r.start });
}
function addDays(date, n) {
  const x = new Date(date + "T00:00:00");
  x.setDate(x.getDate() + n);
  return iso(x);
}
async function refreshDayList() {
  const list = await api("/api/days?monday=" + mondayOf(dayState.date));
  dayState.days = list.days;
  renderDayChips();
}

// ---------- 表示 ----------
function renderDay() {
  const d = new Date(dayState.date + "T00:00:00");
  $("#dayTitle").textContent = `${d.getMonth() + 1}月${d.getDate()}日（${WEEK[(d.getDay() + 6) % 7]}）`;
  renderDayChips();
  renderDayRev();
  renderLines();
}
function renderDayChips() {
  $("#dayChips").innerHTML = dayState.days.map((x) => {
    const dd = new Date(x.date + "T00:00:00");
    const rv = (k) => (x.revs[k] == null ? "－" : revLabel(x.revs[k]) + (x.changed_since_issue[k] ? "*" : ""));
    const warn = x.mismatch || x.changed_since_issue.seikei || x.changed_since_issue.zensho;
    const st = !x.count ? "製造なし" : x.draft ? "案" : "保存済";
    return `<button class="daychip ${x.date === dayState.date ? "on" : ""} ${warn ? "warn" : ""}" data-d="${x.date}"
      title="成型 ${rv("seikei")}／前処理 ${rv("zensho")}${x.mismatch ? "\n週間予定とB数が合わない製品があります" : ""}${warn && !x.mismatch ? "\n＊は発行してから変更あり" : ""}">
      <b>${dd.getDate()}（${WEEK[(dd.getDay() + 6) % 7]}）</b><small>${st}${x.mismatch ? "・不一致" : ""}</small></button>`;
  }).join("");
  $("#dayChips").querySelectorAll(".daychip").forEach((b) => (b.onclick = () => loadDay(b.dataset.d)));
}
function renderDayRev() {
  const r = dayState.data.revs;
  const one = (k) => (r[k] ? `${KIND_TITLE[k]} ${revLabel(r[k].rev)}（${r[k].issued_at.slice(5, 10).replace("-", "/")} ${esc(r[k].user || "")}）` : `${KIND_TITLE[k]} 未発行`);
  $("#dRevInfo").innerHTML = `${one("zensho")}　／　${one("seikei")}`;
}

function renderBanner() {
  const msgs = [];
  if (dayState.data.draft && !dayState.dirty)
    msgs.push(`<div class="banner info">まだ保存していない<b>案</b>です。週間予定の順番で並べ、${hm(dayState.settings.night)} をまたぐ製品は分けて休止（${dayState.settings.night_gap}分）を入れています。直してから保存してください。</div>`);
  const mm = dayMismatch();
  if (mm.length) {
    const li = mm.map((m) => `<li>${m.line}ライン ${esc(state.products[m.code]?.name || m.code)}：週間予定 ${fmtB(m.weekly)}B ／ 作業予定 ${fmtB(m.day)}B</li>`).join("");
    msgs.push(`<div class="banner warn"><b>週間予定と B数が合いません</b><ul>${li}</ul><button id="dSync" class="edit-only">週間予定に合わせる</button></div>`);
  }
  const noTime = [...new Set(LINES.flatMap((ln) => dayState.lines[ln]).filter((r) => !state.products[r.code]?.batch_min).map((r) => r.code))];
  if (noTime.length)
    msgs.push(`<div class="banner warn">分/B が未設定の製品があります（${noTime.map((c) => esc(`${c} ${state.products[c]?.name || ""}`)).join("、")}）。終了時刻が計算できないので、製品マスタで入れてください。</div>`);
  $("#dBanner").innerHTML = msgs.join("");
  const sync = $("#dSync");
  if (sync) sync.onclick = () => syncDay().catch((e) => toast("合わせられませんでした：" + e.message));
}

const TL_FROM = 6 * 60, TL_TO = 28 * 60; // タイムラインの範囲（6:00〜翌4:00）
function timelineHtml(rows) {
  const pct = (m) => `${(Math.min(Math.max(m, TL_FROM), TL_TO) - TL_FROM) / (TL_TO - TL_FROM) * 100}%`;
  const ticks = [];
  for (let h = 6; h <= 28; h += 2) ticks.push(`<span class="tick" style="left:${pct(h * 60)}">${h}</span>`);
  const night = `<span class="nightmark" style="left:${pct(dayState.settings.night)}" title="夜の休止 ${hm(dayState.settings.night)}"></span>`;
  const codes = [...new Set(rows.map((r) => r.code))];
  const bars = rows.map((r) => `<span class="bar c${codes.indexOf(r.code) % 6}" style="left:${pct(r.start_at)};width:calc(${pct(r.end_at)} - ${pct(r.start_at)})"
      title="${esc(state.products[r.code]?.name || r.code)} ${fmtB(r.batches)}B ${hm(r.start_at)}〜${hm(r.end5)}"></span>`).join("");
  return `<div class="timeline">${ticks.join("")}${night}${bars}</div>`;
}

// 画面の表の列の幅（px）。項目はサーバーの day.COLUMNS の順にすべて出す
const COL_WIDTH = {
  code: 64, name: 170, batches: 62, shot: 50, batch_min: 50, mix: 100, gap: 58, start: 66, end: 52, cs: 62,
  mold: 140, plate: 70, form_g: 50, meat: 56, pre_note: 140, fry: 42, pre_color: 110, pre_regen: 70, wheat: 40,
  egg: 36, milk: 36, caution: 130, wash: 100, spec: 190, packaging: 70, post_regen: 74, kenkin: 52,
};
const STICKY_KEYS = ["code", "name"]; // 横にスクロールしても左に残す列
const SCREEN_LABEL = { batch_min: "分/B", batches: "B", gap: "切替", cs: "予定cs" };

function cellHtml(key, r, i, p, dis) {
  const auto = r.start == null;
  switch (key) {
    case "code": return esc(r.code);
    case "name": return esc(p.name || "マスタに無いコード");
    case "batches": return `<input class="b" type="number" step="0.25" min="0" value="${fmtB(r.batches)}" ${dis}>`;
    case "batch_min": return r.mpb ? fmtB(r.mpb) : '<span class="unknown">未設定</span>';
    case "gap": return i === 0 && auto ? '<span class="sub">朝一</span>'
      : `<input class="gap" type="number" step="5" min="0" value="${r.gap ?? ""}" placeholder="${dayState.settings.gap}" title="前の製品の終了から開始までの分（空なら${dayState.settings.gap}分）" ${dis}>`;
    case "start": return `<input class="start ${auto ? "auto" : "fixed"}" value="${hm(r.start_at)}" title="${auto ? "自動で計算（時刻を入れると固定）" : "固定した開始時刻（空にすると自動に戻る）"}" ${dis}>`;
    case "end": return hm(r.end5);
    case "cs": return p.yield_per_b ? String(Math.round(Number(r.batches) * p.yield_per_b)) : "";
    default: return esc(p[key] ?? "");
  }
}
const NUM_KEYS = new Set(["batch_min", "end", "cs", "shot", "form_g"]);
const MARK_KEYS = new Set(["fry", "wheat", "egg", "milk", "meat", "pre_regen", "post_regen", "kenkin", "plate", "packaging"]);

function renderLines() {
  const { keys, removed } = dayChanges();
  const dis = state.editable ? "" : "disabled";
  const cols = dayState.columns.map((c) => c.key);
  const printed = new Set(dayState.printCols);
  const ed = state.editable; // 閲覧のみのときは ⋮⋮ と操作の列を出さない
  const STICKY = {};
  let left = ed ? 18 : 0;
  for (const k of STICKY_KEYS) { STICKY[k] = left; left += COL_WIDTH[k]; }
  const stickyStyle = (k) => (k in STICKY ? ` style="left:${STICKY[k]}px"` : "");
  const width = (ed ? 18 + 96 : 0) + cols.reduce((a, k) => a + (COL_WIDTH[k] || 60), 0);
  const head = `<tr>${ed ? '<th class="sticky" style="left:0"></th>' : ""}${dayState.columns.map((c) =>
    `<th class="${c.key in STICKY ? "sticky" : ""} ${printed.has(c.key) ? "" : "noprint"}"${stickyStyle(c.key)}
      title="${printed.has(c.key) ? "成型予定表に印刷する項目" : "画面だけに出す項目（印刷しない）"}">${esc(SCREEN_LABEL[c.key] || c.label)}</th>`).join("")}${ed ? "<th></th>" : ""}</tr>`;
  const colgroup = `<colgroup>${ed ? '<col style="width:18px">' : ""}${cols.map((k) => `<col style="width:${COL_WIDTH[k] || 60}px">`).join("")}${ed ? '<col style="width:96px">' : ""}</colgroup>`;
  $("#dayLines").innerHTML = LINES.map((ln) => {
    const rows = computeLine(ln);
    const keyOf = [...keyedRows(rows).entries()].reduce((m, [k, r]) => m.set(r, k), new Map());
    const total = rows.reduce((a, r) => a + (Number(r.batches) || 0), 0);
    const span = rows.length ? `計 ${fmtB(total)}B　${hm(rows[0].start_at)}〜${hm(rows[rows.length - 1].end5)}` : "製造なし";
    const body = rows.map((r, i) => {
      const p = state.products[r.code] || {};
      const red = keys.has(keyOf.get(r));
      const tds = cols.map((k) => {
        const cls = [k in STICKY ? "sticky" : "", NUM_KEYS.has(k) ? "num" : "", MARK_KEYS.has(k) ? "mark" : "", k === "end" ? "end" : "",
          ["code", "name", "batches", "gap", "start", "end", "batch_min"].includes(k) ? "" : "info"].join(" ");
        const title = k === "end" ? `計算上の終了 ${hm(r.end_at)}` : typeof p[k] === "string" ? p[k] : "";
        return `<td class="${cls}"${stickyStyle(k)} title="${esc(title)}">${cellHtml(k, r, i, p, dis)}</td>`;
      }).join("");
      return `<tr class="drow ${red ? "changed" : ""}" data-line="${ln}" data-idx="${i}">
        ${ed ? '<td class="handle sticky" style="left:0" title="ドラッグで順番を変える">⋮⋮</td>' : ""}${tds}
        ${ed ? '<td class="ops"><button class="split" title="この行を2つに分ける（休止をはさむときなど）">分ける</button><button class="del" title="この行を消す（同じ製品の行があれば、B数をそちらへ足します）">✕</button></td>' : ""}
      </tr>`;
    }).join("");
    return `<div class="card dline" data-line="${ln}">
      <div class="dlinehead"><h3>${ln}ライン</h3><span class="sub">${span}</span></div>
      ${rows.length ? timelineHtml(rows) : ""}
      ${rows.length ? `<div class="tscroll"><table class="daytable" style="width:${width}px">${colgroup}<thead>${head}</thead><tbody>${body}</tbody></table></div>` : ""}
    </div>`;
  }).join("");
  syncScroll();
  bindLines();
  renderBanner();
  const box = $("#dRemoved");
  box.hidden = !removed.length;
  box.innerHTML = removed.length ? `<b>成型予定表の発行から消えた行</b><ul>${removed.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : "";
}

// 4ラインの表の横スクロールをそろえる（描き直しても位置を残す）
let dayScrollLeft = 0;
function syncScroll() {
  const boxes = [...document.querySelectorAll("#dayLines .tscroll")];
  boxes.forEach((b) => {
    b.scrollLeft = dayScrollLeft;
    b.addEventListener("scroll", () => {
      if (b.scrollLeft === dayScrollLeft) return;
      dayScrollLeft = b.scrollLeft;
      boxes.forEach((o) => { if (o !== b) o.scrollLeft = dayScrollLeft; });
    });
  });
}

function dayEdited() {
  setDayDirty(true);
  renderLines();
}

let dayDrag = null;
function bindLines() {
  document.querySelectorAll("#dayLines .drow").forEach((tr) => {
    const ln = tr.dataset.line, idx = Number(tr.dataset.idx);
    const r = dayState.lines[ln][idx];
    tr.querySelector(".b").addEventListener("change", (ev) => { r.batches = Number(ev.target.value) || 0; dayEdited(); });
    tr.querySelector(".gap")?.addEventListener("change", (ev) => { r.gap = ev.target.value === "" ? null : Number(ev.target.value); dayEdited(); });
    tr.querySelector(".start").addEventListener("change", (ev) => {
      const v = parseHm(ev.target.value);
      if (Number.isNaN(v)) { toast("時刻は 7:30 や 25:10 のように入れてください"); renderLines(); return; }
      const auto = computeLine(ln)[idx];
      r.start = v == null ? null : (r.start == null && v === Math.round(auto.start_at) ? null : v);
      dayEdited();
    });
    if (!state.editable) return; // 閲覧のみ（入力欄は disabled、分ける・✕・⋮⋮ は無い）
    tr.querySelector(".split").onclick = () => {
      const half = fmtB(Math.floor(Number(r.batches)) / 2 || Number(r.batches) / 2);
      const v = prompt(`${state.products[r.code]?.name || r.code}（${fmtB(r.batches)}B）を分けます。前の行の B数は？`, half);
      if (v === null) return;
      const b1 = Number(v);
      if (!(b1 > 0 && b1 < Number(r.batches))) return toast(`0 より大きく ${fmtB(r.batches)} より小さい数を入れてください`);
      const rest = fmtB(Number(r.batches) - b1);
      r.batches = b1;
      dayState.lines[ln].splice(idx + 1, 0, { code: r.code, batches: rest, gap: dayState.settings.night_gap, start: null });
      dayEdited();
    };
    tr.querySelector(".del").onclick = () => {
      const list = dayState.lines[ln];
      const other = list.find((x, i) => i !== idx && x.code === r.code);
      if (other) other.batches = fmtB(Number(other.batches) + Number(r.batches));
      else if (!confirm(`${state.products[r.code]?.name || r.code} を消すと、週間予定と B数が合わなくなります。消しますか？`)) return;
      list.splice(idx, 1);
      dayEdited();
    };
    const h = tr.querySelector(".handle");
    h.addEventListener("mousedown", () => (tr.draggable = true));
    tr.addEventListener("dragstart", (ev) => { dayDrag = { ln, idx }; tr.classList.add("dragging"); ev.dataTransfer.setData("text/plain", ln); });
    tr.addEventListener("dragend", () => { tr.draggable = false; tr.classList.remove("dragging"); dayDrag = null; });
    tr.addEventListener("dragover", (ev) => { if (dayDrag?.ln === ln) { ev.preventDefault(); tr.classList.add("over"); } });
    tr.addEventListener("dragleave", () => tr.classList.remove("over"));
    tr.addEventListener("drop", (ev) => {
      ev.preventDefault();
      tr.classList.remove("over");
      if (!dayDrag || dayDrag.ln !== ln) return;
      const list = dayState.lines[ln];
      const [x] = list.splice(dayDrag.idx, 1);
      list.splice(idx, 0, x);
      dayEdited();
    });
  });
}

// ---------- 保存・発行 ----------
function flatDayRows() {
  return LINES.flatMap((ln) => dayState.lines[ln].map((r) => ({ line: ln, code: r.code, batches: Number(r.batches), gap: r.gap, start: r.start })));
}
async function saveDay() {
  const mm = dayMismatch();
  if (mm.length && !confirm(`週間予定と B数が合わない製品が ${mm.length} 件あります。このまま保存しますか？`)) return false;
  const r = await api("/api/day", { date: dayState.date, user: userName(), rows: flatDayRows() });
  await loadDay(dayState.date, true);
  toast(r.changes.length ? `保存しました（${r.changes.length}件の変更）` : "保存しました");
  return true;
}
async function issueDay(kind) {
  if ((dayState.dirty || dayState.data.draft) && !(await saveDay())) return;
  const r0 = dayState.data.revs[kind];
  const msg = r0 ? `${KIND_TITLE[kind]}の ${revLabel(r0.rev)} からの変更を発行して、Excelを出力します。` : `${KIND_TITLE[kind]}を初版として発行して、Excelを出力します。`;
  if (!confirm(msg + "\n出力先：" + dayState.outputDir)) return;
  const r = await api("/api/day/issue", { date: dayState.date, kind, user: userName() });
  await loadDay(dayState.date, true);
  toast((r.note ? r.note + "\n" : `${KIND_TITLE[kind]} ${revLabel(r.rev)} を発行しました（変更 ${r.changes.length}件）\n`) + r.path, 7000);
}
async function syncDay() {
  const r = await api("/api/day/sync", { date: dayState.date, rows: flatDayRows() });
  setRows(r.rows);
  dayEdited();
  toast("週間予定の B数に合わせました（まだ保存していません）");
}
async function redraftDay() {
  if (!confirm("今の作業予定（順番・切替・固定した時刻）を捨てて、週間予定から案を作り直します。よろしいですか？")) return;
  const r = await api("/api/day/draft", { date: dayState.date });
  setRows(r.rows);
  dayEdited();
  toast("週間予定から案を作り直しました（まだ保存していません）");
}
// ---------- 印刷する項目 ----------
function openPrintCols() {
  const dlg = $("#printDlg");
  const dis = state.editable ? "" : "disabled";
  const render = (sel) => {
    $("#pcList").innerHTML = dayState.columns.map((c) =>
      `<label class="pc"><input type="checkbox" value="${c.key}" ${sel.has(c.key) ? "checked" : ""} ${dis}> ${esc(c.label)}</label>`).join("");
    $("#pcCount").textContent = `${sel.size} / ${dayState.columns.length} 項目`;
    $("#pcList").querySelectorAll("input").forEach((i) => i.addEventListener("change", () => {
      i.checked ? sel.add(i.value) : sel.delete(i.value);
      $("#pcCount").textContent = `${sel.size} / ${dayState.columns.length} 項目`;
    }));
  };
  let sel = new Set(dayState.printCols);
  render(sel);
  $("#pcAll").onclick = () => { sel = new Set(dayState.columns.map((c) => c.key)); render(sel); };
  $("#pcDefault").onclick = () => { sel = new Set(dayState.columns.filter((c) => c.default).map((c) => c.key)); render(sel); };
  $("#pcCancel").onclick = () => dlg.close();
  $("#pcSave").onclick = async () => {
    if (!sel.size) return toast("印刷する項目を1つ以上選んでください");
    try {
      const r = await api("/api/day/print_cols", { cols: dayState.columns.map((c) => c.key).filter((k) => sel.has(k)) });
      dayState.printCols = r.print_cols;
      dlg.close();
      renderLines();
      toast("印刷する項目を保存しました。次に発行する成型予定表から使います（変更がなければ同じ版で出力し直せます）", 5000);
    } catch (e) { toast("保存できませんでした：" + e.message); }
  };
  dlg.showModal();
}
$("#dPrintCols").onclick = openPrintCols;

$("#dSave").onclick = () => saveDay().catch((e) => toast("保存できませんでした：" + e.message));
$("#dRedraft").onclick = () => redraftDay().catch((e) => toast("作り直せませんでした：" + e.message));
$("#dIssueSeikei").onclick = () => issueDay("seikei").catch((e) => toast("発行できませんでした：" + e.message, 7000));
$("#dIssueZensho").onclick = () => issueDay("zensho").catch((e) => toast("発行できませんでした：" + e.message, 7000));
$("#dPrev").onclick = () => { const d = addDays(dayState.date, -1); loadDay(new Date(d + "T00:00:00").getDay() === 0 ? addDays(d, -1) : d); };
$("#dNext").onclick = () => loadDay(addDays(dayState.date, 1));
