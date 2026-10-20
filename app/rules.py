"""時刻計算と制約チェック

レベル:
    NG   … ◎ 必ず守るルールに違反
    注意 … 〇△ なるべく避けるルール
    情報 … 確認を促すお知らせ
"""
import datetime as dt
import math

from db import LINES

# 人の判断が必要なルール（画面のチェックリストに出す）
MANUAL_CHECKS = [
    ("prep_day", "製造可能曜日：前日・前々日準備、材料の入荷日、キャベツ休み明けの品質"),
    ("tofu_cabbage_min", "木綿豆腐150kg以上・キャベツ30kg以上／日の入荷量になっているか"),
    ("tofu_batch", "木綿豆腐が余らないバッチ数（15kgで割り切れる）か"),
    ("tare", "タレの配合回数が6回以内か"),
    ("margin_hard", "手間のかかる製品を最後に流す場合、30〜90分早く終わる設定か"),
    ("long_holiday", "3連休の3〜5日前は全ライン1〜3時間早く終わる設定か"),
    ("egg_after_holiday", "3連休明けは液全卵使用製品を少なめにしたか"),
    ("adjustable_late", "レギュラー品・見込み生産品（ロティール除く）を後半に置いたか"),
    ("post_split", "ムルチ切替が少なくなるよう同じ分割の製品を続けたか"),
]


def fmt(m):
    if m is None:
        return ""
    m = int(round(m))
    return f"{m // 60}:{m % 60:02d}"


def has(text, word):
    return word in (text or "")


def uses_ms(p):
    return has(p.get("mix"), "MS")


def uses_carrier(p):
    mix = p.get("mix") or ""
    return "キャリア" in mix or "ｷｬﾘｱ" in mix


def compute_schedule(items, products, settings):
    """各品目の開始・終了時刻（分）を計算する

    開始時刻が入っていればそれを使い、空なら前の品目の終了（5分単位に切り上げ）＋間隔。
    """
    out = []
    for line in LINES:
        prev_end = None
        for it in sorted((i for i in items if i["line"] == line), key=lambda i: i["seq"]):
            p = products.get(it["code"], {})
            bmin = p.get("batch_min") or 0
            if it.get("start_min") is not None:
                start = it["start_min"]
            elif prev_end is None:
                start = settings["day_start"] + (it.get("gap_min") or 0)
            else:
                start = math.ceil(prev_end / 5) * 5 + (it.get("gap_min") or 0)
            end = start + bmin * (it.get("batches") or 0)
            out.append({**it, "start": start, "end": end, "product": p})
            prev_end = end
    return out


def day_info(date_str, holidays):
    d = dt.date.fromisoformat(date_str)

    def is_off(x):
        return x.weekday() == 6 or x.isoformat() in holidays

    def off_run(start, step):
        """start から step 方向に続く休日の並び（日付リスト）"""
        run, x = [], start
        while is_off(x):
            run.append(x)
            x += dt.timedelta(days=step)
        return run

    def is_long(run):
        # 祝日を含むか2日以上続く休み（ただの日曜は含めない）
        return len(run) >= 2 or any(x.isoformat() in holidays for x in run)

    one = dt.timedelta(days=1)
    return {
        "weekday": d.weekday(),
        "is_saturday": d.weekday() == 5,
        "is_holiday": is_off(d),
        "pre_holiday": is_off(d + one),  # 排水の休日前判定（日曜含む）
        "pre_long": is_long(off_run(d + one, 1)),
        "post_long": is_long(off_run(d - one, -1)),
        "holiday_name": holidays.get(date_str, ""),
    }


def overlaps(a, b):
    return a["start"] < b["end"] and b["start"] < a["end"]


def night_share(s, night_start):
    """品目のうち夜勤時間帯にかかる割合"""
    dur = s["end"] - s["start"]
    if dur <= 0:
        return 0
    return max(0, s["end"] - max(s["start"], night_start)) / dur


def max_concurrent(sched, pred):
    """条件に合う品目が同時に何品動いているかの最大と、その時刻"""
    events = []
    for s in sched:
        if pred(s["product"]) and s["end"] > s["start"]:
            events += [(s["start"], 1), (s["end"], -1)]
    best, at, cur = 0, None, 0
    for t, d in sorted(events, key=lambda e: (e[0], e[1])):
        cur += d
        if cur > best:
            best, at = cur, t
    return best, at


