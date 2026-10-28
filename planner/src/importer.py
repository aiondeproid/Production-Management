"""既存Excelからの取り込み（最初の1回）

    python src/importer.py           … 製品マスタと、今週以降の週間予定を取り込む
    python src/importer.py --reset   … DBを消して取り込み直す（アプリで入力した内容も消える）

元のExcelは読むだけで、書き換えない。
結果は data/import_report.txt に書き出す。
"""
import datetime as dt
import re
import sys
from collections import Counter, defaultdict

import config  # lib/ を読み込み先に追加する
import xlrd

import db
from db import norm

# 週間予定表の列（0始まり）：ライン → (B数, 品名, コード, 数量)
LINE_COLS = {"A": (1, 2, 3, 4), "B": (5, 6, 7, 8), "C": (9, 10, 11, 12)}
D_COLS = (1, 2, 3, 4)  # 「新工場Ｄライン」枠
WEEKDAY_RE = re.compile(r"^[(（]([月火水木金土日])[)）]$")
D_DAY_RE = re.compile(r"^(\d{1,2})\s*[(（]([月火水木金土日])[)）]")


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def code_str(v) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return norm(v)


# ---------- 製品マスタ（Book1.xls Sheet1） ----------
def read_master(path) -> dict:
    sh = xlrd.open_workbook(path, on_demand=True).sheet_by_index(0)
    out = {}
    for r in range(2, sh.nrows):
        code = code_str(sh.cell_value(r, 0))
        if not code:
            continue
        bt = num(sh.cell_value(r, 3))
        out[code] = {
            "code": code,
            "name": norm(sh.cell_value(r, 1)),
            "yield_per_b": num(sh.cell_value(r, 22)),
            "packaging": norm(sh.cell_value(r, 17)),
            "batch_min": round(bt * 1440, 2) if bt else None,
            "memo": "",
            **forming_values(sh, r),
        }
    return out


def forming_values(sh, r) -> dict:
    """前工程の表に出す情報（ショット・配合・モールド板など）。空欄・0 は None にする"""
    out = {}
    for k, (c, typ, _) in db.FORMING_COLS.items():
        v = sh.cell_value(r, c) if c < sh.ncols else ""
        if typ == "REAL":
            v = num(v) or None
        else:
            v = code_str(v)  # 数字だけの欄（計数機設定など）は「100.0」でなく「100」にする
            v = None if v in ("", "0") else v
        out[k] = v
    return out


def fill_forming(con, path=None, overwrite=False) -> int:
    """製品マスタに前工程の情報（と分/B）を Book1 から入れる

    overwrite=False … 空の欄だけ埋める（列を追加したときに自動で実行）
    overwrite=True  … Book1 の値で上書きする（python src/importer.py --forming）
    週間予定表から登録した「92461」のようなコードは、英字違い（92461h）の値を使う。
    """
    master = read_master(path or config.load()["source_master_xls"])
    keys = list(db.FORMING_COLS)
    n = 0
    for code in [r[0] for r in con.execute("SELECT code FROM products")]:
        src = master.get(code)
        if not src:
            variants = [c for c in master if re.fullmatch(re.escape(code) + r"[a-z]", c)]
            src = master[variants[0]] if len(variants) == 1 else None
        if not src:
            continue
        if overwrite:
            sets = ", ".join(f"{k}=?" for k in keys) + ", batch_min=?"
            args = [src[k] for k in keys] + [src["batch_min"]]
        else:
            # 分/B は、前の版で小数1桁に丸めて入れた値（手で直していない値）なら、正確な値に直す
            sets = ", ".join(f"{k}=COALESCE({k}, ?)" for k in keys)
            sets += ", batch_min=CASE WHEN batch_min IS NULL OR round(batch_min, 1)=round(?, 1) THEN ? ELSE batch_min END"
            args = [src[k] for k in keys] + [src["batch_min"], src["batch_min"]]
        con.execute(f"UPDATE products SET {sets} WHERE code=?", args + [code])
        n += 1
    con.commit()
    return n


