"""Boomerang und Video fürs Handy.

Zwei Layouts für das Hochformat:

* ``crop`` — mittiger 9:16-Ausschnitt aus dem Querbild. Groß und nah, gut für 1–3 Personen.
* ``blur`` — das ganze Querbild steht mittig im 9:16-Rahmen, dahinter läuft dasselbe
  Video vergrößert und unscharf. Niemand wird abgeschnitten — die Empfehlung für Gruppen.

Ausgabe: H.264, yuv420p, faststart — spielt auf iOS, Android, WhatsApp, Instagram.

Quelle sind die Live-Bilder, die per USB von der Kamera kommen. Es wird nie
hochskaliert: Liefert die Kamera 1080p, wird es 1080p; liefert sie weniger (das
EDSDK-Live-Bild der M50 ist rund 960×640), wird in dieser nativen Auflösung
kodiert. Breite ``"auto"`` = so groß wie die Quelle hergibt, höchstens ``max_width``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Iterable, List, Optional

import logging

from PIL import Image, ImageFilter, ImageOps

from .compose import crop_to_aspect, parse_aspect


log = logging.getLogger("booth.media")


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # pragma: no cover
        raise RuntimeError("ffmpeg nicht gefunden (pip install imageio-ffmpeg)") from e


def output_size(aspect: str, width: int) -> tuple[int, int]:
    ratio = parse_aspect(aspect)
    w = width - width % 2
    return w, int(round(w / ratio)) // 2 * 2


def native_width(src: tuple[int, int], aspect: str, layout: str, max_width: int = 1080) -> int:
    """Ausgabebreite ohne Hochskalieren.

    ``blur``: das ganze Quellbild steht in voller Breite im Rahmen → Quellbreite.
    ``crop``: nur der Ausschnitt zählt → Breite des Ausschnitts.
    """
    sw, sh = src
    ratio = parse_aspect(aspect)
    crop_w = min(sw, sh * ratio)
    w = sw if (layout == "blur" and sw / sh > ratio + 0.05) else crop_w
    w = int(min(w, max_width))
    return max(240, w - w % 2)


SCALE_TARGETS = {"native": 0, "720p": 720, "1080p": 1080, "1440p": 1440}


def resolve_width(width, src: tuple[int, int], aspect: str, layout: str, max_width: int = 1080,
                  scale: str = "native") -> int:
    """Output width.

    ``scale="native"`` never enlarges — what the camera sends over USB is what gets encoded,
    capped by ``max_width``. A fixed target (``720p``, ``1080p``, ``1440p``) always produces exactly
    that short edge, scaling up when needed, so clips stay usable on Instagram and WhatsApp even
    when the camera's live view is small (the Canon M50 delivers about 1024x576 over USB).
    """
    native = native_width(src, aspect, layout, max_width)
    target = SCALE_TARGETS.get(str(scale).lower(), 0)
    if target:
        ratio = parse_aspect(aspect)
        # target = short edge; portrait formats take the width from it, landscape the height
        want = int(target if ratio < 1 else round(target * ratio))
        return max(240, want - want % 2)
    if str(width).lower() in ("", "auto", "native", "0"):
        return native
    return min(int(width), native)


def layout_frame(frame: Image.Image, aspect: str, width: int, layout: str) -> Image.Image:
    size = output_size(aspect, width)
    src_ratio = frame.width / frame.height
    target_ratio = size[0] / size[1]
    if layout != "blur" or abs(src_ratio - target_ratio) < 0.05:
        return crop_to_aspect(frame, aspect).resize(size, Image.Resampling.LANCZOS)
    # Hintergrund: gefüllt, klein unscharf gerechnet (schnell), dann hochskaliert und abgedunkelt
    small = ImageOps.fit(frame, (size[0] // 6, size[1] // 6), Image.Resampling.BILINEAR)
    bg = small.filter(ImageFilter.GaussianBlur(5)).resize(size, Image.Resampling.BILINEAR)
    bg = Image.eval(bg, lambda v: int(v * 0.72))
    # Vordergrund: ganzes Bild, auf Breite eingepasst, mittig
    fw = size[0]
    fh = int(round(fw / src_ratio))
    if fh > size[1]:
        fh, fw = size[1], int(round(size[1] * src_ratio))
    fg = frame.resize((fw, fh), Image.Resampling.LANCZOS)
    bg.paste(fg, ((size[0] - fw) // 2, (size[1] - fh) // 2))
    return bg


def key_frame_indices(count: int, n: int) -> List[int]:
    """Which of ``count`` recorded frames fill ``n`` slots of a print layout.

    The middle of each of ``n`` equal stretches of the recording: evenly
    spread, never the first or last frame (the guests are still getting
    into position there), and for one slot the middle frame — the poster.
    Fewer frames than slots repeat in order.
    """
    count, n = max(1, int(count)), max(1, int(n))
    return [min(count - 1, (2 * i + 1) * count // (2 * n)) for i in range(n)]


def print_frame(frame: Image.Image, aspect: str, layout: str) -> Image.Image:
    """A frame as the film shows it: the format's middle (``crop``), or the whole frame.

    The same branch as ``layout_frame``, without the blurred backdrop: with
    ``blur`` and aspects that differ, the film shows the whole live frame.
    """
    ratio = parse_aspect(aspect)
    if layout == "blur" and abs(frame.width / frame.height - ratio) >= 0.05:
        return frame.copy()
    return crop_to_aspect(frame, aspect)


def pingpong(frames: List[Image.Image], loops: int) -> List[Image.Image]:
    if len(frames) < 3:
        return frames * max(1, loops)
    cycle = frames + frames[-2:0:-1]
    return cycle * max(1, loops)


def _spawn_kwargs() -> dict:
    """No console window on Windows; a lower priority on a thread marked ``in_background``."""
    creation = 0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0   # kein Konsolenfenster unter Windows
    extra = {}
    if getattr(_low, "on", False):
        if creation:
            creation |= 0x00004000                  # BELOW_NORMAL_PRIORITY_CLASS
        elif hasattr(os, "nice"):
            extra["preexec_fn"] = lambda: os.nice(10)
    return {"creationflags": creation, **extra}


def framed_inputs(layers: dict, fps: int) -> tuple[list, list]:
    """ffmpeg inputs and filter around the piped window frames of a motion frame.

    The still background and top layer are PNG files rendered once
    (``motion_frames.cached_layers``); ffmpeg lays the film into the window
    and the top layer over it, so Python only scales the window's pictures.
    Both pictures are read at the film's rate, so their timestamps meet the
    film's frame for frame; ``shortest`` ends with the film.
    """
    x, y = int(layers["box"][0]), int(layers["box"][1])
    before = ["-framerate", str(fps), "-loop", "1", "-i", str(layers["bottom"])]
    after = ["-framerate", str(fps), "-loop", "1", "-i", str(layers["top"]),
             "-filter_complex",
             f"[0:v][1:v]overlay={x}:{y}:shortest=1[a];[a][2:v]overlay=0:0:shortest=1,format=yuv420p[v]",
             "-map", "[v]"]
    return before, after


def encode_mp4(frames: Iterable[Image.Image], size: tuple[int, int], target: Path, fps: int,
               bframes: bool = True, layers: Optional[dict] = None) -> Path:
    """``layers``: a motion frame's still layers; ``size`` is then the window's."""
    w, h = size
    before, after = framed_inputs(layers, fps) if layers else ([], [])
    cmd = [
        ffmpeg_exe(), "-y", "-loglevel", "error", *before,
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", *after,
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-profile:v", "high",
        *([] if bframes else ["-bf", "0"]),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, **_spawn_kwargs())
    count = 0
    # A film from stills hands the same picture over many times (held for
    # its time, pass after pass): its raw bytes are made once. The pictures
    # stay referenced in here, so an id is never reused while it is a key.
    raw: dict = {}
    try:
        for f in frames:
            hit = raw.get(id(f))
            if hit is None or hit[0] is not f:
                g = f if f.size == size else f.resize(size)
                hit = (f, g.convert("RGB").tobytes())
                raw[id(f)] = hit
                while len(raw) > 32:
                    raw.pop(next(iter(raw)))
            proc.stdin.write(hit[1])
            count += 1
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="ignore")
        if proc.wait(timeout=180) != 0 or not count:
            raise RuntimeError(f"ffmpeg fehlgeschlagen: {err[-400:]}")
    finally:
        if proc.poll() is None:
            proc.kill()
    return target


def encode_passes(one_pass: List[Image.Image], size: tuple[int, int], target: Path, fps: int,
                  passes: int) -> Path:
    """A film of ``passes`` identical passes: one pass is encoded, the others are copies of it.

    Measured on the booth PC (15.11.2026): the 15 s films of a six-shot GIF
    pushed every frame of every pass through x264 — 240 frames at 1080×1920
    for the 9:16 film, 21 s on two cores. The passes are the same pictures in
    the same order, so one is encoded and the rest is joined by stream copy
    (ffmpeg's concat demuxer, ``-c copy``: no decoding, no encoding). The
    pass is encoded without B-frames so every pass starts and ends on whole
    pictures and the joins carry no reordering delay. If the join fails the
    whole film is encoded as before.
    """
    target = Path(target)
    passes = max(1, int(passes))
    if passes == 1:
        return encode_mp4(iter(one_pass), size, target, fps)
    part = target.with_name(target.stem + ".pass.mp4")
    liste = target.with_name(target.stem + ".pass.txt")
    try:
        encode_mp4(iter(one_pass), size, part, fps, bframes=False)
        # Relative names resolve against the list's folder.
        liste.write_text(f"file '{part.name}'\n" * passes, encoding="utf-8")
        cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(liste),
               "-c", "copy", "-movflags", "+faststart", str(target)]
        r = subprocess.run(cmd, capture_output=True, timeout=120, **_spawn_kwargs())
        if r.returncode != 0 or not target.is_file() or target.stat().st_size == 0:
            raise RuntimeError(r.stderr.decode(errors="ignore")[-300:] or "concat failed")
        return target
    except Exception as e:                            # noqa: BLE001 — the slow way still gives the film
        log.warning("Film aus Durchgängen nicht zusammengesetzt (%s) – wird ganz kodiert", e)
        return encode_mp4(iter(one_pass * passes), size, target, fps)
    finally:
        for p in (part, liste):
            try:
                p.unlink()
            except OSError:
                pass


MAX_FRAMES = 900     # 60 s × 15 fps oder 30 s × 30 fps — genug für Grüße, schützt den Speicher


def played_fps(n_frames: int, elapsed: float, nominal: float) -> int:
    """The rate a recorded clip is played at: the rate its frames were taken.

    The setting is a ceiling for the camera, not a promise: an EOS live view
    over USB delivers 10-15 frames a second. Played at the nominal 20 fps a
    2 s recording ran forward for 1.2 s, too fast and shorter than set.
    Never faster than the setting, never slower than five a second.
    """
    nominal = max(1, int(round(float(nominal or 0) or 20)))
    if not elapsed or elapsed <= 0 or n_frames <= 0:
        return nominal
    return max(5, min(nominal, int(round(n_frames / float(elapsed)))))


_low = threading.local()


def in_background() -> None:
    """From now on, films encoded on this thread run at a lower priority.

    The films of a GIF run are made while the next guest already uses the
    booth: live view, countdown and the shutter come first on two cores.
    """
    _low.on = True


def _decode(frame, rotate=None):
    if isinstance(frame, (bytes, bytearray)):
        import io
        img = Image.open(io.BytesIO(frame)).convert("RGB")
        return rotate(img) if rotate else img
    return frame


def make_clip(frames: List, target: Path, *, aspect: str = "9:16", width="auto",
              fps: int = 20, layout: str = "blur", boomerang: bool = True, loops: int = 3,
              max_width: int = 1080, rotate=None, scale: str = "native",
              frame_layers: Optional[dict] = None) -> tuple[Path, Image.Image]:
    """``frames``: PIL-Bilder oder JPEG-Bytes. Bytes werden erst beim Kodieren einzeln entpackt,
    damit auch lange Videos nicht tausende Vollbilder im Speicher halten.

    ``frame_layers``: a motion frame's still layers (``motion_frames.cached_layers``).
    The film is then the frame's size and the recording fills its window
    ("cover"); ``aspect``, ``layout`` and the width rules do not apply.
    """
    frames = list(frames)[:MAX_FRAMES]
    if not frames:
        raise ValueError("keine Frames")
    if frame_layers:
        return _make_framed_clip(frames, target, frame_layers, fps=fps, boomerang=boomerang, loops=loops,
                                 rotate=rotate)
    first = _decode(frames[0], rotate)
    width = resolve_width(width, first.size, aspect, layout, max_width, scale)
    size = output_size(aspect, width)
    order = list(range(len(frames)))
    if boomerang:
        cycle = order + order[-2:0:-1] if len(order) >= 3 else order
        order = cycle * max(1, loops)
    poster_holder = {}
    mid = len(frames) // 2

    def laid():
        cache_idx, cache_img = None, None
        for i in order:
            if i != cache_idx:
                cache_img = layout_frame(_decode(frames[i], rotate), aspect, width, layout)
                cache_idx = i
                if i == mid and "img" not in poster_holder:
                    poster_holder["img"] = cache_img
            yield cache_img

    encode_mp4(laid(), size, target, fps)
    poster = poster_holder.get("img") or layout_frame(_decode(frames[mid], rotate), aspect, width, layout)
    return target, poster


def _make_framed_clip(frames: List, target: Path, layers: dict, *, fps: int, boomerang: bool, loops: int,
                      rotate=None) -> tuple[Path, Image.Image]:
    """The recording in a motion frame's window; the still layers come from ``layers``."""
    from . import motion_frames
    x, y, w, h = [int(v) for v in layers["box"]]
    order = list(range(len(frames)))
    if boomerang:
        cycle = order + order[-2:0:-1] if len(order) >= 3 else order
        order = cycle * max(1, loops)
    mid = len(frames) // 2
    held = {}

    def laid():
        idx, img = None, None
        for i in order:
            if i != idx:
                img = motion_frames.fit(_decode(frames[i], rotate), w, h)
                idx = i
                if i == mid:
                    held["mid"] = img
            yield img

    encode_mp4(laid(), (w, h), target, fps, layers=layers)
    still = held.get("mid") or motion_frames.fit(_decode(frames[mid], rotate), w, h)
    with Image.open(layers["bottom"]) as bottom, Image.open(layers["top"]) as top:
        poster = motion_frames.compose(still, bottom.convert("RGB"), top.convert("RGBA"), (x, y, w, h))
    return target, poster


def make_boomerang(frames, target, *, aspect="9:16", width="auto", fps=15, loops=3, layout="crop"):
    return make_clip(frames, target, aspect=aspect, width=width, fps=fps, layout=layout, boomerang=True, loops=loops)


def make_boomerang_from_stills(images: List[Image.Image], target: Path, *, aspect: str = "9:16",
                               layout: str = "blur", frame_ms: int = 400, fps: int = 15,
                               loops: int = 3, max_width: int = 1080,
                               scale: str = "native", min_seconds: float = 0) -> Path:
    """Aus den Einzelbildern eines GIF-Durchgangs ein Boomerang-Video machen.

    Der Unterschied zum MP4, das neben dem GIF entsteht: Dieses hier hat sein
    eigenes Format. Ein GIF wird meist im Bildformat der Box aufgenommen
    (4:5), fürs Handy gebraucht wird aber 9:16 — und in diesem Fall wäre ein
    Zuschnitt fatal, weil am Rand Leute stehen. Deshalb ``layout``: ``blur``
    stellt das ganze Bild mittig in den Rahmen und legt es vergrößert und
    unscharf dahinter, ``crop`` schneidet zu.

    ``frame_ms`` ist dieselbe Standzeit je Bild wie beim GIF; daraus und aus
    ``fps`` ergibt sich, wie oft ein Bild wiederholt wird. Sonst wäre ein
    Boomerang aus vier Aufnahmen ein Viertelsekunden-Zucken.
    """
    if not images:
        raise ValueError("no frames")
    width = resolve_width("auto", images[0].size, aspect, layout, max_width, scale)
    size = output_size(aspect, width)
    laid = [layout_frame(im, aspect, width, layout) for im in images]
    laid = [im if im.size == size else im.resize(size, Image.Resampling.LANCZOS) for im in laid]
    folge = laid + laid[-2:0:-1] if len(laid) > 2 else laid      # hin und zurück
    halten = max(1, round(max(1, int(fps)) * max(60, int(frame_ms)) / 1000))
    seq = [bild for bild in folge for _ in range(halten)]
    fps = max(1, int(fps))
    passes = max(1, int(loops))
    if min_seconds and min_seconds > 0:        # whole passes until the asked length is reached
        passes = max(passes, -(-int(round(float(min_seconds) * fps)) // len(seq)))
    passes = max(1, min(passes, MAX_FRAMES // len(seq)))
    return encode_passes(seq, size, Path(target), fps, passes)


def gif_film_passes(n_seq: int, frame_ms: int, seconds: float = 0) -> tuple[int, int]:
    """``(fps, passes)`` of the film next to a GIF: three passes, or whole passes up to ``seconds``."""
    fps = max(1, round(1000 / max(60, int(frame_ms))))
    # A GIF loops by itself, a film does not: three passes of a short
    # series are over in a few seconds. Repeat whole passes until the
    # asked length is reached, so the end meets the start without a jump.
    passes = 3
    if seconds and seconds > 0:
        passes = max(3, -(-int(round(float(seconds) * fps)) // n_seq))
    return fps, max(1, min(passes, MAX_FRAMES // n_seq))


def _gif_frames(images: List[Image.Image], aspect: str, max_width: int, scale: str, pingpong: bool,
                mapper=map):
    width = resolve_width("auto", images[0].size, aspect, "crop", max_width, scale)
    size = output_size(aspect, width)
    laid = list(mapper(lambda im: crop_to_aspect(im, aspect).resize(size, Image.Resampling.LANCZOS), images))
    seq = laid + laid[-2:0:-1] if (pingpong and len(laid) > 2) else laid
    return size, laid, seq


def make_gif_film(images: List[Image.Image], target: Path, *, aspect: str = "4:5", frame_ms: int = 400,
                  pingpong: bool = True, max_width: int = 1080, scale: str = "native",
                  seconds: float = 0) -> Path:
    """The MP4 of a GIF: the same pictures, the same order, as a film phones play."""
    if not images:
        raise ValueError("no frames")
    size, _laid, seq = _gif_frames(images, aspect, max_width, scale, pingpong)
    fps, passes = gif_film_passes(len(seq), frame_ms, seconds)
    return encode_passes(seq, size, Path(target), fps, passes)


def make_gif(images: List[Image.Image], target: Path, *, aspect: str = "4:5", frame_ms: int = 400,
             pingpong: bool = True, loops: int = 0, max_width: int = 1080, scale: str = "native",
             mp4: Optional[Path] = None, mp4_seconds: float = 0,
             mapper=map) -> tuple[Path, Image.Image, Optional[Path]]:
    """Animated GIF from the stills of one session (dslrBooth style).

    ``frame_ms`` is how long each still is shown, ``pingpong`` plays the series forwards and back.
    A matching MP4 is written as well when ``mp4`` is given: phones and chat apps handle it better,
    and the gallery can fall back to it.

    ``mapper``: how per-picture work is spread (``map``, or a thread pool's
    ``map`` — the pictures are independent of each other).
    """
    if not images:
        raise ValueError("no frames")
    size, laid, seq = _gif_frames(images, aspect, max_width, scale, pingpong, mapper)
    # Each distinct picture is reduced to its palette once. The way back of
    # a ping-pong shows the same pictures again; Pillow used to quantize each
    # of them a second time. The same call Pillow makes inside (an adaptive
    # palette), so the file is the same.
    paletted = dict(zip((id(im) for im in laid),
                        mapper(lambda im: im.convert("P", palette=Image.Palette.ADAPTIVE), laid)))
    frames = [paletted[id(im)] for im in seq]
    frames[0].save(target, "GIF", save_all=True, append_images=frames[1:], duration=max(60, int(frame_ms)),
                   loop=int(loops), optimize=True, disposal=2)
    clip = None
    if mp4:
        fps, passes = gif_film_passes(len(seq), frame_ms, mp4_seconds)
        try:
            encode_passes(seq, size, Path(mp4), fps, passes)
            clip = mp4
        except Exception as e:      # ffmpeg missing: the GIF alone is enough
            log.warning("MP4 alongside the GIF failed: %s", e)
    return target, laid[0], clip
