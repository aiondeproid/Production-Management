"""週間予定アプリのローカルWebサーバー（Python標準ライブラリ＋同梱ライブラリ）

起動：生産管理.bat（または python src/server.py）→ ブラウザが開く
"""
import atexit
import datetime as dt
import json
import mimetypes
import socket
import sys
import webbrowser
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import config
import day
import day_out
import db
import excel_out
import importer
import lock
from db import LINES

CFG = config.load()
SETTINGS = day.Settings(CFG)
STATIC = config.CODE_DIR / "static"
WEEKS_AHEAD = 9  # 2ヶ月先まで
DAY_KINDS = ("seikei", "zensho")


def md(d: str) -> str:
    return f"{int(d[5:7])}/{int(d[8:])}"


def monday_of(d: str) -> str:
    return db.monday_of(dt.date.fromisoformat(d)).isoformat()


# ---------- 差分 ----------
def totals(items):
    t = defaultdict(float)
    for it in items:
        t[(it["date"], it["line"], it["code"])] += it["batches"]
    return t


def tare_key(t):
    return t.get("code") or t.get("name", "")


def tare_diff(old, new, products):
    """タレ欄の変更点。赤字にするキーは (日付, 'タレ', コードまたは品名)"""
    md = lambda d: f"{int(d[5:7])}/{int(d[8:])}"
    label = lambda t: (products.get(t.get("code")) or {}).get("name") or t.get("name") or t.get("code")
    tot = lambda lst: {(t["date"], "タレ", tare_key(t)): t for t in lst}
    o, n = tot(old), tot(new)
    lines, keys = [], set()
    for k, t in n.items():
        if k not in o:
            keys.add(k)
            lines.append(f"タレ追加：{md(k[0])} {label(t)} {t['kg']:g}kg")
        elif o[k]["kg"] != t["kg"]:
            keys.add(k)
            lines.append(f"タレ変更：{md(k[0])} {label(t)} {o[k]['kg']:g}kg → {t['kg']:g}kg")
    for k, t in o.items():
        if k not in n:
            lines.append(f"タレ削除：{md(k[0])} {label(t)} {t['kg']:g}kg")
    return lines, keys


def betten_diff(old, new, products):
    """別添タレの変更点。赤字にするキーは (日付, '別添', 半製品コード)"""
    md = lambda d: f"{int(d[5:7])}/{int(d[8:])}"
    label = lambda c: (products.get(c) or {}).get("name") or c
    tot = lambda lst: {(b["date"], "別添", b["code"]): b for b in lst}
    o, n = tot(old), tot(new)
    lines, keys = [], set()
    for k, b in n.items():
        if k not in o:
            keys.add(k)
            lines.append(f"別添タレ追加：{md(k[0])} {label(b['code'])} {b['kg']:g}kg")
        elif (o[k]["kg"], o[k].get("bags")) != (b["kg"], b.get("bags")):
            keys.add(k)
            lines.append(f"別添タレ変更：{md(k[0])} {label(b['code'])} {o[k]['kg']:g}kg {o[k].get('bags') or ''}袋 → {b['kg']:g}kg {b.get('bags') or ''}袋")
    for k, b in o.items():
        if k not in n:
            lines.append(f"別添タレ削除：{md(k[0])} {label(b['code'])} {b['kg']:g}kg")
    return lines, keys


def week_diff(old_snapshot, items, tare, betten, products):
    """発行の控え → 今 の変更点（ライン明細＋タレ＋別添タレ）

    控えは起動時に今の形へ直してある（db.upgrade_snapshots）。
    """
    old_items, old_tare, old_betten = db.snapshot_parts(old_snapshot)
    l1, k1 = diff(old_items, items, products)
    l2, k2 = tare_diff(old_tare, tare, products)
    l3, k3 = betten_diff(old_betten, betten, products)
    return l1 + l2 + l3, k1 | k2 | k3


def week_tare(con, monday, items=None, products=None):
    """保存されている手入力の行に、自動の行を足したタレ欄"""
    products = products or db.products(con)
    items = db.load_week(con, monday) if items is None else items
    return db.build_tare(items, db.load_tare(con, monday), products)