# ---------- 週間予定表（計画シート） ----------
def find_weeks(sh):
    """「11月16日～11月21日」の行を探して、各週の月曜日を決める

    表に年が書いていないので、月曜日になる年を前の週から順に当てはめる。
    """
    found = []
    for r in range(sh.nrows):
        t = norm(sh.cell_value(r, 0)).replace(" ", "").replace("～", "~").replace("〜", "~")
        m = re.fullmatch(r"(\d+)月(\d+)日~(\d+)月(\d+)日", t)
        if m:
            found.append((r, int(m.group(1)), int(m.group(2))))
    weeks, prev = [], None
    for r, mon, day in found:
        cands = [dt.date(y, mon, day) for y in range(2020, 2031) if dt.date(y, mon, day).weekday() == 0]
        if not cands:
            continue
        later = [c for c in cands if prev is None or c > prev]
        start = min(later or cands, key=lambda c: abs((c - (prev or dt.date(2024, 1, 1))).days))
        weeks.append((r, start))
        prev = start
    return weeks


def date_in_week(monday: dt.date, day: int):
    for i in range(7):
        d = monday + dt.timedelta(days=i)
        if d.day == day:
            return d
    return None


def read_weekly(path, from_monday: dt.date):
    """今週以降の週を読み、明細と、読めなかった行のメモを返す"""
    sh = xlrd.open_workbook(path, on_demand=True).sheet_by_index(0)
    cell = lambda r, c: sh.cell_value(r, c) if c < sh.ncols else ""
    weeks = find_weeks(sh)
    items, notes, checks, tare, betten = {}, [], [], {}, {}
    for wi, (r0, monday) in enumerate(weeks):
        r_end = weeks[wi + 1][0] if wi + 1 < len(weeks) else sh.nrows
        if monday < from_monday:
            continue
        week_items, week_tare, week_betten = [], [], []
        day, in_d = None, False
        for r in range(r0 + 1, r_end):
            a = norm(cell(r, 0))
            if "週間製造予定表" in a.replace(" ", "") or a.startswith("配布部署"):
                break  # 次の週の見出しに入った
            if "新工場" in a and "D" in a.upper():
                in_d, day = True, None
                continue
            if not in_d:
                # 日付の数字の次の行が「（月）」なら、その数字が日付
                nxt = norm(cell(r + 1, 0))
                if num(cell(r, 0)) is not None and WEEKDAY_RE.match(nxt):
                    day = date_in_week(monday, int(num(cell(r, 0))))
                if day is None:
                    continue
                for line, cols in LINE_COLS.items():
                    week_items += parse_entry(cell, r, cols, line, day, notes, checks)
                t = parse_tare(cell, r, day)
                if t:
                    week_tare.append(t)
                for kc, qc in BETTEN_COLS:
                    b = parse_betten(cell, r, kc, qc, day)
                    if b:
                        week_betten.append(b)
            else:
                m = D_DAY_RE.match(a)
                if m:
                    day = date_in_week(monday, int(m.group(1)))
                if day is None:
                    continue
                week_items += parse_entry(cell, r, D_COLS, "D", day, notes, checks)
        items[monday.isoformat()] = week_items  # 同じ週が2回あれば後の方を使う
        tare[monday.isoformat()] = week_tare
        betten[monday.isoformat()] = week_betten
    return items, notes, checks, tare, betten


TARE_NAME_COL, TARE_KG_COL = 15, 16  # 「タレ」欄（品名・数量）
BETTEN_COLS = ((17, 18), (21, 22))   # 「その他」欄（品名・数量）×2


def parse_betten(cell, r, kc, qc, day):
    """その他欄の別添タレ（4行のかたまり）

        89217   →92461
        JFSAあらびき肉団子
        タレ    1200kg
        包装機  3600袋
    """
    code = code_str(cell(r, kc))
    if not re.fullmatch(r"89\d{3}", code):
        return None
    m_to = re.search(r"(\d{5}[a-z]?)", norm(cell(r, qc)))
    m_kg = re.match(r"([\d,.]+)\s*kg", norm(cell(r + 2, qc)), re.I)
    m_bags = re.match(r"([\d,]+)\s*袋", norm(cell(r + 3, qc)))
    if not m_kg:
        return None
    return {
        "date": day.isoformat(), "code": code, "for": m_to.group(1) if m_to else "",
        "name": norm(cell(r + 1, kc)), "kg": float(m_kg.group(1).replace(",", "")),
        "bags": int(m_bags.group(1).replace(",", "")) if m_bags else None,
    }


