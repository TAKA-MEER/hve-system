"""設定の検証・読み込み・保存。

[DetailedDesign-protocol.md](../../docs/plan/detailed/DetailedDesign-protocol.md) §4。
画面の設定画面と設定 API が使う。検証の規則:

- 4 項目（`lift_up`・`lift_down`・`pitch`・`yaw`）すべてが揃う
- `min ≦ init ≦ max`
- `lift_*` は 0〜100（PWM デューティ比 [%]）・`pitch` / `yaw` は 0 超〜`axis_speed_abs_max_dps`

**ピッチ・ヨーはここで保存、昇降は昇降部へ中継する**（v2）。
そのため読み書きは軸を絞れる（`axes`）。`PUT /api/settings` の検証は 4 軸のまま。
保存は**一時ファイルに書いてから置き換える。**書き込み途中の電源断で壊れたファイルを
読ませないため（[DetailedDesign-protocol.md](../../docs/plan/detailed/DetailedDesign-protocol.md) §3）。
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from hve_camera.params import load_params

#: 速度を設定できる軸。protocol §3。
_SPEED_AXES = ("lift_up", "lift_down", "pitch", "yaw")

#: 1 つの軸が持つ値。
_SPEED_FIELDS = ("min", "max", "init")

#: `lift_*` の絶対範囲の上限。protocol §3「`lift_*` は 0〜100」。
_LIFT_DUTY_MAX_PCT = 100

#: 0 未満はだめ（下限が 0 を含める）軸。
_DUTY_AXES = ("lift_up", "lift_down")

#: 0 以下の速度はだめ（protocol §3「`pitch` / `yaw` は 0 超」）軸。
_POSITIVE_ONLY_AXES = ("pitch", "yaw")

#: ファイルが無い・壊れているときの既定値。protocol §3 の例の値。**仮**（names §5）。
_DEFAULT_SETTINGS: dict[str, dict[str, float]] = {
    "lift_up": {"min": 10, "max": 60, "init": 30},
    "lift_down": {"min": 10, "max": 60, "init": 30},
    "pitch": {"min": 1, "max": 30, "init": 10},
    "yaw": {"min": 1, "max": 30, "init": 10},
}


#: 自分で保存する軸（ピッチ・ヨー）。昇降の 2 軸は昇降部へ中継する（protocol §4）。
OWN_AXES = ("pitch", "yaw")

#: 昇降部へ中継する軸（昇降の 2 軸）。
LIFT_AXES = ("lift_up", "lift_down")


def validate_settings(
    data: Any,
    params: Mapping[str, Any] | None = None,
    *,
    axes: tuple = _SPEED_AXES,
) -> list[str]:
    """設定の検証。**通らなかった理由の一覧**を返す（空のリスト = 問題なし）。

    `axes` を絞るとその軸だけを見る（自分の 2 軸の保存用）。`PUT` の検証は 4 軸のまま。
    """
    if params is None:
        params = load_params()
    if not isinstance(data, dict):
        return [f"設定はオブジェクトでない（{type(data).__name__}）"]

    errors: list[str] = []
    for axis in axes:
        entry = data.get(axis)
        if not isinstance(entry, dict):
            errors.append(f"{axis}: min/max/init のオブジェクトが無い")
            continue

        values: dict[str, float] = {}
        for field in _SPEED_FIELDS:
            if field not in entry:
                errors.append(f"{axis}.{field}: 無い")
            elif not _is_number(entry[field]):
                errors.append(f"{axis}.{field}: 数値でない")
            else:
                values[field] = float(entry[field])
        if len(values) != len(_SPEED_FIELDS):
            continue

        _check_order(axis, values, errors)
        _check_range(axis, values, params, errors)

    return errors


def load_settings(
    path: str | os.PathLike[str] | None = None,
    params: Mapping[str, Any] | None = None,
    *,
    axes: tuple = _SPEED_AXES,
) -> tuple[dict[str, dict[str, float]], bool]:
    """設定を読み込んで `(設定, 既定値で動いているか)` を返す。

    ファイルが無い・壊れている・検証を通らないときは、既定値と「既定値で動いている」印。
    `axes` を絞るとその軸だけを返す（旧版の 4 軸ファイルが残っていても絞った分だけ）。
    """
    if params is None:
        params = load_params()
    target = _target_path(path, params)

    try:
        with target.open(encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, ValueError):  # ValueError に JSON の読み違い（json.JSONDecodeError）も入る
        return _default_settings(axes), True

    if validate_settings(data, params, axes=axes):
        return _default_settings(axes), True
    return {axis: data[axis] for axis in axes}, False


def save_settings(
    data: Any,
    path: str | os.PathLike[str] | None = None,
    params: Mapping[str, Any] | None = None,
    *,
    axes: tuple = _SPEED_AXES,
) -> None:
    """検証を通る設定だけを原子的に保存する。通らないものは**保存しない**。

    一時ファイルに書いてから `os.replace` で置き換える。途中で失敗しても元のファイルは残る。
    通らない設定は `ValueError`（メッセージに理由の一覧）。
    `axes` を絞るとその軸だけを検証・保存する（自分の 2 軸用）。
    """
    if params is None:
        params = load_params()
    errors = validate_settings(data, params, axes=axes)
    if errors:
        raise ValueError("; ".join(errors))

    target = _target_path(path, params)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({axis: data[axis] for axis in axes}, ensure_ascii=False, indent=2) + "\n"

    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            fp.write(payload)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        _unlink_quietly(tmp_name)
        raise


def _default_settings(axes: tuple = _SPEED_AXES) -> dict[str, dict[str, float]]:
    """既定値のコピーを返す（戻った辞書を書き換えても `_DEFAULT_SETTINGS` を壊さない）。"""
    return {axis: copy.deepcopy(_DEFAULT_SETTINGS[axis]) for axis in axes}


def _target_path(
    path: str | os.PathLike[str] | None,
    params: Mapping[str, Any],
) -> Path:
    """保存先。引数を優先し、無ければ `settings_path` を使う。試験は一時ディレクトリを渡す。"""
    raw = path if path is not None else params["settings_path"]
    return Path(raw).expanduser()


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_order(axis: str, values: Mapping[str, float], errors: list[str]) -> None:
    if values["min"] > values["init"]:
        errors.append(f"{axis}: min が init より大きい")
    if values["init"] > values["max"]:
        errors.append(f"{axis}: init が max より大きい")


def _check_range(
    axis: str,
    values: Mapping[str, float],
    params: Mapping[str, Any],
    errors: list[str],
) -> None:
    if axis in _DUTY_AXES:
        high = float(_LIFT_DUTY_MAX_PCT)
        low_inclusive = True
    elif axis in _POSITIVE_ONLY_AXES:
        high = float(params["axis_speed_abs_max_dps"])
        low_inclusive = False
    else:  # _SPEED_AXES を増やしたときにここを直すよう、黙って通さない
        raise ValueError(f"速度の規則が無い軸: {axis}")

    for field in _SPEED_FIELDS:
        value = values[field]
        if (value < 0) or (value == 0 and not low_inclusive):
            errors.append(f"{axis}.{field}: 下限を満たさない（{value:g}）")
        if value > high:
            errors.append(f"{axis}.{field}: {high:g} より大きい（{value:g}）")


def _unlink_quietly(name: str | os.PathLike[str]) -> None:
    try:
        os.unlink(name)
    except OSError:
        pass
