"""Boomerang and film from a movie file the camera recorded itself.

The live view a Canon sends over USB is about 960x640; a 9:16 crop of it is
360x640. With ``boomerang.source`` / ``video.source`` set to ``"camera"`` the
camera records a real movie onto its card (camera/edsdk.py), the booth
downloads it and turns it into the same frames the live-view path hands to
``boomerang.make_clip``. Everything after that (layouts, motion frames,
poster, print frames, gallery) is the code the live view uses.

Sizes are fixed, whatever the camera records (owner's decision 17.11.2026,
the camera runs at Full HD): portrait and square formats get a short side of
1080 (9:16 -> 1080x1920, 4:5 -> 1080x1350), landscape formats a long side of
1920 (16:9 -> 1920x1080). A Full HD source is scaled *up* for portrait crops
(608x1080 -> 1080x1920), with lanczos. The source frame rate is read from the
file (25p or 30p, or whatever the camera is set to); the film never gets
more frames a second than the file has.

Audio is never taken (``-an`` everywhere): the booth makes no sound films.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from .compose import parse_aspect

SHORT_SIDE = 1080
LONG_SIDE = 1920


def _even(v: float) -> int:
    n = int(round(v))
    return max(2, n - n % 2)


def target_size(aspect) -> Tuple[int, int]:
    """The film's size for a format: short side 1080 upright or square, long side 1920 across."""
    ratio = parse_aspect(aspect) if isinstance(aspect, str) else float(aspect)
    if ratio <= 1.0:
        return SHORT_SIDE, _even(SHORT_SIDE / ratio)
    return LONG_SIDE, _even(LONG_SIDE / ratio)


def rotated(size: Tuple[int, int], rotate: int) -> Tuple[int, int]:
    w, h = size
    return (h, w) if int(rotate) % 180 == 90 else (w, h)


def geometry(src: Tuple[int, int], box: Tuple[int, int], fill: bool) -> Tuple[int, int, int, int]:
    """``(crop_w, crop_h, out_w, out_h)`` for a source (already turned) and the film's box.

    ``fill``: the centre of the source in the box's shape, scaled to the box
    exactly (up or down). Otherwise the whole source scaled to fit inside the
    box (the ``blur`` layout puts it on its blurred backdrop).
    """
    sw, sh = int(src[0]), int(src[1])
    bw, bh = int(box[0]), int(box[1])
    if fill:
        ratio = bw / bh
        if sw / sh > ratio:
            cw, ch = sh * ratio, sh
        else:
            cw, ch = sw, sw / ratio
        cw, ch = min(sw, _even(cw)), min(sh, _even(ch))
        return cw, ch, bw - bw % 2, bh - bh % 2
    k = min(bw / sw, bh / sh)
    return sw - sw % 2, sh - sh % 2, min(bw, _even(sw * k)), min(bh, _even(sh * k))


_TURN = {90: "transpose=1", 180: "transpose=1,transpose=1", 270: "transpose=2"}


def video_filter(src: Tuple[int, int], box: Tuple[int, int], fill: bool, rotate: int, fps: int) -> str:
    """The ffmpeg ``-vf`` chain: rate first (fewer frames to work on), turn, crop centred, lanczos."""
    turned = rotated(src, rotate)
    cw, ch, ow, oh = geometry(turned, box, fill)
    parts = [f"fps={int(fps)}"]
    if int(rotate) % 360 in _TURN:
        parts.append(_TURN[int(rotate) % 360])
    if (cw, ch) != turned:
        parts.append(f"crop={cw}:{ch}")
    parts.append(f"scale={ow}:{oh}:flags=lanczos")
    return ",".join(parts)


_SIZE_RE = re.compile(r"Video:.*?(\d{2,5})x(\d{2,5})")
_FPS_RE = re.compile(r"(\d+(?:\.\d+)?) fps")
_DUR_RE = re.compile(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)")


def probe_info(ffmpeg: str, path: Path) -> dict:
    """``{"size": (w, h), "fps": float, "duration": float}`` of the file, read from ``ffmpeg -i``."""
    r = subprocess.run([ffmpeg, "-hide_banner", "-noautorotate", "-i", str(path)], capture_output=True,
                       timeout=30, **_spawn())
    text = r.stderr.decode(errors="ignore")
    line = next((ln for ln in text.splitlines() if "Video:" in ln), "")
    size, rate, dur = _SIZE_RE.search(line), _FPS_RE.search(line), _DUR_RE.search(text)
    if not size:
        raise RuntimeError(f"no video stream in the camera's movie ({text[-200:].strip()})")
    duration = (int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3))) if dur else 0.0
    return {"size": (int(size.group(1)), int(size.group(2))), "fps": float(rate.group(1)) if rate else 25.0,
            "duration": duration}