def betten_master(betten_by_week) -> dict:
    """実績から、別添タレの半製品ごとの「使う製品」と「袋/kg」（いちばん多い値）を求める"""
    fors, ratios, names = defaultdict(Counter), defaultdict(Counter), {}
    for rows in betten_by_week.values():
        for b in rows:
            if b["for"]:
                fors[b["code"]][b["for"]] += 1
            if b["bags"] and b["kg"]:
                ratios[b["code"]][round(b["bags"] / b["kg"], 2)] += 1
            names[b["code"]] = b["name"]
    return {
        code: {
            "for": fors[code].most_common(1)[0][0] if fors[code] else "",
            "bags_per_kg": ratios[code].most_common(1)[0][0] if ratios[code] else None,
            "name": names[code],
        }
        for code in names
    }


def import_betten(con, weeks_betten: dict) -> list:
    """別添タレを取り込む（まだ別添タレのデータが無い週だけ）。袋数が計算と同じなら自動（空）にする"""
    prods = db.products(con)
    have = {r[0] for r in con.execute("SELECT DISTINCT date FROM betten_items")}
    report = []
    for monday, rows in sorted(weeks_betten.items()):
        if set(db.week_dates(monday)) & have:
            continue
        seq = defaultdict(int)
        n_fixed = 0
        for b in rows:
            ratio = (prods.get(b["code"]) or {}).get("bags_per_kg")
            bags = b["bags"]
            if bags is not None and ratio and abs(round(b["kg"] * ratio) - bags) <= 2:
                bags = None  # 自動計算と同じ
            n_fixed += bags is not None
            con.execute(
                "INSERT INTO betten_items(date, seq, code, kg, bags) VALUES(?,?,?,?,?)",
                (b["date"], seq[b["date"]], b["code"], b["kg"], bags),
            )
            seq[b["date"]] += 1
        report.append(f"{monday}：{len(rows)}件（うち袋数を手入力 {n_fixed}件）")
    con.commit()
    return report


def parse_tare(cell, r, day):
    """タレ欄：品名の行と、その下（または同じ行）の「1170kg」を読む"""
    name = norm(cell(r, TARE_NAME_COL))
    if not name or re.fullmatch(r"[\d.]+", name):
        return None
    for rr in (r + 1, r):
        m = re.match(r"([\d,.]+)\s*kg", norm(cell(rr, TARE_KG_COL)), re.I)
        if m:
            return {"date": day.isoformat(), "name": name, "kg": float(m.group(1).replace(",", ""))}
    return None


def same_name(a: str, b: str) -> bool:
    """週間予定表とマスタで表記が少し違っても同じ製品とみなす（先頭8文字で比べる）"""
    a, b = norm(a).replace(" ", ""), norm(b).replace(" ", "")
    return bool(a) and bool(b) and a[:8] == b[:8]


def tare_ratios(items_by_week, tare_by_week) -> dict:
    """過去の実績から、製品ごとの内添タレ kg/B（いちばん多い値）を求める"""
    by_day = defaultdict(list)
    for items in items_by_week.values():
        for it in items:
            by_day[it["date"]].append(it)
    ratios = defaultdict(Counter)
    for tare in tare_by_week.values():
        for t in tare:
            hit = [it for it in by_day[t["date"]] if same_name(it["name"], t["name"])]
            b = sum(it["batches"] for it in hit)
            if b:
                ratios[hit[0]["code"]][round(t["kg"] / b)] += 1
    return {code: c.most_common(1)[0][0] for code, c in ratios.items()}


def parse_entry(cell, r, cols, line, day, notes, checks):
    cb, cn, cc, cq = cols
    b, code, name, qty = num(cell(r, cb)), code_str(cell(r, cc)), norm(cell(r, cn)), cell(r, cq)
    if b and code and not re.fullmatch(r"\d{5}(\.0)?", name):  # 品名の欄が日付（シリアル値）の行は除く
        checks.append((day, line, code, name, b, num(qty)))
        return [{"date": day.isoformat(), "line": line, "code": code, "name": name, "batches": b}]
    if code and re.fullmatch(r"\d{5}[a-z]?", code) and not b and name and not name.startswith(("未確定", "内具")):
        notes.append(f"{day} {line}ライン {r + 1}行目：B数が無いので読み飛ばし（{code} {name}）")
    return []


HISTORY_FROM = dt.date(2025, 4, 1)


