# Marketing page

The product page photobox-gallery.com is not part of this repository. It is a
static Astro site in its own repository (`photobox-gallery-web`), German at
`/` and English at `/en/`, built into an nginx container. What stays here is
what the page and the product share:

* **Screenshots.** `tools/marketing_shots.mjs` starts a seeded demo gallery and
  a real booth (`tools/marketing/stage.py`) whose camera draws a lakeside scene
  (`tools/marketing/scene_camera.py`), so no person appears in any picture and
  nothing is downloaded. It takes every screen the page shows at device scale
  2 and, with `--export <dir>`, writes WebP and AVIF copies at three widths
  plus `sizes.json` (`tools/marketing/export.py`). Point `--export` at the
  site's `public/img/produkt/`.
* **The wording rule.** Everything the page claims has to be true for the code
  in this repository: the software is AGPL and complete for everyone, there
  are no licence keys and no feature held back, the Canon SDK is never part of
  the repository, a CI build or a GitHub release (only of the free-of-charge
  edition with camera SDK on the website, whose Canon files are not under the
  AGPL), and money comes from operation (managed hosting, set-up, support),
  not from a closed feature set. Canon is named only in factual compatibility
  statements ("works with Canon EOS cameras"), never as a product name.
  When a feature changes, take the screenshots again and check the page.

```bash
# needs Node 22+, Chrome, and the Python environment of this repository
node tools/marketing_shots.mjs --out /tmp/shots \
     --export ../photobox-gallery-web/public/img/produkt --widths 480,960,1600
# only the kiosk, from another checkout (a newer booth):
node tools/marketing_shots.mjs --only kiosk --booth-root ../other-checkout --out /tmp/shots
```

The demo uses ports 9810 (gallery), 9811 (booth) and 9820 (Chrome), prints
into a hot folder inside `--work` and deletes its data folder when it starts.
