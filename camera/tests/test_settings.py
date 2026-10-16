"""設定の検証・読み込み・保存の試験。"""

import copy
import json
import os
import stat

import pytest

from hve_camera import settings as settings_mod
from hve_camera.params import load_params
from hve_camera.settings import load_settings, save_settings, validate_settings

PARAMS = load_params()
ABS_MAX_DPS = PARAMS["axis_speed_abs_max_dps"]

# protocol §3 の例の値。実装の定数ではなく仕様どおりの値をここに固定する。
# カメラモジュールのファイルが持つのは 4 軸だけ（`motor_max_pct` の持ち主は昇降部）。
PROTOCOL_DEFAULTS = {
    "lift_up": {"min": 10, "max": 60, "init": 30},
    "lift_down": {"min": 10, "max": 60, "init": 30},
    "pitch": {"min": 1, "max": 30, "init": 10},
    "yaw": {"min": 1, "max": 30, "init": 10},
}

# `PUT /api/settings` の本文（4 軸＋`motor_max_pct`。`PUT` では必須）。
FULL_DEFAULTS = {
    **PROTOCOL_DEFAULTS,
    "motor_max_pct": 50,
}

# 自分のファイルが持つ 2 軸（`motor_max_pct` の持ち主は昇降部なので書かない）。
OWN_DEFAULTS = {
    "pitch": {"min": 1, "max": 30, "init": 10},
    "yaw": {"min": 1, "max": 30, "init": 10},
}


def variant(**changes):
    """既定の設定を壊した設定を作る（`PUT` の本文の形。`motor_max_pct` つき）。"""
    data = copy.deepcopy(FULL_DEFAULTS)
    for axis, values in changes.items():
        data[axis] = values
    return data


def with_field(axis, field, value):
    data = copy.deepcopy(FULL_DEFAULTS)
    data[axis][field] = value
    return data


@pytest.fixture
def settings_path(tmp_path):
    return tmp_path / "settings.json"


# --- 検証 ----------------------------------------------------------------------------------


def test_the_protocol_example_settings_pass():
    assert validate_settings(FULL_DEFAULTS, PARAMS) == []


def test_the_defaults_in_the_code_are_the_protocol_examples():
    """実装の既定値が protocol §3 の例の値と食い違わない。"""
    assert settings_mod._DEFAULT_SETTINGS == PROTOCOL_DEFAULTS


@pytest.mark.parametrize("axis", ["lift_up", "lift_down", "pitch", "yaw"])
def test_every_axis_is_required(axis):
    data = copy.deepcopy(FULL_DEFAULTS)
    del data[axis]
    errors = validate_settings(data, PARAMS)
    assert len(errors) == 1
    assert axis in errors[0]


@pytest.mark.parametrize("axis", ["lift_up", "lift_down", "pitch", "yaw"])
@pytest.mark.parametrize("field", ["min", "max", "init"])
def test_every_field_is_required(axis, field):
    data = copy.deepcopy(FULL_DEFAULTS)
    del data[axis][field]
    errors = validate_settings(data, PARAMS)
    assert len(errors) == 1
    assert f"{axis}.{field}" in errors[0]


@pytest.mark.parametrize("axis", ["lift_up", "lift_down", "pitch", "yaw"])
def test_an_axis_that_is_not_an_object_is_reported(axis):
    errors = validate_settings(variant(**{axis: 30}), PARAMS)
    assert len(errors) == 1
    assert axis in errors[0]


def test_settings_that_are_not_an_object_are_reported():
    assert len(validate_settings([1, 2, 3], PARAMS)) == 1


@pytest.mark.parametrize("bad", ["10", None, True, [10], {"min": 1}])
def test_non_numeric_values_are_reported(bad):
    errors = validate_settings(with_field("pitch", "init", bad), PARAMS)
    assert len(errors) == 1
    assert "pitch.init" in errors[0]


# --- 検証: min ≦ init ≦ max（変異 4 の芯）------------------------------------------------------


@pytest.mark.parametrize("axis", ["lift_up", "lift_down", "pitch", "yaw"])
def test_min_greater_than_init_is_reported(axis):
    errors = validate_settings(variant(**{axis: {"min": 50, "max": 60, "init": 40}}), PARAMS)
    assert len(errors) == 1
    assert axis in errors[0]