def import_tare(con, weeks: dict, tare_by_week: dict) -> list:
    """タレ欄を取り込む（まだタレのデータが無い週だけ）

    B × 内添タレkg/B で計算できる行は、アプリが毎回自動で出すので保存しない。
    計算と合わない行（量を変えた日・4号機の製品化の日など）だけを手入力の行として保存する。
    """
    prods = db.products(con)
    have = {r[0] for r in con.execute("SELECT DISTINCT date FROM tare_items")}
    report = []
    for monday, items in sorted(weeks.items()):
        if set(db.week_dates(monday)) & have:
            continue
        fixed = []
        for t in tare_by_week.get(monday, []):
            hit = [it for it in items if it["date"] == t["date"] and same_name(it["name"], t["name"])]
            code = hit[0]["code"] if hit else next((c for c, p in prods.items() if same_name(p["name"], t["name"])), "")
            kg_per_b = (prods.get(code) or {}).get("tare_kg_per_b")
            if hit and kg_per_b and abs(sum(i["batches"] for i in hit) * kg_per_b - t["kg"]) <= 1:
                continue  # 自動計算と同じ
            fixed.append({"date": t["date"], "code": code, "name": t["name"], "kg": t["kg"]})
        built = db.build_tare(items, fixed, prods)
        for t in built:
            if not t["auto"]:
                con.execute(
                    "INSERT INTO tare_items(date, seq, code, name, kg, auto) VALUES(?,?,?,?,?,0)",
                    (t["date"], t["seq"], t["code"], t["name"], t["kg"]),
                )
        excel_n = len(tare_by_week.get(monday, []))
        auto_n = sum(1 for t in built if t["auto"])
        report.append(f"{monday}：Excel {excel_n}件 → 自動 {auto_n}件＋手入力 {len(fixed)}件")
    con.commit()
    return report


def add_stub(con, it, master):
    """マスタに無いコードを登録する

    週間予定表は「92461」、Book1は「92461h」のように末尾の英字だけ違うことがある。
    英字違いが1つだけなら、その製品の出来高などを引き継ぐ。
    """
    variants = [c for c in master if re.fullmatch(re.escape(it["code"]) + r"[a-z]", c)]
    if len(variants) == 1:
        v = master[variants[0]]
        con.execute(
            "INSERT OR IGNORE INTO products(code, name, yield_per_b, packaging, batch_min, memo) VALUES(?,?,?,?,?,?)",
            (it["code"], it["name"] or v["name"], v["yield_per_b"], v["packaging"], v["batch_min"],
             f"{variants[0]} の値を引き継ぎ"),
        )
    else:
        con.execute(
            "INSERT OR IGNORE INTO products(code, name, memo) VALUES(?,?,?)",
            (it["code"], it["name"], "週間予定表から自動登録（出来高/B未設定）"),
        )


