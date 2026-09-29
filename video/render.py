"""宣传片的预览与渲染（设计文档 7.3、7.4）。

用法（在 servicedeck 项目文件夹里运行）：
  uv run python video/render.py preview                 用 Chrome 打开预览页面（Ctrl+C 结束）
  uv run python video/render.py still 16.5 out.png      截取第 16.5 秒的画面
  uv run python video/render.py render                  渲染整片到 video/out/servicedeck-promo.mp4
  uv run python video/render.py render --from 0 --to 22 --scale 0.5 --out video/out/part.mp4   只渲染一段、半分辨率

截图用本机的 Google Chrome（Playwright 的 channel="chrome"），截图直接经管道送进 ffmpeg，不落盘。
"""

import argparse
import contextlib
import functools
import hashlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VIDEO_DIR = Path(__file__).resolve().parent
OUT_DIR = VIDEO_DIR / "out"
W, H = 1920, 1080
SAMPLE_RATE = 48000


# ---------- 本机临时网页服务（只提供 video/ 目录，只监听 127.0.0.1） ----------

class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@contextlib.contextmanager
def serve(directory: Path = VIDEO_DIR):
    handler = functools.partial(_QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


# ---------- 纯计算（有单元测试） ----------

def frame_times(start: float, end: float, fps: int) -> list[float]:
    """[start, end) 区间内每一帧的时间点。"""
    first = round(start * fps)
    last = round(end * fps)
    return [i / fps for i in range(first, last)]


def audio_mix_args(plan: dict, base: Path, start: float, end: float, out_wav: Path) -> list[str] | None:
    """根据音频计划生成 ffmpeg 混音参数；没有可用的音频文件时返回 None。

    plan：{"music": [{src, at, offset, dur, fadeIn, fadeOut, vol}], "sfx": [{at, src, vol}]}
    只混入 [start, end) 区间，时间整体平移到从 0 开始。文件不存在的片段跳过。
    """
    inputs: list[str] = []
    chains: list[str] = []
    labels: list[str] = []
    fmt = f"aresample={SAMPLE_RATE},aformat=channel_layouts=stereo"

    for clip in plan.get("music", []):
        path = base / clip["src"]
        if not path.exists():
            continue
        c_start, c_end = clip["at"], clip["at"] + clip["dur"]
        if c_end <= start or c_start >= end:
            continue
        skip = max(0.0, start - c_start)  # 片段开头被裁掉的部分
        idx = len(inputs) // 2
        inputs += ["-i", str(path)]
        fade_in, fade_out = clip.get("fadeIn", 0), clip.get("fadeOut", 0)
        parts = [fmt, f"atrim=start={clip.get('offset', 0) + skip:.4f}:duration={clip['dur'] - skip:.4f}",
                 "asetpts=PTS-STARTPTS", f"volume={clip.get('vol', 1):.3f}"]
        if fade_in and skip < fade_in:
            parts.append(f"afade=t=in:st={0:.4f}:d={fade_in - skip:.4f}")
        if fade_out:
            parts.append(f"afade=t=out:st={max(0.0, clip['dur'] - skip - fade_out):.4f}:d={fade_out:.4f}")
        delay = int(round(max(0.0, c_start - start) * 1000))
        parts.append(f"adelay={delay}|{delay}")
        label = f"m{idx}"
        chains.append(f"[{idx}:a]" + ",".join(parts) + f"[{label}]")
        labels.append(label)

    by_file: dict[str, list[dict]] = {}
    for e in plan.get("sfx", []):
        if e.get("src") and start <= e["at"] < end and (base / e["src"]).exists():
            by_file.setdefault(e["src"], []).append(e)
    for src, events in by_file.items():
        idx = len(inputs) // 2
        inputs += ["-i", str(base / src)]
        n = len(events)
        split_labels = [f"s{idx}_{k}" for k in range(n)]
        if n == 1:
            chains.append(f"[{idx}:a]{fmt}[{split_labels[0]}_in]")
            srcs = [f"{split_labels[0]}_in"]
        else:
            chains.append(f"[{idx}:a]{fmt},asplit={n}" + "".join(f"[{s}_in]" for s in split_labels))
            srcs = [f"{s}_in" for s in split_labels]
        for s_in, s, e in zip(srcs, split_labels, events):
            delay = int(round((e["at"] - start) * 1000))
            chains.append(f"[{s_in}]volume={e.get('vol', 0.5):.3f},adelay={delay}|{delay}[{s}]")
            labels.append(s)

    if not labels:
        return None
    duration = end - start
    mix = "".join(f"[{x}]" for x in labels) + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0," \
          f"apad,atrim=duration={duration:.4f},volume=-1.5dB,alimiter=limit=0.8:level=false[out]"  # 峰值留约 2 dB 余量，平台转码后不削波
    chains.append(mix)
    return ["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(chains),
            "-map", "[out]", "-ar", str(SAMPLE_RATE), str(out_wav)]


# ---------- 浏览器 ----------

def wait_ready(page, url: str, tries: int = 3) -> None:
    """打开页面并等到画面可以渲染（字体已加载）。偶发的页面卡住时重新加载，最多 tries 次。"""
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(f"页面错误：{e}"))
    for attempt in range(1, tries + 1):
        try:
            page.goto(url, timeout=15000)
            page.wait_for_function("typeof window.renderAt === 'function' && !!window.fontsReady", polling=100, timeout=15000)
            if not page.evaluate("window.fontsReady"):
                raise SystemExit("字体没有加载成功，先运行 video/make_fonts.py")
            return
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            errors.append(f"第 {attempt} 次打开失败：{str(e).splitlines()[0]}")
    raise SystemExit("页面打不开：\n" + "\n".join(errors))


