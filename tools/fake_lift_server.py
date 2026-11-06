"""偽の昇降部（単体のプロセス）。

    .venv/bin/python tools/fake_lift_server.py --port 18080

`firmware/lift/web/` の画面・`/ws/ui`・`/ws/module`・設定 API を出す
（docs/plan/detailed/DetailedDesign.md §4.2）。
判定は `firmware/lift/lib/lift_core/` と同じ規則を真似る
（同 §3.1〜§3.3・§4.1 の表。定数は DetailedDesign-names.md §5.1）:

- `/ws/ui` で `hello` を受けたらその接続を閉じる
- `hello` の `name`・`role`・`operator`・`ceiling_sensor`・`busy_label` を読む。
  `role: observe` の接続からは命令を受けない。同じ名前で繋ぎ直したら古い接続を閉じる
- `ceiling_sensor` は接続ごとに 1 度だけ受け付ける。欠け・非真偽値・
  `hello` 未受信の `/ws/module` は「持つ」とみなす（自分の命令を縛る側）
- 天井の古さは `age_ms` ＋受け取ってからの経過。`TOO_NEAR` で上昇を止める
- **距離計を持たない上部モジュールの上昇は、`hello` で `ceiling_sensor: true` を
  明示した `role: control` の接続が送る最新の天井で判定する。無い・古い・
  `READ_ERROR` は `CEILING_STALE`**（protocol-modules §2.3・§2.4）
- **画面・物理ボタンの上昇（単体の操作）は、設定 `ceiling_standalone`（既定 true）が true で、
  出どころの新しい値があるときだけ、その中で近い方で判定する**（`READ_ERROR` は止める）。
  設定が false・出どころが無い／全部古いときは天井を使わない（`CEILING_STALE` にしない）。
  `state.ceiling.standalone`（`USE` / `SETTING_OFF` / `NO_SOURCE`）は持ち主がいなくても毎回出す
  （protocol-modules §2.5）
- 持ち主でない接続の古い `press` の `hold`・`goto` は無視する。止まったあとは
  より新しい `press` でなければ持ち主になれない
- 持ち主の接続が閉じたらその場で止める（`OWNER_GONE`）
- 型の違う `ceiling` は `hold`・`goto` を捨てず `MISSING` として受け取る
- `goto` は目標の高さまで動かし、着いたら `state.goto.status` を `ARRIVED` にする。
  着いたあと同じ `press` では動き出さない。安全の判定は `hold` と同じ

`POST /api/fake` は試験用の裏口（names §4）。`height_mm`・`bottom`・
`height_ok`・`homed`・`stall`・`arm`（`state.arm` の各欄。null・真偽値・文字列）と、持ち主がいないときに表示する天井（`ceiling`）を変えられる。
`stall` が真の間は動かそうとすると `ENCODER_STALL` で止まる。
`height_ok` が偽の間は動いていれば `HEIGHT_UNKNOWN` を出すが止めない（表示だけ）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import time

from aiohttp import WSMsgType, web

# --- 定数（DetailedDesign-names.md §5.1。仮のものはそのまま）---
LIFT_CMD_TIMEOUT_MS = 600
CEILING_MARGIN_MM = 500  # 仮
CEILING_STALE_MS = 600  # 仮
LIFT_MAX_RUN_PCT = 150  # 仮（連続駆動の上限 = 全行程の何 % か。spec #4b）
LIFT_TOP_MM = 1800  # 偽物は上端の判定を持たないので、連続駆動の上限の全行程はこれで数える
LIFT_DUTY_ABS_MAX_PCT = 100
LIFT_SPEED_ABS_MAX_MM_S = 100  # 仮（設定の検証の上限・命令の丸め）
LIFT_STALL_MS = 500  # 仮（spec #4d。動き出しの遅れより長く）
LIFT_STATE_PERIOD_MS = 100  # 仮
LIFT_MOTOR_MAX_PCT_DEFAULT = 50  # モータへの出力の上限の既定値 [%]（Spec-ui §2.1）
LIFT_MOTOR_MAX_PCT_MIN = 10  # 同・下限（上限は LIFT_DUTY_ABS_MAX_PCT）
LIFT_GOTO_TOL_MM = 10  # 仮（目標に着いたとみなす幅。spec H-M6）
LIFT_MODULE_MAX_CONN = 6  # 仮（/ws/module の上限。超えた新しい接続は閉じる）
LIFT_WS_UI_PATH = "/ws/ui"
LIFT_WS_MODULE_PATH = "/ws/module"
FW = "fake-0.3.0"
PROVISIONAL = ["CEILING_MARGIN_MM", "CEILING_STALE_MS", "LIFT_MAX_RUN_PCT"]

DEFAULT_SETTINGS = {
    "lift_up": {"min": 10, "max": 60, "init": 30},
    "lift_down": {"min": 10, "max": 60, "init": 30},
    "motor_max_pct": LIFT_MOTOR_MAX_PCT_DEFAULT,
    # 単体の操作（昇降部の画面・物理ボタン）でも天井で止めるか（spec Spec-ui §2.2。既定は入）
    "ceiling_standalone": True,
}

CEILING_KNOWN = ("MEASURED", "TOO_NEAR", "NO_ECHO", "READ_ERROR")

#: `hello` の `name` の形（英小文字・数字・`-` の 1〜16 文字）
NAME_RE = re.compile(r"^[a-z0-9-]{1,16}$")


def now_ms() -> int:
    return int(time.monotonic() * 1000)


def clamp_duty(duty: int) -> int:
    return max(0, min(LIFT_DUTY_ABS_MAX_PCT, int(duty)))


def clamp_speed(speed: int) -> int:
    """速さ [mm/s] を 0〜LIFT_SPEED_ABS_MAX_MM_S に丸める（protocol §2.1）。"""
    return max(0, min(LIFT_SPEED_ABS_MAX_MM_S, int(speed)))


def validate_axis(values: dict) -> bool:
    try:
        lo, hi, init = int(values["min"]), int(values["max"]), int(values["init"])
    except (KeyError, TypeError, ValueError):
        return False
    if not (isinstance(values["min"], int) and isinstance(values["max"], int)
            and isinstance(values["init"], int)):
        return False
    return 0 <= lo <= 100 and 0 <= hi <= 100 and 0 <= init <= 100 and lo <= init <= hi


def validate_settings(body: dict) -> list[str]:
    errors = []
    for key in ("lift_up", "lift_down"):
        if not isinstance(body.get(key), dict) or not validate_axis(body[key]):
            errors.append(f"{key}: 下限 ≦ 初期値 ≦ 上限・0〜100 mm/s の整数にしてください")
    motor = body.get("motor_max_pct")
    if (not isinstance(motor, int) or isinstance(motor, bool)
            or not LIFT_MOTOR_MAX_PCT_MIN <= motor <= LIFT_DUTY_ABS_MAX_PCT):
        errors.append("motor_max_pct: モータへの出力の上限 10〜100（%）の整数にしてください")
    # ceiling_standalone は欠けていてよい（欠ければ今の値を保つ）。あれば真偽値だけ
    if "ceiling_standalone" in body and not isinstance(body["ceiling_standalone"], bool):
        errors.append("ceiling_standalone: true か false にしてください")
    if set(body.keys()) - {"ceiling_standalone"} != {"lift_up", "lift_down", "motor_max_pct"}:
        errors.append("lift_up と lift_down と motor_max_pct をまるごと送ってください")
    return errors


class CeilingReport:
    def __init__(self, status="MISSING", mm=0, age_ms=0, received_at_ms=0):
        self.status = status
        self.mm = mm
        self.age_ms = age_ms
        self.received_at_ms = received_at_ms


def ceiling_check(report: CeilingReport, now: int, has_sensor: bool) -> tuple[bool, str]:
    """lift_core/ceiling_check.cpp と同じ規則。→ (ok, reason)。"""
    if not has_sensor:
        return True, "NONE"
    age = report.age_ms + max(0, now - report.received_at_ms)
    if age > CEILING_STALE_MS:
        return False, "CEILING_STALE"
    if report.status == "MEASURED":
        if report.mm <= CEILING_MARGIN_MM:
            return False, "CEILING_NEAR"
    elif report.status == "TOO_NEAR":
        return False, "CEILING_NEAR"
    elif report.status == "NO_ECHO":
        return True, "OUT_OF_RANGE"
    elif report.status in ("READ_ERROR", "MISSING"):
        return False, "CEILING_STALE"
    else:  # 知らない値は来ないはずだが、安全側に倒す
        return False, "CEILING_STALE"
    return True, "NONE"


def parse_ceiling(raw) -> tuple[bool, CeilingReport]:
    """hold.ceiling を読む。欠け・読めない・知らない status・型違いは
    hold を捨てず (False, MISSING) にする（protocol §2.1）。"""
    rep = CeilingReport()
    if not isinstance(raw, dict):
        return False, rep
    status, age = raw.get("status"), raw.get("age_ms")
    if status not in CEILING_KNOWN or not isinstance(age, int) or isinstance(age, bool) or age < 0:
        return False, rep
    if status == "MEASURED":
        mm = raw.get("mm")
        if not isinstance(mm, int) or isinstance(mm, bool) or mm < 0:
            return False, rep
        rep.status, rep.mm, rep.age_ms = status, mm, age
    else:
        rep.status, rep.age_ms = status, age
    return True, rep


def decode_hold(body: dict, received_at: int):
    """→ HoldMsg の辞書。読めない hold は None（捨てる。protocol §2.1）。

    速さは `speed` [mm/s] で受ける。`speed` が欠けている・整数でない・
    真偽値のものと、`duty` だけの hold は読めない hold として捨てる。
    """
    if not isinstance(body, dict) or body.get("t") != "hold":
        return None
    press, direction, speed = body.get("press"), body.get("dir"), body.get("speed")
    if (not isinstance(press, int) or isinstance(press, bool)
            or direction not in ("up", "down", "stop")
            or not isinstance(speed, int) or isinstance(speed, bool)):
        return None
    has_ceiling, rep = parse_ceiling(body.get("ceiling"))
    rep.received_at_ms = received_at
    return {"press": press, "dir": direction, "speed": clamp_speed(speed),
            "has_ceiling": has_ceiling, "ceiling": rep}


def decode_hello(body: dict):
    """→ (ok, info)。読めない hello は「持つ」既定として扱う（protocol-modules §1.1）。

    info は name / role / operator / has_sensor / explicit_sensor /
    busy_label / fw。`name` が形に合わないときは空（名前無し）にする。
    """
    if not isinstance(body, dict) or body.get("t") != "hello":
        return False, default_hello()
    raw_name = body.get("name")
    name = raw_name if isinstance(raw_name, str) and NAME_RE.fullmatch(raw_name) else ""

    raw_role = body.get("role")
    if raw_role is None:
        role = "control"
    else:
        role = raw_role if raw_role in ("control", "observe") else "observe"

    operator = "auto" if body.get("operator") == "auto" else "human"

    sensor = body.get("ceiling_sensor")
    has_sensor = sensor if isinstance(sensor, bool) else True
    explicit_sensor = sensor is True

    raw_label = body.get("busy_label")
    busy_label = None
    if isinstance(raw_label, str):
        # UTF-8 で 1〜48 バイト。超えた・空は無し（切り詰めない）
        if 1 <= len(raw_label.encode("utf-8")) <= 48:
            busy_label = raw_label

    fw = body.get("fw") if isinstance(body.get("fw"), str) else None
    return True, {"name": name, "role": role, "operator": operator,
                  "has_sensor": has_sensor, "explicit_sensor": explicit_sensor,
                  "busy_label": busy_label, "fw": fw}


def default_hello() -> dict:
    return {"name": "", "role": "control", "operator": "human",
            "has_sensor": True, "explicit_sensor": False,
            "busy_label": None, "fw": None}


def decode_goto(body: dict, received_at: int):
    """→ goto の辞書。読めない goto は None（捨てる。protocol-modules §2.2）。

    `target_mm` の範囲の判定は受け付けのときに行い、ここでは読める形かだけ見る。
    """
    if not isinstance(body, dict) or body.get("t") != "goto":
        return None
    press, target, speed = body.get("press"), body.get("target_mm"), body.get("speed")
    if (not isinstance(press, int) or isinstance(press, bool)
            or not isinstance(target, int) or isinstance(target, bool)
            or not isinstance(speed, int) or isinstance(speed, bool)):
        return None
    has_ceiling, rep = parse_ceiling(body.get("ceiling"))
    rep.received_at_ms = received_at
    return {"press": press, "target_mm": target, "speed": clamp_speed(speed),
            "has_ceiling": has_ceiling, "ceiling": rep}


class FakeLift:
    """持ち主・判定・状態を 1 か所で持つ（lift_core/LiftController の真似）。

    複数の上部モジュールの接続を持つ（protocol-modules §1〜§4）。
    """

    def __init__(self) -> None:
        self.settings = json.loads(json.dumps(DEFAULT_SETTINGS))
        self.using_defaults = False
        self.height_mm = 800
        self.height_ok = True
        self.homed = True
        self.bottom = False
        self.stall = False  # 真の間は動かそうとすると ENCODER_STALL で止まる（POST /api/fake）
        self.arm: dict = {"attached": None, "link": None, "confirm": None,
                          "out": None, "drum": None}  # 分からないときは null（POST /api/fake で変える）
        self.pinned_ceiling: CeilingReport | None = None
        self.seq = 0
        # id → {kind, hello_seen, has_sensor, explicit_sensor, name, role, operator,
        #       busy_label, fw, ip, ceiling}
        self._conns: dict[int, dict] = {}
        self._next_id = 1
        self._seen: dict[int, int] = {}  # id → 前に見た press
        self._owner: tuple[int, str, int] | None = None  # (id, kind, press)
        self._owner_at = 0
        self._owner_cmd: dict | None = None
        self._idle_reason = "CMD_TIMEOUT"
        self._goto: dict | None = None  # 最後の goto（by・press・target_mm・status・reason）
        self._goto_live = False  # 持ち主がいま goto を実行しているか
        self.replaced: list[int] = []  # 同じ名前で置き換えられた接続（呼び出し側が閉じる）
        self._run_ms = 0  # ENCODER_STALL の真似に使う（回していた時間）
        self._run_travel_um = 0  # MAX_RUN 用: 目標の速さで進んだとみなした距離 [µm]
        self._run_speed = 0
        self._run_dir = "stop"
        self._turning = False
        self._run_at = now_ms()
        self.state: dict = {}
        self.step(now_ms())

    # --- 接続の管理 ---

    def add_conn(self, kind: str, ip: str | None = None) -> int:
        conn_id = self._next_id
        self._next_id += 1
        self._conns[conn_id] = {"kind": kind, "hello_seen": False, "ip": ip,
                                "ceiling": None, **default_hello()}
        return conn_id

    def module_count(self) -> int:
        return sum(1 for c in self._conns.values() if c["kind"] == "module")

    def name_of(self, conn_id: int) -> str:
        """表示・出どころに使う名前。名前無しは `anon-<接続番号>`（protocol-modules §1.1）。"""
        info = self._conns.get(conn_id)
        if info is None:
            return ""
        return info["name"] or f"anon-{conn_id}"

    def _lose_owner(self, reason: str) -> None:
        self._owner = None
        self._owner_cmd = None
        self._idle_reason = reason
        self._end_goto(reason)

    def _end_goto(self, reason: str) -> None:
        """実行中の goto が着く前に終わった（NOT_ARRIVED と止めた理由）。"""
        if self._goto_live and self._goto is not None:
            self._goto["status"] = "NOT_ARRIVED"
            self._goto["reason"] = reason
        self._goto_live = False

    def drop_conn(self, conn_id: int) -> None:
        self._conns.pop(conn_id, None)
        if self._owner and self._owner[0] == conn_id:
            self._lose_owner("OWNER_GONE")

    def sensor_of(self, conn_id: int, kind: str) -> bool:
        if kind == "ui":
            return False
        info = self._conns.get(conn_id)
        if info is None:
            return True  # 接続が無い（試験用）は持つとみなす
        return info["has_sensor"]

    def _is_source(self, info: dict) -> bool:
        """天井の値の出どころになれるか（protocol-modules §2.3）。

        `hello` で `ceiling_sensor: true` を明示した `role: control` の接続だけ。
        """
        return (info["kind"] == "module" and info["hello_seen"]
                and info["explicit_sensor"] and info["role"] == "control")

    def on_hello(self, conn_id: int, kind: str, body: dict) -> bool:
        """False なら呼び出し側は接続を閉じること（/ws/ui の hello）。"""
        if kind == "ui":
            return False
        info = self._conns.get(conn_id)
        if info is None:
            return True
        _ok, hello = decode_hello(body)
        if not info["hello_seen"]:  # 2 度目以降の hello は無視する
            info["hello_seen"] = True
            info.update(hello)
            if info["name"]:
                # 同じ名前の別の接続があれば古い方を閉じる（§1.2）
                for other_id, other in list(self._conns.items()):
                    if (other_id != conn_id and other["kind"] == "module"
                            and other["hello_seen"] and other["name"] == info["name"]):
                        self.replaced.append(other_id)
                        self.drop_conn(other_id)
        return True

    def _may_command(self, conn_id: int) -> bool:
        info = self._conns.get(conn_id)
        return info is None or info["role"] != "observe"  # observe からの命令は捨てる

    # --- 持ち主（LiftArbiter と同じ規則）---

    def _claim(self, conn_id: int, kind: str, press: int, now: int) -> bool:
        """`hold`・`goto` 共通の持ち主の規則。受け付けたら True。"""
        if not self._may_command(conn_id):
            return False
        last = self._seen.get(conn_id)
        if last is not None and press < last:
            return False
        if last is None or press > last:
            self._seen[conn_id] = press
            if self._owner is not None:
                self._end_goto("PREEMPTED")  # 取って代わられた
            self._owner = (conn_id, kind, press)
            self._owner_at = now
            return True
        if self._owner and self._owner[0] == conn_id and self._owner[2] == press:
            self._owner_at = now
            return True
        return False

    def _remember_ceiling(self, conn_id: int, report: CeilingReport) -> None:
        info = self._conns.get(conn_id)
        if info is not None and self._is_source(info):
            info["ceiling"] = report

    def on_hold(self, conn_id: int, kind: str, hold: dict, now: int) -> None:
        if not self._claim(conn_id, kind, hold["press"], now):
            return
        if self._goto_live and self._owner_cmd and self._owner_cmd.get("mode") == "goto":
            self._end_goto("CMD_STOP")  # 同じ持ち主が goto から hold に替えた
        ceiling = hold["ceiling"] if hold["has_ceiling"] else CeilingReport("MISSING", 0, 0, now)
        self._owner_cmd = {"mode": "hold", "dir": hold["dir"], "speed": hold["speed"],
                           "ceiling": ceiling, "at": now}
        self._remember_ceiling(conn_id, ceiling)

    def on_goto(self, conn_id: int, kind: str, goto: dict, now: int) -> None:
        """目標の高さへの移動（protocol-modules §2.2）。持ち主の規則は hold と同じ。"""
        if not self._claim(conn_id, kind, goto["press"], now):
            return
        ceiling = goto["ceiling"] if goto["has_ceiling"] else CeilingReport("MISSING", 0, 0, now)
        self._remember_ceiling(conn_id, ceiling)
        is_new = not (self._goto_live and self._goto is not None
                      and self._goto["press"] == goto["press"]
                      and self._goto["by"] == self.name_of(conn_id))
        block = None
        if not self.height_ok:
            block = "HEIGHT_UNKNOWN"
        elif goto["target_mm"] < 0 or goto["target_mm"] > LIFT_TOP_MM:
            block = "TARGET_RANGE"
        self._owner_cmd = {"mode": "goto", "dir": "stop", "speed": goto["speed"],
                           "target_mm": goto["target_mm"], "block": block,
                           "ceiling": ceiling, "at": now}
        if is_new:
            self._goto = {"by": self.name_of(conn_id), "press": goto["press"],
                          "target_mm": goto["target_mm"],
                          "status": "NOT_ARRIVED" if block else "MOVING",
                          "reason": block or "NONE"}
            self._goto_live = block is None

    def on_release(self, conn_id: int, press: int) -> None:
        if not self._may_command(conn_id):
            return
        if self._owner and self._owner[0] == conn_id and self._owner[2] == press:
            self._lose_owner("CMD_STOP")

    def on_ceiling(self, conn_id: int, body: dict, now: int) -> None:
        """昇降を命じていない間も届く天井の値（protocol-modules §2.3）。

        出どころになれない接続（`ceiling_sensor: true` を明示した `control` でない）の
        値は、ほかの接続の判定に使わない（捨てる）。
        """
        info = self._conns.get(conn_id)
        if info is None or not self._is_source(info):
            return
        _has, rep = parse_ceiling(body)  # 読めない値は MISSING（= 止める側）
        rep.received_at_ms = now
        info["ceiling"] = rep

    def tick(self, now: int) -> None:
        if self._owner and now - self._owner_at > LIFT_CMD_TIMEOUT_MS:
            self._lose_owner("CMD_TIMEOUT")

    @staticmethod
    def _max_run_um() -> int:
        return LIFT_TOP_MM * 10 * LIFT_MAX_RUN_PCT

    def _source_ceiling(self, now: int) -> tuple[bool, str, CeilingReport | None, str | None]:
        """距離計を持たない上部モジュールの上昇に使う天井（§2.4）。

        → (ok, reason, 使った値, 出どころの名前)。出どころの値が無い・古いときは
        `CEILING_STALE`。2 台以上なら厳しい側（止める方）を使う。
        """
        results = []
        for conn_id, info in self._conns.items():
            rep = info["ceiling"]
            if rep is None or not self._is_source(info):
                continue
            ok, reason = ceiling_check(rep, now, True)
            results.append((ok, reason, rep, self.name_of(conn_id)))
        if not results:
            return False, "CEILING_STALE", None, None
        for item in results:
            if not item[0]:
                return item
        return results[0]

    @staticmethod
    def _closeness(rep: CeilingReport) -> int:
        """近さの順（小さいほど厳しい）。読めない・近すぎ → 反射なしの順に緩くなる。"""
        if rep.status in ("READ_ERROR", "MISSING"):
            return -2
        if rep.status == "TOO_NEAR":
            return -1
        if rep.status == "NO_ECHO":
            return 1 << 30
        return rep.mm

    def _standalone_ceiling(
        self, now: int
    ) -> tuple[str, bool, str, CeilingReport | None, str | None]:
        """単体の操作（昇降部の画面・物理ボタン）の天井（protocol-modules §2.5）。

        → (standalone, ok, reason, 使った値, 出どころの名前)。`standalone` は
        `USE` / `SETTING_OFF` / `NO_SOURCE`。持ち主がいなくても毎回求める。
        """
        if not self.settings.get("ceiling_standalone", True):
            return "SETTING_OFF", True, "NONE", None, None
        fresh = []
        for conn_id, info in self._conns.items():
            rep = info["ceiling"]
            if rep is None or not self._is_source(info):
                continue
            if rep.age_ms + max(0, now - rep.received_at_ms) > CEILING_STALE_MS:
                continue  # 古い出どころは無視する（新しい出どころが別にあればそちらを使う）
            fresh.append((rep, self.name_of(conn_id)))
        if not fresh:
            # 通信なし: 天井を使わずに許す。CEILING_STALE にしない（上部モジュールと逆）
            return "NO_SOURCE", True, "NONE", None, None
        rep, src = min(fresh, key=lambda f: self._closeness(f[0]))
        ok, reason = ceiling_check(rep, now, True)  # READ_ERROR・MISSING は止める
        return "USE", ok, reason, rep, src

    def _owner_ceiling(self, now: int) -> tuple[bool, str, CeilingReport | None, str | None]:
        """持ち主の命令に使う天井。→ (ok, reason, 値, 出どころ)。使わないときは値が None。"""
        if self._owner and self._owner_cmd and self._owner[1] in ("ui", "button"):
            _st, ok, reason, rep, src = self._standalone_ceiling(now)
            return ok, reason, rep, src
        if not self._owner or not self._owner_cmd or self._owner[1] != "module":
            return True, "NONE", None, None
        conn_id = self._owner[0]
        info = self._conns.get(conn_id)
        if info is None or info["has_sensor"]:
            rep = self._owner_cmd["ceiling"]
            ok, reason = ceiling_check(rep, now, True)
            return ok, reason, rep, self.name_of(conn_id)
        return self._source_ceiling(now)

    # --- 判定（lift_decide の表の順）---

    def step(self, now: int) -> dict:
        self.tick(now)
        elapsed = now - self._run_at
        if self._turning and elapsed > 0:
            self._run_ms = min(LIFT_STALL_MS + 1, self._run_ms + elapsed)
            # 直前の step で決めた目標の速さ [mm/s] × 経過 [ms] = µm（測った速さは使わない）
            self._run_travel_um = min(self._max_run_um() + 1,
                                      self._run_travel_um + self._run_speed * elapsed)
        cmd = self._owner_cmd
        goto_block = None
        arrived = False
        if cmd is not None and cmd["mode"] == "goto":
            diff = cmd["target_mm"] - float(self.height_mm)
            if cmd["block"]:
                cmd_dir, goto_block = "stop", cmd["block"]
            elif abs(diff) <= LIFT_GOTO_TOL_MM:
                cmd_dir, arrived = "stop", True
            else:
                cmd_dir = "up" if diff > 0 else "down"
        else:
            cmd_dir = cmd["dir"] if cmd else "stop"
        if cmd_dir == "stop" or cmd_dir != self._run_dir:
            self._run_ms = 0
            self._run_travel_um = 0
        self._run_dir = cmd_dir

        sel_ok, sel_reason, sel_rep, sel_from = self._owner_ceiling(now)
        if arrived and self._owner is not None:
            # 着いたら止めて持ち主を離す。同じ press の goto では動き出さない（§2.2）
            if self._goto is not None:
                self._goto["status"], self._goto["reason"] = "ARRIVED", "NONE"
            self._goto_live = False
            self._owner = None
            self._owner_cmd = None
            self._idle_reason = "ARRIVED"
            cmd = None
            cmd_dir = "stop"
        if not self._owner or not cmd:
            out_dir, out_duty, reason = "stop", 0, self._idle_reason
            ok, ceiling_reason = True, "NONE"
            cmd_speed = 0
        elif goto_block:
            out_dir, out_duty, reason = "stop", 0, goto_block
            ok, ceiling_reason = True, "NONE"
            cmd_speed = 0
        elif cmd_dir == "stop":
            out_dir, out_duty, reason = "stop", 0, "CMD_STOP"
            ok, ceiling_reason = True, "NONE"
            cmd_speed = 0
        else:
            cmd_speed = clamp_speed(cmd["speed"])
            ok, ceiling_reason = sel_ok, sel_reason
            if cmd_dir == "up" and not ok:
                out_dir, out_duty, reason = "stop", 0, ceiling_reason
            elif cmd_dir == "down" and self.bottom:
                out_dir, out_duty, reason = "stop", 0, "BOTTOM"
            elif self._run_travel_um > self._max_run_um():
                out_dir, out_duty, reason = "stop", 0, "MAX_RUN"
            elif self.stall and self._run_ms > LIFT_STALL_MS:
                # エンコーダが回らない（#8a の真似。数え方は MAX_RUN と同じ）。
                # 命令が stop になるか向きが替わるまで続ける
                out_dir, out_duty, reason = "stop", 0, "ENCODER_STALL"
            else:
                # 偽物のモータへの出力は速さと同じ数（protocol §2.2 の SpeedLoop の出力の真似）
                out_dir, out_duty, reason = cmd_dir, cmd_speed, "NONE"

        self._turning = (reason == "NONE" and out_dir in ("up", "down")
                         and self._owner is not None and out_dir == cmd_dir)
        self._run_at = now
        self._run_speed = cmd_speed if self._turning else 0

        moving = self._turning
        if moving:
            # 偽の高さは速さ [mm/s] × 経過時間で動かす
            sign = 1 if out_dir == "up" else -1
            self.height_mm = max(0.0, float(self.height_mm) + sign * cmd_speed * elapsed / 1000)
        if reason == "NONE" and not self.height_ok:
            # W-1 の間は昇降部は上昇を止めないので、偽物も止めない。表示だけ
            reason = "HEIGHT_UNKNOWN"

        if self._goto_live and self._goto is not None:
            # 実行中の goto: 動いている間 MOVING、安全の判定で止まっている間は NOT_ARRIVED と理由
            stopped_reason = reason if reason != "NONE" else None
            self._goto["status"] = "NOT_ARRIVED" if stopped_reason else "MOVING"
            self._goto["reason"] = stopped_reason or "NONE"

        ui_clients = sum(1 for c in self._conns.values() if c["kind"] == "ui")
        owner_conn = self._owner[0] if self._owner else None
        owner_kind = self._owner[1] if self._owner else None
        owner_info = self._conns.get(owner_conn) if owner_conn is not None else None
        if owner_kind == "module":
            owner_name = self.name_of(owner_conn)
            owner_operator = owner_info["operator"] if owner_info else "human"
        else:
            owner_name = None
            owner_operator = "human" if owner_kind in ("ui", "button") else None
        if self._owner_cmd and sel_rep is not None:
            rep = sel_rep
            shown = {"used": True, "from": sel_from, "status": rep.status,
                     "mm": rep.mm if rep.status == "MEASURED" else None,
                     "age_ms": rep.age_ms + max(0, now - rep.received_at_ms),
                     "ok": ok, "reason": ceiling_reason}
        elif self._owner_cmd:
            shown = None  # 距離計を使わない持ち主（画面・物理ボタン・出どころ無しの上部モジュール）
        else:
            shown = None
        if shown is None and self._owner_cmd is None:
            # 持ち主がいないとき: 出どころの最新の値（無ければ試験用の固定値）を参考に出す
            fresh = [(info["ceiling"], self.name_of(cid)) for cid, info in self._conns.items()
                     if info["ceiling"] is not None and self._is_source(info)]
            if fresh:
                rep, src = max(fresh, key=lambda f: f[0].received_at_ms)
            elif self.pinned_ceiling:
                rep, src = self.pinned_ceiling, None
            else:
                rep, src = None, None
            if rep is not None:
                ok2, reason2 = ceiling_check(rep, now, True)
                shown = {"used": False, "from": src, "status": rep.status,
                         "mm": rep.mm if rep.status == "MEASURED" else None,
                         "age_ms": rep.age_ms + max(0, now - rep.received_at_ms),
                         "ok": ok2, "reason": reason2}
        if shown is None:
            # 値が無くても standalone は毎回出す（画面が押す前から理由を出せるように）
            shown = {"used": False, "from": None, "status": None, "mm": None,
                     "age_ms": None, "ok": None, "reason": None}
        shown["standalone"] = self._standalone_ceiling(now)[0]
        modules = [{"name": self.name_of(cid), "role": c["role"], "operator": c["operator"],
                    "ceiling_sensor": c["has_sensor"], "busy_label": c["busy_label"],
                    "ip": c["ip"], "fw": c["fw"]}
                   for cid, c in self._conns.items() if c["kind"] == "module"]
        self.state = {
            "t": "state", "seq": self.seq, "dir": out_dir, "duty": out_duty,
            "speed": cmd_speed if self._owner_cmd else 0,
            "speed_meas": cmd_speed if moving else 0,
            "reason": reason, "bottom": self.bottom,
            "height_mm": int(round(float(self.height_mm))), "height_ok": self.height_ok,
            "homed": self.homed,
            "top_detect": False, "ceiling": shown,
            "owner": owner_kind, "owner_name": owner_name, "owner_operator": owner_operator,
            "ui_clients": ui_clients,
            "modules": modules,
            "goto": dict(self._goto) if self._goto else None,
            "arm": dict(self.arm),
            "provisional": list(PROVISIONAL), "cmd_age_ms": 0, "fw": FW,
        }
        if self._owner_cmd:
            self.state["cmd_age_ms"] = max(0, now - self._owner_cmd["at"])
        return self.state


def make_app(web_dir: pathlib.Path, lift: FakeLift) -> web.Application:
    app = web.Application()
    app["lift"] = lift
    app["sockets"] = set()
    app["conn_ws"] = {}

    async def index(_request: web.Request) -> web.Response:
        return web.FileResponse(web_dir / "index.html")

    async def get_settings(_request: web.Request) -> web.Response:
        lift = _request.app["lift"]
        return web.json_response({"settings": lift.settings,
                                  "using_defaults": lift.using_defaults})

    async def put_settings(request: web.Request) -> web.Response:
        lift = request.app["lift"]
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response({"errors": ["JSON が読めません"]}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"errors": ["JSON が読めません"]}, status=400)
        errors = validate_settings(body)
        if errors:
            return web.json_response({"errors": errors}, status=400)
        lift.settings = {"lift_up": {k: int(body["lift_up"][k]) for k in ("min", "max", "init")},
                         "lift_down": {k: int(body["lift_down"][k]) for k in ("min", "max", "init")},
                         "motor_max_pct": int(body["motor_max_pct"]),
                         # 欠けた PUT は今の値を保つ（true に戻さない）
                         "ceiling_standalone": body.get(
                             "ceiling_standalone", lift.settings.get("ceiling_standalone", True))}
        lift.using_defaults = False
        return web.json_response({"settings": lift.settings})

    async def post_fake(request: web.Request) -> web.Response:
        """試験用の裏口。height_mm・bottom・height_ok・stall と表示用の天井を変える。

        `stall` が真の間は、動かそうとすると `ENCODER_STALL` で止まる
        （命令が `stop` になるか向きが替わるまで。`MAX_RUN` と同じ数え方）。
        `height_ok` を偽にすると `HEIGHT_UNKNOWN` を出すが、止めない（表示だけ。
        `W-1` の間は昇降部も上昇を止めないため）。
        """
        lift = request.app["lift"]
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response({"errors": ["JSON が読めません"]}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"errors": ["JSON が読めません"]}, status=400)
        if "height_mm" in body:
            lift.height_mm = int(body["height_mm"])
        if "bottom" in body:
            lift.bottom = bool(body["bottom"])
        if "height_ok" in body:
            lift.height_ok = bool(body["height_ok"])
        if "stall" in body:
            lift.stall = bool(body["stall"])
        ceiling = body.get("ceiling")
        if isinstance(ceiling, dict) and ceiling.get("status") in CEILING_KNOWN:
            rep = CeilingReport(ceiling["status"], int(ceiling.get("mm", 0) or 0), 0, now_ms())
            lift.pinned_ceiling = rep
        elif "ceiling" in body:
            lift.pinned_ceiling = None
        if "homed" in body:
            lift.homed = bool(body["homed"])
        arm = body.get("arm")
        if isinstance(arm, dict):
            # 腕の状態の試験用の値。null・真偽値・文字列だけ受ける
            for key in lift.arm:
                if key in arm and (arm[key] is None or isinstance(arm[key], (bool, str))):
                    lift.arm[key] = arm[key]
        return web.json_response({"ok": True})

    async def websocket(request: web.Request) -> web.WebSocketResponse:
        lift = request.app["lift"]
        kind = "ui" if request.path == LIFT_WS_UI_PATH else "module"
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        if kind == "module" and lift.module_count() >= LIFT_MODULE_MAX_CONN:
            # 超えた新しい接続は、その場で閉じる（古い方は閉じない。protocol-modules §1.3）
            await ws.close()
            return ws
        conn_id = lift.add_conn(kind, request.remote)
        request.app["sockets"].add(ws)
        request.app["conn_ws"][conn_id] = ws
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                try:
                    body = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue  # 読めない JSON は捨てる
                if not isinstance(body, dict):
                    continue
                t = body.get("t")
                if t == "hello":
                    if not lift.on_hello(conn_id, kind, body):
                        await ws.close()
                        break
                    while lift.replaced:  # 同じ名前の古い接続を閉じる（§1.2）
                        old = request.app["conn_ws"].pop(lift.replaced.pop(), None)
                        if old is not None:
                            await old.close()
                elif t == "hold":
                    hold = decode_hold(body, now_ms())
                    if hold is not None:
                        lift.on_hold(conn_id, kind, hold, now_ms())
                elif t == "goto":
                    goto = decode_goto(body, now_ms())
                    if goto is not None:
                        lift.on_goto(conn_id, kind, goto, now_ms())
                elif t == "ceiling" and kind == "module":
                    lift.on_ceiling(conn_id, body, now_ms())
                elif t == "release":
                    press = body.get("press")
                    if isinstance(press, int) and not isinstance(press, bool):
                        lift.on_release(conn_id, press)
                # 知らない t・読めない release は捨てる
        finally:
            request.app["sockets"].discard(ws)
            request.app["conn_ws"].pop(conn_id, None)
            lift.drop_conn(conn_id)
        return ws

    async def broadcast(app: web.Application) -> None:
        lift = app["lift"]
        while True:
            await asyncio.sleep(LIFT_STATE_PERIOD_MS / 1000)
            state = lift.step(now_ms())
            state["seq"] = lift.seq
            lift.seq += 1
            text = json.dumps(state)
            for ws in list(app["sockets"]):
                try:
                    await ws.send_str(text)
                except (ConnectionResetError, RuntimeError):
                    pass

    async def on_startup(app: web.Application) -> None:
        app["broadcaster"] = asyncio.create_task(broadcast(app))

    async def on_cleanup(app: web.Application) -> None:
        app["broadcaster"].cancel()

    app.router.add_get("/", index)
    app.router.add_get("/api/settings", get_settings)
    app.router.add_put("/api/settings", put_settings)
    app.router.add_post("/api/fake", post_fake)
    app.router.add_get(LIFT_WS_UI_PATH, websocket)
    app.router.add_get(LIFT_WS_MODULE_PATH, websocket)
    app.router.add_static("/", web_dir, show_index=False)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="偽の昇降部（画面・/ws/ui・/ws/module・設定 API）")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--web", default=str(pathlib.Path(__file__).resolve().parents[1]
                                             / "firmware" / "lift" / "web"))
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    lift = FakeLift()
    app = make_app(pathlib.Path(args.web), lift)
    web.run_app(app, port=args.port, print=lambda *a: print(*a, flush=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
