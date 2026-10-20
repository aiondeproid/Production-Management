"""設定（パス・ライブラリ）の読み込み

設定は planner/config.json に書く。無ければ初期値で作る。
"""
import json
import os
import sys
from pathlib import Path

# プログラム（src・static・lib）の場所
CODE_DIR = Path(__file__).resolve().parent.parent
# データ・設定・出力の場所。共有フォルダから起動したときは、生産管理.bat がプログラムを
# 各PCにコピーして動かし（共有フォルダはファイルを開くのが遅いため）、ここに共有フォルダを渡す
APP_DIR = Path(os.environ["PLANNER_HOME"]) if os.environ.get("PLANNER_HOME") else CODE_DIR
sys.path.insert(0, str(CODE_DIR / "lib"))  # 同梱ライブラリ（openpyxl・xlrd）

CONFIG_PATH = APP_DIR / "config.json"

# パスは planner フォルダからの相対パスでも書ける（フォルダごとコピーしても動くように）
PATH_KEYS = ("output_dir", "day_output_dir", "source_master_xls", "source_weekly_xls")
DEFAULTS = {
    # 配布用Excelの出力先（今の共有フォルダに書き換える）
    "output_dir": "output",
    # 成型予定表・前処理製造予定表の出力先
    "day_output_dir": "output",
    # 作業予定：各ラインの朝一の開始時刻・切替の初期値（分）・夜の休止（この時刻をまたぐ製品を分けて休止を入れる）
    "line_start": {"A": "7:30", "B": "7:20", "C": "7:35", "D": "7:20"},
    "gap_min": 30,
    "night_break": "20:00",
    "night_break_min": 120,
    # 取り込み元のExcel（最初の1回だけ使う）
    "source_master_xls": "../Book1.xls",
    "source_weekly_xls": "../2022年10月週間予定.xls",
    # 週間予定表の上に印刷する配布先
    "distribution": "配布部署：品管1・品管2・資材・マゼラー・玉ネギ",
    "port": 8770,
    "backup_keep": 30,
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    else:
        CONFIG_PATH.write_text(json.dumps(DEFAULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    for k in PATH_KEYS:
        p = Path(cfg[k])
        cfg[k] = str(p if p.is_absolute() else (APP_DIR / p).resolve())
    return cfg