@contextlib.contextmanager
def open_page(scale: float = 1.0, bg: str | None = None):
    from playwright.sync_api import sync_playwright

    with serve() as base, sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        try:
            page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=scale)
            page.url_for_render = f"{base}/index.html?render=1" + (f"&bg={bg}" if bg else "")
            wait_ready(page, page.url_for_render)
            yield page
        finally:
            browser.close()


def capture(page, t: float, fast: bool = False) -> bytes:
    """截取第 t 秒的画面。fast=True 用高质量 JPEG（成片渲染用：背景颗粒让 PNG 压缩很慢，JPEG 快 5 倍以上，
    编成视频后看不出差别）；默认 PNG 无损（截图检查、一致性比对用）。"""
    page.evaluate("t => window.renderAt(t)", t)
    if fast:
        return page.screenshot(type="jpeg", quality=96, animations="disabled", caret="initial")
    return page.screenshot(type="png", animations="disabled", caret="initial")


# ---------- 命令 ----------

def cmd_preview(args):
    with serve() as base:
        url = f"{base}/index.html"
        subprocess.run(["open", "-a", "Google Chrome", url], check=False)
        print(f"预览地址：{url}（按 Ctrl+C 结束）", flush=True)
        with contextlib.suppress(KeyboardInterrupt):
            while True:
                time.sleep(1)


def cmd_still(args):
    with open_page(args.scale, args.bg) as page:
        data = capture(page, args.t)
    Path(args.out).write_bytes(data)
    print(f"已保存 {args.out}（sha1 {hashlib.sha1(data).hexdigest()[:12]}）")


def cmd_plan(args):
    """导出音效事件表，供 audio.py 生成"配乐 + 音效"试听版。"""
    import json
    with open_page(0.25) as page:
        plan = page.evaluate("window.audioPlan()")
    out = VIDEO_DIR / "assets" / "audio" / "sfx-plan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan["sfx"], ensure_ascii=False, indent=0))
    print(f"音效事件 {len(plan['sfx'])} 个，片长 {plan['duration']} 秒 → {out}")


def diff_pixels(a: bytes, b: bytes, threshold: int = 24) -> int:
    """两张截图中明显不同的像素个数（用 ffmpeg 解码后逐像素比较）。"""
    import numpy as np

    def decode(png: bytes):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", "-", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                             input=png, capture_output=True, check=True).stdout
        return np.frombuffer(raw, dtype=np.uint8).astype(np.int16)

    x, y = decode(a), decode(b)
    if x.shape != y.shape:
        return max(len(x), len(y))
    return int(np.count_nonzero(np.abs(x - y) > threshold))


def cmd_check(args):
    """一致性检查：同一时间点"新开页面直接画"和"先按顺序播过前面再画"必须完全一样（设计文档 7.2）。"""
    times = [float(x) for x in args.times.split(",")]
    with open_page(0.5) as page:
        fresh = {}
        for t in times:
            wait_ready(page, page.url_for_render)
            fresh[t] = capture(page, t)
        wait_ready(page, page.url_for_render)
        bad = 0
        t = 0.0
        for target in sorted(times):
            while t < target - 0.5:          # 每 0.5 秒画一帧，模拟顺序播放
                page.evaluate("t => window.renderAt(t)", t)
                t += 0.5
            n = diff_pixels(capture(page, target), fresh[target])
            ok = n <= 50   # 半分辨率下约 0.01%：Chrome 自身的抗锯齿差异；残影之类的问题会有成千上万个像素
            bad += not ok
            print(f"{target:7.2f} 秒  {'一致' if ok else '不一致！'}（差异像素 {n}）")
    sys.exit(1 if bad else 0)


