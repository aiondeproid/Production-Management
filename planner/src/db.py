"""データベース（SQLite）の定義と共通処理

共有フォルダに置くため、ネットワーク上でも安全な journal_mode=DELETE（初期値）のまま使う。
同時に書き込むのは「編集の鍵」を持つ1人だけ（lock.py）。
"""
import datetime as dt
import sqlite3
import sys
import unicodedata

from config import APP_DIR

DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "plan.db"
LINES = ["A", "B", "C", "D"]
WEEKDAYS = "月火水木金土日"
DAYS_PER_WEEK = 6  # 月〜土

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    yield_per_b REAL,          -- 出来高/B（cs）
    packaging TEXT DEFAULT '',  -- 包装形態
    batch_min REAL,             -- 1バッチの時間（分）… Step 2 で使う
    memo TEXT DEFAULT '',
    tare_kg_per_b REAL,         -- 内添タレ kg/B（タレ欄の自動計算に使う）
    betten_for TEXT,            -- 別添タレの半製品（89xxx）が使われる製品のコード
    bags_per_kg REAL            -- 別添タレ 1kgあたりの袋数（袋数の自動計算に使う）
);
-- 別添タレ：作り置きする小袋のタレ（半製品コード・kg・袋数）
--   bags が NULL なら kg × 袋/kg で自動計算する
CREATE TABLE IF NOT EXISTS betten_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    seq INTEGER NOT NULL,
    code TEXT NOT NULL,
    kg REAL NOT NULL,
    bags INTEGER
);
CREATE INDEX IF NOT EXISTS ix_betten_date ON betten_items(date);
-- タレ欄（内添タレ）：日ごとの品名と kg
--   auto=1 … その日のライン明細から自動計算した行（B × 内添タレkg/B）
--   auto=0 … 手で入れた行・手で kg を直した行
CREATE TABLE IF NOT EXISTS tare_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    seq INTEGER NOT NULL,
    code TEXT DEFAULT '',
    name TEXT DEFAULT '',
    kg REAL NOT NULL,
    auto INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_tare_date ON tare_items(date);
-- 週間予定の明細：日・ライン・製品・B数
CREATE TABLE IF NOT EXISTS weekly_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    line TEXT NOT NULL,
    seq INTEGER NOT NULL,       -- マスの中の並び順
    code TEXT NOT NULL,
    batches REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_weekly_date ON weekly_items(date);