@pytest.mark.parametrize("axis", ["lift_up", "lift_down", "pitch", "yaw"])
def test_init_greater_than_max_is_reported(axis):
    errors = validate_settings(variant(**{axis: {"min": 10, "max": 40, "init": 50}}), PARAMS)
    assert len(errors) == 1
    assert axis in errors[0]


@pytest.mark.parametrize("axis", ["lift_up", "lift_down", "pitch", "yaw"])
def test_the_order_check_is_not_only_about_adjacent_pairs(axis):
    """`min` が `max` を越えていても、`init` を挟んでいても落とす。"""
    errors = validate_settings(variant(**{axis: {"min": 90, "max": 20, "init": 50}}), PARAMS)
    assert errors


@pytest.mark.parametrize("axis", ["lift_up", "lift_down", "pitch", "yaw"])
def test_min_equal_to_init_equal_to_max_passes(axis):
    low = 0 if axis.startswith("lift") else 1
    assert validate_settings(variant(**{axis: {"min": low, "max": low, "init": low}}), PARAMS) == []


def test_several_problems_are_all_listed():
    data = variant(
        lift_up={"min": 10, "max": 200, "init": 30},
        pitch={"min": 0, "max": ABS_MAX_DPS + 10, "init": 0},
    )
    errors = validate_settings(data, PARAMS)
    assert len(errors) >= 3
    joined = " ".join(errors)
    for name in ("lift_up.max", "pitch.min", "pitch.max"):
        assert name in joined


# --- 検証: 絶対範囲 ---------------------------------------------------------------------------


@pytest.mark.parametrize("axis", ["lift_up", "lift_down"])
def test_lift_speed_0_to_100_is_inclusive(axis):
    assert validate_settings(variant(**{axis: {"min": 0, "max": 100, "init": 0}}), PARAMS) == []


@pytest.mark.parametrize("axis", ["lift_up", "lift_down"])
def test_lift_speed_over_100_is_reported(axis):
    errors = validate_settings(variant(**{axis: {"min": 10, "max": 101, "init": 30}}), PARAMS)
    assert len(errors) == 1
    assert f"{axis}.max" in errors[0]


@pytest.mark.parametrize("axis", ["lift_up", "lift_down"])
def test_lift_speed_below_0_is_reported(axis):
    errors = validate_settings(variant(**{axis: {"min": -1, "max": 60, "init": 30}}), PARAMS)
    assert len(errors) == 1
    assert f"{axis}.min" in errors[0]


@pytest.mark.parametrize("axis", ["lift_up", "lift_down"])
@pytest.mark.parametrize("field,value", [("min", 10.5), ("max", 60.5), ("init", 30.5)])
def test_lift_speed_must_be_an_integer(axis, field, value):
    """昇降の速さは mm/s の整数（protocol §3）。小数は落とす（順序は保った値で）。"""
    errors = validate_settings(with_field(axis, field, value), PARAMS)
    assert len(errors) == 1
    assert f"{axis}.{field}" in errors[0]


@pytest.mark.parametrize("axis", ["pitch", "yaw"])
def test_axis_speed_may_be_fractional(axis):
    """ピッチ・ヨーは小数を許す（昇降だけが整数）。"""
    data = variant(**{axis: {"min": 0.5, "max": 30, "init": 10}})
    assert validate_settings(data, PARAMS) == []


@pytest.mark.parametrize("axis", ["pitch", "yaw"])
def test_axis_speed_exactly_at_the_absolute_max_passes(axis):
    limit = ABS_MAX_DPS
    data = variant(**{axis: {"min": 1, "max": limit, "init": limit}})
    assert validate_settings(data, PARAMS) == []


@pytest.mark.parametrize("axis", ["pitch", "yaw"])
def test_axis_speed_over_the_absolute_max_is_reported(axis):
    limit = ABS_MAX_DPS + 1
    errors = validate_settings(variant(**{axis: {"min": 1, "max": limit, "init": 1}}), PARAMS)
    assert len(errors) == 1
    assert f"{axis}.max" in errors[0]


@pytest.mark.parametrize("axis", ["pitch", "yaw"])
def test_zero_speed_is_reported_for_pitch_and_yaw(axis):
    errors = validate_settings(variant(**{axis: {"min": 0, "max": 30, "init": 10}}), PARAMS)
    assert len(errors) == 1
    assert f"{axis}.min" in errors[0]


@pytest.mark.parametrize("axis", ["pitch", "yaw"])
def test_negative_speed_is_reported_for_pitch_and_yaw(axis):
    errors = validate_settings(variant(**{axis: {"min": -5, "max": 30, "init": 10}}), PARAMS)
    assert len(errors) == 1
    assert f"{axis}.min" in errors[0]


