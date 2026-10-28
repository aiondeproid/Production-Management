"use strict";
// ---------- 後処理日報・洗浄記録 ----------
// 行は作業予定（保存した行。無ければ週間予定からの案）から作る。製品の情報は製品マスタ（Book1 から取り込んだ値）。
// 書き込む欄（出来高・洗浄時刻・担当者など）は日ごとに保存する。行は (ライン, コード, 何回目) のキーで持つ。
//   出始め = 成型の開始 ＋ ライン占拠（分）、出終り = 成型の終了（5分単位） ＋ ライン占拠（分）
//   予定数 = B数 × 出来高/B
const postState = {
  date: null,
  data: null,   // サーバーから来た内容（rows・draft・updated_at など）
  rec: null,    // 書き込んだ内容 { rows: { キー: { actual, wash, … } }, extra: [{ code, plan, actual }], qc }
  dirty: false,
};
const POST_MIN_ROWS = { A: 4, B: 4, C: 3, D: 3 }; // 印刷するときのラインごとの最低行数（手書きの追加用）
const WASH_NOTE = "卵注意喚起表示有り、順番を卵なしの後にする";

function setPostDirty(v) {
  postState.dirty = v;
  $("#pDirty").hidden = !v;
}
const postProduct = (code) => state.products[code] || {};
const postShift = (m, p) => (m == null ? "" : hm(m + (p.occupy_min || 0)));
const postPlan = (code, b) => { const y = postProduct(code).yield_per_b; return y && b ? String(Math.round(Number(b) * y)) : ""; };
// ライン → [{ key, r, p, no }]
function postLines() {
  const keyOf = [...keyedRows(postState.data.rows).entries()].reduce((m, [k, r]) => m.set(r, k), new Map());
  return Object.fromEntries(LINES.map((ln) => [ln, postState.data.rows.filter((r) => r.line === ln)
    .map((r, i) => ({ key: keyOf.get(r), r, p: postProduct(r.code), no: i + 1 }))]));
}
const recOf = (key) => postState.rec.rows[key] || {};

// ---------- 読み込み ----------
async function openPostTab() {
  if (!postState.date) await loadPost(store.get("post") || state.today, true);
  else if (!postState.dirty) await loadPost(postState.date, true);
}
async function loadPost(date, force = false) {
  if (!force && postState.dirty && !confirm("保存していない書き込みがあります。破棄して移動しますか？")) return;
  if (new Date(date + "T00:00:00").getDay() === 0) date = addDays(date, 1); // 日曜は飛ばす
  const d = await api("/api/post?date=" + date);
  postState.date = date;
  postState.data = d;
  postState.rec = JSON.parse(JSON.stringify(d.data));
  store.set("post", date);
  setPostDirty(false);
  renderPost();
}

