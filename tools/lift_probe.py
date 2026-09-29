"""PC から昇降部（ESP32）を確かめる道具。

    .venv/bin/python tools/lift_probe.py ws://hve-lift.local/ws

**キー操作だけ**で昇降部を動かし、受けた `state` を 1 行ずつ表示する
（DetailedDesign-protocol.md §2.2・§2.3。実際の画面は WP-UI-01 が作る）。

- `lift_probe_period_ms` ごと（既定 100 ms）に `cmd` を送り続ける。止まっている間も
  送る（protocol §1「生存確認」）。送り続けるのは「押しっぱなし」を再現するため。
- **既定は `ceil_ok: false`（上昇しない側）**。上昇を試すときは `c` を押す
  （[DetailedDesign.md](../../docs/plan/detailed/DetailedDesign.md) §3）。
- `x` で送信を止める。止めると `LIFT_CMD_TIMEOUT_MS` で昇降部側が止まる
  ことを確かめられる（protocol §1 のウォッチドッグ）。`x` で再開する。

キーの解釈と `cmd` の組み立ては純関数（`key_action` / `apply_action` / `build_cmd`）に
分けてあるので、`tools/tests/test_lift_probe.py` でネットワーク無しで試験できる。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
from typing import Any

import aiohttp

# `cmd` を送る周期。**仮**（names.md §5。カメラ部の lift_cmd_period_ms と同じ）
lift_probe_period_ms = 100
# 起動直後のデューティ。**仮**（names.md §5。protocol §2.2 の例の値）
lift_probe_duty_pct = 40
# `+` / `-` で 1 回に変える量。**仮**（names.md §5）
lift_probe_duty_step_pct = 5

#: キーの対応表（names.md §5 の lift_probe_keys）。
#: キーボードの `+` は Shift を押さないと `=` が出るので、両方を受ける。
KEY_ACTIONS: dict[str, str] = {
    "u": "up",
    "d": "down",
    "s": "stop",
    "c": "toggle_ceil_ok",
    "+": "duty_up",
    "=": "duty_up",
    "-": "duty_down",
    "x": "toggle_sending",
    "q": "quit",
}

#: 立ち上がりは安全側。上昇しない。上昇を試すときは `c` を押す
DEFAULT_CEIL_OK = False


def key_action(key: str) -> str | None:
    """キーを操作名に変える。知らないキーは `None`（何もしない）。"""
    return KEY_ACTIONS.get(key)


def clamp_duty(duty_pct: int) -> int:
    """デューティを 0〜100 に丸める（protocol §2.2）。"""
    return max(0, min(100, int(duty_pct)))


def initial_state(duty_pct: int = lift_probe_duty_pct, ceil_ok: bool = DEFAULT_CEIL_OK) -> dict:
    """起動直後の状態。**止まっていて、上昇の許可は外れている**。"""
    return {"dir": "stop", "duty_pct": clamp_duty(duty_pct), "ceil_ok": bool(ceil_ok)}


def apply_action(state: dict, action: str) -> dict:
    """操作を `state` へ適用した新しい `state` を返す（元の辞書は変えない）。

    `stop` でもデューティは保持する。0 に戻すと、次押した瞬間に消えるから。
    """
    out = dict(state)
    if action == "up":
        out["dir"] = "up"
    elif action == "down":
        out["dir"] = "down"
    elif action == "stop":
        out["dir"] = "stop"
    elif action == "toggle_ceil_ok":
        out["ceil_ok"] = not out["ceil_ok"]
    elif action == "duty_up":
        out["duty_pct"] = clamp_duty(out["duty_pct"] + lift_probe_duty_step_pct)
    elif action == "duty_down":
        out["duty_pct"] = clamp_duty(out["duty_pct"] - lift_probe_duty_step_pct)
    return out


def build_cmd(seq: int, state: dict) -> dict:
    """昇降部へ送る `cmd` を作る（DetailedDesign-protocol.md §2.2）。

    **`dir` が `up` / `down` でなくても `ceil_ok` を必ず載せる。**
    ここを落とすと「天井の許可は移動のたびに測り直す」という協定
    （[DetailedDesign.md](../../docs/plan/detailed/DetailedDesign.md) §3）が崩れる。
    """
    return {
        "t": "cmd",
        "seq": int(seq),
        "dir": state["dir"],
        "duty": clamp_duty(state["duty_pct"]),
        "ceil_ok": bool(state["ceil_ok"]),
    }


def format_state(state: dict) -> str:
    """受け取った `state` を 1 行の文字列にする。"""
    top = state.get("top_mm")
    top_text = "null" if top is None else str(top)
    return (
        f"state seq={state.get('seq')} dir={state.get('dir')} duty={state.get('duty')} "
        f"reason={state.get('reason')} bottom={state.get('bottom')} "
        f"height_mm={state.get('height_mm')} height_ok={state.get('height_ok')} "
        f"top_mm={top_text} cmd_age_ms={state.get('cmd_age_ms')} fw={state.get('fw')}"
    )


def read_keys(on_key: Any) -> None:
    """キーボードを 1 文字ずつ読んで `on_key` を呼ぶ（ブロッキング）。

    POSIX の端末では cbreak（Enter 無しで 1 文字ずつ）にして読む。cbreak が使えない
    環境（Windows など）では 1 行ずつ読んで 1 文字ずつ渡す（Enter が必要）。
    """
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
        self.seq = 0
        self._task: asyncio.Task | None = None

    def apply(self, action: str) -> None:
        """操作を当てる。`toggle_sending` は送信の ON / OFF を切り替える。"""
        if action == "toggle_sending":
            self.sending = not self.sending
            self.report("送信を再開した" if self.sending else "送信を止めた（応答を待つ）")
            return
        if action == "quit":
            self.quitting = True
            return
        self.state = apply_action(self.state, action)

    def report(self, message: str) -> None:
        print(f"* {message}", flush=True)

    def show_sent(self) -> None:
        cmd = build_cmd(self.seq, self.state)
        print(
            f"> 送る: dir={cmd['dir']} duty={cmd['duty']} ceil_ok={cmd['ceil_ok']}",
            flush=True,
        )

    async def send_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """`period_ms` ごとに `cmd` を送り続ける。"""
        while not self.quitting:
            if self.sending:
                await ws.send_str(json.dumps(build_cmd(self.seq, self.state)))
                self.seq += 1
            await asyncio.sleep(self.period_ms / 1000)

    async def receive_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """受け取った `state` を 1 行ずつ表示する。読むまで 1 周期ぶん回す。"""
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
                self.show_sent()
                self._task = asyncio.create_task(self.receive_loop(ws))
                try:
                    while not self.quitting:
                        await asyncio.sleep(self.period_ms / 1000)
                finally:
                    self._task.cancel()
        return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lift_probe.py",
        description=(
            "PC から昇降部（ESP32）をキー操作で確かめる。cmd を送り続け、"
            "受けた state を 1 行ずつ表示する。\n"
            "既定は ceil_ok=false（上昇しない）。上昇を試すときは c を押す。"
        ),
        epilog=(
            "キー:\n"
            "  u 上昇 / d 下降 / s 停止\n"
            "  c 天井の許可を切り替える（既定は off = 上昇しない）\n"
            "  + （ = も同じ）デューティ +5 / - デューティ -5\n"
            "  x 送信を止める・再開する / q 終了\n"
            f"\ncmd は {lift_probe_period_ms} ms ごと。x で送信を止めると、"
            "昇降部側のウォッチドッグ（LIFT_CMD_TIMEOUT_MS）で止まる。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", help="昇降部の WS の URL（例 ws://hve-lift.local/ws）")
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
        help=f"cmd を送る周期 ms（既定 {lift_probe_period_ms}）",
    )
    parser.add_argument(
        "--ceil-ok",
        action="store_true",
        help=f"起動直後から ceil_ok=true にする（既定 {DEFAULT_CEIL_OK} = 上昇しない）",
    )
    parser.add_argument(
        "--stop-sending",
        action="store_true",
        help="起動直後から送信を止める（ウォッチドッグの確認用。x で再開できる）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    state = initial_state(args.duty, ceil_ok=args.ceil_ok or DEFAULT_CEIL_OK)
    probe = Probe(args.url, state, sending=not args.stop_sending, period_ms=args.period_ms)
    print(
        f"* dir={state['dir']} duty={state['duty_pct']} ceil_ok={state['ceil_ok']} "
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
    probe.apply(action)
    if not probe.quitting:
        probe.show_sent()


if __name__ == "__main__":
    sys.exit(main())