def probe(ffmpeg: str, path: Path) -> Tuple[int, int, float]:
    """``(width, height, fps)`` of the file's video stream."""
    info = probe_info(ffmpeg, path)
    return info["size"][0], info["size"][1], info["fps"]


def tail_start(duration: float, seconds: float) -> float:
    """Where the last ``seconds`` of the file begin.

    The camera starts writing a moment after Record=4 is accepted (EOS M50:
    a 2.0 s recording gave a 1.3 s clip), so the booth records a little
    longer and keeps the end: the last seconds before the stop.
    """
    if not duration or not seconds or duration <= seconds:
        return 0.0
    return round(duration - float(seconds), 3)


def film_fps(wanted, source_fps: float) -> int:
    """The film's rate: the setting, never more than the file has."""
    wanted = max(1, int(round(float(wanted or 25))))
    return max(1, min(wanted, int(round(source_fps or wanted))))


def _spawn() -> dict:
    from .boomerang import _spawn_kwargs
    return _spawn_kwargs()


def extract_frames(path: Path, work: Path, *, box: Tuple[int, int], fill: bool, rotate: int = 0,
                   fps=25, seconds: float = 0, ffmpeg: Optional[str] = None) -> Tuple[List[bytes], int]:
    """The movie as JPEG frames in the film's size: ``(frames, fps)``.

    Written to ``work`` (a folder of its own, removed afterwards) and read
    back: the frames then go through ``make_clip`` like live-view frames.
    """
    from .boomerang import MAX_FRAMES, ffmpeg_exe
    exe = ffmpeg or ffmpeg_exe()
    info = probe_info(exe, path)
    (w, h), src_fps = info["size"], info["fps"]
    rate = film_fps(fps, src_fps)
    start = tail_start(info["duration"], seconds)
    vf = video_filter((w, h), box, fill, rotate, rate)
    work = Path(work)
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        cmd = [exe, "-y", "-hide_banner", "-loglevel", "error", "-noautorotate", "-threads", "0"]
        if start:
            cmd += ["-ss", f"{start:.3f}"]
        if seconds and seconds > 0:
            cmd += ["-t", f"{float(seconds):.3f}"]
        cmd += ["-i", str(path)]
        cmd += ["-an", "-vf", vf, "-frames:v", str(MAX_FRAMES), "-q:v", "3",
                str(work / "f_%05d.jpg")]
        r = subprocess.run(cmd, capture_output=True, timeout=300, **_spawn())
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg: {r.stderr.decode(errors='ignore')[-300:].strip()}")
        frames = [p.read_bytes() for p in sorted(work.glob("f_*.jpg"))]
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if len(frames) < 2:
        raise RuntimeError(f"only {len(frames)} frames in the camera's movie")
    return frames, rate


# ── One pass: the film straight from the movie ────────────
#
# Measured on the booth PC (device test 17.11.2026): a 2 s boomerang took
# 17.5 s from the movie to the film. The frame path decodes the movie to
# JPEGs, Python decodes them again and pipes 6 MB of raw picture per frame
# into x264 -- three passes of 26 frames there and back, 150 frames at
# 1080x1920. Here ffmpeg reads the movie once, crops, scales, plays it
# forward and back and encodes ONE pass; the other passes are joined by
# stream copy. Used when no Python work is needed per frame (no motion
# frame, no print sheet).

