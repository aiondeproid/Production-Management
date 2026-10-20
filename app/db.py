"""データベース（SQLite）の定義と共通処理"""
import json
import sqlite3
import unicodedata
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "data" / "plan.db"

LINES = ["A", "B", "C", "D"]

# 製品マスタの列（キー, 表示名, 型）
PRODUCT_FIELDS = [
    ("code", "コード", "text"),
    ("name", "製品名", "text"),
    ("shot", "ショット", "real"),
    ("batch_min", "分/B", "real"),
    ("mix", "配合", "text"),
    ("mold", "モールド板", "text"),
    ("plate", "プレート", "text"),
    ("pre_note", "前処理その他", "text"),
    ("pre_color", "前処理焼色", "text"),
    ("pre_regen", "前処理再生", "text"),
    ("metal_check", "前金検", "text"),
    ("wheat", "小麦", "int"),
    ("milk", "乳", "int"),
    ("egg", "卵", "int"),
    ("packaging", "包装形態", "text"),
    ("post_color", "後処理焼色", "text"),
    ("spec", "規格・備考", "text"),
    ("yield_per_b", "出来高/B", "real"),
    ("post_regen", "後処理再生", "text"),
    ("caution", "注意事項", "text"),
    ("wash_note", "洗浄事項", "text"),
    ("meat", "肉種", "text"),
    # ここから計画ルール用の属性（アプリで編集する）
    ("default_line", "標準ライン", "text"),
    ("thaw_kg", "解凍肉kg/B", "real"),
    ("asa_ichi", "朝一限定", "int"),
    ("tanitsu", "単一", "int"),
    ("last_special", "特殊原料(最後)", "int"),
    ("rotiru", "ロティール区分", "text"),  # '' / 'main' / 'derived'
    ("drain_group", "排水区分", "text"),  # '' / 'ロティール' / 'フジ食品'
    ("cheese", "チーズ使用", "int"),
    ("kaseijin", "火星人使用", "int"),
    ("dango_type", "形状", "text"),  # '' / '団子' / 'HB'
    ("is_new", "新規品", "int"),
    ("memo", "メモ", "text"),
]
PRODUCT_KEYS = [k for k, _, _ in PRODUCT_FIELDS]

DEFAULT_SETTINGS = {
    "day_start": 420,  # 7:00（分）
    "night_start": 1020,  # 17:00（分）夜勤の開始
    # 曜日別の解凍庫容量 kg（0=月 … 6=日）
    "thaw_caps": {"0": 4500, "1": 7500, "2": 4500, "3": 7500, "4": 4500, "5": 7500, "6": 0},
    "machine_limit": 2,  # MS・キャリアの同時使用製品数
    "drain_rotiru": 20,
    "drain_rotiru_pre_holiday": 10,
    "drain_rotiru_night": 10,
    "drain_fuji": 13,
    "export_dir": str(APP_DIR / "export"),
}

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS products (
    {", ".join(f"{k} {'TEXT' if t == 'text' else 'REAL' if t == 'real' else 'INTEGER'}" + (" PRIMARY KEY" if k == "code" else "") for k, _, t in PRODUCT_FIELDS)}
);
CREATE TABLE IF NOT EXISTS plan_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    line TEXT NOT NULL,
    seq INTEGER NOT NULL,
    code TEXT NOT NULL,
    batches REAL NOT NULL,
    start_min INTEGER,
    gap_min INTEGER DEFAULT 0,
    memo TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_plan_date ON plan_items(date);
CREATE TABLE IF NOT EXISTS day_meta (
    date TEXT PRIMARY KEY,
    checklist TEXT DEFAULT '{{}}',
    memo TEXT DEFAULT '',
    revision INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS changelog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    date TEXT NOT NULL,
    user TEXT,
    message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS holidays (
    date TEXT PRIMARY KEY,
    name TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def norm(s) -> str:
    """半角カナ・全角英数を揃えて比較しやすくする"""
    return unicodedata.normalize("NFKC", str(s or "")).strip()


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def get_settings(con) -> dict:
    s = json.loads(json.dumps(DEFAULT_SETTINGS))
    for row in con.execute("SELECT key, value FROM settings"):
        s[row["key"]] = json.loads(row["value"])
    return s


def save_settings(con, values: dict):
    for k, v in values.items():
        if k in DEFAULT_SETTINGS:
            con.execute("REPLACE INTO settings(key, value) VALUES(?, ?)", (k, json.dumps(v, ensure_ascii=False)))
    con.commit()


def all_products(con) -> dict:
    return {r["code"]: dict(r) for r in con.execute("SELECT * FROM products ORDER BY code")}


def holidays(con) -> dict:
    return {r["date"]: r["name"] for r in con.execute("SELECT date, name FROM holidays")}


def load_plan(con, date: str) -> list:
    rows = con.execute(
        "SELECT line, seq, code, batches, start_min, gap_min, memo FROM plan_items WHERE date=? ORDER BY line, seq",
        (date,),
    )
    return [dict(r) for r in rows]


def load_meta(con, date: str) -> dict:
    r = con.execute("SELECT checklist, memo, revision FROM day_meta WHERE date=?", (date,)).fetchone()
    if not r:
        return {"checklist": {}, "memo": "", "revision": 0}
    return {"checklist": json.loads(r["checklist"] or "{}"), "memo": r["memo"] or "", "revision": r["revision"]}
