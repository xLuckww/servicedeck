"""用代码合成宣传片的全部音乐和音效，不使用任何外部音频素材（负责人 2026-09-29 决定）。

用法：uv run python video/audio.py
输出（不提交，随时可重新生成）：
  video/assets/audio/music.wav        与画面等长的配乐（0–15 秒无音乐）
  video/assets/audio/sfx/<名字>.wav   各个音效
  video/assets/audio/soundtrack-preview.wav   配乐 + 全部音效按时间轴混好的试听版（可选 --preview）

节拍与画面共用一套参数：120 BPM，一小节 2 秒，第一拍在片中第 15 秒（与 timeline.js 的 MUSIC 一致）。
全部是加法合成（若干正弦分音叠加 + 包络）和噪声整形，只依赖 numpy。
"""

import argparse
import json
import subprocess
import wave
from pathlib import Path

import numpy as np

VIDEO = Path(__file__).resolve().parent
OUT = VIDEO / "assets" / "audio"
SR = 48000
BPM = 120
BEAT = 60 / BPM
BAR = BEAT * 4
T0 = 15.0            # 电子乐第一拍（片中秒数）
PIANO_AT = 99.0      # 钢琴段开始
DURATION = 123.0

RNG = np.random.default_rng(20260929)  # 固定种子：每次生成的声音完全一样


# ================= 基础工具 =================

def secs(d: float) -> np.ndarray:
    return np.arange(int(round(d * SR))) / SR


def hz(note: str) -> float:
    """音名 → 频率，如 'A4' = 440，'C#3'。"""
    names = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
    n = names[note[0]]
    rest = note[1:]
    if rest.startswith("#"):
        n += 1; rest = rest[1:]
    elif rest.startswith("b"):
        n -= 1; rest = rest[1:]
    midi = 12 * (int(rest) + 1) + n
    return 440.0 * 2 ** ((midi - 69) / 12)


def adsr(n: int, a: float, d: float, s: float, r: float) -> np.ndarray:
    """长度 n 的包络；最后 r 秒为释放。"""
    t = np.arange(n) / SR
    total = n / SR
    env = np.where(t < a, t / max(a, 1e-6), s + (1 - s) * np.exp(-(t - a) / max(d, 1e-6)))
    rel = np.clip((total - t) / max(r, 1e-6), 0, 1)
    return env * rel


def fft_band(sig: np.ndarray, lo: float, hi: float, soft: float = 0.3) -> np.ndarray:
    """用频域遮罩做带通（短信号用）。soft 为边缘过渡宽度（倍频程比例）。"""
    n = len(sig)
    spec = np.fft.rfft(sig)
    f = np.fft.rfftfreq(n, 1 / SR)
    lo_m = np.clip((f - lo * (1 - soft)) / (lo * soft + 1e-9), 0, 1) if lo > 0 else np.ones_like(f)
    hi_m = np.clip((hi * (1 + soft) - f) / (hi * soft + 1e-9), 0, 1) if hi < SR / 2 else np.ones_like(f)
    return np.fft.irfft(spec * lo_m * hi_m, n)


def partials(freq: float, t: np.ndarray, amps, decays=None, detune: float = 0.0, inharm: float = 0.0) -> np.ndarray:
    """加法合成：第 k 分音频率 k·f（可带非谐性），振幅 amps[k-1]，各自按 decays 指数衰减。"""
    out = np.zeros_like(t)
    for k, a in enumerate(amps, start=1):
        fk = k * freq * np.sqrt(1 + inharm * k * k) * (1 + detune)
        if fk >= SR / 2 * 0.95:
            break
        part = a * np.sin(2 * np.pi * fk * t + RNG.uniform(0, 2 * np.pi))
        if decays is not None:
            part *= np.exp(-t * decays[k - 1])
        out += part
    return out


class Track:
    """立体声缓冲区，按秒数放入声音。"""

    def __init__(self, duration: float):
        self.buf = np.zeros((2, int(round(duration * SR))))

    def add(self, at: float, sig: np.ndarray, gain: float = 1.0, pan: float = 0.0):
        i = int(round(at * SR))
        if i >= self.buf.shape[1] or i + len(sig) <= 0:
            return
        sig = sig * gain
        if i < 0:
            sig, i = sig[-i:], 0
        n = min(len(sig), self.buf.shape[1] - i)
        l, r = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
        self.buf[0, i:i + n] += sig[:n] * l * np.sqrt(2)
        self.buf[1, i:i + n] += sig[:n] * r * np.sqrt(2)


