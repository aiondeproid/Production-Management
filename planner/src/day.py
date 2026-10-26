"""作業予定（日ごと・ライン別の製造順と時刻）

週間予定の明細（日・ライン・製品・B数）を受け取って、成型予定表・前処理製造予定表のもとにする。
保存するまでは、週間予定から自動で作った「案」を出す（夜の休止をまたぐ製品は分けておく）。

時刻の計算（今の作業予定表と同じ）：
    開始 = 前の製品の終了を5分単位に切り上げた時刻 ＋ 切替（分）   … 朝一は各ラインの開始時刻
    終了 = 開始 ＋ B数 × 分/B
開始時刻を固定した行は、その時刻から始める。
"""
import math
from collections import defaultdict

import db
from db import LINES


# 作業予定の項目（画面にはすべて出す。成型予定表には選んだ項目だけ印刷する）
#   (キー, 見出し, Excel の幅, 初期値で印刷するか)
#   キーは製品マスタの列名。batches・gap・start・end・cs だけは作業予定の行から出す
COLUMNS = [
    ("code", "コードNO", 9, True),
    ("name", "製品名", 26, True),
    ("batches", "バッチ", 6, True),
    ("shot", "ショット", 6, True),
    ("batch_min", "時間/B", 7, True),
    ("mix", "配合", 14, True),
    ("gap", "切替（分）", 6, False),
    ("start", "開始", 7, True),
    ("end", "終了", 7, True),
    ("cs", "予定出来高（cs）", 9, False),
    ("mold", "モールド板", 22, True),
    ("plate", "プレート", 9, True),
    ("form_g", "成型g", 7, False),
    ("meat", "食肉区分", 7, False),
    ("pre_note", "その他", 24, True),
    ("fry", "油揚", 5, True),
    ("pre_color", "色", 16, False),
    ("pre_regen", "再生", 9, False),
    ("wheat", "小麦", 4, False),
    ("egg", "卵", 4, True),
    ("milk", "乳", 4, True),
    ("caution", "注意事項", 20, True),
    ("wash", "洗浄事項", 14, False),
    ("spec", "後処理　箱詰め規格", 34, True),
    ("packaging", "包装形態", 9, True),
    ("post_regen", "後処理番号", 7, False),
    ("kenkin", "金検", 6, False),
]
COLUMN_KEYS = [c[0] for c in COLUMNS]
DEFAULT_PRINT = [c[0] for c in COLUMNS if c[3]]


def print_columns(con) -> list:
    """成型予定表に印刷する項目（保存した選択。知らないキーは除く）"""
    keys = db.get_setting(con, "seikei_print_cols", DEFAULT_PRINT)
    keys = [k for k in COLUMN_KEYS if k in keys]
    return keys or DEFAULT_PRINT


def hm_to_min(s) -> float | None:
    """'7:30' → 450、'25:10' → 1510"""
    if s in (None, ""):
        return None
    if isinstance(s, (int, float)):
        return float(s)
    h, _, m = str(s).replace("：", ":").partition(":")
    return int(h) * 60 + int(m or 0)


def min_to_hm(m) -> str:
    if m is None:
        return ""
    m = round(m)
    return f"{m // 60}:{m % 60:02d}"


def ceil5(m: float) -> float:
    return math.ceil(m / 5 - 1e-9) * 5


class Settings:
    def __init__(self, cfg: dict):
        self.line_start = {ln: hm_to_min(cfg["line_start"].get(ln, "7:30")) for ln in LINES}
        self.gap = float(cfg["gap_min"])
        self.night = hm_to_min(cfg["night_break"])
        self.night_gap = float(cfg["night_break_min"])
        self.machines = {db.norm(k): int(v) for k, v in cfg["machines"].items() if int(v) > 0}

    def as_dict(self):
        return {"line_start": self.line_start, "gap": self.gap, "night": self.night, "night_gap": self.night_gap,
                "machines": self.machines}


