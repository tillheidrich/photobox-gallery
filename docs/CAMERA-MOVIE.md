# Boomerang and video from the camera's own movie

German: [KAMERAFILM.md](KAMERAFILM.md)

By default a boomerang or a video is built from the live view the camera
sends over USB. On a Canon EOS that is about 960x640: a 9:16 clip is 360x640.

`boomerang.source` and `video.source` switch each mode on its own:

| value | what happens |
|---|---|
| `live` (default) | frames from the live view, as before |
| `camera` | the camera records a real movie onto its SD card, the booth downloads it, deletes it from the card and builds the clip from it |

Set it in the gallery (booth profile, "Boomerang & Video", field "Quelle") or
in the booth's own admin (`boomerang.source` / `video.source`).

## What it needs

* The Canon SDK backend (`edsdk`) and a body that can film over USB (tested
  with an EOS M50). gphoto2, digiCamControl, webcam and folder backends say
  "not supported" and the booth films from the live view.
* An SD card in the camera, with room. Each movie is deleted from the card
  after it was downloaded whole; nothing else on the card is touched. Keep the
  card nearly empty: the booth lists the card's DCIM folders before and after
  every movie.
* The camera's movie size set on the camera. Full HD 25p or 30p is the
  recommendation: a 4K second is about 15 MB and takes longer to download.

## The clip

* Fixed size: upright and square formats 1080 wide (9:16 1080x1920, 4:5
  1080x1350, 1:1 1080x1080), landscape formats 1920 wide (16:9 1920x1080, 3:2
  1920x1280). A Full HD portrait crop (608x1080) is scaled up with lanczos.
* Layout `crop` (and motion frames) take the centre of the movie in the
  film's shape; `blur` fits the whole movie frame onto the blurred backdrop.
* The rate is read from the file; the film never has more frames a second than
  the movie, and never more than `fps`.
* No sound: the camera's audio track is dropped, as for every booth film.
* The camera starts writing a moment after the start command, so it records
  0.7 s longer than set and the clip keeps the last `seconds` of the file.
* Without a motion frame or a print sheet, ffmpeg makes the film in one pass
  from the movie (one boomerang pass encoded, the others joined by stream copy).
* booth.log: `Film: Zeiten ab Anfrage {...}` (card, mode, start, stop, file,
  download, restored, in seconds since the request) and `Warten auf den Film …,
  ffmpeg {...}` for the clip.
* The raw movie lands in `<data>/cache/kamerafilm/`, never in the run's
  folder, the upload queue or a backup, and is deleted once the clip exists
  (or failed). Leftovers older than ten minutes are removed before the next
  recording.

## When it does not work

Every failure gives the guest a clip anyway, within the same run, and logs
why ("Film der Kamera ..." in the booth log):

* the camera refuses movie mode or the start (no card, dial, body that cannot
  film): the booth records from the live view instead; the guest waited up to
  about 6 s longer on the recording screen;
* no file, a broken download, ffmpeg fails: the live frames taken *during*
  the movie are used, nobody records twice.

The camera is always put back: recording stopped, movie mode off, pictures to
the PC with the same set-up as at connect (SaveTo host, then the capacity,
single shot, clock, live view), the mode dial value checked and SaveTo read back. If that fails the
booth opens the camera afresh, as after any camera error.