def reverb(buf: np.ndarray, length: float = 2.2, wet: float = 0.22) -> np.ndarray:
    """用合成的冲激响应（指数衰减噪声，左右声道不同）做卷积混响。"""
    t = secs(length)
    n = buf.shape[1] + len(t)
    size = 1 << (n - 1).bit_length()
    out = np.empty_like(buf)
    for ch in range(2):
        ir = RNG.standard_normal(len(t)) * np.exp(-t * 6.9 / length)
        ir = fft_band(ir, 200, 7000)
        ir /= np.sqrt(np.sum(ir ** 2))
        conv = np.fft.irfft(np.fft.rfft(buf[ch], size) * np.fft.rfft(ir, size), size)[:buf.shape[1]]
        out[ch] = buf[ch] + wet * conv
    return out


def write_wav(path: Path, buf: np.ndarray, peak: float = 0.89):
    """buf 形状 (2, n) 或 (n,)；归一化到 peak 后写 16 位 WAV。"""
    if buf.ndim == 1:
        buf = np.stack([buf, buf])
    m = np.max(np.abs(buf)) or 1.0
    data = (np.clip(buf / m * peak, -1, 1) * 32767).astype("<i2").T.copy()
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())


# ================= 乐器 =================

def pad(freq: float, dur: float, bright: float = 6.0) -> np.ndarray:
    """柔和的合成器铺底：三个略微失谐的锯齿波（加法合成），缓起缓收。"""
    t = secs(dur)
    amps = [np.exp(-k / bright) / k for k in range(1, 18)]
    sig = sum(partials(freq, t, amps, detune=d) for d in (-0.004, 0.0, 0.0045)) / 3
    return sig * adsr(len(t), 0.35, 1.0, 1.0, 0.5)


def pluck(freq: float, dur: float = 0.4) -> np.ndarray:
    """拨弦音色（琶音用）：高次分音衰减更快。"""
    t = secs(dur)
    amps = [1 / k for k in range(1, 11)]
    decays = [5 + k * 2.2 for k in range(1, 11)]
    return partials(freq, t, amps, decays) * adsr(len(t), 0.003, 1, 1, 0.05)


def bass(freq: float, dur: float) -> np.ndarray:
    t = secs(dur)
    amps = [1, 0.35, 0.18, 0.1, 0.05]
    return partials(freq, t, amps) * adsr(len(t), 0.006, 0.12, 0.6, 0.04)


def kick() -> np.ndarray:
    t = secs(0.45)
    f = 44 + 110 * np.exp(-t * 32)
    phase = 2 * np.pi * np.cumsum(f) / SR
    body = np.sin(phase) * np.exp(-t * 7)
    click = fft_band(RNG.standard_normal(len(t)), 1500, 6000) * np.exp(-t * 400) * 0.25
    return body + click


def clap() -> np.ndarray:
    t = secs(0.3)
    noise = fft_band(RNG.standard_normal(len(t)), 900, 4200)
    env = np.zeros_like(t)
    for off in (0.0, 0.011, 0.022):
        env += np.where(t >= off, np.exp(-(t - off) * (180 if off < 0.02 else 18)), 0)
    return noise * env * 0.5


def hat(open_: bool = False) -> np.ndarray:
    t = secs(0.35 if open_ else 0.08)
    noise = fft_band(RNG.standard_normal(len(t)), 7500, 16000)
    return noise * np.exp(-t * (9 if open_ else 55)) * 0.5


def riser(dur: float) -> np.ndarray:
    """段落转换前的上升音效：噪声渐强 + 上滑的正弦。"""
    t = secs(dur)
    p = t / dur
    noise = fft_band(RNG.standard_normal(len(t)), 2000, 12000) * p ** 2 * 0.35
    f = 300 + 1500 * p ** 2
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * p ** 3 * 0.12
    return (noise + tone) * np.clip((dur - t) / 0.02, 0, 1)


def impact() -> np.ndarray:
    t = secs(2.2)
    boom = np.sin(2 * np.pi * np.cumsum(38 + 60 * np.exp(-t * 8)) / SR) * np.exp(-t * 2.5)
    air = fft_band(RNG.standard_normal(len(t)), 3000, 15000) * np.exp(-t * 2.2) * 0.25
    return boom * 0.8 + air


