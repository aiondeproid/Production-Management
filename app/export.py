"""閲覧用HTML（前処理・後処理・品管・変更履歴）を共有フォルダへ書き出す

書き出したHTMLはブラウザで開くだけで見られる（サーバー不要）。
"""
import datetime as dt
import html
from pathlib import Path

import db
import rules
from db import LINES

WEEK = "月火水木金土日"


def e(v):
    return html.escape("" if v is None else str(v))


def num(v):
    if v is None or v == "":
        return ""
    return f"{v:g}" if isinstance(v, float) else str(v)


def mark(flag):
    return "○" if flag else "－"


def timeline(sched, settings):
    t0, t1 = 6 * 60, 30 * 60
    span = t1 - t0
    pct = lambda m: max(0, min(100, (m - t0) / span * 100))
    ticks = "".join(
        f'<div class="tick" style="left:{pct(h * 60):.2f}%">{h % 24}</div>' for h in range(6, 31, 2)
    )
    night = f'<div class="night" style="left:{pct(settings["night_start"]):.2f}%"></div>'
    rows = ""
    for ln in LINES:
        bars = ""
        for s in (x for x in sched if x["line"] == ln):
            w = pct(s["end"]) - pct(s["start"])
            bars += (
                f'<div class="bar" style="left:{pct(s["start"]):.2f}%;width:{max(w, 0.5):.2f}%" '
                f'title="{e(s["name"])} {s["start_text"]}〜{s["end_text"]}">{e(s["name"])}</div>'
            )
        rows += f'<div class="tl-row"><div class="tl-label">{ln}</div><div class="tl-track">{night}{bars}</div></div>'
    return f'<div class="timeline"><div class="tl-row"><div class="tl-label"></div><div class="tl-track ticks">{ticks}</div></div>{rows}</div>'


def line_tables(sched, columns):
    out = ""
    for ln in LINES:
        rows = [s for s in sched if s["line"] == ln]
        if not rows:
            continue
        head = "".join(f"<th>{e(c[0])}</th>" for c in columns)
        body = "".join("<tr>" + "".join(f"<td>{c[1](s)}</td>" for c in columns) + "</tr>" for s in rows)
        out += f"<h3>{ln}ライン</h3><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    return out or "<p>予定なし</p>"


def build_day(con, date):
    products = db.all_products(con)
    settings = db.get_settings(con)
    items = db.load_plan(con, date)
    meta = db.load_meta(con, date)
    result = rules.check(date, items, products, settings, db.holidays(con))
    sched = []
    for s in result["schedule"]:
        p = products.get(s["code"], {})
        sched.append({**s, "p": p, "name": p.get("name") or s["code"]})

    time = lambda s: f'{s["start_text"]}〜{s["end_text"]}'
    pre_cols = [
        ("時刻", time),
        ("コード", lambda s: e(s["code"])),
        ("製品名", lambda s: f'<b>{e(s["name"])}</b>'),
        ("バッチ", lambda s: num(s["batches"])),
        ("金検", lambda s: e(s["p"].get("metal_check"))),
        ("前処理その他", lambda s: e(s["p"].get("pre_note"))),
        ("焼色", lambda s: e(s["p"].get("pre_color"))),
        ("前処理再生", lambda s: e(s["p"].get("pre_regen"))),
        ("モールド板", lambda s: e(s["p"].get("mold"))),
        ("解凍kg", lambda s: num(round((s["p"].get("thaw_kg") or 0) * s["batches"], 1)) if s["p"].get("thaw_kg") is not None else "未設定"),
    ]
    post_cols = [
        ("時刻", time),
        ("製品名", lambda s: f'<b>{e(s["name"])}</b>'),
        ("バッチ", lambda s: num(s["batches"])),
        ("出来高見込(cs)", lambda s: num(round((s["p"].get("yield_per_b") or 0) * s["batches"])) if s["p"].get("yield_per_b") else ""),
        ("箱詰め規格・備考", lambda s: e(s["p"].get("spec"))),
        ("包装形態", lambda s: e(s["p"].get("packaging"))),
        ("焼色", lambda s: e(s["p"].get("post_color"))),
        ("後処理再生", lambda s: e(s["p"].get("post_regen"))),
    ]
    qa_cols = [
        ("時刻", time),
        ("製品名", lambda s: f'<b>{e(s["name"])}</b>' + (' <span class="new">新規</span>' if s["p"].get("is_new") else "")),
        ("小麦", lambda s: mark(s["p"].get("wheat"))),
        ("乳", lambda s: mark(s["p"].get("milk"))),
        ("卵", lambda s: mark(s["p"].get("egg"))),
        ("肉種", lambda s: e(s["p"].get("meat"))),
        ("洗浄事項", lambda s: e(s["p"].get("wash_note"))),
        ("注意事項", lambda s: e(s["p"].get("caution"))),
    ]
    alerts = "".join(
        f'<li class="lv-{e(a["level"])}"><b>{e(a["level"])}</b> {e(a["rule"])}：{e(a["message"])}</li>'
        for a in result["alerts"] if a["level"] != "情報"
    ) or "<li>警告なし</li>"
    logs = con.execute("SELECT ts, user, message FROM changelog WHERE date=? ORDER BY id DESC", (date,)).fetchall()
    log_rows = "".join(
        f"<tr><td>{e(r['ts'])}</td><td>{e(r['user'])}</td><td>{e(r['message']).replace(chr(10), '<br>')}</td></tr>" for r in logs
    )
    thaw = result["gauges"]["解凍肉"]

    d = dt.date.fromisoformat(date)
    title = f"{d.month}月{d.day}日（{WEEK[d.weekday()]}）"
    sections = {
        "前処理": f'<p class="sum">解凍量 合計 <b>{thaw["value"]:,}kg</b> ／ 容量 {thaw["limit"]:,}kg'
        + (f'（未設定 {thaw["missing"]}品）' if thaw["missing"] else "") + "</p>" + line_tables(sched, pre_cols),
        "後処理": line_tables(sched, post_cols),
        "品管": f'<ul class="alerts">{alerts}</ul>' + line_tables(sched, qa_cols),
        "変更履歴": f"<table><thead><tr><th>日時</th><th>担当</th><th>内容</th></tr></thead><tbody>{log_rows}</tbody></table>",
    }
    tabs = "".join(f'<button onclick="show(\'{k}\')" id="b-{k}">{k}</button>' for k in sections)
    panes = "".join(f'<section id="s-{k}"><h2>{k}</h2>{v}</section>' for k, v in sections.items())
    memo = f'<p class="memo">メモ：{e(meta["memo"])}</p>' if meta["memo"] else ""
    return PAGE.format(
        title=e(title), rev=meta["revision"], now=dt.datetime.now().strftime("%m/%d %H:%M"),
        timeline=timeline(sched, settings), tabs=tabs, panes=panes, memo=memo,
    )


