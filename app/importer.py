"""既存Excelから製品マスタ・作業予定を取り込む

使い方:
    python importer.py            … まだ無い製品・日付だけ追加する
    python importer.py --reset    … DBを作り直して全部取り込む

元のExcelは読み取るだけで変更しない。
"""
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import xlrd

import db
from db import norm

SRC_DIR = db.APP_DIR.parent
MASTER_XLS = SRC_DIR / "Book1.xls"
SCHEDULE_XLS = SRC_DIR / "作業予定表_復旧 (2).xls"

# 作業予定表の各ライン見出し行（0始まり）。全シート共通の配置
LINE_HEADER_ROWS = {"A": 2, "B": 13, "C": 24, "D": 31}


def code_str(v) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return norm(v)


def num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def has_any(text: str, words) -> bool:
    t = norm(text)
    return any(norm(w) in t for w in words)


def derive_flags(p: dict) -> dict:
    """製品名や備考から計画ルール用の属性を推定する（後で画面から直せる）"""
    name, pre, caution = p.get("name", ""), p.get("pre_note", ""), p.get("caution", "")
    pack, mold = p.get("packaging", ""), p.get("mold", "")
    text = f"{name} {pre} {caution}"
    f = {}
    f["asa_ichi"] = int(
        has_any(name, ["PG", "HG豆腐", "HG新豆腐", "三育", "ビーフパティ", "ボタプラス"])
        or has_any(pack, ["ムルチ4", "ポリスター"])
    )
    f["tanitsu"] = int(has_any(text, ["単一"]))
    f["last_special"] = int(
        has_any(text, ["魚", "鮭", "サケ", "たら", "タラ", "あじ", "アジ", "さんが焼", "ひじき", "ヒジキ", "大葉", "いわし", "サバ"])
    )
    if has_any(name, ["ロティール"]):
        f["rotiru"] = "derived" if has_any(name, ["ミートボール", "バーガーパティ"]) else "main"
        f["drain_group"] = "ロティール"
    elif has_any(name, ["フジ"]):
        f["rotiru"], f["drain_group"] = "", "フジ食品"
    else:
        f["rotiru"], f["drain_group"] = "", ""
    f["cheese"] = int(has_any(f"{name} {pre}", ["チーズ"]))
    f["kaseijin"] = int(has_any(mold, ["火星人"]))
    if has_any(name, ["団子", "だんご", "ボール"]):
        f["dango_type"] = "団子"
    elif has_any(name, ["ハンバーグ", "バーグ", "HB", "パティ"]):
        f["dango_type"] = "HB"
    else:
        f["dango_type"] = ""
    return f


def read_master() -> dict:
    sh = xlrd.open_workbook(MASTER_XLS, on_demand=True).sheet_by_index(0)
    cell = lambda r, c: sh.cell_value(r, c) if c < sh.ncols else ""
    out = {}
    for r in range(2, sh.nrows):
        code = code_str(cell(r, 0))
        if not code:
            continue
        batch_days = num(cell(r, 3))
        p = {
            "code": code,
            "name": norm(cell(r, 1)),
            "shot": num(cell(r, 2)),
            "batch_min": round(batch_days * 1440, 1) if batch_days else None,
            "mix": norm(cell(r, 4)),
            "mold": norm(cell(r, 5)),
            "pre_note": norm(cell(r, 6)),
            "pre_color": norm(cell(r, 7)),
            "wheat": int(norm(cell(r, 11)) == "○"),
            "milk": int(norm(cell(r, 12)) == "○"),
            "egg": int(norm(cell(r, 13)) == "○"),
            "pre_regen": norm(cell(r, 14)),
            "packaging": norm(cell(r, 17)),
            "post_color": norm(cell(r, 19)),
            "spec": norm(cell(r, 21)),
            "yield_per_b": num(cell(r, 22)),
            "caution": norm(cell(r, 52)),
            "post_regen": norm(cell(r, 53)),
            "wash_note": norm(cell(r, 54)),
            "meat": norm(cell(r, 55)),
            "plate": norm(cell(r, 80)),
            "metal_check": norm(cell(r, 81)),
            "thaw_kg": None,
            "default_line": "",
            "is_new": 0,
            "memo": "",
        }
        p.update(derive_flags(p))
        out[code] = p  # 同じコードが複数あれば下の行（新しい方）を採用
    return out


def schedule_sheets(wb):
    """「10月13日」のような日付シートだけを返す（コピー・(2)などは除く）"""
    for i, name in enumerate(wb.sheet_names()):
        if re.fullmatch(r"\d{1,2}月\d{1,2}日", name.strip()):
            yield wb.sheet_by_index(i)