def _layout_filter(src: Tuple[int, int], box: Tuple[int, int], fill: bool, rotate: int, fps: int) -> str:
    """``[0:v]`` → ``[c]``: the film's picture, cropped, or whole on the blurred backdrop like ``layout_frame``."""
    if fill:
        return f"[0:v]{video_filter(src, box, True, rotate, fps)}[c]"
    turned = rotated(src, rotate)
    bw, bh = box
    _cw, _ch, fw, fh = geometry(turned, box, False)
    if abs(turned[0] / turned[1] - bw / bh) < 0.05:
        return f"[0:v]{video_filter(src, box, True, rotate, fps)}[c]"
    head = [f"fps={int(fps)}"] + ([_TURN[int(rotate) % 360]] if int(rotate) % 360 in _TURN else [])
    sw, sh = max(2, bw // 6 // 2 * 2), max(2, bh // 6 // 2 * 2)
    return (f"[0:v]{','.join(head)},split[fa][fb];"
            f"[fb]scale={sw}:{sh}:force_original_aspect_ratio=increase:flags=bilinear,crop={sw}:{sh},"
            f"gblur=sigma=5,scale={bw}:{bh}:flags=bilinear,colorchannelmixer=rr=0.72:gg=0.72:bb=0.72[bg];"
            f"[fa]scale={fw}:{fh}:flags=lanczos[fg];[bg][fg]overlay=({bw}-w)/2:({bh}-h)/2[c]")


def film_filter(src, box, fill: bool, rotate: int, fps: int, boomerang: bool) -> str:
    """The whole ``-filter_complex``: layout, and for a boomerang forward then back (one pass)."""
    chain = _layout_filter(src, box, fill, rotate, fps)
    if boomerang:
        # Back without the frame the forward part ends on (no stand-still at the turn).
        chain += (";[c]split[f][b];[b]reverse,trim=start_frame=1,setpts=PTS-STARTPTS[r];"
                  "[f][r]concat=n=2:v=1:a=0,format=yuv420p[v]")
    else:
        chain += ";[c]format=yuv420p[v]"
    return chain


def make_film(path: Path, target: Path, *, box: Tuple[int, int], fill: bool, rotate: int = 0, fps=25,
              seconds: float = 0, boomerang: bool = False, loops: int = 1,
              ffmpeg: Optional[str] = None) -> Tuple[Path, "Image.Image", dict]:
    """The film from the camera's movie in one ffmpeg pass: ``(target, poster, times)``.

    Same codec settings as ``boomerang.encode_mp4`` (libx264 veryfast, crf
    20, high, yuv420p, faststart, no audio). ``times`` says where the time
    went (probe, encode, join, poster) for booth.log.
    """
    import io
    import time as _time
    from PIL import Image
    from .boomerang import ffmpeg_exe
    exe = ffmpeg or ffmpeg_exe()
    times = {}
    t = _time.monotonic()
    info = probe_info(exe, path)
    rate = film_fps(fps, info["fps"])
    start = tail_start(info["duration"], seconds)
    times["probe"] = round(_time.monotonic() - t, 2)
    target = Path(target)
    passes = max(1, int(loops)) if boomerang else 1
    one = target.with_name(target.stem + ".pass.mp4") if passes > 1 else target
    cmd = [exe, "-y", "-hide_banner", "-loglevel", "error", "-noautorotate", "-threads", "0"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    if seconds and seconds > 0:
        cmd += ["-t", f"{float(seconds):.3f}"]
    cmd += ["-i", str(path), "-filter_complex", film_filter(info["size"], box, fill, rotate, rate, boomerang),
            "-map", "[v]", "-an", "-r", str(rate), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-profile:v", "high", *(["-bf", "0"] if passes > 1 else []),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(one)]
    t = _time.monotonic()
    r = subprocess.run(cmd, capture_output=True, timeout=300, **_spawn())
    if r.returncode != 0 or not one.is_file() or one.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg: {r.stderr.decode(errors='ignore')[-300:].strip()}")
    times["encode"] = round(_time.monotonic() - t, 2)
    if passes > 1:
        t = _time.monotonic()
        liste = target.with_name(target.stem + ".pass.txt")
        try:
            liste.write_text(f"file '{one.name}'\n" * passes, encoding="utf-8")
            r = subprocess.run([exe, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(liste),
                                "-c", "copy", "-movflags", "+faststart", str(target)],
                               capture_output=True, timeout=120, **_spawn())
            if r.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
                raise RuntimeError(f"ffmpeg concat: {r.stderr.decode(errors='ignore')[-300:].strip()}")
        finally:
            for p in (one, liste):
                try:
                    p.unlink()
                except OSError:
                    pass
        times["join"] = round(_time.monotonic() - t, 2)
    # The poster: the middle of the forward part, as the frame path takes it.
    t = _time.monotonic()
    mid = max(0.0, (min(float(seconds or info["duration"] or 1), info["duration"] or 99)) / 2)
    r = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-ss", f"{mid:.3f}", "-i", str(target),
                        "-frames:v", "1", "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "2", "-"],
                       capture_output=True, timeout=60, **_spawn())
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"ffmpeg poster: {r.stderr.decode(errors='ignore')[-200:].strip()}")
    poster = Image.open(io.BytesIO(r.stdout)).convert("RGB")
    times["poster"] = round(_time.monotonic() - t, 2)
    times["fps"] = rate
    return target, poster, times