def compute(rows: list, prods: dict, st: Settings) -> list:
    """各行に start_at / end_at / end5（0時からの分）を付けて返す（ラインごと・seq の順）"""
    out, prev_end = [], {}
    for r in sorted(rows, key=lambda r: (LINES.index(r["line"]), r["seq"])):
        mpb = (prods.get(r["code"]) or {}).get("batch_min") or 0
        gap_used = None  # 実際に使った切替（朝一・時刻を固定した行は無し）
        if r.get("start") is not None:
            s = r["start"]
        elif r["line"] not in prev_end:
            s = st.line_start[r["line"]]
        else:
            gap_used = st.gap if r.get("gap") is None else r["gap"]
            s = ceil5(prev_end[r["line"]]) + gap_used
        e = s + r["batches"] * mpb
        prev_end[r["line"]] = e
        out.append({**r, "start_at": s, "end_at": e, "end5": ceil5(e), "gap_used": gap_used})
    return out


def weekly_rows(con, date: str) -> list:
    rows = con.execute("SELECT line, seq, code, batches FROM weekly_items WHERE date=? ORDER BY line, seq", (date,))
    return [dict(r, date=date) for r in rows]


def draft(weekly: list, prods: dict, st: Settings) -> list:
    """週間予定から作業予定の案を作る（夜の休止をまたぐ製品は分ける）"""
    rows = [{"line": w["line"], "seq": w["seq"], "code": w["code"], "batches": w["batches"], "gap": None, "start": None}
            for w in weekly]
    out = []
    for ln in LINES:
        out += add_night_break([r for r in rows if r["line"] == ln], prods, st)
    return renumber(out)


def add_night_break(rows: list, prods: dict, st: Settings) -> list:
    """夜の休止の時刻をまたぐ製品を、休止の前後に分ける

    休止の前は 0.5B 単位で、終了がいちばん休止の時刻に近くなる B数（終了は休止の時刻＋30分まで）にする。
    残りは休止（night_break_min 分）のあとに続ける。休止の時刻＋30分までに終わる製品は分けず、
    次の製品の切替を休止の分にする。
    """
    rows = renumber([dict(r) for r in rows])
    for i, r in enumerate(compute(rows, prods, st)):
        s, e = r["start_at"], r["end_at"]
        if s >= st.night:  # 前の製品が休止の前に終わり、この製品から休止のあと
            if i > 0 and (rows[i]["gap"] or 0) < st.night_gap and rows[i]["start"] is None:
                rows[i]["gap"] = st.night_gap
            return rows
        if e <= st.night + 30:  # 休止の時刻＋30分までに終わる製品は分けない
            continue
        mpb = (prods.get(r["code"]) or {}).get("batch_min")
        if not mpb:
            return rows
        cands = [k / 2 for k in range(1, int(r["batches"] * 2)) if s + k / 2 * mpb <= st.night + 30]
        if not cands:  # 休止の前に1Bも入らない → 製品ごと休止のあとへ
            rows[i]["gap"] = st.night_gap if i > 0 else rows[i]["gap"]
            return rows
        b1 = min(cands, key=lambda b: abs(s + b * mpb - st.night))
        rest = round(r["batches"] - b1, 2)
        rows[i]["batches"] = b1
        rows.insert(i + 1, {**rows[i], "batches": rest, "gap": st.night_gap, "start": None})
        return renumber(rows)
    return rows


def renumber(rows: list) -> list:
    seq = defaultdict(int)
    out = []
    for r in sorted(rows, key=lambda r: (LINES.index(r["line"]), r["seq"])):
        out.append({**r, "seq": seq[r["line"]]})
        seq[r["line"]] += 1
    return out


def load(con, date: str) -> list:
    rows = con.execute("SELECT line, seq, code, batches, gap, start FROM day_rows WHERE date=? ORDER BY line, seq", (date,))
    return [dict(r) for r in rows]


def is_saved(con, date: str) -> bool:
    return con.execute("SELECT 1 FROM day_rows WHERE date=? LIMIT 1", (date,)).fetchone() is not None


def rows_for(con, date: str, prods: dict, st: Settings) -> tuple:
    """(行, 案かどうか)。保存した行が無ければ週間予定から案を作る"""
    if is_saved(con, date):
        return load(con, date), False
    return draft(weekly_rows(con, date), prods, st), True


