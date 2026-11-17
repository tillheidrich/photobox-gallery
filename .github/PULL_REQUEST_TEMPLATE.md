## What changes for a user

<!-- One or two sentences. The diff already knows the files. -->

## Why this way

<!-- What you decided against, if it is not obvious. -->

## Tested

- [ ] `python -m pytest tests -q` (gallery)
- [ ] `cd booth && python -m pytest tests -q`
- [ ] `cd sync && python -m pytest tests -q`
- [ ] Ran the booth with `BOOTH_CAMERA=mock` where it matters

What I could **not** test and why:

<!-- "No printer here, the CUPS path is untested" is a fine answer. -->

## Checklist

- [ ] No Canon EDSDK, no other non-redistributable SDK, no secrets
- [ ] Profile schema changes are in `booth/boothcore/config.py` **and** `app/ecosystem.py`, plus the admin form
- [ ] Database migrations are additive
- [ ] Code, comments and this description in English; user-facing strings translated
- [ ] Commits signed off (`git commit -s`)
- [ ] I have read [CLA.md](../CLA.md) and I agree to it