-- 発行（改版）ごとの控え。次の発行で「変更箇所を赤」にするための比較元
CREATE TABLE IF NOT EXISTS revisions (
    week TEXT NOT NULL,         -- 週の月曜日
    rev INTEGER NOT NULL,
    issued_at TEXT NOT NULL,
    user TEXT,
    snapshot TEXT NOT NULL,     -- JSON（その時点の明細）
    changes TEXT DEFAULT '',
    PRIMARY KEY (week, rev)
);
-- 作業予定（日ごと・ライン別の製造順と時刻）。保存するまでは週間予定から自動で作った案を出す
--   gap   … 前の製品の終了から開始までの分（切替・休止）。NULL なら初期値
--   start … 開始時刻を固定したとき（0時からの分。25:10 = 1510）。NULL なら自動計算
CREATE TABLE IF NOT EXISTS day_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    line TEXT NOT NULL,
    seq INTEGER NOT NULL,
    code TEXT NOT NULL,
    batches REAL NOT NULL,
    gap REAL,
    start REAL
);
CREATE INDEX IF NOT EXISTS ix_day_date ON day_rows(date);
-- 作業予定の発行の控え。kind = 'seikei'（成型予定表）/ 'zensho'（前処理製造予定表）
CREATE TABLE IF NOT EXISTS day_revisions (
    date TEXT NOT NULL,
    kind TEXT NOT NULL,
    rev INTEGER NOT NULL,
    issued_at TEXT NOT NULL,
    user TEXT,
    snapshot TEXT NOT NULL,
    changes TEXT DEFAULT '',
    PRIMARY KEY (date, kind, rev)
);
-- 画面で決める設定（成型予定表に印刷する項目など）。みんなで共通
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- 後処理日報・洗浄記録に書き込む内容（日ごと。出来高・洗浄時刻・担当者など）。JSON
--   行は (ライン, コード, 何回目) のキーで持つ。作業予定の行は day_rows から作る
CREATE TABLE IF NOT EXISTS post_records (
    date TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    updated_at TEXT,
    user TEXT
);
CREATE TABLE IF NOT EXISTS changelog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    week TEXT NOT NULL,
    user TEXT,
    message TEXT NOT NULL
);
"""


def norm(s) -> str:
    """半角カナ・全角英数をそろえる"""
    return unicodedata.normalize("NFKC", str(s if s is not None else "")).strip()


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    added = migrate(con)
    if set(added) & set(FORMING_COLS) and con.execute("SELECT COUNT(*) FROM products").fetchone()[0]:
        import importer  # 製品の情報の列を足したときは、Book1 から値を入れる（空の欄だけ・1回だけ）
        try:
            importer.fill_forming(con)
        except OSError as ex:  # Book1 が見つからなくてもアプリは動かす（値は製品マスタで入れる）
            print("Book1 から製品の情報を入れられませんでした:", ex, file=sys.stderr)
    return con


def get_setting(con, key: str, default=None):
    import json
    r = con.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def set_setting(con, key: str, value):
    import json
    con.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES(?, ?)", (key, json.dumps(value, ensure_ascii=False)))
    con.commit()


# 成型予定表・前処理製造予定表・後処理日報・洗浄記録に出す製品の情報：列名 → (Book1 の列（0始まり）, 型, 見出し)
FORMING_COLS = {
    "shot": (2, "REAL", "ショット"),
    "mix": (4, "TEXT", "配合"),
    "mold": (5, "TEXT", "モールド板"),
    "pre_note": (6, "TEXT", "その他"),
    "pre_color": (7, "TEXT", "色"),
    "fry": (8, "TEXT", "油揚"),
    "milk": (12, "TEXT", "乳"),
    "egg": (13, "TEXT", "卵"),
    "pre_regen": (14, "TEXT", "前処理再生"),
    "wheat": (11, "TEXT", "小麦"),
    "spec": (21, "TEXT", "後処理 箱詰め規格"),
    "caution": (52, "TEXT", "注意事項"),
    "post_regen": (53, "TEXT", "後処理番号"),
    "wash": (54, "TEXT", "洗浄事項"),
    "form_g": (57, "REAL", "成型g"),
    "meat": (66, "TEXT", "食肉区分"),
    "plate": (80, "TEXT", "プレート"),
    "kenkin": (81, "TEXT", "金検"),
    # 後処理日報・洗浄記録
    "film": (18, "TEXT", "フィルム"),
    "post_color": (19, "TEXT", "後処理焼色"),
    "counter": (20, "TEXT", "計数機設定"),
    "meat_comp": (55, "TEXT", "肉組成"),
    "occupy_min": (72, "REAL", "ライン占拠（分）"),
}


def migrate(con) -> list:
    """前の版で作ったDBに、足りない列を追加する。追加した列名を返す"""
    cols = {r["name"] for r in con.execute("PRAGMA table_info(products)")}
    want = [("tare_kg_per_b", "REAL"), ("betten_for", "TEXT"), ("bags_per_kg", "REAL")]
    want += [(k, v[1]) for k, v in FORMING_COLS.items()]
    added = []
    for col, typ in want:
        if col not in cols:
            con.execute(f"ALTER TABLE products ADD COLUMN {col} {typ}")
            added.append(col)
    con.commit()
    return added


def monday_of(date: dt.date) -> dt.date:
    return date - dt.timedelta(days=date.weekday())


def week_dates(monday: str) -> list:
    m = dt.date.fromisoformat(monday)
    return [(m + dt.timedelta(days=i)).isoformat() for i in range(DAYS_PER_WEEK)]


def week_title(monday: str) -> str:
    """今の週間予定表と同じ呼び方：「11月 第3週」（月曜日の月・日で決める）"""
    m = dt.date.fromisoformat(monday)
    return f"{m.month}月 第{(m.day - 1) // 7 + 1}週"


def products(con) -> dict:
    return {r["code"]: dict(r) for r in con.execute("SELECT * FROM products ORDER BY code")}


def load_week(con, monday: str) -> list:
    dates = week_dates(monday)
    rows = con.execute(
        f"SELECT date, line, seq, code, batches FROM weekly_items WHERE date IN ({','.join('?' * len(dates))}) ORDER BY date, line, seq",
        dates,
    )
    return [dict(r) for r in rows]


def load_tare(con, monday: str) -> list:
    dates = week_dates(monday)
    rows = con.execute(
        f"SELECT date, seq, code, name, kg, auto FROM tare_items WHERE date IN ({','.join('?' * len(dates))}) ORDER BY date, seq",
        dates,
    )
    return [dict(r) for r in rows]


def build_tare(items: list, fixed: list, prods: dict) -> list:
    """タレ欄を組み立てる

    その日のライン明細のうち「内添タレ kg/B」がある製品は、B × kg/B を自動の行にする。
    同じ日・同じ製品の手入力の行（kg を手で直した行）があれば、そちらを優先する。
    並びは「自動の行（ラインA→Dの順）」→「手入力の行」。
    """
    fixed = [dict(t, auto=0) for t in fixed]
    fixed_keys = {(t["date"], t.get("code")) for t in fixed if t.get("code")}
    out, sums = [], {}
    for it in sorted(items, key=lambda i: (i["date"], LINES.index(i["line"]), i.get("seq", 0))):
        p = prods.get(it["code"]) or {}
        if p.get("tare_kg_per_b") and (it["date"], it["code"]) not in fixed_keys:
            key = (it["date"], it["code"])
            if key not in sums:
                sums[key] = {"date": it["date"], "code": it["code"], "name": p.get("name", ""), "kg": 0.0, "auto": 1}
                out.append(sums[key])
            sums[key]["kg"] += it["batches"] * p["tare_kg_per_b"]
    for t in out:
        t["kg"] = round(t["kg"], 1)
    result = []
    for d in sorted({t["date"] for t in out + fixed}):
        day = [t for t in out if t["date"] == d] + [t for t in fixed if t["date"] == d]
        result += [dict(t, seq=i) for i, t in enumerate(day)]
    return result


def load_betten(con, monday: str, prods: dict | None = None) -> list:
    """別添タレ（袋数が空なら kg × 袋/kg で計算し、bags_auto を付ける）"""
    prods = prods or products(con)
    dates = week_dates(monday)
    rows = con.execute(
        f"SELECT date, seq, code, kg, bags FROM betten_items WHERE date IN ({','.join('?' * len(dates))}) ORDER BY date, seq",
        dates,
    )
    return [with_bags(dict(r), prods) for r in rows]


def with_bags(b: dict, prods: dict) -> dict:
    p = prods.get(b["code"]) or {}
    auto = b.get("bags") is None
    bags = round(b["kg"] * p["bags_per_kg"]) if auto and p.get("bags_per_kg") else b.get("bags")
    return {**b, "bags": bags, "bags_auto": auto}


def upgrade_snapshots(con):
    """前の版で発行した控えを、今の形（ライン明細＋タレ＋別添タレ）に直す（足りない部分だけ、1回だけ）

    タレ・別添タレは、取り込んだデータ（まだ誰も編集していない状態）から当時の内容を組み立てる。
    """
    import json
    prods = products(con)
    rows = con.execute("SELECT week, rev, snapshot FROM revisions").fetchall()
    for r in rows:
        snap = json.loads(r["snapshot"])
        if isinstance(snap, list):
            snap = {"items": snap}
        elif "betten" in snap:
            continue
        snap.setdefault("tare", build_tare(snap["items"], load_tare(con, r["week"]), prods))
        snap.setdefault("betten", load_betten(con, r["week"], prods))
        con.execute(
            "UPDATE revisions SET snapshot=? WHERE week=? AND rev=?",
            (json.dumps(snap, ensure_ascii=False), r["week"], r["rev"]),
        )
    con.commit()


def snapshot_parts(snapshot) -> tuple:
    """発行の控え（JSON）から (ライン明細, タレ, 別添タレ) を取り出す"""
    if isinstance(snapshot, list):
        return snapshot, [], []
    return snapshot.get("items", []), snapshot.get("tare", []), snapshot.get("betten", [])


def current_rev(con, monday: str) -> dict | None:
    r = con.execute("SELECT * FROM revisions WHERE week=? ORDER BY rev DESC LIMIT 1", (monday,)).fetchone()
    return dict(r) if r else None
