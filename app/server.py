"""計画ボードのローカルWebサーバー（Python標準ライブラリのみ）

起動: python server.py  →  http://127.0.0.1:8765/
"""
import json
import mimetypes
import sys
import webbrowser
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import db
import export
import importer
import rules

PORT = 8765
STATIC = db.APP_DIR / "static"


def describe_changes(old, new, products):
    """保存前後の差分を、人が読める文章にする"""
    name = lambda c: (products.get(c) or {}).get("name") or c

    def totals(items):
        t = defaultdict(float)
        for it in items:
            t[(it["line"], it["code"])] += it["batches"]
        return t

    o, n = totals(old), totals(new)
    lines = []
    for key in sorted(set(o) | set(n)):
        ln, code = key
        if key not in o:
            moved = [k[0] for k in o if k[1] == code and k not in n]
            if moved:
                lines.append(f"移動：{name(code)} {moved[0]}→{ln}ライン {n[key]:g}B")
            else:
                lines.append(f"追加：{ln}ライン {name(code)} {n[key]:g}B")
        elif key not in n:
            if not any(k[1] == code and k not in o for k in n):
                lines.append(f"削除：{ln}ライン {name(code)} {o[key]:g}B")
        elif o[key] != n[key]:
            lines.append(f"変更：{ln}ライン {name(code)} {o[key]:g}B→{n[key]:g}B")
    for ln in db.LINES:
        seq_o = [i["code"] for i in sorted(old, key=lambda i: i["seq"]) if i["line"] == ln]
        seq_n = [i["code"] for i in sorted(new, key=lambda i: i["seq"]) if i["line"] == ln]
        if seq_o != seq_n and sorted(seq_o) == sorted(seq_n):
            lines.append(f"順番変更：{ln}ライン")
    starts_o = {(i["line"], i["seq"], i["code"]): i.get("start_min") for i in old}
    for i in new:
        k = (i["line"], i["seq"], i["code"])
        if k in starts_o and starts_o[k] != i.get("start_min"):
            lines.append(f"時刻変更：{i['line']}ライン {name(i['code'])}")
    return lines