# --- 検証: モータへの出力の上限（spec Spec-ui §2.1） ----------------------------------------------


@pytest.mark.parametrize("value", [10, 50, 100])
def test_motor_max_pct_at_the_edges_passes(value):
    data = copy.deepcopy(FULL_DEFAULTS)
    data["motor_max_pct"] = value
    assert validate_settings(data, PARAMS) == []


@pytest.mark.parametrize("value", [9, 101, 50.5, True, "50", None])
def test_motor_max_pct_outside_10_to_100_or_not_an_integer_is_reported(value):
    """9・101・小数・真偽値・文字列・欠けは通さない（`PUT` では必須）。"""
    data = copy.deepcopy(FULL_DEFAULTS)
    data["motor_max_pct"] = value
    errors = validate_settings(data, PARAMS)
    assert len(errors) == 1
    assert "motor_max_pct" in errors[0]


def test_motor_max_pct_is_required_in_put():
    data = copy.deepcopy(FULL_DEFAULTS)
    del data["motor_max_pct"]
    errors = validate_settings(data, PARAMS)
    assert len(errors) == 1
    assert "motor_max_pct" in errors[0]


def test_motor_max_pct_is_not_checked_for_the_own_axes():
    """自分の保存（`axes=OWN_AXES`）には含めない（持ち主は昇降部）。"""
    own = {axis: copy.deepcopy(PROTOCOL_DEFAULTS[axis]) for axis in ("pitch", "yaw")}
    assert validate_settings(own, PARAMS, axes=("pitch", "yaw")) == []


def test_save_and_load_with_the_own_axes_do_not_keep_the_motor(settings_path):
    """自分の 2 軸だけを保存する（`motor_max_pct` はファイルに書かない）。"""
    own = {axis: copy.deepcopy(PROTOCOL_DEFAULTS[axis]) for axis in ("pitch", "yaw")}
    save_settings(own, settings_path, PARAMS, axes=("pitch", "yaw"))
    text = settings_path.read_text(encoding="utf-8")
    assert "motor_max_pct" not in text
    data, is_default = load_settings(settings_path, PARAMS, axes=("pitch", "yaw"))
    assert is_default is False
    assert data == own


# --- 読み込み ---------------------------------------------------------------------------------


def test_load_returns_the_defaults_when_the_file_is_missing(settings_path):
    data, is_default = load_settings(settings_path, PARAMS)
    assert is_default is True
    assert data == PROTOCOL_DEFAULTS


def test_load_returns_the_defaults_when_the_file_is_broken(settings_path):
    settings_path.write_text("{これは JSON ではない", encoding="utf-8")
    data, is_default = load_settings(settings_path, PARAMS)
    assert is_default is True
    assert data == PROTOCOL_DEFAULTS


def test_load_returns_the_defaults_when_the_file_is_empty(settings_path):
    settings_path.write_text("", encoding="utf-8")
    data, is_default = load_settings(settings_path, PARAMS)
    assert is_default is True
    assert data == PROTOCOL_DEFAULTS


def test_load_returns_the_defaults_when_the_file_is_not_an_object(settings_path):
    settings_path.write_text("[1, 2, 3]", encoding="utf-8")
    data, is_default = load_settings(settings_path, PARAMS)
    assert is_default is True
    assert data == PROTOCOL_DEFAULTS


def test_load_returns_the_defaults_when_the_contents_do_not_validate(settings_path):
    settings_path.write_text(
        json.dumps(variant(pitch={"min": 50, "max": 10, "init": 30})), encoding="utf-8"
    )
    data, is_default = load_settings(settings_path, PARAMS)
    assert is_default is True
    assert data == PROTOCOL_DEFAULTS


def test_load_reads_a_valid_file(settings_path):
    settings_path.write_text(json.dumps(FULL_DEFAULTS), encoding="utf-8")
    data, is_default = load_settings(settings_path, PARAMS)
    assert is_default is False
    assert data == PROTOCOL_DEFAULTS


def test_load_uses_the_settings_path_parameter_when_no_path_is_given(tmp_path, monkeypatch):
    params = dict(PARAMS)
    params["settings_path"] = str(tmp_path / "from-params.json")
    save_settings(OWN_DEFAULTS, params=params, axes=("pitch", "yaw"))
    data, is_default = load_settings(params=params, axes=("pitch", "yaw"))
    assert is_default is False
    assert data == OWN_DEFAULTS


