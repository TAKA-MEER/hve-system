"""`python3 -m hve_video` の入口の試験。"""

from __future__ import annotations

from aiohttp import web

from hve_video import __main__ as video_main


def test_main_runs_the_app_with_aiohttp_3_6_run_app_and_releases_the_source(monkeypatch):
    """**aiohttp 3.6.2（UnitV2）の `run_app` は `on_shutdown` 引数を持たない。**

    引数は `app` と `port` だけで呼び、止めるときの解放はアプリの `on_shutdown` に登録する。
    """
    captured = []

    def run_app_3_6(app, *, port=8080):  # 3.6.2 の署名（on_shutdown 引数なし）
        captured.append((app, port))

    monkeypatch.setattr(web, "run_app", run_app_3_6)
    assert video_main.main(["--fake", "--port", "18080"]) == 0
    app, port = captured[0]
    assert port == 18080
    pipeline = app["pipeline"]
    assert pipeline.next_frame() is not None
    import asyncio

    asyncio.new_event_loop().run_until_complete(app.on_shutdown[0](app))
    assert pipeline.next_frame() is None  # 解放されて取り込みが止まる