// ---------- 表示 ----------
function renderPost() {
  const d = new Date(postState.date + "T00:00:00");
  $("#postTitle").textContent = `${d.getMonth() + 1}月${d.getDate()}日（${WEEK[(d.getDay() + 6) % 7]}）`;
  const u = postState.data;
  $("#pInfo").textContent = u.updated_at ? `最終保存 ${u.updated_at.slice(5, 16).replace("-", "/")} ${u.user || ""}` : "まだ書き込みなし";
  const lines = postLines();
  renderPostBanner(lines);
  $("#pNippo").innerHTML = nippoScreen(lines);
  $("#pWash").innerHTML = washScreen(lines);
  $("#pQc").value = postState.rec.qc || "";
  document.querySelectorAll("#tab-post input").forEach((i) => (i.disabled = !state.editable));
}
function renderPostBanner(lines) {
  const msgs = [];
  if (postState.data.draft && postState.data.rows.length)
    msgs.push(`<div class="banner info">この日の作業予定はまだ保存されていません。週間予定からの案の順番・時刻で出しています。</div>`);
  if (!postState.data.rows.length) msgs.push(`<div class="banner info">この日は製造がありません（作業予定・週間予定に製品がありません）。</div>`);
  const keys = new Set(Object.values(lines).flat().map((x) => x.key));
  const orphans = Object.keys(postState.rec.rows).filter((k) => !keys.has(k));
  if (orphans.length) {
    const li = orphans.map((k) => {
      const [ln, code] = k.split("|");
      const v = Object.entries(recOf(k)).map(([f, x]) => `${POST_LABEL[f]} ${esc(x)}`).join("・");
      return `<li>${ln}ライン ${esc(postProduct(code).name || code)}：${v}</li>`;
    }).join("");
    msgs.push(`<div class="banner warn"><b>作業予定から消えた行の書き込みがあります</b>（作業予定で製品やラインを変えたため）<ul>${li}</ul>
      <button id="pDropOrphans" class="edit-only">この書き込みを消す</button></div>`);
  }
  $("#pBanner").innerHTML = msgs.join("");
  const b = $("#pDropOrphans");
  if (b) b.onclick = () => { orphans.forEach((k) => delete postState.rec.rows[k]); setPostDirty(true); renderPost(); };
}
const POST_LABEL = { actual: "出来高", limited: "数量限定", wash: "洗浄", wash_mix: "配合", wash_form: "成型", wash_time: "洗浄時刻", wash_by: "担当者", checked_by: "確認者" };
const pIn = (key, f, size, extra = "") => `<input data-k="${esc(key)}" data-f="${f}" value="${esc(recOf(key)[f] || "")}" size="${size}" ${extra}>`;
const pChk = (key, f) => `<input type="checkbox" data-k="${esc(key)}" data-f="${f}" ${recOf(key)[f] ? "checked" : ""}>`;

function nippoScreen(lines) {
  const head = `<tr><th>No</th><th>コード</th><th>製品名</th><th>B</th><th>成型</th><th>出始め</th><th>出終り</th><th>予定数</th>
    <th title="印を付けると予定数に ※ を付けて印刷します">数量限定</th><th>出来高</th><th>包装形態</th><th>フィルム</th><th>焼色</th><th>再生</th><th>規格・備考</th><th>計数機</th></tr>`;
  const body = LINES.map((ln) => {
    const list = lines[ln];
    const rows = list.map(({ key, r, p, no }) => `<tr>
      <td class="c">${no}</td><td>${esc(r.code)}</td><td class="nm">${esc(p.name || r.code)}</td><td class="c">${fmtB(r.batches)}</td>
      <td class="c sub">${hm(r.start_at)}～${hm(r.end5)}</td>
      <td class="c">${p.occupy_min ? postShift(r.start_at, p) : `<span class="warnc" title="製品マスタにライン占拠（分）がありません">－</span>`}</td>
      <td class="c">${p.occupy_min ? postShift(r.end5, p) : "－"}</td>
      <td class="c">${postPlan(r.code, r.batches)}</td><td class="c">${pChk(key, "limited")}</td>
      <td>${pIn(key, "actual", 6, 'inputmode="decimal"')}</td>
      <td>${esc(p.packaging || "")}</td><td>${esc(p.film || "")}</td><td>${esc(p.post_color || "")}</td><td class="c">${esc(p.post_regen || "")}</td>
      <td class="wrap">${esc(p.spec || "")}</td><td class="c">${esc(p.counter || "")}</td></tr>`).join("");
    return `<tr class="lnrow"><th colspan="16">${ln}ライン</th></tr>${rows || `<tr><td colspan="16" class="sub">製造なし</td></tr>`}`;
  }).join("");
  const extra = Array.from({ length: postState.data.extra_max }, (_, i) => {
    const x = postState.rec.extra[i] || {};
    const p = postProduct(x.code);
    return `<tr><td class="c">${i + 1}</td>
      <td><input data-x="${i}" data-f="code" value="${esc(x.code || "")}" size="7" list="productList"></td>
      <td class="nm">${esc(x.code ? p.name || "（製品マスタにありません）" : "")}</td><td></td><td></td><td></td><td></td>
      <td><input data-x="${i}" data-f="plan" value="${esc(x.plan || "")}" size="5"></td><td></td>
      <td><input data-x="${i}" data-f="actual" value="${esc(x.actual || "")}" size="6" inputmode="decimal"></td>
      <td>${esc(p.packaging || "")}</td><td>${esc(p.film || "")}</td><td>${esc(p.post_color || "")}</td><td class="c">${esc(p.post_regen || "")}</td>
      <td class="wrap">${esc(p.spec || "")}</td><td class="c">${esc(p.counter || "")}</td></tr>`;
  }).join("");
  return `<div class="tablewrap pwrap"><table class="grid ptbl"><thead>${head}</thead><tbody>${body}
    <tr class="lnrow"><th colspan="16">その他 <small>（ラインを通さない製品など。コードと予定数を入れてください）</small></th></tr>${extra}</tbody></table></div>`;
}

