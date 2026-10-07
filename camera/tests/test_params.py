"""`camera/config/params.toml` が DetailedDesign-names.md §5 のとおりかを確かめる。"""

from pathlib import Path

from hve_camera.params import load_params

# DetailedDesign-names.md §5 の「カメラ部」の行（名前 → 値）
EXPECTED = {
    "hold_timeout_ms": 400,
    "lift_cmd_period_ms": 100,
    "lift_state_timeout_ms": 600,
    "state_period_ms": 100,
    "axis_speed_abs_max_dps": 60,
    "yaw_steps_per_rev": 4096,
    "srf02_min_range_mm": 150,
    "srf02_max_range_mm": 6000,
    "pitch_min_deg": -45,
    "pitch_max_deg": 45,
    "zoom_max": 4,
    "zoom_step": 0.5,
    "video_capture_width": 1280,
    "video_capture_height": 720,
    "video_capture_fps": 10,
    "video_device": "/dev/video0",
    "video_out_height": 480,
    "video_fps": 10,
    "video_jpeg_quality": 80,
    "settings_path": "/home/m5stack/hve_data/settings.json",
    "lift_host": "",
    "lift_mdns_name": "hve-lift",
    "lift_port": 80,
    "module_name": "hve-cam",
    "module_ceiling_sensor": True,
    "io_device": "/dev/ttyS1",
    "io_baud": 115200,
    "io_cmd_period_ms": 50,
    "io_lost_ms": 600,
    "ceiling_read_stale_ms": 600,
    "uno_clock_window": 20,
    "video_port": 8080,
    "provisional": [
        "hold_timeout_ms",
        "lift_cmd_period_ms",
        "lift_state_timeout_ms",
        "state_period_ms",
        "pitch_min_deg",
        "pitch_max_deg",
        "video_capture_fps",
        "video_out_height",
        "video_fps",
        "video_jpeg_quality",
        "io_cmd_period_ms",
        "io_lost_ms",
        "ceiling_read_stale_ms",
        "uno_clock_window",
    ],
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
