"""成型予定表・前処理製造予定表（.xlsx）を作る

今の作業予定表（成型予定表）・再生投入スタンダード（前処理製造予定表）に近い形。
前回の発行から変わった行は赤字にし、削除した行は下の「変更内容」に書く。
"""
import datetime as dt
from pathlib import Path

import config  # noqa: F401  lib/ を読み込み先に追加する
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

import day
from db import LINES, WEEKDAYS
from excel_out import FONT, rev_label

KIND_TITLE = {"seikei": "成型予定表", "zensho": "前処理製造予定表"}

thin = Side(style="thin", color="808080")
BOX = Border(left=thin, right=thin, top=thin, bottom=thin)
HEAD_FILL = PatternFill("solid", fgColor="E8ECF3")
LINE_FILL = PatternFill("solid", fgColor="D9E1F2")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(vertical="center", wrap_text=True)
f = lambda **kw: Font(name=FONT, **kw)


def date_label(date: str) -> str:
    d = dt.date.fromisoformat(date)
    return f"{d.year}年{d.month}月{d.day}日（{WEEKDAYS[d.weekday()]}）"


def page_setup(ws, landscape=True):
    ws.page_setup.paperSize = ws.PAPERSIZE_A4 if not landscape else ws.PAPERSIZE_A3
    ws.page_setup.orientation = "landscape" if landscape else "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = ws.page_margins.right = 0.3


def header_row(ws, r, heads):
    for c, h in enumerate(heads, start=1):
        cell = ws.cell(r, c, h)
        cell.font, cell.fill, cell.border, cell.alignment = f(bold=True, size=10), HEAD_FILL, BOX, CENTER


def changes_block(ws, r, rev, change_lines):
    if change_lines:
        r += 1
        ws.cell(r, 1, f"■ 変更内容（{rev_label(rev)}）").font = f(bold=True, color="C00000")
        for line in change_lines:
            r += 1
            ws.cell(r, 2, line).font = f(color="C00000")
    return r


# ---------- 成型予定表 ----------
COL_INFO = {k: (label, width) for k, label, width, _ in day.COLUMNS}
TIME_FORMATS = {"batch_min": "h:mm", "start": "[h]:mm", "end": "[h]:mm"}
WRAP_KEYS = {"name", "mix", "mold", "pre_note", "pre_color", "caution", "wash", "spec"}


def cell_value(key, r, p):
    """成型予定表の1つのマスの値（時刻は Excel の時刻＝1日を1とした数）"""
    if key == "code":
        return r["code"]
    if key == "name":
        return p.get("name") or r["code"]
    if key == "batches":
        return r["batches"]
    if key == "batch_min":
        return (p.get("batch_min") or 0) / 1440 or None
    if key == "gap":
        return r.get("gap_used")
    if key == "start":
        return r["start_at"] / 1440
    if key == "end":
        return r["end5"] / 1440
    if key == "cs":
        return round(r["batches"] * p["yield_per_b"]) if p.get("yield_per_b") else None
    return p.get(key)


def build_seikei(date, rows, prods, rev, issued_at, changed_keys, change_lines, cols=None):
    """rows は day.compute 済みの行（開始・終了の時刻付き）。cols は印刷する項目のキー（並びは day.COLUMNS の順）"""
    cols = [k for k in day.COLUMN_KEYS if k in (cols or day.DEFAULT_PRINT)]
    wb = Workbook()
    ws = wb.active
    ws.title = "成型予定表"
    for c, k in enumerate(cols, start=1):
        ws.column_dimensions[ws.cell(1, c).column_letter].width = COL_INFO[k][1]
    last = len(cols)
    ws.cell(1, 1, date_label(date)).font = f(size=14, bold=True)
    ws.cell(1, min(4, last), "成　型　予　定　表").font = f(size=18, bold=True)
    info_col = max(1, last - 2)
    ws.cell(2 if last < 6 else 1, info_col, f"{issued_at.month}月{issued_at.day}日 発行　{rev_label(rev)}").font = f(size=12, bold=True)
    ws.cell(3 if last < 6 else 2, info_col, "（変更箇所　赤色）").font = f(size=10, color="C00000")
    timed = rows  # day.compute 済み（start_at・end5 付き）
    keyed = day.keyed(timed)
    key_of = {id(v): k for k, v in keyed.items()}
    r = 4
    for ln in LINES:
        line_rows = [x for x in timed if x["line"] == ln]
        cell = ws.cell(r, 1, f"{ln}ライン")
        cell.font, cell.fill = f(bold=True, size=12), LINE_FILL
        if line_rows:
            span = f"計 {sum(x['batches'] for x in line_rows):g}B　{day.min_to_hm(line_rows[0]['start_at'])}～{day.min_to_hm(line_rows[-1]['end5'])}"
            ws.cell(r, min(3, last), span).font = f(size=10, color="5D6678")
        r += 1
        header_row(ws, r, [COL_INFO[k][0] for k in cols])
        r += 1
        for k in range(max(len(line_rows), 3)):
            x = line_rows[k] if k < len(line_rows) else None
            red = x is not None and key_of.get(id(x)) in changed_keys
            p = (prods.get(x["code"]) or {}) if x else {}
            for c, key in enumerate(cols, start=1):
                cell = ws.cell(r, c, cell_value(key, x, p) if x else None)
                cell.border = BOX
                cell.font = f(size=10, color="C00000", bold=True) if red else f(size=10)
                cell.alignment = LEFT if key in WRAP_KEYS else CENTER
                if key in TIME_FORMATS:
                    cell.number_format = TIME_FORMATS[key]
                elif key == "batches":
                    cell.number_format = "0.##"
            ws.row_dimensions[r].height = 30
            r += 1
        r += 1
    changes_block(ws, r, rev, change_lines)
    ws.print_title_rows = "1:2"
    page_setup(ws)
    return wb