function washScreen(lines) {
  const head = `<tr><th>No</th><th>コード</th><th>品名</th><th>B</th><th>終了</th><th>卵</th><th>乳</th><th>小麦</th><th>その他注意事項</th>
    <th>洗浄</th><th>肉組成</th><th>配合</th><th>成型</th><th>洗浄時刻</th><th>担当者</th><th>確認者</th></tr>`;
  const body = LINES.map((ln) => {
    const rows = lines[ln].map(({ key, r, p, no }) => `<tr>
      <td class="c">${no}</td><td>${esc(r.code)}</td><td class="nm">${esc(p.name || r.code)}</td><td class="c">${fmtB(r.batches)}</td>
      <td class="c sub">${hm(r.end5)}</td>
      <td class="c">${esc(p.egg || "")}</td><td class="c">${esc(p.milk || "")}</td><td class="c">${esc(p.wheat || "")}</td>
      <td class="wrap">${esc(p.wash || "")}</td>
      <td>${pIn(key, "wash", 4, 'list="washList"')}</td><td>${esc(p.meat_comp || "")}</td>
      <td class="c">${pChk(key, "wash_mix")}</td><td class="c">${pChk(key, "wash_form")}</td>
      <td>${pIn(key, "wash_time", 5, 'title="15:30 や 1530 のように入れます"')}</td><td>${pIn(key, "wash_by", 7)}</td><td>${pIn(key, "checked_by", 7)}</td></tr>`).join("");
    return `<tr class="lnrow"><th colspan="16">${ln}ライン</th></tr>${rows || `<tr><td colspan="16" class="sub">製造なし</td></tr>`}`;
  }).join("");
  return `<div class="tablewrap pwrap"><table class="grid ptbl"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
}

// ---------- 書き込み ----------
$("#tab-post").addEventListener("change", (ev) => {
  const t = ev.target;
  if (!t.matches("input[data-f]")) return;
  const f = t.dataset.f;
  if (t.dataset.x != null) {
    const i = Number(t.dataset.x);
    const ex = postState.rec.extra;
    while (ex.length <= i) ex.push({ code: "", plan: "", actual: "" });
    ex[i][f] = t.value.trim();
    if (f === "code") { ex[i].code = ex[i].code.normalize("NFKC"); renderPost(); }
  } else {
    const k = t.dataset.k;
    let v = t.type === "checkbox" ? (t.checked ? "✓" : "") : t.value.trim();
    if (f === "wash_time" && v) {
      const m = parseHm(v);
      if (Number.isNaN(m)) { toast("洗浄時刻は 15:30 のように入れてください"); t.value = recOf(k)[f] || ""; return; }
      v = t.value = hm(m);
    }
    const r = { ...recOf(k), [f]: v };
    if (!v) delete r[f];
    if (Object.keys(r).length) postState.rec.rows[k] = r;
    else delete postState.rec.rows[k];
  }
  setPostDirty(true);
});
$("#pQc").addEventListener("change", (ev) => { postState.rec.qc = ev.target.value.trim(); setPostDirty(true); });
// 担当者・確認者：上の行と同じ名前を Enter で続けて入れやすいように、空欄で Enter を押すと上の行の名前を入れる
$("#tab-post").addEventListener("keydown", (ev) => {
  const t = ev.target;
  if (ev.key !== "Enter" || !t.matches("input[data-f=wash_by], input[data-f=checked_by]") || t.value) return;
  const all = [...document.querySelectorAll(`#tab-post input[data-f=${t.dataset.f}]`)];
  const prev = all.slice(0, all.indexOf(t)).reverse().find((i) => i.value);
  if (prev) { t.value = prev.value; t.dispatchEvent(new Event("change", { bubbles: true })); }
});