def build_index(out_dir: Path):
    files = sorted(out_dir.glob("計画_*.html"), reverse=True)
    links = ""
    for f in files:
        d = dt.date.fromisoformat(f.stem.split("_", 1)[1])
        links += f'<li><a href="{e(f.name)}">{d.month}月{d.day}日（{WEEK[d.weekday()]}）</a></li>'
    (out_dir / "index.html").write_text(INDEX.format(links=links), encoding="utf-8")


def export_day(con, date) -> Path:
    out_dir = Path(db.get_settings(con)["export_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"計画_{date}.html"
    path.write_text(build_day(con, date), encoding="utf-8")
    build_index(out_dir)
    return path


STYLE = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2433;--sub:#5b6476;--line:#d9dde5;--accent:#2f6fd6;--ng:#c62828;--warn:#b26a00}
*{box-sizing:border-box}body{margin:0;font-family:"Yu Gothic UI","Meiryo",sans-serif;background:var(--bg);color:var(--ink);font-size:14px}
header{background:var(--card);border-bottom:1px solid var(--line);padding:12px 16px;position:sticky;top:0;z-index:2}
h1{font-size:20px;margin:0 0 8px}h1 small{font-size:13px;color:var(--sub);font-weight:normal;margin-left:8px}
nav button{font:inherit;padding:6px 14px;margin-right:4px;border:1px solid var(--line);background:var(--card);border-radius:6px;cursor:pointer}
nav button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
main{padding:12px 16px;max-width:1400px}section{display:none}section.on{display:block}
h2{font-size:17px;margin:8px 0}h3{font-size:15px;margin:16px 0 6px}
table{border-collapse:collapse;width:100%;background:var(--card);font-size:13px}
th,td{border:1px solid var(--line);padding:4px 6px;text-align:left;vertical-align:top}th{background:#eef1f6;white-space:nowrap}
.timeline{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px;margin-bottom:8px}
.tl-row{display:flex;align-items:center;height:26px}.tl-label{width:24px;font-weight:bold}
.tl-track{position:relative;flex:1;height:22px;border-bottom:1px dashed var(--line)}
.ticks{border:none;height:16px}.tick{position:absolute;font-size:11px;color:var(--sub);transform:translateX(-50%)}
.night{position:absolute;top:0;bottom:0;right:0;background:#e9ecf5}
.bar{position:absolute;top:2px;height:18px;background:#cfe0fb;border:1px solid var(--accent);border-radius:4px;font-size:11px;overflow:hidden;white-space:nowrap;padding:0 3px}
.alerts{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 8px 8px 28px}
.lv-NG{color:var(--ng)}.lv-注意{color:var(--warn)}.new{background:var(--ng);color:#fff;border-radius:4px;padding:0 4px;font-size:11px}
.sum,.memo{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 10px}
@media print{header{position:static}nav{display:none}section{display:block;page-break-after:always}}
"""

PAGE = (
    "<!doctype html><html lang='ja'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>{title} 作業予定</title><style>" + STYLE.replace("{", "{{").replace("}", "}}") + "</style></head><body>"
    "<header><h1>{title} 作業予定 <small>改-{rev}／{now} 出力／<a href='index.html'>日付一覧</a></small></h1>"
    "<nav>{tabs}</nav></header><main>{memo}{timeline}{panes}</main>"
    "<script>function show(k){{document.querySelectorAll('section').forEach(s=>s.classList.toggle('on',s.id==='s-'+k));"
    "document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('on',b.id==='b-'+k));"
    "try{{localStorage.setItem('tab',k)}}catch(e){{}}}}"
    "var t='前処理';try{{t=localStorage.getItem('tab')||t}}catch(e){{}}if(!document.getElementById('s-'+t))t='前処理';show(t);</script>"
    "</body></html>"
)

INDEX = (
    "<!doctype html><html lang='ja'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>作業予定 一覧</title><style>" + STYLE.replace("{", "{{").replace("}", "}}") + "li{{margin:6px 0;font-size:16px}}</style></head>"
    "<body><header><h1>作業予定 一覧</h1></header><main><ul>{links}</ul></main></body></html>"
)