def diff(old, new, products):
    """old→new の変更点。（文章のリスト, 赤字にするキーの集合）"""
    name = lambda c: (products.get(c) or {}).get("name") or c
    md = lambda d: f"{int(d[5:7])}/{int(d[8:])}"
    o, n = totals(old), totals(new)
    lines, keys = [], set()
    removed = [k for k in o if k not in n]
    added = [k for k in n if k not in o]
    for k in added:
        keys.add(k)
        src = next((r for r in removed if r[2] == k[2]), None)
        if src:  # 同じ製品が別の日・ラインから来た＝移動
            removed.remove(src)
            lines.append(f"移動：{name(k[2])} {md(src[0])} {src[1]}ライン → {md(k[0])} {k[1]}ライン {n[k]:g}B")
        else:
            lines.append(f"追加：{md(k[0])} {k[1]}ライン {name(k[2])} {n[k]:g}B")
    for k in removed:
        lines.append(f"削除：{md(k[0])} {k[1]}ライン {name(k[2])} {o[k]:g}B")
    for k in n:
        if k in o and o[k] != n[k]:
            keys.add(k)
            lines.append(f"変更：{md(k[0])} {k[1]}ライン {name(k[2])} {o[k]:g}B → {n[k]:g}B")
    return lines, keys


def clean(monday, raw):
    dates = set(db.week_dates(monday))
    out, seq = [], defaultdict(int)
    for it in raw:
        code = db.norm(it.get("code"))
        try:
            b = float(it.get("batches") or 0)
        except ValueError:
            b = 0
        if it.get("date") not in dates or it.get("line") not in LINES or not code or b <= 0:
            continue
        k = (it["date"], it["line"])
        out.append({"date": it["date"], "line": it["line"], "seq": seq[k], "code": code, "batches": b})
        seq[k] += 1
    return out


def clean_tare(monday, raw, products):
    """画面から来たタレ欄のうち、手入力の行（auto でない行）だけを取り出す"""
    dates = set(db.week_dates(monday))
    out, seq = [], defaultdict(int)
    for t in raw:
        if t.get("auto") or t.get("date") not in dates:
            continue
        code, name = db.norm(t.get("code")), db.norm(t.get("name"))
        try:
            kg = float(t.get("kg") or 0)
        except ValueError:
            kg = 0
        if kg <= 0 or not (code or name):
            continue
        name = name or (products.get(code) or {}).get("name", "")
        out.append({"date": t["date"], "seq": seq[t["date"]], "code": code, "name": name, "kg": kg})
        seq[t["date"]] += 1
    return out


def clean_betten(monday, raw):
    """画面から来た別添タレ。袋数を自動のままにした行は bags を空（NULL）で保存する"""
    dates = set(db.week_dates(monday))
    out, seq = [], defaultdict(int)
    for b in raw:
        code = db.norm(b.get("code"))
        try:
            kg = float(b.get("kg") or 0)
        except ValueError:
            kg = 0
        if b.get("date") not in dates or not code or kg <= 0:
            continue
        bags = None
        if not b.get("bags_auto") and b.get("bags") not in (None, ""):
            bags = int(float(b["bags"]))
        out.append({"date": b["date"], "seq": seq[b["date"]], "code": code, "kg": kg, "bags": bags})
        seq[b["date"]] += 1
    return out


def week_list(con):
    this = db.monday_of(dt.date.today())
    mondays = {(this + dt.timedelta(weeks=i)).isoformat() for i in range(-1, WEEKS_AHEAD + 1)}
    for (d,) in con.execute("SELECT DISTINCT date FROM weekly_items"):
        mondays.add(db.monday_of(dt.date.fromisoformat(d)).isoformat())
    out = []
    products = db.products(con)
    for m in sorted(mondays):
        items = db.load_week(con, m)
        rev = db.current_rev(con, m)
        changed = bool(rev) and bool(week_diff(
            json.loads(rev["snapshot"]), items, week_tare(con, m, items, products),
            db.load_betten(con, m, products), products,
        )[0])
        out.append({
            "monday": m, "title": db.week_title(m), "count": len(items),
            "rev": rev["rev"] if rev else None, "changed_since_issue": changed,
        })
    return out