def read_schedules():
    wb = xlrd.open_workbook(SCHEDULE_XLS, on_demand=True)
    plans, stubs, thaw = {}, {}, {}
    line_count = defaultdict(Counter)
    sheets = list(schedule_sheets(wb))
    for sh in sheets:
        serial = num(sh.cell_value(1, 2))
        if not serial:
            continue
        date = xlrd.xldate_as_datetime(serial, wb.datemode).strftime("%Y-%m-%d")
        items = []
        order = list(LINE_HEADER_ROWS.items())
        for idx, (line, hdr) in enumerate(order):
            end = order[idx + 1][1] if idx + 1 < len(order) else sh.nrows
            seq = 0
            for r in range(hdr + 2, end):
                code = code_str(sh.cell_value(r, 1))
                batches = num(sh.cell_value(r, 3))
                if not code or not batches or code.endswith("ライン") or code == "コードNO":
                    continue
                start = num(sh.cell_value(r, 7))
                items.append({
                    "line": line, "seq": seq, "code": code, "batches": batches,
                    "start_min": round(start * 1440) if start else None, "gap_min": 0, "memo": "",
                })
                seq += 1
                line_count[code][line] += 1
                bt = num(sh.cell_value(r, 5))
                stubs.setdefault(code, {
                    "code": code, "name": norm(sh.cell_value(r, 2)),
                    "batch_min": round(bt * 1440, 1) if bt else None,
                    "shot": num(sh.cell_value(r, 4)), "mix": norm(sh.cell_value(r, 6)),
                    "mold": norm(sh.cell_value(r, 11)), "plate": norm(sh.cell_value(r, 12)),
                    "spec": norm(sh.cell_value(r, 20)), "packaging": norm(sh.cell_value(r, 21)),
                })
        plans[date] = items
    # 解凍肉の表（最新シートの右下）から 1バッチあたりkg を計算
    if sheets:
        thaw = read_thaw_table(sheets[-1])
    default_line = {c: cnt.most_common(1)[0][0] for c, cnt in line_count.items()}
    return plans, stubs, thaw, default_line


def read_thaw_table(sh) -> dict:
    anchor = None
    for r in range(sh.nrows):
        if sh.ncols > 26 and norm(sh.cell_value(r, 26)) == "解凍棚使用量":
            anchor = r
            break
    if anchor is None:
        return {}
    kg_row = anchor + 4
    kg_per_case = {}
    for c in range(28, min(35, sh.ncols)):
        m = re.match(r"[\d.]+", norm(sh.cell_value(kg_row, c)))  # 「20Kg」→ 20
        kg_per_case[c] = float(m.group()) if m else None
    result, last_code = {}, None
    for r in range(anchor + 5, sh.nrows):
        if norm(sh.cell_value(r, 26)).startswith("合計"):
            break
        code = code_str(sh.cell_value(r, 25))
        kg = sum((num(sh.cell_value(r, c), 0) or 0) * (kg_per_case.get(c) or 0) for c in range(28, min(35, sh.ncols)))
        if code:
            last_code = code
            if kg:
                result[code] = round(kg, 1)
        elif kg and last_code and norm(sh.cell_value(r, 24)) == "内具":
            result[last_code] = round(result.get(last_code, 0) + kg, 1)
    return result


def upsert_product(con, p: dict, overwrite: bool):
    p = {k: p.get(k) for k in db.PRODUCT_KEYS if k in p}
    cols = ", ".join(p)
    marks = ", ".join("?" for _ in p)
    verb = "REPLACE" if overwrite else "INSERT OR IGNORE"
    con.execute(f"{verb} INTO products({cols}) VALUES({marks})", list(p.values()))


def run(reset: bool = False):
    if reset and db.DB_PATH.exists():
        db.DB_PATH.unlink()
    con = db.connect()
    master = read_master()
    plans, stubs, thaw, default_line = read_schedules()

    for code, stub in stubs.items():
        if code not in master:
            stub.update({"wheat": 0, "milk": 0, "egg": 0, "thaw_kg": None, "is_new": 0, "memo": "作業予定表から自動登録"})
            stub.update(derive_flags(stub))
            master[code] = stub
    for code, p in master.items():
        p["default_line"] = default_line.get(code, "")
        if code in thaw:
            p["thaw_kg"] = thaw[code]
        upsert_product(con, p, overwrite=reset)

    existing = {r[0] for r in con.execute("SELECT DISTINCT date FROM plan_items")}
    added = 0
    for date, items in plans.items():
        if date in existing:
            continue
        for it in items:
            con.execute(
                "INSERT INTO plan_items(date, line, seq, code, batches, start_min, gap_min, memo) VALUES(?,?,?,?,?,?,?,?)",
                (date, it["line"], it["seq"], it["code"], it["batches"], it["start_min"], it["gap_min"], it["memo"]),
            )
        con.execute("INSERT OR IGNORE INTO day_meta(date) VALUES(?)", (date,))
        con.execute(
            "INSERT INTO changelog(ts, date, user, message) VALUES(datetime('now','localtime'), ?, ?, ?)",
            (date, "取込", f"作業予定表から取り込み（{len(items)}品目）"),
        )
        added += 1
    con.commit()
    n_prod = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    print(f"製品マスタ: {n_prod}件 / 取り込んだ日付: {added}日 / 解凍kg設定: {len(thaw)}製品")


if __name__ == "__main__":
    run(reset="--reset" in sys.argv)