def cmd_cover(args):
    """B 站封面（16:10，1920×1200）→ video/out/cover.png"""
    from playwright.sync_api import sync_playwright

    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / "cover.png"
    with serve() as base, sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1920, "height": 1200})
        page.goto(f"{base}/cover.html")
        page.wait_for_function("window.coverReady === true", polling=100, timeout=15000)
        page.locator("#cover").screenshot(path=str(out))
        browser.close()
    print(f"已输出 {out}")


def cmd_remux(args):
    """只重新混音并替换成片的音轨（画面不重新渲染）。"""
    src = Path(args.video)
    with open_page(0.25) as page:
        plan = page.evaluate("window.audioPlan()")
    with tempfile.TemporaryDirectory(prefix="sdaud") as tmp:
        wav = Path(tmp) / "mix.wav"
        mix = audio_mix_args(plan, VIDEO_DIR, 0.0, plan["duration"], wav)
        if not mix:
            sys.exit("没有可用的音频文件")
        subprocess.run(mix, check=True)
        out = src.with_name(src.stem + ".remux.mp4")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-i", str(wav), "-map", "0:v", "-map", "1:a",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-shortest", "-movflags", "+faststart", str(out)], check=True)
        out.replace(src)
    print(f"已替换音轨：{src}")


def cmd_render(args):
    if not shutil.which("ffmpeg"):
        sys.exit("找不到 ffmpeg")
    OUT_DIR.mkdir(exist_ok=True)
    out = Path(args.out)
    with open_page(args.scale) as page:
        plan = page.evaluate("window.audioPlan()")
        end = plan["duration"] if args.to is None else min(args.to, plan["duration"])
        fps = plan["fps"]
        times = frame_times(args.start, end, fps)
        with tempfile.TemporaryDirectory(prefix="sdvid") as tmp:
            silent = Path(tmp) / "silent.mp4"
            enc = subprocess.Popen(
                ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg",
                 "-i", "-", "-c:v", "libx264", "-preset", args.preset, "-crf", str(args.crf), "-pix_fmt", "yuv420p",
                 "-movflags", "+faststart", str(silent)],
                stdin=subprocess.PIPE)
            began = time.time()
            for i, t in enumerate(times):
                enc.stdin.write(capture(page, t, fast=True))
                if i % 60 == 0 or i == len(times) - 1:
                    done = i + 1
                    eta = (time.time() - began) / done * (len(times) - done)
                    print(f"第 {done}/{len(times)} 帧 · 视频第 {t:6.2f} 秒 · 预计还需 {eta / 60:4.1f} 分钟", flush=True)
            enc.stdin.close()
            if enc.wait() != 0:
                sys.exit("\nffmpeg 编码失败")
            print()
            wav = Path(tmp) / "mix.wav"
            mix = audio_mix_args(plan, VIDEO_DIR, args.start, end, wav)
            if mix and subprocess.run(mix).returncode == 0:
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent), "-i", str(wav), "-c:v", "copy",
                                "-c:a", "aac", "-b:a", "256k", "-shortest", str(out)], check=True)
            else:
                print("没有可用的音频文件，输出无声视频")
                shutil.move(str(silent), out)
    print(f"已输出 {out}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="ServiceDeck 宣传片预览与渲染")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("preview", help="用 Chrome 打开预览页面")
    sub.add_parser("plan", help="导出音效事件表（给 audio.py 做试听版）")
    sub.add_parser("cover", help="生成 B 站封面 video/out/cover.png")
    p = sub.add_parser("remux", help="只重新混音、替换成片音轨（画面不重渲染）")
    p.add_argument("--video", default=str(OUT_DIR / "servicedeck-promo.mp4"))
    p = sub.add_parser("check", help="检查顺序播放与直接跳转画出的画面是否一致")
    p.add_argument("--times", default="10,20,30,44,60,78,92,110")
    p = sub.add_parser("still", help="截取某一秒的画面")
    p.add_argument("t", type=float)
    p.add_argument("out")
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--bg", default=None, help="背景样式对比：mac / grid")
    p = sub.add_parser("render", help="渲染视频")
    p.add_argument("--from", dest="start", type=float, default=0.0)
    p.add_argument("--to", type=float, default=None)
    p.add_argument("--scale", type=float, default=1.0, help="分辨率倍数，0.5 为 960×540 快速试渲")
    p.add_argument("--crf", type=int, default=16)
    p.add_argument("--preset", default="slow")
    p.add_argument("--out", default=str(OUT_DIR / "servicedeck-promo.mp4"))
    args = parser.parse_args(argv)
    {"preview": cmd_preview, "plan": cmd_plan, "cover": cmd_cover, "remux": cmd_remux, "check": cmd_check, "still": cmd_still, "render": cmd_render}[args.cmd](args)


if __name__ == "__main__":
    main()
