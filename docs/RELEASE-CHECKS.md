# Release checks

The record of the checklist in `AGENTS.md` ("Before a release"). One block per
release or real event, newest first. Write what was actually done; "not done,
no iPhone here" is a valid line and better than a tick that is not true.

```
## <version or event> — <date>
Reviewed diff: <from>..<to>, by <who>; findings: <…>
Guest on a phone: <device, browser> — <result>
Link sharer: <apps> — <result>
Privacy-minded guest: <result>
First-time installer: <machine> — <result>
Real phone, large view: iPhone <model/iOS>: <result>; Android <model>: <result>
```

## fix/lightbox-mobile — 2026-11-18

Reviewed diff: not yet (the author's own branch; needs an independent review).
Guest on a phone, real phone, large view: not done on a device; headless
Chrome with mobile emulation only (tests/test_lightbox_on_a_phone.py).
Link sharer, privacy-minded guest: Flask test client only
(tests/test_hidden_gallery_is_unknown_everywhere.py).
First-time installer: not done.