def mismatch(rows: list, weekly: list) -> list:
    """週間予定と B数が合わない製品 [{line, code, weekly, day}]"""
    w, d = defaultdict(float), defaultdict(float)
    for r in weekly:
        w[(r["line"], r["code"])] += r["batches"]
    for r in rows:
        d[(r["line"], r["code"])] += r["batches"]
    out = []
    for k in sorted(set(w) | set(d), key=lambda k: (LINES.index(k[0]), k[1])):
        if abs(w.get(k, 0) - d.get(k, 0)) > 1e-6:
            out.append({"line": k[0], "code": k[1], "weekly": w.get(k, 0), "day": d.get(k, 0)})
    return out


def sync(rows: list, weekly: list, prods: dict, st: Settings) -> list:
    """作業予定を週間予定の B数に合わせる（順番・切替・固定した時刻はできるだけ残す）

    - 週間予定から消えた製品は消す
    - B数が変わった製品は、最後の行で増減する（足りなければ前の行も減らす）
    - 増えた製品は、ラインの最後に足す
    """
    want = defaultdict(float)
    for r in weekly:
        want[(r["line"], r["code"])] += r["batches"]
    rows = [dict(r) for r in rows if (r["line"], r["code"]) in want]
    have = defaultdict(float)
    for r in rows:
        have[(r["line"], r["code"])] += r["batches"]
    for k, b in want.items():
        diff = round(b - have.get(k, 0), 4)
        same = [r for r in rows if (r["line"], r["code"]) == k]
        if not same:
            seq = max([r["seq"] for r in rows if r["line"] == k[0]], default=-1) + 1
            rows.append({"line": k[0], "seq": seq, "code": k[1], "batches": b, "gap": None, "start": None})
            continue
        for r in reversed(same):
            if diff == 0:
                break
            nb = max(0.0, r["batches"] + diff)
            diff = round(diff - (nb - r["batches"]), 4)
            r["batches"] = nb
    return renumber([r for r in rows if r["batches"] > 0])


def to_weekly(date: str, weekly: list, old: list, new: list) -> list:
    """作業予定で変えた B数を、その日の週間予定に反映した行を返す

    作業予定の 保存前 → 保存後 の差だけを足し引きする（週間予定のほうで先に変えた分は残す）。
    - B数が変わった製品は、マスの最後の明細で増減する（足りなければ前の明細も減らす）
    - 作業予定で消した製品は、その分を減らす（0 になれば週間予定からも消える）
    - 週間予定に無い製品が増えたら、マスの最後に足す
    マスの中の並び順は、作業予定の順番に合わせる（作業予定に無い製品はマスの最後に残す）。
    """
    delta = defaultdict(float)
    for r in new:
        delta[(r["line"], r["code"])] += r["batches"]
    for r in old:
        delta[(r["line"], r["code"])] -= r["batches"]
    rows = [dict(w) for w in weekly]
    for k, d in delta.items():
        d = round(d, 4)
        if d == 0:
            continue
        same = [w for w in rows if (w["line"], w["code"]) == k]
        if not same:
            if d > 0:
                seq = max([w["seq"] for w in rows if w["line"] == k[0]], default=-1) + 1
                rows.append({"date": date, "line": k[0], "seq": seq, "code": k[1], "batches": d})
            continue
        for w in reversed(same):
            if d == 0:
                break
            nb = round(max(0.0, w["batches"] + d), 4)
            d = round(d - (nb - w["batches"]), 4)
            w["batches"] = nb
    return weekly_order([w for w in rows if w["batches"] > 0], new)


