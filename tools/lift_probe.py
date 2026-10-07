"""PC から昇降部を確かめる道具（v2）。

    .venv/bin/python tools/lift_probe.py ws://127.0.0.1:18080/ws/module

**キー操作だけ**で昇降部を動かし、受けた `state` を 1 行ずつ表示する
（docs/plan/detailed/DetailedDesign-protocol.md §2）。
`/ws/module` へ繋ぎ、繋いだら `hello`（`ceiling_sensor: true`）を 1 度送る。
動かしている間だけ `lift_probe_period_ms` ごと（既定 100 ms）に `hold` を送り、
離したら `release` を送る。**止まっている間は送らない**（protocol §1）。

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
import sys
import threading
from typing import Any

import aiohttp

# `hold` を送る周期。**仮**（DetailedDesign-names.md §5.2 の ui_hold_period_ms と同じ）
lift_probe_period_ms = 100
# 起動直後のデューティ。**仮**（旧版の例の値）
lift_probe_duty_pct = 40
# `+` / `-` で 1 回に変える量
lift_probe_duty_step_pct = 5
# `c` で遠い側にしたときの天井の値（CEILING_MARGIN_MM=500 より十分遠い）
lift_probe_far_mm = 2000
# 送る `hello` の名前（protocol §2.1。判定には使わない）
lift_probe_name = "lift-probe"
lift_probe_fw = "probe-0.2.0"

#: キーの対応表。`+` は Shift を押さないと `=` が出るので、両方を受ける。
KEY_ACTIONS: dict[str, str] = {
    "u": "up",
    "d": "down",
    "s": "stop",
    "c": "toggle_ceiling",
    "+": "duty_up",
    "=": "duty_up",
    "-": "duty_down",
    "x": "toggle_sending",
    "q": "quit",
}


def key_action(key: str) -> str | None:
    """キーを操作名に変える。知らないキーは `None`（何もしない）。"""
    return KEY_ACTIONS.get(key)


def clamp_duty(duty_pct: int) -> int:
    """デューティを 0〜100 に丸める（protocol §2.1 は 0〜LIFT_DUTY_ABS_MAX_PCT）。"""
    return max(0, min(100, int(duty_pct)))


def initial_state(duty_pct: int = lift_probe_duty_pct) -> dict:
    """起動直後の状態。**止まっていて、天井は近すぎ（上昇しない側）**。"""
    return {"dir": "stop", "duty_pct": clamp_duty(duty_pct), "press": 0,
            "ceiling_near": True}


def apply_action(state: dict, action: str) -> dict:
    """操作を `state` へ適用した新しい `state` を返す（元の辞書は変えない）。

    `press` は押し始め（止まっている向きと違う向き・`up` と `down` の入れ替え）
    ごとに 1 増やす。`stop` でもデューティは保持する。
    """
    out = dict(state)
    if action == "up":
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
    elif action == "duty_up":
        out["duty_pct"] = clamp_duty(out["duty_pct"] + lift_probe_duty_step_pct)
    elif action == "duty_down":
        out["duty_pct"] = clamp_duty(out["duty_pct"] - lift_probe_duty_step_pct)
    return out


def build_hello() -> dict:
    """繋いだら 1 度送る `hello`（protocol §2.1）。距離計を持つ側として振る舞う。"""
    return {"t": "hello", "ceiling_sensor": True,
            "name": lift_probe_name, "fw": lift_probe_fw}


def ceiling_of(state: dict) -> dict:
    """いまの天井の読み値。`hold` を送るその瞬間の古さは 0 に近いので 50 ms と置く。"""
    if state["ceiling_near"]:
        return {"status": "TOO_NEAR", "age_ms": 50}
    return {"status": "MEASURED", "mm": lift_probe_far_mm, "age_ms": 50}


def build_hold(state: dict) -> dict:
    """昇降部へ送る `hold`（protocol §2.1）。"""
    return {"t": "hold", "press": int(state["press"]), "dir": state["dir"],
            "duty": clamp_duty(state["duty_pct"]), "ceiling": ceiling_of(state)}


def build_release(state: dict) -> dict:
    """離したときに送る `release`（protocol §2.1）。"""
    return {"t": "release", "press": int(state["press"])}


def format_state(state: dict) -> str:
    """受け取った `state` を 1 行の文字列にする。"""
    ceiling = state.get("ceiling") or {}
    return (
        f"state seq={state.get('seq')} dir={state.get('dir')} duty={state.get('duty')} "
        f"reason={state.get('reason')} bottom={state.get('bottom')} "
        f"height_mm={state.get('height_mm')} height_ok={state.get('height_ok')} "
        f"top_detect={state.get('top_detect')} "
        f"ceiling={ceiling.get('status')}/{ceiling.get('reason')} "
        f"owner={state.get('owner')} ui_clients={state.get('ui_clients')} "
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

    def __init__(self, url: str, state: dict, sending: bool, period_ms: int) -> None:
        self.url = url
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
        if action in ("up", "down"):
            return build_hold(self.state)
        if action == "stop" and before["dir"] in ("up", "down"):
            return build_release(self.state)
        return None

    def report(self, message: str) -> None:
        print(f"* {message}", flush=True)

    def show_sent(self, message: dict | None) -> None:
        if message is None:
            return
        print(f"> 送る: {json.dumps(message, ensure_ascii=False)}", flush=True)

    async def send_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """動かしている間だけ `hold` を送り続ける。止まっている間は送らない。"""
        await ws.send_str(json.dumps(build_hello()))
        while not self.quitting:
            if self.sending and self.state["dir"] in ("up", "down"):
                hold = build_hold(self.state)
                await ws.send_str(json.dumps(hold))
                self._sent_press = hold["press"]
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
            "  c 天井を 近すぎ/遠い（2000 mm）に切り替える（既定は近すぎ = 上昇しない）\n"
            "  + （ = も同じ）デューティ +5 / - デューティ -5\n"
            "  x 送信を止める・再開する / q 終了\n"
            f"\nhold は {lift_probe_period_ms} ms ごと。x で送信を止めると、"
            "昇降部側のウォッチドッグ（LIFT_CMD_TIMEOUT_MS=600 ms）で止まる。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", help="昇降部の WS の URL（例 ws://127.0.0.1:18080/ws/module）")
    parser.add_argument(
        "--duty",
        type=int,
        default=lift_probe_duty_pct,
        help=f"起動直後のデューティ %%（既定 {lift_probe_duty_pct}）",
    )
    parser.add_argument(
        "--period-ms",
        type=int,
        default=lift_probe_period_ms,
        help=f"hold を送る周期 ms（既定 {lift_probe_period_ms}）",
    )
    parser.add_argument(
        "--stop-sending",
        action="store_true",
        help="起動直後から送信を止める（ウォッチドッグの確認用。x で再開できる）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    state = initial_state(args.duty)
    probe = Probe(args.url, state, sending=not args.stop_sending, period_ms=args.period_ms)
    print(
        f"* dir={state['dir']} duty={state['duty_pct']} "
        f"天井={'近すぎ' if state['ceiling_near'] else '遠い'} "
        f"送信={'する' if probe.sending else '止めた'}",
        flush=True,
    )
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
