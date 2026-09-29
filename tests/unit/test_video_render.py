"""video/render.py 的纯计算部分：帧时间点、混音参数。不启动浏览器。"""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "video" / "render.py"


def _load():
    spec = importlib.util.spec_from_file_location("video_render", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


render = _load()


def test_frame_times_cover_range_at_fps():
    ts = render.frame_times(0, 2, 60)
    assert len(ts) == 120
    assert ts[0] == 0 and abs(ts[-1] - 119 / 60) < 1e-9
    part = render.frame_times(15, 22, 60)
    assert len(part) == 420 and abs(part[0] - 15) < 1e-9


def test_audio_mix_none_when_no_files(tmp_path):
    plan = {"music": [{"src": "missing.mp3", "at": 15, "offset": 0, "dur": 80}], "sfx": [{"at": 1, "src": "no.wav", "vol": 0.5}]}
    assert render.audio_mix_args(plan, tmp_path, 0, 122, tmp_path / "o.wav") is None


def test_audio_mix_shares_input_per_sfx_file_and_shifts_time(tmp_path):
    (tmp_path / "key.wav").write_bytes(b"x")
    (tmp_path / "m.mp3").write_bytes(b"x")
    plan = {
        "music": [{"src": "m.mp3", "at": 15, "offset": 2, "dur": 80, "fadeIn": 0.5, "fadeOut": 2, "vol": 0.9}],
        "sfx": [{"at": 1.0, "src": "key.wav", "vol": 0.3}, {"at": 1.05, "src": "key.wav", "vol": 0.3},
                {"at": 30, "src": "key.wav", "vol": 0.3}],
    }
    args = render.audio_mix_args(plan, tmp_path, 0.5, 20, tmp_path / "o.wav")
    assert args.count("-i") == 2                      # 音乐 1 个输入 + 同一个音效文件只输入 1 次
    graph = args[args.index("-filter_complex") + 1]
    assert "asplit=2" in graph                        # 区间外（30 秒）的音效不混入
    assert "adelay=500|500" in graph and "adelay=550|550" in graph   # 1.0 秒、1.05 秒平移到从 0.5 秒开始
    assert "adelay=14500|14500" in graph              # 音乐在片中 15 秒进入
    assert "amix=inputs=3" in graph and "atrim=duration=19.5000" in graph


def test_audio_mix_trims_music_started_before_range(tmp_path):
    (tmp_path / "m.mp3").write_bytes(b"x")
    plan = {"music": [{"src": "m.mp3", "at": 15, "offset": 0, "dur": 80, "fadeIn": 0.3}], "sfx": []}
    graph = render.audio_mix_args(plan, tmp_path, 20, 30, tmp_path / "o.wav")
    graph = graph[graph.index("-filter_complex") + 1]
    assert "atrim=start=5.0000:duration=75.0000" in graph and "adelay=0|0" in graph
    assert "afade=t=in" not in graph                  # 淡入已经在区间之前结束