# ---------- 作業予定 ----------
def day_state(con, date, products=None):
    """作業予定の画面に出す内容（行は時刻付き）"""
    products = products or db.products(con)
    rows, is_draft = day.rows_for(con, date, products, SETTINGS)
    weekly = day.weekly_rows(con, date)
    revs, snaps, changed = {}, {}, {}
    timed = day.compute(rows, products, SETTINGS)
    for kind in DAY_KINDS:
        rev = day.current_rev(con, date, kind)
        revs[kind] = {k: rev[k] for k in ("rev", "issued_at", "user")} if rev else None
        snaps[kind] = json.loads(rev["snapshot"]) if rev else None
        changed[kind] = bool(rev) and bool(day.diff(kind, snaps[kind], timed, products)[0])
    return {
        "date": date, "rows": timed, "draft": is_draft, "weekly": weekly,
        "mismatch": day.mismatch(rows, weekly), "revs": revs, "snapshots": snaps,
        "changed_since_issue": changed,
    }


def day_list(con, monday):
    products = db.products(con)
    out = []
    for d in db.week_dates(monday):
        s = day_state(con, d, products)
        out.append({
            "date": d, "count": len(s["weekly"]), "draft": s["draft"], "mismatch": len(s["mismatch"]),
            "revs": {k: (v["rev"] if v else None) for k, v in s["revs"].items()},
            "changed_since_issue": s["changed_since_issue"],
        })
    return out


# ---------- 後処理日報・洗浄記録 ----------
# 行ごとに書き込む欄 → 変更履歴に出す名前（✓の欄は "✓" か ""）
POST_FIELDS = {
    "actual": "出来高", "limited": "数量限定", "wash": "洗浄", "wash_mix": "洗浄（配合）", "wash_form": "洗浄（成型）",
    "wash_time": "洗浄時刻", "wash_by": "担当者", "checked_by": "確認者",
}
EXTRA_MAX = 4  # 後処理日報の「その他」の行数（用紙の行数）


def clean_post(raw: dict) -> dict:
    s = lambda v: db.norm(v)[:60]
    rows = {}
    for k, r in (raw.get("rows") or {}).items():
        if len(str(k).split("|")) != 3 or not isinstance(r, dict):
            continue
        r = {f: s(r.get(f)) for f in POST_FIELDS if s(r.get(f))}
        if r:
            rows[str(k)] = r
    extra = []
    for x in (raw.get("extra") or [])[:EXTRA_MAX]:
        x = {"code": s(x.get("code")), "plan": s(x.get("plan")), "actual": s(x.get("actual"))}
        if any(x.values()):
            extra.append(x)
    return {"rows": rows, "extra": extra, "qc": s(raw.get("qc"))}


def load_post(con, date: str) -> dict:
    r = con.execute("SELECT data, updated_at, user FROM post_records WHERE date=?", (date,)).fetchone()
    data = clean_post(json.loads(r["data"])) if r else clean_post({})
    return {"data": data, "updated_at": r["updated_at"] if r else None, "user": r["user"] if r else None}


def post_state(con, date: str) -> dict:
    """後処理の画面に出す内容：作業予定の行（時刻付き）と書き込んだ内容"""
    products = db.products(con)
    rows, is_draft = day.rows_for(con, date, products, SETTINGS)
    return {"date": date, "rows": day.compute(rows, products, SETTINGS), "draft": is_draft,
            "extra_max": EXTRA_MAX, **load_post(con, date)}