def test_load_does_not_hand_out_the_defaults_by_reference(settings_path):
    data, _ = load_settings(settings_path, PARAMS)
    data["lift_up"]["init"] = 999
    again, _ = load_settings(settings_path, PARAMS)
    assert again["lift_up"]["init"] == 30


# --- 保存 -------------------------------------------------------------------------------------


def test_save_then_load_round_trips(settings_path):
    save_settings(OWN_DEFAULTS, settings_path, PARAMS, axes=("pitch", "yaw"))
    data, is_default = load_settings(settings_path, PARAMS, axes=("pitch", "yaw"))
    assert data == OWN_DEFAULTS
    assert is_default is False


def test_save_creates_the_parent_directory(tmp_path):
    target = tmp_path / "not" / "yet" / "settings.json"
    save_settings(OWN_DEFAULTS, target, PARAMS, axes=("pitch", "yaw"))
    assert target.is_file()


def test_save_leaves_no_temporary_file_behind(settings_path):
    save_settings(OWN_DEFAULTS, settings_path, PARAMS, axes=("pitch", "yaw"))
    assert [p.name for p in settings_path.parent.iterdir()] == [settings_path.name]


def test_save_does_not_change_the_file_when_validation_fails(settings_path):
    save_settings(OWN_DEFAULTS, settings_path, PARAMS, axes=("pitch", "yaw"))
    before = settings_path.read_text(encoding="utf-8")

    with pytest.raises(ValueError) as raised:
        save_settings(variant(pitch={"min": 50, "max": 10, "init": 30}), settings_path, PARAMS)

    assert "pitch" in str(raised.value)
    assert settings_path.read_text(encoding="utf-8") == before
    assert [p.name for p in settings_path.parent.iterdir()] == [settings_path.name]


def test_save_keeps_the_original_when_the_replace_fails(settings_path, monkeypatch):
    """書き込みの最後の差し替えで失敗しても、元のファイルは残る。"""
    save_settings(OWN_DEFAULTS, settings_path, PARAMS, axes=("pitch", "yaw"))
    before = settings_path.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("差し替えに失敗した")

    monkeypatch.setattr(settings_mod.os, "replace", boom)

    with pytest.raises(OSError):
        save_settings(variant(lift_up={"min": 20, "max": 70, "init": 40}), settings_path, PARAMS)

    assert settings_path.read_text(encoding="utf-8") == before
    assert [p.name for p in settings_path.parent.iterdir()] == [settings_path.name]


def test_save_keeps_the_original_when_the_directory_is_read_only(settings_path):
    """一時ファイルを作れないとき、元ファイルは書き換えられない。"""
    if os.geteuid() == 0:
        pytest.skip("root では書き込み権限を落とす意味が無い")

    save_settings(OWN_DEFAULTS, settings_path, PARAMS, axes=("pitch", "yaw"))
    before = settings_path.read_text(encoding="utf-8")
    parent = settings_path.parent
    parent.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(OSError):
            save_settings(variant(lift_up={"min": 20, "max": 70, "init": 40}), settings_path, PARAMS)
    finally:
        parent.chmod(stat.S_IRWXU)

    assert settings_path.read_text(encoding="utf-8") == before
    assert [p.name for p in parent.iterdir()] == [settings_path.name]


def test_save_writes_readable_utf8_json(settings_path):
    save_settings(OWN_DEFAULTS, settings_path, PARAMS, axes=("pitch", "yaw"))
    text = settings_path.read_text(encoding="utf-8")
    assert json.loads(text) == OWN_DEFAULTS
    assert text.endswith("\n")


def test_save_drops_the_motor_even_when_it_is_given(settings_path):
    """`motor_max_pct` はカメラモジュールのファイルに書かない（持ち主は昇降部）。"""
    save_settings(FULL_DEFAULTS, settings_path, PARAMS)
    text = settings_path.read_text(encoding="utf-8")
    assert "motor_max_pct" not in text
    assert json.loads(text) == PROTOCOL_DEFAULTS


def test_save_expands_a_tilde_in_the_settings_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    params = dict(PARAMS)
    params["settings_path"] = "~/hve_data/settings.json"
    save_settings(OWN_DEFAULTS, params=params, axes=("pitch", "yaw"))
    assert (tmp_path / "hve_data" / "settings.json").is_file()
