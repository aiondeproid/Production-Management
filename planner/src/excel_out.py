"""配布用の週間製造予定表（.xlsx）を作る

今の週間予定表に近い形：日付 × A〜Dライン（B・品名・コード・数量）。
前回の発行から変わった明細は赤字にし、削除した明細は下の「変更内容」に書く。
"""
import datetime as dt
from pathlib import Path

import config  # noqa: F401  lib/ を読み込み先に追加する
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import db
from db import LINES, WEEKDAYS

CIRCLED = "⓪①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
FONT = "Meiryo UI"


def rev_label(rev: int) -> str:
    if rev == 0:
        return "初版"
    return "改-" + (CIRCLED[rev] if rev < len(CIRCLED) else str(rev))


def cs_of(item, products):
    y = (products.get(item["code"]) or {}).get("yield_per_b")
    return round(item["batches"] * y) if y else None


def build(monday: str, items: list, tare: list, betten: list, products: dict, rev: int, issued_at: dt.datetime,
          changed_keys: set, change_lines: list, distribution: str):
    wb = Workbook()
    ws = wb.active
    ws.title = "週間予定"
    m = dt.date.fromisoformat(monday)
    dates = db.week_dates(monday)
    sat = dt.date.fromisoformat(dates[-1])

    thin = Side(style="thin", color="808080")
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    head_fill = PatternFill("solid", fgColor="E8ECF3")
    sat_fill = PatternFill("solid", fgColor="EEF4FF")
    f = lambda **kw: Font(name=FONT, **kw)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # 列：日付 | (B 品名 コード 数量) × 4ライン | タレ（品名 kg） | 別添タレ（品名 kg 袋）
    widths = [9] + [5, 26, 8, 7] * len(LINES) + [24, 8] + [24, 8, 8]
    tare_col = 2 + len(LINES) * 4
    bet_col = tare_col + 2
    name_cols = {tare_col, bet_col} | {2 + li * 4 + 1 for li in range(len(LINES))}
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    last_col = len(widths)

    ws.cell(1, 1, distribution).font = f(size=10)
    ws.cell(2, 1, "週　間　製　造　予　定　表").font = f(size=18, bold=True)
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=last_col - 6)
    ws.cell(2, 1).alignment = Alignment(horizontal="center")
    ws.cell(2, last_col - 5, "（変更箇所　赤色）").font = f(size=10, color="C00000")
    ws.cell(3, 1, f"{m.month}月　『　第　{(m.day - 1) // 7 + 1}　週　』").font = f(size=14, bold=True)
    ws.cell(3, last_col - 5, f"{issued_at.month}月{issued_at.day}日 発行　{rev_label(rev)}").font = f(size=12, bold=True)
    ws.cell(4, 1, f"{m.month}月{m.day}日　～　{sat.month}月{sat.day}日").font = f(size=12)

    # 見出し 2段
    r = 6
    ws.cell(r, 1, "日付")
    ws.merge_cells(start_row=r, start_column=1, end_row=r + 1, end_column=1)
    for li, ln in enumerate(LINES):
        c0 = 2 + li * 4
        ws.cell(r, c0, f"{ln}ライン")
        ws.merge_cells(start_row=r, start_column=c0, end_row=r, end_column=c0 + 3)
        for j, h in enumerate(["B", "品　名", "コード", "数量"]):
            ws.cell(r + 1, c0 + j, h)
    ws.cell(r, tare_col, "タレ")
    ws.merge_cells(start_row=r, start_column=tare_col, end_row=r, end_column=tare_col + 1)
    ws.cell(r + 1, tare_col, "品　名")
    ws.cell(r + 1, tare_col + 1, "数量")
    ws.cell(r, bet_col, "別添タレ")
    ws.merge_cells(start_row=r, start_column=bet_col, end_row=r, end_column=bet_col + 2)
    for j, h in enumerate(["品　名（→製品）", "タレ", "包装機"]):
        ws.cell(r + 1, bet_col + j, h)
    for rr in (r, r + 1):
        for c in range(1, last_col + 1):
            cell = ws.cell(rr, c)
            cell.font, cell.fill, cell.border, cell.alignment = f(bold=True), head_fill, box, center
    r += 2
    ws.freeze_panes = ws.cell(r, 2)

    for di, d in enumerate(dates):
        day = dt.date.fromisoformat(d)
        per_line = {ln: [it for it in items if it["date"] == d and it["line"] == ln] for ln in LINES}
        day_tare = [t for t in tare if t["date"] == d]
        day_bet = [b for b in betten if b["date"] == d]
        n = max(3, len(day_tare) + 1, len(day_bet), *(len(v) + 1 for v in per_line.values()))  # 最後の1行は合計
        ws.cell(r, 1, f"{day.day}\n（{WEEKDAYS[di]}）")
        ws.merge_cells(start_row=r, start_column=1, end_row=r + n - 1, end_column=1)
        ws.cell(r, 1).font = f(size=12, bold=True, color="1F4E9E" if di == 5 else "000000")
        ws.cell(r, 1).alignment = center
        for li, ln in enumerate(LINES):
            c0 = 2 + li * 4
            for k, it in enumerate(per_line[ln]):
                red = (it["date"], it["line"], it["code"]) in changed_keys
                font = f(color="C00000", bold=red) if red else f()
                p = products.get(it["code"]) or {}
                vals = [it["batches"], p.get("name") or it["code"], it["code"], cs_of(it, products)]
                for j, v in enumerate(vals):
                    cell = ws.cell(r + k, c0 + j, v)
                    cell.font = font
                    if j == 0:
                        cell.number_format = "0.##"
            if per_line[ln]:
                total = sum(it["batches"] for it in per_line[ln])
                cell = ws.cell(r + n - 1, c0, f"計 {total:g}B")
                cell.font = f(size=9, color="5D6678")
        for k, t in enumerate(day_tare):
            red = (t["date"], "タレ", t.get("code") or t.get("name", "")) in changed_keys
            font = f(color="C00000", bold=red) if red else f()
            name = (products.get(t.get("code")) or {}).get("name") or t.get("name") or t.get("code")
            ws.cell(r + k, tare_col, name).font = font
            cell = ws.cell(r + k, tare_col + 1, f"{t['kg']:g}kg")
            cell.font = font
        if day_tare:
            cell = ws.cell(r + n - 1, tare_col, f"計 {sum(t['kg'] for t in day_tare):g}kg")
            cell.font = f(size=9, color="5D6678")
        for k, b in enumerate(day_bet):
            red = (b["date"], "別添", b["code"]) in changed_keys
            font = f(color="C00000", bold=red) if red else f()
            p = products.get(b["code"]) or {}
            target = (products.get(p.get("betten_for")) or {}).get("name") or p.get("name") or b["code"]
            vals = [f"{b['code']} →{target}", f"{b['kg']:g}kg", f"{b['bags']}袋" if b.get("bags") else ""]
            for j, v in enumerate(vals):
                ws.cell(r + k, bet_col + j, v).font = font
        for rr in range(r, r + n):
            ws.row_dimensions[rr].height = 18
            for c in range(1, last_col + 1):
                cell = ws.cell(rr, c)
                cell.border = box
                if di == 5:
                    cell.fill = sat_fill
                if c in name_cols:
                    cell.alignment = Alignment(vertical="center", shrink_to_fit=True)
                elif c > 1:
                    cell.alignment = Alignment(horizontal="center", vertical="center")
        r += n

    if change_lines:
        r += 1
        ws.cell(r, 1, f"■ 変更内容（{rev_label(rev)}）").font = f(bold=True, color="C00000")
        for line in change_lines:
            r += 1
            ws.cell(r, 2, line).font = f(color="C00000")

    ws.page_setup.paperSize = ws.PAPERSIZE_A3
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = "6:7"
    ws.page_margins.left = ws.page_margins.right = 0.3
    return wb


def file_name(monday: str) -> str:
    m = dt.date.fromisoformat(monday)
    return f"週間予定_{m.year}年{m.month}月第{(m.day - 1) // 7 + 1}週.xlsx"


def save(wb, out_dir: str, monday: str) -> Path:
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / file_name(monday)
    try:
        wb.save(path)
    except PermissionError:
        raise RuntimeError(f"{path.name} が開かれているため書き込めません。Excelを閉じてからもう一度発行してください")
    return path
