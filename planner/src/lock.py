"""編集の鍵（共有フォルダで2人が同時に書き込まないようにする）

data/editing.lock に「誰が・どのPCで」編集中かを書く。
編集中のアプリは1分ごとに時刻を更新する。3分以上更新が無い鍵は、
PCが落ちたなどで残ったものとみなし、次に起動した人が引き継げる。
"""
import datetime as dt
import json
import os
import shutil
import socket
import threading

import db

LOCK_PATH = db.DATA_DIR / "editing.lock"
STALE_SEC = 180
BEAT_SEC = 60

ME = {
    "user": os.environ.get("USERNAME", "?"),
    "host": os.environ.get("COMPUTERNAME") or socket.gethostname(),
    "pid": os.getpid(),
}
_state = {"mine": False, "name": ""}


def _now():
    return dt.datetime.now()


def read():
    try:
        return json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None


def _is_me(info):
    return bool(info) and info.get("host") == ME["host"] and info.get("pid") == ME["pid"]


def _same_person(info):
    """同じPC・同じWindowsユーザーの鍵（画面を閉じて開き直した場合など）"""
    return bool(info) and info.get("host") == ME["host"] and info.get("user") == ME["user"]


def _fresh(info):
    try:
        beat = dt.datetime.fromisoformat(info["heartbeat"])
    except (KeyError, TypeError, ValueError):
        return False
    return (_now() - beat).total_seconds() < STALE_SEC


def _write(exclusive):
    data = {**ME, "name": _state["name"], "since": _now().isoformat(timespec="seconds"),
            "heartbeat": _now().isoformat(timespec="seconds")}
    text = json.dumps(data, ensure_ascii=False)
    if exclusive:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)  # 無いときだけ作る
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        LOCK_PATH.write_text(text, encoding="utf-8")


def acquire(force=False) -> bool:
    """鍵を取る。他の人が編集中なら False"""
    db.DATA_DIR.mkdir(parents=True, exist_ok=True)
    info = read()
    try:
        if info is None:
            _write(exclusive=True)
        elif _is_me(info) or _same_person(info) or force or not _fresh(info):
            _write(exclusive=False)
        else:
            _state["mine"] = False
            return False
    except FileExistsError:  # ほぼ同時に誰かが取った
        _state["mine"] = False
        return False
    _state["mine"] = _is_me(read())  # 書けたか読み直して確かめる
    return _state["mine"]


def is_mine() -> bool:
    """今も鍵を持っているか（他の人に引き継がれていないか）"""
    if _state["mine"] and not _is_me(read()):
        _state["mine"] = False
    return _state["mine"]


def set_name(name: str):
    _state["name"] = name
    if is_mine():
        _write(exclusive=False)


def release():
    if is_mine():
        try:
            LOCK_PATH.unlink()
        except FileNotFoundError:
            pass
    _state["mine"] = False


def _beat():
    while True:
        threading.Event().wait(BEAT_SEC)
        if is_mine():
            info = read()
            info["heartbeat"] = _now().isoformat(timespec="seconds")
            LOCK_PATH.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")


def start_heartbeat():
    threading.Thread(target=_beat, daemon=True).start()


def status() -> dict:
    info = read()
    return {
        "editable": is_mine(),
        "holder": info if info and not _is_me(info) and _fresh(info) else None,
        "me": ME,
    }


def backup(keep: int):
    """データを data/backup に日時付きでコピーする（鍵を取れたときだけ呼ぶ）"""
    if not db.DB_PATH.exists():
        return None
    folder = db.DATA_DIR / "backup"
    folder.mkdir(exist_ok=True)
    dst = folder / f"plan_{_now():%Y%m%d_%H%M%S}.db"
    shutil.copy2(db.DB_PATH, dst)
    for old in sorted(folder.glob("plan_*.db"))[:-keep]:
        old.unlink()
    return dst