def piano(freq: float, dur: float, vel: float = 1.0) -> np.ndarray:
    """钢琴近似：带非谐性的分音，高次衰减更快，低音衰减更慢；加一点击弦噪声。"""
    t = secs(dur + 0.6)
    k = np.arange(1, 13)
    amps = vel * (1 / k ** 1.15) * np.exp(-k * (0.08 + 0.1 * (1 - vel)))
    base = 0.55 + 0.9 * (freq / 440)
    decays = base * (0.6 + 0.45 * k)
    sig = partials(freq, t, amps, decays, inharm=0.00035)
    hammer = fft_band(RNG.standard_normal(len(t)), 800, 5000) * np.exp(-t * 120) * 0.04 * vel
    rel = np.clip((dur + 0.6 - t) / 0.6, 0, 1) ** 2
    return (sig + hammer) * adsr(len(t), 0.002, 1, 1, 0.01) * rel


# ================= 音效 =================

def sfx_key(variant: int) -> np.ndarray:
    t = secs(0.06)
    center = [3200, 4200, 2600][variant % 3]
    click = fft_band(RNG.standard_normal(len(t)), center * 0.6, center * 1.6) * np.exp(-t * 160)
    thump = np.sin(2 * np.pi * (150 + 30 * variant) * t) * np.exp(-t * 90) * 0.4
    return click * 0.6 + thump


def sfx_click() -> np.ndarray:
    t = secs(0.05)
    a = fft_band(RNG.standard_normal(len(t)), 1800, 6000) * np.exp(-t * 300)
    b = np.roll(a, int(0.009 * SR)) * 0.6
    b[: int(0.009 * SR)] = 0
    return a + b + np.sin(2 * np.pi * 900 * t) * np.exp(-t * 200) * 0.2


def sfx_ding(freq: float) -> np.ndarray:
    t = secs(1.2)
    sig = np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(2 * np.pi * freq * 2.76 * t) * np.exp(-t * 6) \
        + 0.1 * np.sin(2 * np.pi * freq * 5.4 * t) * np.exp(-t * 12)
    return sig * np.exp(-t * 3.2) * adsr(len(t), 0.003, 1, 1, 0.02)


def sfx_error() -> np.ndarray:
    out = []
    for f in (466.2, 349.2):
        t = secs(0.11)
        tone = partials(f, t, [1, 0, 0.3, 0, 0.12])
        out += [tone * adsr(len(t), 0.004, 1, 1, 0.02), np.zeros(int(0.03 * SR))]
    return np.concatenate(out) * 0.7


def sfx_whoosh() -> np.ndarray:
    dur = 0.6
    t = secs(dur)
    noise = RNG.standard_normal(len(t))
    out = np.zeros_like(t)
    chunks = 24
    size = len(t) // chunks
    win = np.hanning(size * 2)
    for c in range(chunks):
        center = 400 * 2 ** (c / chunks * 4)
        a = max(0, (c - 1) * size)
        seg = noise[a:a + size * 2]
        seg = fft_band(seg, center * 0.7, center * 1.4) * win[: len(seg)]
        out[a:a + len(seg)] += seg
    return out * np.sin(np.pi * t / dur) ** 2


def sfx_pop() -> np.ndarray:
    t = secs(0.12)
    f = 300 + 500 * np.exp(-t * 40)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 30)


DING_NOTES = {"ding": "E6", "ding1": "C6", "ding2": "D6", "ding3": "E6", "ding4": "G6", "ding5": "A6", "ding6": "C7"}


def make_sfx() -> dict[str, np.ndarray]:
    s = {f"key{i + 1}": sfx_key(i) for i in range(3)}
    s.update(click=sfx_click(), error=sfx_error(), whoosh=sfx_whoosh(), pop=sfx_pop())
    s.update({name: sfx_ding(hz(note)) for name, note in DING_NOTES.items()})
    return s


# ================= 编曲 =================