# ---------- 前処理製造予定表 ----------
ZENSHO_HEADS = [("コードNO", 9), ("後処理\n番号", 7), ("金検", 7), ("製　品　名", 30), ("バッチ", 7),
                ("１Ｂの\n投入袋数", 9), ("前処理再生\n番号", 10), ("製品ｺｰﾄﾞ", 9), ("製品名", 20), ("日付", 8), ("総投入Ｋ", 9)]


def build_zensho(date, rows, prods, rev, issued_at, changed_keys, change_lines):
    """ライン別に「コード・後処理番号・金検・製品名・B数」。投入袋数・前処理再生の欄は手書き用に空けておく"""
    wb = Workbook()
    ws = wb.active
    ws.title = "前処理製造予定表"
    for c, (_, w) in enumerate(ZENSHO_HEADS, start=1):
        ws.column_dimensions[ws.cell(1, c).column_letter].width = w
    ampm = "ＡＭ" if issued_at.hour < 12 else "ＰＭ"
    ws.cell(1, 1, "前処理工程　　製造予定表").font = f(size=16, bold=True)
    ws.cell(1, 8, f"{issued_at.month}月{issued_at.day}日 発行　{rev_label(rev)}").font = f(size=12, bold=True)
    ws.cell(2, 1, f"{issued_at.month}／{issued_at.day}　{ampm}時点の計画です。よろしくお願いします。").font = f(size=11)
    ws.cell(2, 8, "（変更箇所　赤色）").font = f(size=10, color="C00000")
    ws.cell(3, 4, date_label(date)).font = f(size=14, bold=True)
    summed = day.zensho_rows(rows)
    r = 5
    for ln in LINES:
        line_rows = [x for x in summed if x["line"] == ln]
        cell = ws.cell(r, 1, f"{ln}ライン")
        cell.font, cell.fill = f(bold=True, size=12), LINE_FILL
        ws.cell(r, 2, "後処理").font = f(size=9)
        ws.cell(r, 3, "前処理").font = f(size=9)
        ws.cell(r, 7, "前処理再生").font = f(size=9)
        r += 1
        header_row(ws, r, [h for h, _ in ZENSHO_HEADS])
        r += 1
        for k in range(max(len(line_rows) + 1, 4)):
            x = line_rows[k] if k < len(line_rows) else None
            red = x is not None and (x["line"], x["code"], 0) in changed_keys
            p = prods.get(x["code"]) or {} if x else {}
            vals = [x["code"], p.get("post_regen"), p.get("kenkin"), p.get("name") or x["code"], x["batches"]] if x else []
            for c in range(1, len(ZENSHO_HEADS) + 1):
                cell = ws.cell(r, c, vals[c - 1] if c <= len(vals) else None)
                cell.border = BOX
                cell.font = f(size=11, color="C00000", bold=True) if red else f(size=11)
                cell.alignment = LEFT if c == 4 else CENTER
                if c == 5:
                    cell.number_format = "0.##"
            ws.row_dimensions[r].height = 20
            r += 1
        r += 1
    changes_block(ws, r, rev, change_lines)
    page_setup(ws, landscape=False)
    return wb


def build(kind, *args, cols=None):
    """cols … 成型予定表に印刷する項目（前処理製造予定表は決まった形なので使わない）"""
    if kind == "seikei":
        return build_seikei(*args, cols=cols)
    return build_zensho(*args)


def file_name(kind: str, date: str) -> str:
    d = dt.date.fromisoformat(date)
    return f"{KIND_TITLE[kind]}_{d.year}年{d.month}月{d.day}日.xlsx"


def save(wb, out_dir: str, kind: str, date: str) -> Path:
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / file_name(kind, date)
    try:
        wb.save(path)
    except PermissionError:
        raise RuntimeError(f"{path.name} が開かれているため書き込めません。Excelを閉じてからもう一度発行してください")
    return path
