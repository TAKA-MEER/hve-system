"""デジタルズームの切り出し（DetailedDesign.md §4.3・spec Spec-ui.md §1.5）。"""

from __future__ import annotations


def crop_rect(
    capture_width: int,
    capture_height: int,
    zoom: float,
    zoom_max: float,
    zoom_step: float,
) -> tuple[int, int, int, int]:
    """取り込みの中央から倍率ぶんの矩形を切り出す位置と大きさを返す。

    倍率は 1〜`zoom_max` に丸め、`zoom_step` の倍数にそろえる。
    切り出す枠は**取り込みと同じ縦横比**（幅・高さとも 1/倍率）で、画像からはみ出さない。

    倍率の丸めは受け取る側（`hve_video`）でも行う。画面 → `hve_camera` → `hve_video` の
    2 経路で倍率が届くので、受け取った側でも同じ規則にそろえる。
    """
    if capture_width < 1 or capture_height < 1:
        raise ValueError(f"取り込みの大きさが 0 以下: {capture_width}x{capture_height}")
    if zoom_max < 1.0:
        raise ValueError(f"zoom_max は 1 以上: {zoom_max}")
    if zoom_step <= 0.0:
        raise ValueError(f"zoom_step は 0 より大きい: {zoom_step}")

    # 倍率を 1〜zoom_max に丸め、zoom_step の倍数にそろえる
    level = min(max(float(zoom), 1.0), float(zoom_max))
    level = round(level / zoom_step) * zoom_step
    # zoom_max が zoom_step の倍数でないときは、そろえた結果が上限を超えることがある
    level = min(max(level, 1.0), float(zoom_max))

    # 幅・高さは整数に丸める。縦横比をできるだけ保つので、高さは丸めた幅から決める
    width = max(1, min(capture_width, round(capture_width / level)))
    height = max(1, min(capture_height, round(width * capture_height / capture_width)))

    return (capture_width - width) // 2, (capture_height - height) // 2, width, height