CHORDS = [  # 每小节一个和弦：C – G – Am – F
    {"root": "C2", "pad": ["C4", "E4", "G4"], "arp": ["C5", "E5", "G5", "C6"]},
    {"root": "G1", "pad": ["B3", "D4", "G4"], "arp": ["B4", "D5", "G5", "B5"]},
    {"root": "A1", "pad": ["A3", "C4", "E4"], "arp": ["A4", "C5", "E5", "A5"]},
    {"root": "F1", "pad": ["A3", "C4", "F4"], "arp": ["A4", "C5", "F5", "A5"]},
]
LEAD = ["E5", None, "G5", None, "A5", "G5", None, "E5", "D5", None, "E5", None, "C5", None, None, None]


def section(bar: int) -> dict:
    """每小节有哪些声部（小节从片中第 15 秒算起，0 起）。与画面段落对齐：
    0–3 出场 · 4–10 看得清 · 11–17 一键启动 · 18–27 自己能加 · 28–34 不漏网 · 35–41 放得下心。"""
    s = dict(pad=True, arp=False, kick=False, clap=False, hat=False, bass=False, lead=False, open_hat=False)
    if bar <= 1:
        return s
    if bar <= 3:
        return {**s, "arp": True}
    if bar >= 40:
        return {**s, "arp": True, "bass": bar == 40}
    s.update(arp=True, kick=True, clap=True, hat=True, bass=True)
    if 11 <= bar <= 17:
        s.update(lead=True, open_hat=True)
    if bar in (18, 19, 28, 29):        # 新段开头的轻一点的两小节
        s.update(clap=False, open_hat=False)
    if 35 <= bar <= 39:
        s.update(lead=True, open_hat=True)
    return s


def make_electro(track: Track):
    bars = int((PIANO_AT - T0) / BAR)  # 42 小节
    pad_bus = Track(DURATION)
    k, cl, oh = kick(), clap(), hat(True)
    for b in range(bars):
        at = T0 + b * BAR
        ch = CHORDS[b % 4]
        s = section(b)
        if s["pad"]:
            gain = 0.10 if b >= 2 else 0.10 * (0.35 + 0.65 * b / 2)
            for i, n in enumerate(ch["pad"]):
                pad_bus.add(at, pad(hz(n), BAR + 0.4), gain, pan=(i - 1) * 0.35)
        if s["arp"]:
            for i in range(16):
                n = ch["arp"][[0, 1, 2, 3, 2, 1, 2, 3][i % 8]]
                vel = 0.07 if i % 4 == 0 else 0.045
                if b >= 40:
                    vel *= max(0.0, 1 - ((b - 40) * 16 + i) / 32)
                track.add(at + i * BEAT / 4, pluck(hz(n)), vel, pan=0.4 * np.sin(i * 0.8))
        if s["kick"]:
            for i in range(4):
                track.add(at + i * BEAT, k, 0.5)
        if s["clap"]:
            for i in (1, 3):
                track.add(at + i * BEAT, cl, 0.22, pan=0.05)
        if s["hat"]:
            for i in range(8):
                if i % 2 == 1:
                    track.add(at + i * BEAT / 2, hat(), 0.14 if not s["open_hat"] else 0.1, pan=-0.3)
        if s["open_hat"]:
            for i in (1, 3, 5, 7):
                track.add(at + i * BEAT / 2, oh, 0.05, pan=0.3)
        if s["bass"]:
            for i in range(4):  # 反拍贝斯
                track.add(at + i * BEAT + BEAT / 2, bass(hz(ch["root"]) * 2, BEAT / 2 * 0.9), 0.3)
        if s["lead"]:
            for i, n in enumerate(LEAD[(b % 2) * 8:(b % 2) * 8 + 8]):
                if n:
                    track.add(at + i * BEAT / 2, pluck(hz(n), 0.6), 0.09, pan=-0.15)
    # 侧链压缩的"呼吸感"：底鼓响时铺底让一下
    t = np.arange(pad_bus.buf.shape[1]) / SR
    beat_pos = ((t - T0) % BEAT) / BEAT
    duck = np.where((t >= T0 + 4 * BAR) & (t < T0 + 40 * BAR), 0.45 + 0.55 * np.clip(beat_pos / 0.45, 0, 1) ** 1.5, 1.0)
    track.buf += pad_bus.buf * duck
    # 段落转换：上升音效 + 落地
    for b in (4, 11, 18, 28, 35):
        track.add(T0 + b * BAR - 1.5, riser(1.5), 0.5)
        track.add(T0 + b * BAR, impact(), 0.22)


