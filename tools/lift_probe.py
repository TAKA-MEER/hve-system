"""PC から昇降部を確かめる道具（v2）。

    .venv/bin/python tools/lift_probe.py ws://127.0.0.1:18080/ws/module

**キー操作だけ**で昇降部を動かし、受けた `state` を 1 行ずつ表示する
（docs/plan/detailed/DetailedDesign-protocol.md §2・DetailedDesign-protocol-modules.md §1〜§2）。
`/ws/module` へ繋ぎ、繋いだら `hello` を 1 度送る。`--name`・`--role`・`--operator`・
`--no-ceiling-sensor`・`--busy-label` で上部モジュールとして名乗れる
（例: `--name fk --operator auto --no-ceiling-sensor --busy-label 測定操作中`）。
動かしている間だけ `lift_probe_period_ms` ごと（既定 100 ms）に `hold`
（`g` のときは `goto`）を送り、離したら `release` を送る。
**距離計を持つ名乗り（既定）のときは、止まっている間も `ceiling` を同じ周期で送る**
（protocol-modules §2.3。ほかの上部モジュールの上昇に値を貸す）。距離計を持たない名乗りは
`ceiling` を一切載せない。

- 立ち上がりは安全側（`stop`・天井 `TOO_NEAR` で上昇しない）。上昇を試すときは
  `c` で天井を遠い値（`MEASURED` 2000 mm）に替える。
- `press` は押し始めごとに 1 増やす（DetailedDesign.md §3.3）。`u`/`d` で
  止まっている向きと違う向きへ動かすとき・`u` と `d` が入れ替わるときに増える。
- `x` で送信を止める。止めると `LIFT_CMD_TIMEOUT_MS`（600 ms）で昇降部側が止まる。

キーの解釈とメッセージの組み立ては純関数（`key_action` / `apply_action` /
`build_hello` / `build_hold` / `build_release`）に分けてあるので、
`tools/tests/test_lift_probe.py` でネットワーク無しで試験できる。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import threading
from typing import Any

import aiohttp

# `hold` を送る周期。**仮**（DetailedDesign-names.md §5.2 の ui_hold_period_ms と同じ）
lift_probe_period_ms = 100
# 起動直後の速さ [mm/s]。**仮**（旧版の例の値。単位だけ替わった）
lift_probe_speed_mm_s = 40
# `+` / `-` で 1 回に変える量 [mm/s]
lift_probe_speed_step_mm_s = 5
# `c` で遠い側にしたときの天井の値（CEILING_MARGIN_MM=500 より十分遠い）
lift_probe_far_mm = 2000
# 送る `hello` の名前（protocol §2.1。判定には使わない）
lift_probe_name = "lift-probe"
lift_probe_fw = "probe-0.3.0"
# `hello` の既定の名乗り（protocol-modules §1.1）
lift_probe_role = "control"
lift_probe_operator = "human"
# `goto` の目標の高さの既定 [mm] と `]` / `[` で 1 回に変える量
lift_probe_goto_mm = 1000
lift_probe_goto_step_mm = 100
# `busy_label` の長さの上限（UTF-8 のバイト数。protocol-modules §1.1）
lift_probe_busy_label_max_bytes = 48
#: `name` の形（英小文字・数字・`-` の 1〜16 文字）
NAME_RE = re.compile(r"^[a-z0-9-]{1,16}$")

#: キーの対応表。`+` は Shift を押さないと `=` が出るので、両方を受ける。
KEY_ACTIONS: dict[str, str] = {
    "u": "up",
    "d": "down",
    "s": "stop",
    "c": "toggle_ceiling",
    "+": "speed_up",
    "=": "speed_up",
    "-": "speed_down",
    "g": "goto",
    "]": "goto_up",
    "[": "goto_down",
    "x": "toggle_sending",
    "q": "quit",
}


def key_action(key: str) -> str | None:
    """キーを操作名に変える。知らないキーは `None`（何もしない）。"""
    return KEY_ACTIONS.get(key)


def clamp_speed(speed_mm_s: int) -> int:
    """速さを 0〜100 [mm/s] に丸める（protocol §2.1 は 0〜LIFT_SPEED_ABS_MAX_MM_S）。"""
    return max(0, min(100, int(speed_mm_s)))


def initial_state(speed_mm_s: int = lift_probe_speed_mm_s,
                  goto_mm: int = lift_probe_goto_mm) -> dict:
    """起動直後の状態。**止まっていて、天井は近すぎ（上昇しない側）**。

    `dir` は `up` / `down` / `goto`（目標の高さへ。`goto_mm`）/ `stop`。
    """
    return {"dir": "stop", "speed_mm_s": clamp_speed(speed_mm_s), "press": 0,
            "ceiling_near": True, "goto_mm": max(0, int(goto_mm))}


def apply_action(state: dict, action: str) -> dict:
    """操作を `state` へ適用した新しい `state` を返す（元の辞書は変えない）。

    `press` は押し始め（止まっている向きと違う向き・`up` `down` `goto` の入れ替え）
    ごとに 1 増やす。`stop` でも速さは保持する。
    """
    out = dict(state)
    if action == "goto":
        if out["dir"] != "goto":
            out["press"] = int(out["press"]) + 1
        out["dir"] = "goto"
    elif action == "goto_up":
        out["goto_mm"] = int(out["goto_mm"]) + lift_probe_goto_step_mm
    elif action == "goto_down":
        out["goto_mm"] = max(0, int(out["goto_mm"]) - lift_probe_goto_step_mm)
    elif action == "up":
        if out["dir"] != "up":
            out["press"] = int(out["press"]) + 1
        out["dir"] = "up"
    elif action == "down":
        if out["dir"] != "down":
            out["press"] = int(out["press"]) + 1
        out["dir"] = "down"
    elif action == "stop":
        out["dir"] = "stop"
    elif action == "toggle_ceiling":
        out["ceiling_near"] = not out["ceiling_near"]
    elif action == "speed_up":
        out["speed_mm_s"] = clamp_speed(out["speed_mm_s"] + lift_probe_speed_step_mm_s)
    elif action == "speed_down":
        out["speed_mm_s"] = clamp_speed(out["speed_mm_s"] - lift_probe_speed_step_mm_s)
    return out


def build_hello(name: str = lift_probe_name, role: str = lift_probe_role,
                operator: str = lift_probe_operator, ceiling_sensor: bool = True,
                busy_label: str | None = None) -> dict:
    """繋いだら 1 度送る `hello`（protocol-modules §1.1）。既定は距離計を持つ人の操作。

    `busy_label` は無いとき載せない（昇降部が名前を出す）。
    """
    hello: dict = {"t": "hello", "name": name, "role": role, "operator": operator,
                   "ceiling_sensor": bool(ceiling_sensor), "fw": lift_probe_fw}
    if busy_label is not None:
        hello["busy_label"] = busy_label
    return hello


def ceiling_of(state: dict) -> dict:
    """いまの天井の読み値。`hold` を送るその瞬間の古さは 0 に近いので 50 ms と置く。"""
    if state["ceiling_near"]:
        return {"status": "TOO_NEAR", "age_ms": 50}
    return {"status": "MEASURED", "mm": lift_probe_far_mm, "age_ms": 50}


def build_hold(state: dict, ceiling_sensor: bool = True) -> dict:
    """昇降部へ送る `hold`（protocol §2.1）。速さは目標の mm/s。

    距離計を持たない名乗り（`ceiling_sensor` が偽）は `ceiling` を載せない。
    """
    hold = {"t": "hold", "press": int(state["press"]), "dir": state["dir"],
            "speed": clamp_speed(state["speed_mm_s"])}
    if ceiling_sensor:
        hold["ceiling"] = ceiling_of(state)
    return hold


def build_goto(state: dict, ceiling_sensor: bool = True) -> dict:
    """昇降部へ送る `goto`（protocol-modules §2.2）。`hold` と同じく送り続ける。"""
    goto = {"t": "goto", "press": int(state["press"]), "target_mm": int(state["goto_mm"]),
            "speed": clamp_speed(state["speed_mm_s"])}
    if ceiling_sensor:
        goto["ceiling"] = ceiling_of(state)
    return goto


def build_ceiling(state: dict) -> dict:
    """止まっている間も送る `ceiling`（protocol-modules §2.3。距離計を持つ名乗りだけ）。"""
    return {"t": "ceiling", **ceiling_of(state)}


def command_message(state: dict, ceiling_sensor: bool = True) -> dict | None:
    """いまの操作で周期ごとに送る命令。止まっている・離しているときは `None`。"""
    if state["dir"] in ("up", "down"):
        return build_hold(state, ceiling_sensor)
    if state["dir"] == "goto":
        return build_goto(state, ceiling_sensor)
    return None


def build_release(state: dict) -> dict:
    """離したときに送る `release`（protocol §2.1）。"""
    return {"t": "release", "press": int(state["press"])}


def format_state(state: dict) -> str:
    """受け取った `state` を 1 行の文字列にする。"""
    ceiling = state.get("ceiling") or {}
    return (
        f"state seq={state.get('seq')} dir={state.get('dir')} duty={state.get('duty')} "
        f"speed={state.get('speed')} speed_meas={state.get('speed_meas')} "
        f"reason={state.get('reason')} bottom={state.get('bottom')} "
        f"height_mm={state.get('height_mm')} height_ok={state.get('height_ok')} "
        f"top_detect={state.get('top_detect')} "
        f"ceiling={ceiling.get('status')}/{ceiling.get('reason')} "
        f"owner={state.get('owner')}/{state.get('owner_name')}/{state.get('owner_operator')} "
        f"modules={','.join(str((m or {}).get('name')) for m in state.get('modules') or [])} "
        f"goto={(state.get('goto') or {}).get('status')} arm_out={(state.get('arm') or {}).get('out')} "
        f"ui_clients={state.get('ui_clients')} "
        f"cmd_age_ms={state.get('cmd_age_ms')} fw={state.get('fw')}"
    )


def read_keys(on_key: Any) -> None:
    """キーボードを 1 文字ずつ読んで `on_key` を呼ぶ（ブロッキング）。"""
    try:
        import termios
        import tty
    except ImportError:
        for line in sys.stdin:
            for char in line.strip():
                on_key(char)
        return

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            char = sys.stdin.read(1)
            if char == "":  # 端末を閉じた
                break
            if char in ("\x03", "\x04"):  # Ctrl-C / Ctrl-D
                on_key("q")
                break
            on_key(char)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


class Probe:
    """キー入力と WS の待ち受けを 1 か所で扱う。"""

    def __init__(self, url: str, state: dict, sending: bool, period_ms: int,
                 hello: dict | None = None) -> None:
        self.url = url
        self.hello = hello if hello is not None else build_hello()
        self.ceiling_sensor = bool(self.hello.get("ceiling_sensor"))
        self.state = state
        self.sending = sending
        self.period_ms = period_ms
        self.quitting = False
        self._sent_press: int | None = None  # 最後に hold/release を送った press

    def apply(self, action: str) -> dict | None:
        """操作を当て、送るべきメッセージ（あれば）を返す。"""
        if action == "toggle_sending":
            self.sending = not self.sending
            self.report("送信を再開した" if self.sending else "送信を止めた（応答を待つ）")
            return None
        if action == "quit":
            self.quitting = True
            return None
        before = self.state
        self.state = apply_action(self.state, action)
        if action in ("up", "down", "goto"):
            return command_message(self.state, self.ceiling_sensor)
        if action == "stop" and before["dir"] in ("up", "down", "goto"):
            return build_release(self.state)
        return None

    def report(self, message: str) -> None:
        print(f"* {message}", flush=True)

    def show_sent(self, message: dict | None) -> None:
        if message is None:
            return
        print(f"> 送る: {json.dumps(message, ensure_ascii=False)}", flush=True)

    async def send_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """動かしている間は `hold`・`goto` を送り続ける。止まっている間は距離計を持つ名乗りだけ
        `ceiling` を送る（protocol-modules §2.3）。"""
        await ws.send_str(json.dumps(self.hello))
        while not self.quitting:
            if self.sending:
                message = command_message(self.state, self.ceiling_sensor)
                if message is not None:
                    await ws.send_str(json.dumps(message))
                    self._sent_press = message["press"]
                elif self.ceiling_sensor:
                    await ws.send_str(json.dumps(build_ceiling(self.state)))
            await asyncio.sleep(self.period_ms / 1000)

    async def receive_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """受け取った `state` を 1 行ずつ表示する。"""
        async for message in ws:
            if message.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                state = json.loads(message.data)
            except json.JSONDecodeError:
                print(f"! 読めない JSON: {message.data!r}", flush=True)
                continue
            if state.get("t") != "state":
                continue
            print(f"  {format_state(state)}", flush=True)

    async def run(self) -> int:
        async with aiohttp.ClientSession() as session:
            print(f"* {self.url} に繋ぐ", flush=True)
            async with session.ws_connect(self.url) as ws:
                self.report("繋がった。cbreak が使える環境では Enter 無しで反応する")
                sender = asyncio.create_task(self.send_loop(ws))
                try:
                    await self.receive_loop(ws)
                finally:
                    sender.cancel()
        return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lift_probe.py",
        description=(
            "PC から昇降部をキー操作で確かめる（v2）。/ws/module へ繋ぎ、"
            "hello のあと動かしている間だけ hold を送り、離したら release を送る。"
            "既定は天井 TOO_NEAR（上昇しない）。上昇を試すときは c を押す。"
        ),
        epilog=(
            "キー:\n"
            "  u 上昇 / d 下降 / s 停止（離す・release を送る）\n"
            "  g 目標の高さへ（goto。] / [ で目標を ±100 mm）\n"
            "  c 天井を 近すぎ/遠い（2000 mm）に切り替える（既定は近すぎ = 上昇しない）\n"
            "  + （ = も同じ）速さ +5 mm/s / - 速さ -5 mm/s\n"
            "  x 送信を止める・再開する / q 終了\n"
            f"\nhold は {lift_probe_period_ms} ms ごと。x で送信を止めると、"
            "昇降部側のウォッチドッグ（LIFT_CMD_TIMEOUT_MS=600 ms）で止まる。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", help="昇降部の WS の URL（例 ws://127.0.0.1:18080/ws/module）")
    parser.add_argument(
        "--speed",
        type=int,
        default=lift_probe_speed_mm_s,
        help=f"起動直後の速さ mm/s（既定 {lift_probe_speed_mm_s}）",
    )
    parser.add_argument(
        "--period-ms",
        type=int,
        default=lift_probe_period_ms,
        help=f"hold を送る周期 ms（既定 {lift_probe_period_ms}）",
    )
    parser.add_argument(
        "--name", default=lift_probe_name,
        help=f"hello の name（英小文字・数字・- の 1〜16 文字。既定 {lift_probe_name}）")
    parser.add_argument(
        "--role", choices=("control", "observe"), default=lift_probe_role,
        help=f"hello の role（observe は状態を読むだけ。既定 {lift_probe_role}）")
    parser.add_argument(
        "--operator", choices=("human", "auto"), default=lift_probe_operator,
        help=f"hello の operator（既定 {lift_probe_operator}。fk・ss は auto）")
    parser.add_argument(
        "--no-ceiling-sensor", action="store_true",
        help="距離計を持たない名乗り（ceiling_sensor: false。ceiling も載せない）")
    parser.add_argument(
        "--busy-label", default=None,
        help="動かしている間に画面に出す文字（UTF-8 で 1〜48 バイト。例 測定操作中）")
    parser.add_argument(
        "--goto-mm", type=int, default=lift_probe_goto_mm,
        help=f"g（goto）の目標の高さ mm（既定 {lift_probe_goto_mm}。] / [ で ±{lift_probe_goto_step_mm}）")
    parser.add_argument(
        "--stop-sending",
        action="store_true",
        help="起動直後から送信を止める（ウォッチドッグの確認用。x で再開できる）",
    )
    args = parser.parse_args(argv)
    if not NAME_RE.fullmatch(args.name):
        parser.error("--name は英小文字・数字・- の 1〜16 文字にしてください")
    if args.busy_label is not None and not (
            1 <= len(args.busy_label.encode("utf-8")) <= lift_probe_busy_label_max_bytes):
        parser.error(f"--busy-label は UTF-8 で 1〜{lift_probe_busy_label_max_bytes} バイトにしてください")
    return args


def hello_from_args(args: argparse.Namespace) -> dict:
    """引数から `hello` を組み立てる。"""
    return build_hello(args.name, args.role, args.operator,
                       not args.no_ceiling_sensor, args.busy_label)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    state = initial_state(args.speed, args.goto_mm)
    probe = Probe(args.url, state, sending=not args.stop_sending, period_ms=args.period_ms,
                  hello=hello_from_args(args))
    print(
        f"* dir={state['dir']} speed={state['speed_mm_s']} mm/s "
        f"天井={'近すぎ' if state['ceiling_near'] else '遠い'} "
        f"送信={'する' if probe.sending else '止めた'}",
        flush=True,
    )
    print(f"* hello: {json.dumps(probe.hello, ensure_ascii=False)}", flush=True)
    loop = asyncio.new_event_loop()
    # キー入力は別スレッドで読む（端末待ちで loop() を止めない）
    threading.Thread(
        target=read_keys, args=(lambda key: loop.call_soon_threadsafe(_on_key, probe, key),),
        daemon=True,
    ).start()
    try:
        return loop.run_until_complete(probe.run())
    except KeyboardInterrupt:
        return 0
    except aiohttp.ClientError as err:
        print(f"! 通信に失敗しました: {err}", file=sys.stderr)
        return 1
    finally:
        loop.close()


def _on_key(probe: Probe, key: str) -> None:
    action = key_action(key)
    if action is None:
        return
    message = probe.apply(action)
    if not probe.quitting:
        probe.show_sent(message)


if __name__ == "__main__":
    sys.exit(main())