# ---------- 実行 ----------
def run(reset=False):
    cfg = config.load()
    if reset and db.DB_PATH.exists():
        db.DB_PATH.unlink()
    con = db.connect()
    master = read_master(cfg["source_master_xls"])
    for p in master.values():
        con.execute(
            "INSERT OR IGNORE INTO products(code, name, yield_per_b, packaging, batch_min, memo) VALUES(?,?,?,?,?,?)",
            (p["code"], p["name"], p["yield_per_b"], p["packaging"], p["batch_min"], p["memo"]),
        )
    con.commit()
    fill_forming(con, cfg["source_master_xls"])

    from_monday = db.monday_of(dt.date.today())
    # 内添タレ kg/B は過去の実績から求めるので、少し前から読む
    all_weeks, notes, all_checks, all_tare, all_betten = read_weekly(cfg["source_weekly_xls"], HISTORY_FROM)
    weeks = {m: v for m, v in all_weeks.items() if m >= from_monday.isoformat()}
    checks = [c for c in all_checks if c[0] >= from_monday]
    notes = [n for n in notes if n[:10] >= from_monday.isoformat()]
    ratios = tare_ratios(all_weeks, all_tare)
    for code, kg in ratios.items():
        # 末尾に英字が付いた同じ製品（91719b など）にも入れる
        con.execute(
            "UPDATE products SET tare_kg_per_b=? WHERE (code=? OR (code GLOB ? AND length(code)=?)) AND tare_kg_per_b IS NULL",
            (kg, code, code + "[a-z]", len(code) + 1),
        )

    existing = {r[0] for r in con.execute("SELECT DISTINCT date FROM weekly_items")}
    report, added_weeks = [], 0
    tare_report = import_tare(con, weeks, all_tare)

    # 別添タレ：半製品コードをマスタに登録（または「使う製品」「袋/kg」を補う）してから取り込む
    bmaster = betten_master(all_betten)
    for code, v in bmaster.items():
        target = (db.products(con).get(v["for"]) or {}).get("name") or v["name"]
        con.execute(
            "INSERT OR IGNORE INTO products(code, name, memo) VALUES(?,?,?)",
            (code, f"{target}のタレ（別添）", "週間予定表の別添タレから自動登録"),
        )
        con.execute(
            "UPDATE products SET betten_for=COALESCE(betten_for, ?), bags_per_kg=COALESCE(bags_per_kg, ?) WHERE code=?",
            (v["for"], v["bags_per_kg"], code),
        )
    con.commit()
    betten_report = import_betten(con, {m: v for m, v in all_betten.items() if m >= from_monday.isoformat()})
    # 発行の控えの形を直すのは、アプリ起動時（server.py）に行う
    for monday, items in sorted(weeks.items()):
        dates = set(db.week_dates(monday))
        if dates & existing:
            report.append(f"{monday} の週はすでにデータがあるので取り込みませんでした")
            continue
        seq = {}
        for it in items:
            if it["code"] not in master:
                add_stub(con, it, master)
            k = (it["date"], it["line"])
            con.execute(
                "INSERT INTO weekly_items(date, line, seq, code, batches) VALUES(?,?,?,?,?)",
                (it["date"], it["line"], seq.get(k, 0), it["code"], it["batches"]),
            )
            seq[k] = seq.get(k, 0) + 1
        con.execute(
            "INSERT INTO changelog(ts, week, user, message) VALUES(datetime('now','localtime'), ?, ?, ?)",
            (monday, "取込", f"週間予定表から取り込み（{len(items)}件）"),
        )
        added_weeks += 1
        report.append(f"{monday}（{db.week_title(monday)}）：{len(items)}件")
    con.commit()
    fill_forming(con, cfg["source_master_xls"])  # 週間予定表から自動登録したコードにも入れる

    # cs の照合：Excelの数量 と B数×出来高/B
    prods = db.products(con)
    mismatch = []
    for day, line, code, name, b, qty in checks:
        y = (prods.get(code) or {}).get("yield_per_b")
        if qty is not None and y and abs(round(b * y) - qty) > 1:
            mismatch.append(f"{day} {line} {code} {name}：Excel {qty:g}cs ／ 計算 {b:g}B×{y:g}={b * y:g}cs")
    no_yield = sorted({f"{c} {n}" for _, _, c, n, _, _ in checks if not (prods.get(c) or {}).get("yield_per_b")})

    lines = [
        f"取り込み日時：{dt.datetime.now():%Y-%m-%d %H:%M}",
        f"製品マスタ：{con.execute('SELECT COUNT(*) FROM products').fetchone()[0]}件",
        f"週間予定：{added_weeks}週を取り込み（{from_monday} の週以降）",
        *report, "",
        f"■ csがExcelと合わない明細（{len(mismatch)}件）", *mismatch, "",
        f"■ 出来高/B が未設定の製品（{len(no_yield)}件）… 製品マスタ画面で入力してください", *no_yield, "",
        f"■ 読み飛ばした行（{len(notes)}件）", *notes, "",
        f"■ 内添タレ kg/B を実績から設定（{len(ratios)}製品）",
        *[f"{c} {(prods.get(c) or {}).get('name', '')}：{kg}kg/B" for c, kg in sorted(ratios.items())], "",
        "■ タレ欄の取り込み（自動＝B×kg/Bで計算できた行、手入力＝計算と合わない行）", *tare_report, "",
        f"■ 別添タレの半製品（{len(bmaster)}件）",
        *[f"{c} → {v['for']} {v['name']}：{v['bags_per_kg']}袋/kg" for c, v in sorted(bmaster.items())], "",
        "■ 別添タレの取り込み", *betten_report,
    ]
    out = db.DATA_DIR / "import_report.txt"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:4 + len(report)]))
    print(f"csの不一致 {len(mismatch)}件／出来高未設定 {len(no_yield)}製品／読み飛ばし {len(notes)}行 → {out}")


if __name__ == "__main__":
    if "--forming" in sys.argv:
        c = db.connect()
        print(f"Book1 から前工程の情報（ショット・配合・モールド板・分/B など）を {fill_forming(c, overwrite=True)} 製品に入れ直しました")
    else:
        run(reset="--reset" in sys.argv)