def check(date_str, items, products, settings, holidays):
    sched = compute_schedule(items, products, settings)
    info = day_info(date_str, holidays)
    alerts = []

    def add(level, rule, msg, s=None):
        alerts.append({
            "level": level, "rule": rule, "message": msg,
            "line": s["line"] if s else None, "seq": s["seq"] if s else None,
        })

    by_line = {ln: [s for s in sched if s["line"] == ln] for ln in LINES}
    night = settings["night_start"]
    name = lambda s: s["product"].get("name") or s["code"]

    # 製品マスタに無い・時間未設定
    for s in sched:
        if not s["product"]:
            add("NG", "マスタ", f"{s['code']} は製品マスタにありません", s)
        elif not s["product"].get("batch_min"):
            add("注意", "マスタ", f"{name(s)}：1バッチの時間が未設定のため時刻を計算できません", s)

    for ln, rows in by_line.items():
        for i, s in enumerate(rows):
            p = s["product"]
            # 朝一限定
            # 同じ製品を分けて流している場合は、続きとして扱う
            first_run = all(r["code"] == s["code"] for r in rows[:i])
            # 朝一品が朝から同じ包装形態（例：ムルチ4号機）で続いている場合も可
            asa_run = all(
                r["product"].get("asa_ichi") and r["product"].get("packaging") == p.get("packaging") for r in rows[:i]
            )
            if p.get("asa_ichi") and not (first_run or asa_run):
                add("NG", "朝一限定", f"{ln}ライン：{name(s)} は朝一限定です（今は{i + 1}番目）", s)
            # 単一は基本朝一
            if p.get("tanitsu") and not first_run and not p.get("asa_ichi"):
                add("注意", "単一", f"{ln}ライン：{name(s)}（単一）は基本朝一です", s)
            # 特殊原料は最後（後ろが特殊原料品だけなら可）
            if p.get("last_special") and not all(r["product"].get("last_special") for r in rows[i + 1:]):
                add("NG", "製造順番ラスト", f"{ln}ライン：{name(s)} は特殊原料使用のため最後に製造", s)
            if p.get("last_special") and i == len(rows) - 1 and s["end"] > night:
                add("情報", "余裕時間①", f"{ln}ライン：特殊原料品が夜勤の最後です。30〜90分早く終わる設定か確認", s)
            # チーズ・火星人は昼勤
            if p.get("cheese") and s["end"] > night:
                add("NG", "チーズ使用製品", f"{ln}ライン：{name(s)} はチーズ使用のため昼勤で（終了 {fmt(s['end'])}）", s)
            elif p.get("kaseijin") and not p.get("last_special") and s["end"] > night:
                add("注意", "火星人使用製品", f"{ln}ライン：{name(s)} は火星人使用のため基本昼勤", s)
            # 卵なし → 卵あり の順
            if i > 0 and not p.get("egg") and rows[i - 1]["product"].get("egg"):
                add("注意", "卵の順番", f"{ln}ライン：卵なしの {name(s)} が卵ありの後になっています", s)
            # 新規品の初回
            if p.get("is_new"):
                add("情報", "余裕時間③", f"{ln}ライン：{name(s)} は新規品。終わりを30〜120分遅く設定したか確認", s)
            # 前の品目と時間が重なっていないか
            if i > 0 and s["start"] < rows[i - 1]["end"]:
                add("NG", "時刻", f"{ln}ライン：{name(s)} の開始 {fmt(s['start'])} が前の品目の終了 {fmt(rows[i - 1]['end'])} より前です", s)

    # Bライン 団子→HB→団子 の切替
    b = by_line["B"]
    for i in range(2, len(b)):
        t = [b[j]["product"].get("dango_type") for j in (i - 2, i - 1, i)]
        if all(t) and t[0] == t[2] != t[1]:
            add("注意", "Bライン切替", f"Bライン：{t[0]}→{t[1]}→{t[2]} の切替になっています", b[i])

    # Dライン DeJong：三育の後のロティール
    d = by_line["D"]
    for i in range(1, len(d)):
        if "三育" in name(d[i - 1]) and d[i]["product"].get("rotiru"):
            add("注意", "DeJong洗浄", "Dライン：三育の後にロティールがあり、DeJongの洗浄が必要になります", d[i])

    # 単一どうしの重なり
    tan = [s for s in sched if s["product"].get("tanitsu")]
    for i, a in enumerate(tan):
        for c in tan[i + 1:]:
            if a["line"] != c["line"] and overlaps(a, c):
                add("NG", "単一", f"単一の {name(a)}（{a['line']}）と {name(c)}（{c['line']}）が同時刻に重なっています", c)

    # ロティール
    rot = [s for s in sched if s["product"].get("rotiru") == "main"]
    for i, a in enumerate(rot):
        for c in rot[i + 1:]:
            if a["line"] != c["line"] and overlaps(a, c):
                add("NG", "ロティールかぶり①", f"ロティールが2ライン同時です（{a['line']}・{c['line']}）。前処理が追い付きません", c)
    if rot and any(s["product"].get("rotiru") == "derived" for s in sched):
        add("NG", "ロティールかぶり②", "ロティールと、ロティールミートボール／バーガーパティが同じ日にあります")

    # 機械割り：MSとキャリアの両方を使う製品が、製造時間で重なるのは上限まで
    limit = settings["machine_limit"]
    gauges = {}
    n, at = max_concurrent(sched, lambda p: uses_ms(p) and uses_carrier(p))
    gauges["MS＋キャリア"] = {"value": n, "limit": limit, "unit": "品"}
    if n > limit:
        names = "、".join(
            name(s) for s in sched
            if uses_ms(s["product"]) and uses_carrier(s["product"]) and s["start"] <= at < s["end"]
        )
        add("NG", "機械割り", f"MS・キャリアを両方使う製品が {fmt(at)} 頃に {n} 品同時です（上限 {limit}）：{names}")

    # 解凍肉
    cap = settings["thaw_caps"].get(str(info["weekday"]), 0)
    thaw_total = sum((s["product"].get("thaw_kg") or 0) * s["batches"] for s in sched)
    missing = sorted({name(s) for s in sched if s["product"] and s["product"].get("thaw_kg") is None})
    gauges["解凍肉"] = {"value": round(thaw_total), "limit": cap, "unit": "kg", "missing": len(missing)}
    if cap and thaw_total > cap:
        add("NG", "解凍肉", f"解凍量 {thaw_total:,.0f}kg が解凍庫の容量 {cap:,}kg を超えています")
    if missing:
        add("情報", "解凍肉", f"解凍kg/B が未設定の製品があります（{len(missing)}品）：" + "、".join(missing[:6]) + ("…" if len(missing) > 6 else ""))

    # 排水処理
    rot_all = [s for s in sched if s["product"].get("drain_group") == "ロティール"]
    rot_b = sum(s["batches"] for s in rot_all)
    rot_night = sum(s["batches"] * night_share(s, night) for s in rot_all)
    rot_lim = settings["drain_rotiru_pre_holiday"] if info["pre_holiday"] else settings["drain_rotiru"]
    gauges["排水 ロティール"] = {"value": rot_b, "limit": rot_lim, "unit": "B"}
    if rot_b > rot_lim:
        add("NG", "排水処理", f"ロティール {rot_b:g}B が排水の上限 {rot_lim}B を超えています" + ("（休日前）" if info["pre_holiday"] else ""))
    if rot_night > settings["drain_rotiru_night"]:
        add("NG", "排水処理", f"ロティールの夜間分 約{rot_night:.1f}B が上限 {settings['drain_rotiru_night']}B を超えています")
    fuji_b = sum(s["batches"] for s in sched if s["product"].get("drain_group") == "フジ食品")
    gauges["排水 フジ食品"] = {"value": fuji_b, "limit": settings["drain_fuji"], "unit": "B"}
    if fuji_b > settings["drain_fuji"]:
        add("NG", "排水処理", f"フジ食品 {fuji_b:g}B が排水の上限 {settings['drain_fuji']}B を超えています")

    # 土曜・祝日・連休前後は包装機製品
    if info["is_saturday"] or info["pre_long"] or info["post_long"] or info["is_holiday"]:
        for s in sched:
            pack, nm = s["product"].get("packaging") or "", name(s)
            if s["product"] and "包装機" not in pack and "PG" not in nm:
                add("NG", "土曜・休日前後", f"{s['line']}ライン：{nm} は {pack or '包装形態不明'}。土曜・休日前後は包装機製品にします", s)

    # 稼働ライン数
    active = [ln for ln in LINES if by_line[ln]]
    gauges["稼働ライン"] = {"value": len(active), "limit": 3, "unit": "ライン"}
    if len(active) >= 4:
        add("注意", "3ライン稼働", "4ライン稼働です。外注清掃費削減のためできるだけ3ラインに")

    # 切替のタイミング
    changes = sorted((s["start"], s["line"]) for ln in LINES for s in by_line[ln][1:])
    for i in range(1, len(changes)):
        if changes[i][0] - changes[i - 1][0] < 15 and changes[i][1] != changes[i - 1][1]:
            add("情報", "切替のタイミング", f"{changes[i - 1][1]}・{changes[i][1]}ラインの切替が {fmt(changes[i][0])} 頃に重なっています")
    night_changes = sum(1 for t, _ in changes if t >= night)
    if night_changes >= 3:
        add("情報", "切替のタイミング", f"夜勤の切替が {night_changes} 回あります。できるだけ少なく")

    order = {"NG": 0, "注意": 1, "情報": 2}
    alerts.sort(key=lambda a: order[a["level"]])
    rows = [
        {k: v for k, v in s.items() if k != "product"} | {"start_text": fmt(s["start"]), "end_text": fmt(s["end"])}
        for s in sched
    ]
    return {"schedule": rows, "alerts": alerts, "gauges": gauges, "day": info}
