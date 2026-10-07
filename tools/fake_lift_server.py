"""偽の昇降部（単体のプロセス）。

    .venv/bin/python tools/fake_lift_server.py --port 18080

`firmware/lift/web/` の画面・`/ws/ui`・`/ws/module`・設定 API を出す
（docs/plan/detailed/DetailedDesign.md §4.2）。
判定は `firmware/lift/lib/lift_core/` と同じ規則を真似る
（同 §3.1〜§3.3・§4.1 の表。定数は DetailedDesign-names.md §5.1）:

- `/ws/ui` で `hello` を受けたらその接続を閉じる
- `ceiling_sensor` は接続ごとに 1 度だけ受け付ける。欠け・非真偽値・
  `hello` 未受信の `/ws/module` は「持つ」とみなす
- 天井の古さは `age_ms` ＋受け取ってからの経過。`TOO_NEAR` で上昇を止める
- 持ち主でない接続の古い `press` の `hold` は無視する。止まったあとは
  より新しい `press` でなければ持ち主になれない
- 持ち主の接続が閉じたらその場で止める（`OWNER_GONE`）
- 型の違う `ceiling` は `hold` を捨てず `MISSING` として受け取る

`POST /api/fake` は試験用の裏口（names §4）。`height_mm`・`bottom`・
`height_ok` と、持ち主がいないときに表示する天井（`ceiling`）を変えられる。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import time

from aiohttp import WSMsgType, web

# --- 定数（DetailedDesign-names.md §5.1。仮のものはそのまま）---
LIFT_CMD_TIMEOUT_MS = 600
CEILING_MARGIN_MM = 500  # 仮
CEILING_STALE_MS = 600  # 仮
LIFT_MAX_RUN_MS = 10000  # 仮
LIFT_DUTY_ABS_MAX_PCT = 100
LIFT_STATE_PERIOD_MS = 100  # 仮
LIFT_WS_UI_PATH = "/ws/ui"
LIFT_WS_MODULE_PATH = "/ws/module"
FW = "fake-0.2.0"
PROVISIONAL = ["CEILING_MARGIN_MM", "CEILING_STALE_MS", "LIFT_MAX_RUN_MS"]

DEFAULT_SETTINGS = {
    "lift_up": {"min": 10, "max": 60, "init": 30},
    "lift_down": {"min": 10, "max": 60, "init": 30},
}

CEILING_KNOWN = ("MEASURED", "TOO_NEAR", "NO_ECHO", "READ_ERROR")


def now_ms() -> int:
    return int(time.monotonic() * 1000)


def clamp_duty(duty: int) -> int:
    return max(0, min(LIFT_DUTY_ABS_MAX_PCT, int(duty)))


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
            errors.append(f"{key}: 下限 ≦ 初期値 ≦ 上限・0〜100 の整数にしてください")
    if set(body.keys()) != {"lift_up", "lift_down"}:
        errors.append("lift_up と lift_down をまるごと送ってください")
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
    """→ HoldMsg の辞書。読めない hold は None（捨てる。protocol §2.1）。"""
    if not isinstance(body, dict) or body.get("t") != "hold":
        return None
    press, direction, duty = body.get("press"), body.get("dir"), body.get("duty")
    if (not isinstance(press, int) or isinstance(press, bool)
            or direction not in ("up", "down", "stop")
            or not isinstance(duty, int) or isinstance(duty, bool)):
        return None
    has_ceiling, rep = parse_ceiling(body.get("ceiling"))
    rep.received_at_ms = received_at
    return {"press": press, "dir": direction, "duty": duty,
            "has_ceiling": has_ceiling, "ceiling": rep}


def decode_hello(body: dict):
    """→ (ok, has_sensor, name)。読めない hello は「持つ」として扱う（protocol §2.1）。"""
    if not isinstance(body, dict) or body.get("t") != "hello":
        return False, True, ""
    sensor = body.get("ceiling_sensor")
    has_sensor = sensor if isinstance(sensor, bool) else True
    name = body.get("name") if isinstance(body.get("name"), str) else ""
    return True, has_sensor, name


class FakeLift:
    """持ち主・判定・状態を 1 か所で持つ（lift_core/LiftController の真似）。"""

    def __init__(self) -> None:
        self.settings = json.loads(json.dumps(DEFAULT_SETTINGS))
        self.using_defaults = False
        self.height_mm = 800
        self.height_ok = True
        self.bottom = False
        self.pinned_ceiling: CeilingReport | None = None
        self.seq = 0
        self._conns: dict[int, dict] = {}  # id → {kind, hello_seen, has_sensor, name}
        self._next_id = 1
        self._seen: dict[int, int] = {}  # id → 前に見た press
        self._owner: tuple[int, str, int] | None = None  # (id, kind, press)
        self._owner_at = 0
        self._owner_cmd: dict | None = None
        self._owner_sensor = False
        self._idle_reason = "CMD_TIMEOUT"
        self._last_module_ceiling: CeilingReport | None = None
        self._run_ms = 0
        self._run_dir = "stop"
        self._turning = False
        self._run_at = now_ms()
        self.state: dict = {}
        self.step(now_ms())

    # --- 接続の管理 ---

    def add_conn(self, kind: str) -> int:
        conn_id = self._next_id
        self._next_id += 1
        self._conns[conn_id] = {"kind": kind, "hello_seen": False,
                                "has_sensor": True, "name": ""}
        return conn_id

    def drop_conn(self, conn_id: int) -> None:
        self._conns.pop(conn_id, None)
        if self._owner and self._owner[0] == conn_id:
            self._owner = None
            self._owner_cmd = None
            self._idle_reason = "OWNER_GONE"

    def sensor_of(self, conn_id: int, kind: str) -> bool:
        if kind == "ui":
            return False
        info = self._conns.get(conn_id)
        if info is None:
            return True  # hello 未受信は持つとみなす
        return info["has_sensor"]

    def on_hello(self, conn_id: int, kind: str, body: dict) -> bool:
        """False なら呼び出し側は接続を閉じること（/ws/ui の hello）。"""
        if kind == "ui":
            return False
        info = self._conns.get(conn_id)
        if info is None:
            return True
        ok, has_sensor, name = decode_hello(body)
        if not info["hello_seen"]:
            info["hello_seen"] = True
            info["has_sensor"] = has_sensor
            info["name"] = name
        return True

    # --- 持ち主（LiftArbiter と同じ規則）---

    def on_hold(self, conn_id: int, kind: str, hold: dict, now: int) -> None:
        press = hold["press"]
        last = self._seen.get(conn_id)
        if last is not None and press < last:
            return
        if last is None or press > last:
            self._seen[conn_id] = press
            self._owner = (conn_id, kind, press)
            self._owner_at = now
        elif self._owner and self._owner[0] == conn_id and self._owner[2] == press:
            self._owner_at = now
        else:
            return
        sensor = self.sensor_of(conn_id, kind)
        self._owner_cmd = {"dir": hold["dir"], "duty": hold["duty"],
                           "sensor": sensor, "ceiling": hold["ceiling"],
                           "has_ceiling": hold["has_ceiling"], "at": now}
        if not hold["has_ceiling"]:
            missing = CeilingReport("MISSING", 0, 0, now)
            self._owner_cmd["ceiling"] = missing
        self._owner_sensor = sensor
        if kind == "module":
            self._last_module_ceiling = self._owner_cmd["ceiling"]

    def on_release(self, conn_id: int, press: int) -> None:
        if self._owner and self._owner[0] == conn_id and self._owner[2] == press:
            self._owner = None
            self._owner_cmd = None
            self._idle_reason = "CMD_STOP"

    def tick(self, now: int) -> None:
        if self._owner and now - self._owner_at > LIFT_CMD_TIMEOUT_MS:
            self._owner = None
            self._owner_cmd = None
            self._idle_reason = "CMD_TIMEOUT"

    # --- 判定（lift_decide の表の順）---

    def step(self, now: int) -> dict:
        self.tick(now)
        elapsed = now - self._run_at
        if self._turning and elapsed > 0:
            self._run_ms = min(LIFT_MAX_RUN_MS + 1, self._run_ms + elapsed)
        cmd_dir = self._owner_cmd["dir"] if self._owner_cmd else "stop"
        if cmd_dir == "stop" or cmd_dir != self._run_dir:
            self._run_ms = 0
        self._run_dir = cmd_dir

        if not self._owner or not self._owner_cmd:
            out_dir, out_duty, reason = "stop", 0, self._idle_reason
            ok, ceiling_reason = True, "NONE"
        elif cmd_dir == "stop":
            out_dir, out_duty, reason = "stop", 0, "CMD_STOP"
            ok, ceiling_reason = True, "NONE"
        else:
            cmd = self._owner_cmd
            ok, ceiling_reason = ceiling_check(cmd["ceiling"], now, cmd["sensor"])
            if cmd_dir == "up" and not ok:
                out_dir, out_duty, reason = "stop", 0, ceiling_reason
            elif cmd_dir == "down" and self.bottom:
                out_dir, out_duty, reason = "stop", 0, "BOTTOM"
            elif self._run_ms > LIFT_MAX_RUN_MS:
                out_dir, out_duty, reason = "stop", 0, "MAX_RUN"
            else:
                out_dir, out_duty, reason = cmd_dir, clamp_duty(cmd["duty"]), "NONE"

        self._turning = (reason == "NONE" and out_dir in ("up", "down")
                         and self._owner is not None and out_dir == cmd_dir)
        self._run_at = now

        ui_clients = sum(1 for c in self._conns.values() if c["kind"] == "ui")
        module = next((c for c in self._conns.values()
                       if c["kind"] == "module" and c["hello_seen"]), None)
        if self._owner_cmd:
            rep = self._owner_cmd["ceiling"]
            shown = {"used": self._owner is not None and self._owner_sensor,
                     "status": rep.status,
                     "mm": rep.mm if rep.status == "MEASURED" else None,
                     "age_ms": rep.age_ms + max(0, now - rep.received_at_ms),
                     "ok": ok, "reason": ceiling_reason}
            present = True
        elif self._last_module_ceiling or self.pinned_ceiling:
            rep = self._last_module_ceiling or self.pinned_ceiling
            assert rep is not None
            ok2, reason2 = ceiling_check(rep, now, True)
            shown = {"used": False, "status": rep.status,
                     "mm": rep.mm if rep.status == "MEASURED" else None,
                     "age_ms": rep.age_ms + max(0, now - rep.received_at_ms),
                     "ok": ok2, "reason": reason2}
            present = True
        else:
            shown, present = None, False
        self.state = {
            "t": "state", "seq": self.seq, "dir": out_dir, "duty": out_duty,
            "reason": reason, "bottom": self.bottom,
            "height_mm": self.height_mm, "height_ok": self.height_ok,
            "top_detect": False, "ceiling": shown,
            "owner": self._owner[1] if self._owner else None,
            "ui_clients": ui_clients,
            "module": {"connected": module is not None,
                       "ceiling_sensor": module["has_sensor"] if module else None,
                       "ip": None, "name": module["name"] if module else None},
            "provisional": list(PROVISIONAL), "cmd_age_ms": 0, "fw": FW,
        }
        if self._owner_cmd:
            self.state["cmd_age_ms"] = max(0, now - self._owner_cmd["at"])
        return self.state


def make_app(web_dir: pathlib.Path, lift: FakeLift) -> web.Application:
    app = web.Application()
    app["lift"] = lift
    app["sockets"] = set()

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
                         "lift_down": {k: int(body["lift_down"][k]) for k in ("min", "max", "init")}}
        lift.using_defaults = False
        return web.json_response({"settings": lift.settings})

    async def post_fake(request: web.Request) -> web.Response:
        """試験用の裏口。height_mm・bottom・height_ok と表示用の天井を変える。"""
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
        ceiling = body.get("ceiling")
        if isinstance(ceiling, dict) and ceiling.get("status") in CEILING_KNOWN:
            rep = CeilingReport(ceiling["status"], int(ceiling.get("mm", 0) or 0), 0, now_ms())
            lift.pinned_ceiling = rep
        elif "ceiling" in body:
            lift.pinned_ceiling = None
        return web.json_response({"ok": True})

    async def websocket(request: web.Request) -> web.WebSocketResponse:
        lift = request.app["lift"]
        kind = "ui" if request.path == LIFT_WS_UI_PATH else "module"
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        conn_id = lift.add_conn(kind)
        request.app["sockets"].add(ws)
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
                elif t == "hold":
                    hold = decode_hold(body, now_ms())
                    if hold is not None:
                        lift.on_hold(conn_id, kind, hold, now_ms())
                elif t == "release":
                    press = body.get("press")
                    if isinstance(press, int) and not isinstance(press, bool):
                        lift.on_release(conn_id, press)
                # 知らない t・読めない release は捨てる
        finally:
            request.app["sockets"].discard(ws)
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
