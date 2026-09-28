"""`camera/config/params.toml` が DetailedDesign-names.md §5 のとおりかを確かめる。"""

from pathlib import Path

from hve_camera.params import load_params

# DetailedDesign-names.md §5 の「カメラ部」の行（名前 → 値）
EXPECTED = {
    "ceiling_margin_mm": 500,
    "ceiling_stale_ms": 600,
    "hold_timeout_ms": 400,
    "lift_cmd_period_ms": 100,
    "lift_state_timeout_ms": 600,
    "state_period_ms": 100,
    "axis_speed_abs_max_dps": 60,
    "yaw_steps_per_rev": 4096,
    # names §5 は 0x70 と書いてある。TOML は 16 進が書けるので params.toml もその表記。
    "srf02_i2c_addr": 0x70,
    "srf02_min_range_mm": 150,
    "srf02_max_range_mm": 6000,
    "srf02_ranging_wait_ms": 70,
    "pitch_min_deg": -45,
    "pitch_max_deg": 45,
    "zoom_max": 4,
    "zoom_step": 0.5,
    "settings_path": "~/hve_data/settings.json",
}

PARAMS_PATH = Path(__file__).resolve().parent.parent / "config" / "params.toml"


def test_load_params_reads_the_config_file():
    params = load_params(PARAMS_PATH)
    assert isinstance(params, dict)
    assert params


def test_load_params_default_path_is_the_config_file():
    assert load_params() == load_params(PARAMS_PATH)


def test_params_values_match_names_section_5():
    params = load_params()
    for name, expected in EXPECTED.items():
        assert params[name] == expected, name


def test_params_has_no_other_keys():
    assert set(load_params()) == set(EXPECTED)