def weekly_order(weekly: list, rows: list) -> list:
    """週間予定のマスの並び順を、作業予定の順番に合わせる

    同じ製品の明細が2つ以上あるときは、n 番目の明細を作業予定の n 番目の行の位置に置く
    （作業予定で分けた行のほうが多いときは、余った行は無視する。明細のほうが多いときは最後の行の位置）。
    """
    pos = defaultdict(list)
    for i, r in enumerate(sorted(rows, key=lambda r: (LINES.index(r["line"]), r["seq"]))):
        pos[(r["line"], r["code"])].append(i)
    n, keyed = defaultdict(int), []
    for w in sorted(weekly, key=lambda w: (LINES.index(w["line"]), w["seq"])):
        k = (w["line"], w["code"])
        p = pos.get(k)
        keyed.append((LINES.index(w["line"]), p[min(n[k], len(p) - 1)] if p else len(rows), w["seq"], w))
        n[k] += 1
    return renumber([dict(x[3], seq=i) for i, x in enumerate(sorted(keyed, key=lambda x: x[:3]))])


def zensho_rows(rows: list) -> list:
    """前処理製造予定表：ラインごとに、製品の並び順で B数を合計する（分けた行は1行にまとめる）"""
    out, idx = [], {}
    for r in sorted(rows, key=lambda r: (LINES.index(r["line"]), r["seq"])):
        k = (r["line"], r["code"])
        if k not in idx:
            idx[k] = len(out)
            out.append({"line": r["line"], "code": r["code"], "batches": 0.0})
        out[idx[k]]["batches"] += r["batches"]
    return out


# ---------- 発行の控えとの差分 ----------
def keyed(rows: list) -> dict:
    """(ライン, コード, 何回目) → 行。分けた行を区別する"""
    n, out = defaultdict(int), {}
    for r in sorted(rows, key=lambda r: (LINES.index(r["line"]), r["seq"])):
        k = (r["line"], r["code"], n[(r["line"], r["code"])])
        n[(r["line"], r["code"])] += 1
        out[k] = r
    return out


def diff(kind: str, old: list, new: list, prods: dict) -> tuple:
    """発行の控え → 今 の変更点（文章のリスト, 赤字にするキーの集合）

    成型予定表は B数と開始時刻、前処理製造予定表は B数を比べる。
    """
    name = lambda c: (prods.get(c) or {}).get("name") or c
    if kind == "zensho":
        o = {(r["line"], r["code"], 0): r for r in zensho_rows(old)}
        n = {(r["line"], r["code"], 0): r for r in zensho_rows(new)}
    else:
        o, n = keyed(old), keyed(new)
    lines, keys = [], set()
    for k, r in n.items():
        p = o.get(k)
        if p is None:
            keys.add(k)
            lines.append(f"追加：{k[0]}ライン {name(k[1])} {r['batches']:g}B")
            continue
        parts = []
        if abs(p["batches"] - r["batches"]) > 1e-6:
            parts.append(f"{p['batches']:g}B → {r['batches']:g}B")
        if kind == "seikei" and round(p.get("start_at") or 0) != round(r.get("start_at") or 0):
            parts.append(f"開始 {min_to_hm(p.get('start_at'))} → {min_to_hm(r.get('start_at'))}")
        if parts:
            keys.add(k)
            lines.append(f"変更：{k[0]}ライン {name(k[1])} " + "、".join(parts))
    for k, r in o.items():
        if k not in n:
            lines.append(f"削除：{k[0]}ライン {name(k[1])} {r['batches']:g}B")
    return lines, keys


def current_rev(con, date: str, kind: str) -> dict | None:
    r = con.execute(
        "SELECT * FROM day_revisions WHERE date=? AND kind=? ORDER BY rev DESC LIMIT 1", (date, kind)
    ).fetchone()
    return dict(r) if r else None


def clean(date: str, raw: list) -> list:
    """画面から来た作業予定の行"""
    out = []
    for r in raw:
        code = db.norm(r.get("code"))
        try:
            b = float(r.get("batches") or 0)
        except ValueError:
            b = 0
        if r.get("line") not in LINES or not code or b <= 0:
            continue
        gap = r.get("gap")
        start = hm_to_min(r.get("start")) if isinstance(r.get("start"), str) else r.get("start")
        out.append({
            "line": r["line"], "seq": len([x for x in out if x["line"] == r["line"]]), "code": code, "batches": b,
            "gap": None if gap in (None, "") else float(gap), "start": None if start in (None, "") else float(start),
        })
    return out