# ---------- HTTP ----------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if not url.path.startswith("/api/"):
            return self.static(url.path)
        con = db.connect()
        try:
            if url.path == "/api/state":
                return self.send_json({
                    "lock": lock.status(), "weeks": week_list(con), "today": dt.date.today().isoformat(),
                    "output_dir": CFG["output_dir"], "distribution": CFG["distribution"],
                })
            if url.path == "/api/products":
                return self.send_json(list(db.products(con).values()))
            if url.path == "/api/week":
                m = q["monday"]
                rev = db.current_rev(con, m)
                return self.send_json({
                    "monday": m, "title": db.week_title(m), "dates": db.week_dates(m),
                    "items": db.load_week(con, m),
                    "tare": week_tare(con, m),
                    "betten": db.load_betten(con, m),
                    "rev": {k: rev[k] for k in ("rev", "issued_at", "user")} if rev else None,
                    "snapshot": json.loads(rev["snapshot"]) if rev else None,
                })
            if url.path == "/api/days":
                return self.send_json({
                    "monday": q["monday"], "days": day_list(con, q["monday"]), "settings": SETTINGS.as_dict(),
                    "day_output_dir": CFG["day_output_dir"],
                    "columns": [{"key": k, "label": label, "width": w, "default": d} for k, label, w, d in day.COLUMNS],
                    "print_cols": day.print_columns(con),
                })
            if url.path == "/api/day":
                return self.send_json(day_state(con, q["date"]))
            if url.path == "/api/post":
                return self.send_json(post_state(con, q["date"]))
            if url.path == "/api/changelog":
                sql, args = "SELECT * FROM changelog", ()
                if q.get("week"):
                    sql, args = sql + " WHERE week=?", (q["week"],)
                return self.send_json([dict(r) for r in con.execute(sql + " ORDER BY id DESC LIMIT 300", args)])
            self.send_json({"error": "not found"}, 404)
        finally:
            con.close()

    def do_POST(self):
        path = urlparse(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        data = json.loads(self.rfile.read(n) or b"{}")
        if path == "/api/lock/take":
            lock.set_name(data.get("user", ""))
            ok = lock.acquire(force=True)
            if ok:
                lock.backup(CFG["backup_keep"])
            return self.send_json(lock.status())
        if path == "/api/lock/name":
            lock.set_name(data.get("user", ""))
            return self.send_json({"ok": True})
        if not lock.is_mine():
            return self.send_json({"error": "他の人が編集中のため、保存できません（閲覧のみ）"}, 409)
        con = db.connect()
        try:
            if path == "/api/week":
                return self.save_week(con, data)
            if path == "/api/issue":
                return self.issue(con, data)
            if path == "/api/products":
                return self.save_product(con, data)
            if path == "/api/day":
                return self.save_day(con, data)
            if path == "/api/day/issue":
                return self.issue_day(con, data)
            if path == "/api/post":
                return self.save_post(con, data)
            if path == "/api/day/print_cols":
                # 成型予定表に印刷する項目を保存する（みんなで共通）
                cols = [k for k in day.COLUMN_KEYS if k in (data.get("cols") or [])]
                if not cols:
                    raise ValueError("印刷する項目を1つ以上選んでください")
                db.set_setting(con, "seikei_print_cols", cols)
                return self.send_json({"ok": True, "print_cols": cols})
            if path == "/api/day/sync":
                # 画面の行を週間予定の B数に合わせた行を返す（保存はしない）
                products = db.products(con)
                rows = day.sync(day.clean(data["date"], data.get("rows", [])), day.weekly_rows(con, data["date"]), products, SETTINGS)
                return self.send_json({"rows": day.compute(rows, products, SETTINGS)})
            if path == "/api/day/draft":
                # 週間予定から作り直した案を返す（保存はしない）
                products = db.products(con)
                rows = day.draft(day.weekly_rows(con, data["date"]), products, SETTINGS)
                return self.send_json({"rows": day.compute(rows, products, SETTINGS)})
            self.send_json({"error": "not found"}, 404)
        except Exception as ex:
            self.send_json({"error": str(ex)}, 400)
        finally:
            con.close()

    def save_week(self, con, data):
        m, user = data["monday"], (data.get("user") or "").strip() or lock.ME["user"]
        products = db.products(con)
        new = clean(m, data.get("items", []))
        new_fixed = clean_tare(m, data.get("tare", []), products)
        new_betten = clean_betten(m, data.get("betten", []))
        old = db.load_week(con, m)
        changes, _ = diff(old, new, products)
        changes += tare_diff(week_tare(con, m, old, products), db.build_tare(new, new_fixed, products), products)[0]
        changes += betten_diff(
            db.load_betten(con, m, products), [db.with_bags(b, products) for b in new_betten], products
        )[0]
        dates = db.week_dates(m)
        marks = ",".join("?" * len(dates))
        con.execute(f"DELETE FROM weekly_items WHERE date IN ({marks})", dates)
        con.executemany(
            "INSERT INTO weekly_items(date, line, seq, code, batches) VALUES(?,?,?,?,?)",
            [(i["date"], i["line"], i["seq"], i["code"], i["batches"]) for i in new],
        )
        con.execute(f"DELETE FROM tare_items WHERE date IN ({marks})", dates)
        con.executemany(
            "INSERT INTO tare_items(date, seq, code, name, kg, auto) VALUES(?,?,?,?,?,0)",
            [(t["date"], t["seq"], t["code"], t["name"], t["kg"]) for t in new_fixed],
        )
        con.execute(f"DELETE FROM betten_items WHERE date IN ({marks})", dates)
        con.executemany(
            "INSERT INTO betten_items(date, seq, code, kg, bags) VALUES(?,?,?,?,?)",
            [(b["date"], b["seq"], b["code"], b["kg"], b["bags"]) for b in new_betten],
        )
        if changes:
            con.execute(
                "INSERT INTO changelog(ts, week, user, message) VALUES(datetime('now','localtime'), ?, ?, ?)",
                (m, user, "保存\n" + "\n".join(changes)),
            )
        con.commit()
        self.send_json({"ok": True, "changes": changes})

    def issue(self, con, data):
        """発行：控えを残して改版を上げ、配布用Excelを書き出す"""
        m, user = data["monday"], (data.get("user") or "").strip() or lock.ME["user"]
        products = db.products(con)
        items = db.load_week(con, m)
        tare = week_tare(con, m, items, products)
        betten = db.load_betten(con, m, products)
        prev = db.current_rev(con, m)
        if prev:
            changes, keys = week_diff(json.loads(prev["snapshot"]), items, tare, betten, products)
            if not changes:
                when = dt.datetime.fromisoformat(prev["issued_at"])
                path = self.write_excel(m, items, tare, betten, products, prev["rev"], when, set(), [])
                return self.send_json({"rev": prev["rev"], "path": str(path), "changes": [], "note": "前回の発行から変更がないので、同じ版で出力し直しました"})
            rev = prev["rev"] + 1
        else:
            changes, keys, rev = [], set(), 0
        now = dt.datetime.now()
        path = self.write_excel(m, items, tare, betten, products, rev, now, keys, changes)
        snapshot = {"items": items, "tare": tare, "betten": betten}
        con.execute(
            "INSERT INTO revisions(week, rev, issued_at, user, snapshot, changes) VALUES(?,?,?,?,?,?)",
            (m, rev, now.isoformat(timespec="seconds"), user, json.dumps(snapshot, ensure_ascii=False), "\n".join(changes)),
        )
        con.execute(
            "INSERT INTO changelog(ts, week, user, message) VALUES(datetime('now','localtime'), ?, ?, ?)",
            (m, user, f"発行 {excel_out.rev_label(rev)}" + ("\n" + "\n".join(changes) if changes else "")),
        )
        con.commit()
        self.send_json({"rev": rev, "label": excel_out.rev_label(rev), "path": str(path), "changes": changes})

    def write_excel(self, m, items, tare, betten, products, rev, when, keys, changes):
        wb = excel_out.build(m, items, tare, betten, products, rev, when, keys, changes, CFG["distribution"])
        return excel_out.save(wb, CFG["output_dir"], m)

    def save_day(self, con, data):
        date, user = data["date"], (data.get("user") or "").strip() or lock.ME["user"]
        products = db.products(con)
        new = day.clean(date, data.get("rows", []))
        old_rows, was_draft = day.rows_for(con, date, products, SETTINGS)
        changes, _ = day.diff("seikei", day.compute(old_rows, products, SETTINGS), day.compute(new, products, SETTINGS), products)
        con.execute("DELETE FROM day_rows WHERE date=?", (date,))
        con.executemany(
            "INSERT INTO day_rows(date, line, seq, code, batches, gap, start) VALUES(?,?,?,?,?,?,?)",
            [(date, r["line"], r["seq"], r["code"], r["batches"], r["gap"], r["start"]) for r in new],
        )
        # 作業予定で変えた B数を週間予定にも反映する（タレ欄の自動の行は週間予定から計算するので追従する）
        weekly = day.weekly_rows(con, date)
        new_weekly = day.to_weekly(date, weekly, old_rows, new)
        weekly_changes, _ = diff(weekly, new_weekly, products)
        name = lambda c: (products.get(c) or {}).get("name") or c
        for ln in LINES:
            before = [w["code"] for w in weekly if w["line"] == ln]
            after = [w["code"] for w in new_weekly if w["line"] == ln]
            if before != after and sorted(before) == sorted(after):
                weekly_changes.append(f"順番：{md(date)} {ln}ライン " + " → ".join(name(c) for c in after))
        if weekly_changes:
            con.execute("DELETE FROM weekly_items WHERE date=?", (date,))
            con.executemany(
                "INSERT INTO weekly_items(date, line, seq, code, batches) VALUES(?,?,?,?,?)",
                [(w["date"], w["line"], w["seq"], w["code"], w["batches"]) for w in new_weekly],
            )
        if changes or was_draft or weekly_changes:
            msg = f"作業予定 {md(date)} 保存" + ("（週間予定からの案を保存）" if was_draft else "")
            if changes:
                msg += "\n" + "\n".join(changes)
            if weekly_changes:
                msg += "\n週間予定に反映\n" + "\n".join(weekly_changes)
            con.execute(
                "INSERT INTO changelog(ts, week, user, message) VALUES(datetime('now','localtime'), ?, ?, ?)",
                (monday_of(date), user, msg),
            )
        con.commit()
        self.send_json({"ok": True, "changes": changes, "weekly_changes": weekly_changes})

    def save_post(self, con, data):
        """後処理日報・洗浄記録に書き込んだ内容を保存する（変えた欄を変更履歴に残す）"""
        date, user = data["date"], (data.get("user") or "").strip() or lock.ME["user"]
        new = clean_post(data.get("data") or {})
        old = load_post(con, date)["data"]
        products = db.products(con)
        name = lambda c: (products.get(c) or {}).get("name") or c
        changes = []
        for k in sorted(set(old["rows"]) | set(new["rows"])):
            ln, code, _ = k.split("|")
            o, n = old["rows"].get(k, {}), new["rows"].get(k, {})
            for f, label in POST_FIELDS.items():
                if (o.get(f) or "") != (n.get(f) or ""):
                    changes.append(f"{ln}ライン {name(code)} {label}：{o.get(f) or '（空）'} → {n.get(f) or '（空）'}")
        if old["extra"] != new["extra"]:
            changes.append("その他：" + ("、".join(f"{name(x['code'])} {x['plan']}/{x['actual']}" for x in new["extra"]) or "（なし）"))
        if old["qc"] != new["qc"]:
            changes.append(f"品管確認：{old['qc'] or '（空）'} → {new['qc'] or '（空）'}")
        con.execute(
            "INSERT OR REPLACE INTO post_records(date, data, updated_at, user) VALUES(?, ?, datetime('now','localtime'), ?)",
            (date, json.dumps(new, ensure_ascii=False), user),
        )
        if changes:
            con.execute(
                "INSERT INTO changelog(ts, week, user, message) VALUES(datetime('now','localtime'), ?, ?, ?)",
                (monday_of(date), user, f"後処理日報・洗浄記録 {md(date)} 保存\n" + "\n".join(changes)),
            )
        con.commit()
        self.send_json({"ok": True, "changes": changes})

    def issue_day(self, con, data):
        """成型予定表・前処理製造予定表の発行：控えを残して改版を上げ、Excelを書き出す"""
        date, kind = data["date"], data["kind"]
        user = (data.get("user") or "").strip() or lock.ME["user"]
        if kind not in DAY_KINDS:
            raise ValueError("発行する表の種類が違います")
        products = db.products(con)
        if not day.is_saved(con, date):
            raise ValueError("先に作業予定を保存してください")
        rows = day.compute(day.load(con, date), products, SETTINGS)
        prev = day.current_rev(con, date, kind)
        title = day_out.KIND_TITLE[kind]
        cols = day.print_columns(con)
        if prev:
            changes, keys = day.diff(kind, json.loads(prev["snapshot"]), rows, products)
            if not changes:
                when = dt.datetime.fromisoformat(prev["issued_at"])
                path = day_out.save(day_out.build(kind, date, rows, products, prev["rev"], when, set(), [], cols=cols),
                                    CFG["day_output_dir"], kind, date)
                return self.send_json({"rev": prev["rev"], "path": str(path), "changes": [],
                                       "note": "前回の発行から変更がないので、同じ版で出力し直しました"})
            rev = prev["rev"] + 1
        else:
            changes, keys, rev = [], set(), 0
        now = dt.datetime.now()
        path = day_out.save(day_out.build(kind, date, rows, products, rev, now, keys, changes, cols=cols),
                            CFG["day_output_dir"], kind, date)
        con.execute(
            "INSERT INTO day_revisions(date, kind, rev, issued_at, user, snapshot, changes) VALUES(?,?,?,?,?,?,?)",
            (date, kind, rev, now.isoformat(timespec="seconds"), user, json.dumps(rows, ensure_ascii=False), "\n".join(changes)),
        )
        con.execute(
            "INSERT INTO changelog(ts, week, user, message) VALUES(datetime('now','localtime'), ?, ?, ?)",
            (monday_of(date), user, f"{title} {md(date)} 発行 {excel_out.rev_label(rev)}" + ("\n" + "\n".join(changes) if changes else "")),
        )
        con.commit()
        self.send_json({"rev": rev, "label": excel_out.rev_label(rev), "path": str(path), "changes": changes})

    def save_product(self, con, data):
        code = db.norm(data.get("code"))
        if not code:
            return self.send_json({"error": "コードが空です"}, 400)
        keys = ("name", "yield_per_b", "packaging", "batch_min", "memo", "tare_kg_per_b", "betten_for", "bags_per_kg",
                *db.FORMING_COLS)
        fields = {k: data[k] for k in keys if k in data}
        if con.execute("SELECT 1 FROM products WHERE code=?", (code,)).fetchone():
            if fields:
                con.execute(f"UPDATE products SET {', '.join(f'{k}=?' for k in fields)} WHERE code=?", [*fields.values(), code])
        else:
            fields.setdefault("name", code)
            con.execute(
                f"INSERT INTO products(code, {', '.join(fields)}) VALUES(?{', ?' * len(fields)})", [code, *fields.values()]
            )
        con.commit()
        self.send_json({"ok": True})

    def static(self, path):
        rel = unquote(path.lstrip("/")) or "index.html"
        f = (STATIC / rel).resolve()
        if STATIC.resolve() not in f.parents or not f.is_file():
            self.send_response(404)
            self.end_headers()
            return
        body = f.read_bytes()
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        if ctype.startswith("text/") or "javascript" in ctype:
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class Server(ThreadingHTTPServer):
    # Windows では SO_REUSEADDR だと同じポートを二重に使えてしまうので、使わずに専有する
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def open_server():
    """使っていないポートを探して起動する（同じPCで2つ開いた場合など）"""
    for port in range(CFG["port"], CFG["port"] + 10):
        try:
            return Server(("127.0.0.1", port), Handler), port
        except OSError:
            continue
    raise SystemExit("空いているポートが見つかりません")


def main():
    if not db.DB_PATH.exists():
        print("初回起動：Excelから製品マスタと週間予定を取り込みます…")
        importer.run()
    if lock.acquire():
        dst = lock.backup(CFG["backup_keep"])
        con = db.connect()
        db.upgrade_snapshots(con)  # 前の版で発行した控えをタレ入りの形に直す
        con.close()
        print(f"編集モードで起動しました（バックアップ：{dst.name if dst else 'なし'}）")
    else:
        h = lock.read() or {}
        print(f"{h.get('name') or h.get('user')}さん（{h.get('host')}）が編集中のため、閲覧のみで起動しました")
    lock.start_heartbeat()
    atexit.register(lock.release)
    server, port = open_server()
    url = f"http://127.0.0.1:{port}/"
    print(f"週間予定アプリ：{url}　（終了するときはこの画面を閉じるか Ctrl+C）")
    print("※このURLはこのPCの中だけで使えます。他のPCの人は、そのPCで 生産管理.bat を起動してください")
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        lock.release()


if __name__ == "__main__":
    main()
