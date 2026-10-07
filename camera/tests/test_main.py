"""`python -m hve_camera` の起動の試験（brief WP-UI-01 §1・§3 #2）。

偽物のモードで立ち上げた直後から**画面を操作できること**を確かめる。
天井の読み値が無く起動すると、画面が「天井 値なし」になり上昇ボタンが薄いまま
動かせない（`POST /api/fake` で測らせるまで誰も触れない）ため、起動時に差す。
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from hve_camera import __main__ as cli
from hve_camera.ceiling import CeilingStatus, classify_srf02
from hve_camera.hw.fake_hw import FakeHardware
from hve_camera.params import load_params


async def test_fake_mode_starts_with_a_high_measured_ceiling(monkeypatch) -> None:
    """**偽物は起動直後に天井が `MEASURED` で十分遠い。**上昇が許されることまで確かめる。"""
    made: list[FakeHardware] = []

    class SpyHardware(FakeHardware):
        """作った `FakeHardware` を試験側でも見えるようにする。"""

        def __init__(self, clock: Any) -> None:
            super().__init__(clock)
            made.append(self)

    monkeypatch.setattr("hve_camera.hw.fake_hw.FakeHardware", SpyHardware)
    # 実際に待ち受けない（`run_app` までで止める）
    monkeypatch.setattr(web, "run_app", lambda app, **kwargs: None)

    assert cli.main(["--fake", "--port", "0"]) == 0
    assert len(made) == 1, "偽物の機構を 1 つだけ作る"

    reading = await made[0].read_ceiling()
    assert reading is not None, "起動時に天井の読み値を差す"
    assert reading.status is CeilingStatus.MEASURED, "反射が返ったのと同じ扱いにする"
    assert reading.distance_mm == cli.FAKE_CEILING_MM

    # 汎用の 4 状態に直すと MEASURED（許可の計算は昇降部がする）
    status, mm = classify_srf02(0, reading.distance_mm // 10, 0, load_params())
    assert (status, mm) == (CeilingStatus.MEASURED, cli.FAKE_CEILING_MM), "起動直後から上昇できる"


async def test_fake_ceiling_stays_fresh_and_can_be_replaced(monkeypatch) -> None:
    """**起動時の天井は古くならない（毎回測り直した扱い）。**API で差し替え・止めもできる。"""
    made: list[FakeHardware] = []

    class SpyHardware(FakeHardware):
        def __init__(self, clock: Any) -> None:
            super().__init__(clock)
            made.append(self)

    monkeypatch.setattr("hve_camera.hw.fake_hw.FakeHardware", SpyHardware)
    monkeypatch.setattr(web, "run_app", lambda app, **kwargs: None)

    assert cli.main(["--fake", "--port", "0"]) == 0
    hw = made[0]
    for _ in range(3):  # 何回読んでも返る（1 回で尽きると 1 秒ほどで「値なし」に戻る）
        reading = await hw.read_ceiling()
        assert reading is not None and reading.distance_mm == cli.FAKE_CEILING_MM
    # `POST /api/fake` と同じ操作: 差し込みが先に返る
    hw.set_ceiling(CeilingStatus.MEASURED, 300)
    replaced = await hw.read_ceiling()
    assert replaced is not None and replaced.distance_mm == 300
    # 止める（古い状態を再現する）と以降返らない
    hw.freeze_ceiling()
    assert await hw.read_ceiling() is None


async def test_fake_mode_saves_settings_under_a_host_writable_dir(monkeypatch, aiohttp_client) -> None:
    """偽物のモードは既定の `/home/m5stack/...` ではなく書ける場所に保存し、PUT が 200 になる。"""
    captured: list[web.Application] = []
    monkeypatch.setattr(web, "run_app", lambda app, **kwargs: captured.append(app))
    assert cli.main(["--fake", "--port", "0"]) == 0

    client = await aiohttp_client(captured[0])
    good = {
        "lift_up": {"min": 10, "max": 20, "init": 15},
        "lift_down": {"min": 10, "max": 60, "init": 30},
        "pitch": {"min": 1, "max": 30, "init": 10},
        "yaw": {"min": 2, "max": 30, "init": 10},
    }
    resp = await client.put("/api/settings", json=good)
    assert resp.status == 200, await resp.text()