def clean_items(raw):
    items = []
    for ln in db.LINES:
        seq = 0
        for it in (x for x in raw if x.get("line") == ln):
            code = db.norm(it.get("code"))
            try:
                batches = float(it.get("batches") or 0)
            except ValueError:
                batches = 0
            if not code or batches <= 0:
                continue
            start = it.get("start_min")
            items.append({
                "line": ln, "seq": seq, "code": code, "batches": batches,
                "start_min": int(start) if start not in (None, "") else None,
                "gap_min": int(it.get("gap_min") or 0), "memo": str(it.get("memo") or ""),
            })
            seq += 1
    return items


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path
        if not path.startswith("/api/"):
            return self.static(path)
        con = db.connect()
        try:
            if path == "/api/products":
                return self.send_json({"products": list(db.all_products(con).values()), "fields": db.PRODUCT_FIELDS})
            if path == "/api/plan":
                date = q["date"]
                items = db.load_plan(con, date)
                result = rules.check(date, items, db.all_products(con), db.get_settings(con), db.holidays(con))
                return self.send_json({"items": items, "meta": db.load_meta(con, date), "manual": rules.MANUAL_CHECKS, **result})
            if path == "/api/dates":
                rows = con.execute("SELECT DISTINCT date FROM plan_items ORDER BY date").fetchall()
                return self.send_json([r[0] for r in rows])
            if path == "/api/changelog":
                sql, args = "SELECT * FROM changelog", ()
                if q.get("date"):
                    sql, args = sql + " WHERE date=?", (q["date"],)
                rows = con.execute(sql + " ORDER BY id DESC LIMIT 300", args).fetchall()
                return self.send_json([dict(r) for r in rows])
            if path == "/api/settings":
                return self.send_json({"settings": db.get_settings(con), "holidays": db.holidays(con)})
            self.send_json({"error": "not found"}, 404)
        finally:
            con.close()

    def do_POST(self):
        path = urlparse(self.path).path
        data = self.body()
        con = db.connect()
        try:
            if path == "/api/check":
                items = clean_items(data.get("items", []))
                return self.send_json(rules.check(data["date"], items, db.all_products(con), db.get_settings(con), db.holidays(con)))
            if path == "/api/plan":
                return self.save_plan(con, data)
            if path == "/api/export":
                p = export.export_day(con, data["date"])
                return self.send_json({"path": str(p)})
            if path == "/api/products":
                return self.save_product(con, data)
            if path == "/api/settings":
                db.save_settings(con, data.get("settings", {}))
                if "holidays" in data:
                    con.execute("DELETE FROM holidays")
                    for d, name in data["holidays"].items():
                        con.execute("INSERT INTO holidays(date, name) VALUES(?, ?)", (d, name))
                    con.commit()
                return self.send_json({"ok": True})
            self.send_json({"error": "not found"}, 404)
        except Exception as ex:  # 画面にエラー内容を返す
            self.send_json({"error": str(ex)}, 400)
        finally:
            con.close()

    def save_plan(self, con, data):
        date, user = data["date"], (data.get("user") or "").strip() or "未入力"
        items = clean_items(data.get("items", []))
        products = db.all_products(con)
        old = db.load_plan(con, date)
        changes = describe_changes(old, items, products)
        meta = data.get("meta", {})
        con.execute("DELETE FROM plan_items WHERE date=?", (date,))
        for it in items:
            con.execute(
                "INSERT INTO plan_items(date, line, seq, code, batches, start_min, gap_min, memo) VALUES(?,?,?,?,?,?,?,?)",
                (date, it["line"], it["seq"], it["code"], it["batches"], it["start_min"], it["gap_min"], it["memo"]),
            )
        rev = db.load_meta(con, date)["revision"] + (1 if changes else 0)
        con.execute(
            "REPLACE INTO day_meta(date, checklist, memo, revision) VALUES(?,?,?,?)",
            (date, json.dumps(meta.get("checklist", {}), ensure_ascii=False), meta.get("memo", ""), rev),
        )
        if changes:
            con.execute(
                "INSERT INTO changelog(ts, date, user, message) VALUES(datetime('now','localtime'), ?, ?, ?)",
                (date, user, f"改-{rev}\n" + "\n".join(changes)),
            )
        con.commit()
        self.send_json({"ok": True, "revision": rev, "changes": changes})

    def save_product(self, con, data):
        code = db.norm(data.get("code"))
        if not code:
            return self.send_json({"error": "コードが空です"}, 400)
        fields = {k: data[k] for k in db.PRODUCT_KEYS if k in data}
        fields["code"] = code
        exists = con.execute("SELECT 1 FROM products WHERE code=?", (code,)).fetchone()
        if exists:
            sets = ", ".join(f"{k}=?" for k in fields if k != "code")
            con.execute(f"UPDATE products SET {sets} WHERE code=?", [v for k, v in fields.items() if k != "code"] + [code])
        else:
            cols = ", ".join(fields)
            con.execute(f"INSERT INTO products({cols}) VALUES({', '.join('?' for _ in fields)})", list(fields.values()))
        con.commit()
        self.send_json({"ok": True})

    def static(self, path):
        rel = unquote(path.lstrip("/")) or "index.html"
        f = (STATIC / rel).resolve()
        if STATIC.resolve() not in f.parents or not f.is_file():
            self.send_response(404)
            self.end_headers()
            return
        data = f.read_bytes()
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def main():
    if not db.DB_PATH.exists():
        print("初回起動：Excelから製品マスタと作業予定を取り込みます…")
        importer.run()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}/"
    print(f"計画ボードを起動しました：{url}（終了は Ctrl+C）")
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