async function savePost() {
  const r = await api("/api/post", { date: postState.date, user: userName(), data: postState.rec });
  await loadPost(postState.date, true);
  toast(r.changes.length ? `保存しました（${r.changes.length}件の変更）` : "保存しました（変更なし）");
}

// ---------- 印刷（A4横。様式は今の Excel と同じ並び） ----------
const postDateText = () => { const d = new Date(postState.date + "T00:00:00"); return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日（${WEEK[(d.getDay() + 6) % 7]}）`; };
const colgroupOf = (ws) => { const t = ws.reduce((a, w) => a + w, 0); return `<colgroup>${ws.map((w) => `<col style="width:${(w / t * 100).toFixed(2)}%">`).join("")}</colgroup>`; };
const blankRows = (n, cols) => `<tr>${"<td></td>".repeat(cols)}</tr>`.repeat(Math.max(0, n));
function nippoHtml() {
  const lines = postLines();
  const W = [3, 6, 30, 5, 6, 6, 7, 7, 9, 9, 9, 50, 6];
  const head = `<tr><th>No</th><th>コード</th><th>製　品　名</th><th>B</th><th>出始め</th><th>出終り</th><th>予定数</th><th>出来高</th>
    <th>包装形態</th><th>ﾌｨﾙﾑ</th><th>焼色</th><th>規　格　・　備　考</th><th>計数機<br>設定</th></tr>`;
  const tables = LINES.map((ln) => {
    const list = lines[ln];
    const rows = list.map(({ key, r, p, no }) => {
      const rec = recOf(key), plan = postPlan(r.code, r.batches);
      return `<tr><td>${no}</td><td class="num">${esc(r.code)}</td><td class="name">${esc(p.name || r.code)}</td><td class="num">${fmtB(r.batches)}</td>
        <td class="num">${p.occupy_min ? postShift(r.start_at, p) : ""}</td><td class="num">${p.occupy_min ? postShift(r.end5, p) : ""}</td>
        <td class="num">${rec.limited && plan ? "※" : ""}${plan}</td><td class="num big">${esc(rec.actual || "")}</td>
        <td>${esc(p.packaging || "")}</td><td>${esc(p.film || "")}</td><td>${esc(p.post_color || "")}</td>
        <td class="name">${esc(p.spec || "")}</td><td>${esc(p.counter || "")}</td></tr>`;
    }).join("");
    return `<tr class="lnhead"><th colspan="13">${ln}ライン</th></tr>${rows}${blankRows(POST_MIN_ROWS[ln] - list.length, 13)}`;
  }).join("");
  const extra = Array.from({ length: postState.data.extra_max }, (_, i) => {
    const x = postState.rec.extra[i];
    if (!x) return blankRows(1, 13);
    const p = postProduct(x.code);
    return `<tr><td>${i + 1}</td><td class="num">${esc(x.code)}</td><td class="name">${esc(p.name || "")}</td><td></td><td></td><td></td>
      <td class="num">${esc(x.plan)}</td><td class="num big">${esc(x.actual)}</td><td>${esc(p.packaging || "")}</td><td>${esc(p.film || "")}</td>
      <td>${esc(p.post_color || "")}</td><td class="name">${esc(p.spec || "")}</td><td>${esc(p.counter || "")}</td></tr>`;
  }).join("");
  return `<div class="zsheet psheet">
    <div class="zhead"><span>製造年月日　<b class="pdate">${postDateText()}</b></span><h1>後処理箱詰め作業日報書</h1>
      <span class="plegend">※印…数量限定品　　○…55ﾐｸﾛﾝ ﾌｨﾙﾑ使用</span></div>
    <table>${colgroupOf(W)}<thead>${head}</thead><tbody>${tables}<tr class="lnhead"><th colspan="13">その他</th></tr>${extra}</tbody></table>
    <div class="pfoot">印刷 ${new Date().toLocaleString("ja-JP", { dateStyle: "short", timeStyle: "short" })}${postState.dirty ? "　※保存していない書き込みを含む" : ""}</div>
  </div>`;
}
function washHtml() {
  const lines = postLines();
  const W = [3, 7.7, 23, 4.4, 3, 3, 3.5, 24, 5.5, 9.6, 5.3, 5.3, 12.7, 8.5, 8.5];
  const head = `<tr><th>No</th><th>コード</th><th>品名</th><th>バッチ</th><th>卵</th><th>乳</th><th>小麦</th><th>その他注意事項</th>
    <th>洗浄</th><th>肉組成</th><th>配合</th><th>成型</th><th>洗浄時刻</th><th>担当者</th><th>確認者</th></tr>`;
  const tables = LINES.map((ln) => {
    const list = lines[ln];
    const rows = list.map(({ key, r, p, no }) => {
      const rec = recOf(key);
      return `<tr><td>${no}</td><td class="num">${esc(r.code)}</td><td class="name">${esc(p.name || r.code)}</td><td class="num">${fmtB(r.batches)}</td>
        <td>${esc(p.egg || "")}</td><td>${esc(p.milk || "")}</td><td>${esc(p.wheat || "")}</td><td class="name">${esc(p.wash || "")}</td>
        <td>${esc(rec.wash || "")}</td><td>${esc(p.meat_comp || "")}</td><td>${esc(rec.wash_mix || "")}</td><td>${esc(rec.wash_form || "")}</td>
        <td class="num">${esc(rec.wash_time || "")}</td><td>${esc(rec.wash_by || "")}</td><td>${esc(rec.checked_by || "")}</td></tr>`;
    }).join("");
    return `<tr class="lnhead"><th colspan="15">${ln}ライン</th></tr>${rows}${blankRows(POST_MIN_ROWS[ln] - list.length, 15)}`;
  }).join("");
  return `<div class="zsheet psheet">
    <div class="zhead"><span>様式番号：Ｆ-08-013</span><h1>アレルゲン・単一肉種など対応　洗浄記録</h1>
      <table class="stamp"><tr><th>品管</th></tr><tr><td>${esc(postState.rec.qc || "")}</td></tr></table></div>
    <div class="zhead"><b class="pdate">${postDateText()}</b><span>配布前　品管確認：<span class="uline">${esc(postState.rec.qc || "")}</span></span></div>
    <table>${colgroupOf(W)}<thead>${head}</thead><tbody>${tables}</tbody></table>
    <div class="pfoot"><b>${WASH_NOTE}</b>　　印刷 ${new Date().toLocaleString("ja-JP", { dateStyle: "short", timeStyle: "short" })}${postState.dirty ? "　※保存していない書き込みを含む" : ""}</div>
  </div>`;
}

$("#pSave").onclick = () => savePost().catch((e) => toast("保存できませんでした：" + e.message));
$("#pPrintNippo").onclick = () => printHtml("post", nippoHtml());
$("#pPrintWash").onclick = () => printHtml("post", washHtml());
$("#pPrev").onclick = () => { const d = addDays(postState.date, -1); loadPost(new Date(d + "T00:00:00").getDay() === 0 ? addDays(d, -1) : d); };
$("#pNext").onclick = () => loadPost(addDays(postState.date, 1));
$("#pToday").onclick = () => loadPost(state.today);