def make_piano(track: Track):
    """钢琴段：快剪（99–103）分解和弦引入，数字段（103–115）每 3 秒一个和弦，反转（115–123）收尾。"""
    for i, n in enumerate(["C4", "G4", "E5", "G4", "C5", "G4", "E5", "C6"]):
        track.add(PIANO_AT + i * 0.45, piano(hz(n), 1.6, 0.55), 0.22, pan=-0.2 + 0.05 * i)
    track.add(PIANO_AT, piano(hz("C2"), 4.0, 0.6), 0.25)
    prog_ = [("C3", ["E4", "G4", "C5"], "E5"), ("B2", ["D4", "G4", "B4"], "D5"),
             ("A2", ["C4", "E4", "A4"], "C5"), ("F2", ["C4", "F4", "A4"], "A5")]
    for i, (root, chord, top) in enumerate(prog_):
        at = 103 + i * 3
        track.add(at, piano(hz(root), 3.2, 0.7), 0.3)
        for j, n in enumerate(chord):
            track.add(at + 0.02 * j, piano(hz(n), 3.0, 0.5), 0.2, pan=(j - 1) * 0.3)
        track.add(at + 1.5, piano(hz(top), 1.8, 0.45), 0.16, pan=0.2)
    # 反转：停半拍后挂留和弦 → 主和弦，长长的余音
    track.add(115.3, piano(hz("G2"), 3.0, 0.6), 0.28)
    for j, n in enumerate(["C4", "F4", "G4", "D5"]):
        track.add(115.3 + 0.03 * j, piano(hz(n), 2.8, 0.5), 0.18, pan=(j - 1.5) * 0.25)
    track.add(118.6, piano(hz("C2"), 4.4, 0.75), 0.3)
    for j, n in enumerate(["C4", "E4", "G4", "C5", "E5"]):
        track.add(118.6 + 0.04 * j, piano(hz(n), 4.2, 0.55), 0.17, pan=(j - 2) * 0.2)


def make_music() -> np.ndarray:
    electro, keys = Track(DURATION), Track(DURATION)
    make_electro(electro)
    make_piano(keys)
    buf = reverb(electro.buf, 1.6, 0.14) + 1.8 * reverb(keys.buf, 2.8, 0.35)  # 钢琴段整体提升约 5 dB，避免结尾突然变小声
    t = np.arange(buf.shape[1]) / SR
    buf *= np.clip((DURATION - t) / 1.0, 0, 1)   # 最后 1 秒收尾
    return np.tanh(buf * 1.2) / 1.2               # 轻微饱和，避免削波


def mix_preview(music: np.ndarray, sfx: dict[str, np.ndarray]) -> np.ndarray | None:
    """按 timeline.js 的音效事件表把音效混进配乐，供不看画面先试听整条声音。"""
    plan_file = OUT / "sfx-plan.json"
    if not plan_file.exists():
        return None
    track = Track(DURATION)
    track.buf += music / (np.max(np.abs(music)) or 1) * 0.8
    for e in json.loads(plan_file.read_text()):
        if e["name"] in sfx:
            s = sfx[e["name"]]
            track.add(e["at"], s / (np.max(np.abs(s)) or 1), e["vol"] * 0.9)
    return track.buf


def main():
    parser = argparse.ArgumentParser(description="合成宣传片的配乐和音效")
    parser.add_argument("--preview", action="store_true", help="另外生成配乐+音效的试听版（需要先渲染出 sfx-plan.json）")
    args = parser.parse_args()
    sfx = make_sfx()
    for name, sig in sfx.items():
        write_wav(OUT / "sfx" / f"{name}.wav", sig)
    print(f"音效 {len(sfx)} 个 → {OUT / 'sfx'}")
    music = make_music()
    write_wav(OUT / "music.wav", music, peak=0.8)
    print(f"配乐 {DURATION:.0f} 秒 → {OUT / 'music.wav'}")
    if args.preview:
        pv = mix_preview(music, sfx)
        if pv is None:
            print("没有 sfx-plan.json，跳过试听版")
        else:
            wav = OUT / "soundtrack-preview.wav"
            write_wav(wav, pv, peak=0.89)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-b:a", "192k", str(wav.with_suffix(".mp3"))])
            print(f"试听版 → {wav.with_suffix('.mp3')}")


if __name__ == "__main__":
    main()
