# Contributing

Thanks for looking. Contributions are welcome from humans and from AI coding
agents alike — the bar is the same for both: a small, finished, tested change
that someone can review in one sitting.

Read [`AGENTS.md`](AGENTS.md) first. It has the repository map, the commands,
the hard rules and the definition of done. This file is only the process.

## Before you write code

* **Open an issue** unless the fix is obvious and small. Say what you intend to
  do, not only what is wrong. That is where a bad idea costs ten minutes instead
  of a weekend.
* **Check the labels.** `good first issue` is self-contained. `needs-hardware`
  cannot be finished without a camera or a printer on your desk. `wontfix-scope`
  means we discussed it and said no — the reason is in the issue.
* **Ask before large changes.** A refactor across the whole booth, a new
  dependency, a different database: fine to propose, not fine to arrive as a
  surprise pull request.

## Pull requests

1. Branch from `ecosystem` (the integration branch; `main` is the release branch).
2. One topic per pull request. If you fixed a typo on the way, that is fine;
   if you reformatted three files, split it.
3. Run all three test suites and say so in the description, including what you
   could **not** test and why. "No printer here, the CUPS path is untested" is
   an honest sentence and will not be held against you.
4. Sign off your commits (`git commit -s`). That line is the
   [Developer Certificate of Origin](https://developercertificate.org/): you
   confirm you may contribute this code under the project's licence. Code
   written with an AI assistant is fine; code copied out of a proprietary
   product is not, and you are the one stating the difference.
5. Expect review. Maintainer review is a human reading your change — answer
   questions rather than force-pushing a rewrite mid-discussion.

## Running the tests

Three suites, three roots, plus two checks. Install each part from its own
folder — `booth/requirements.txt` says `-e ../sync`, which pip resolves
against the working directory:

```bash
pip install -r requirements.txt -r requirements-dev.txt pytest -c constraints.txt
(cd booth && pip install -r requirements.txt -c ../constraints.txt)

python -m pytest tests -q                      # gallery
(cd booth && BOOTH_CAMERA=mock python -m pytest tests -q)
(cd sync && python -m pytest tests -q)
python -m pytest packaging/macos/test_packaging.py -q   # build plan checks, no Mac needed
python tools/rehearsal.py                      # gallery + booth end to end, mock camera
python tools/check_hygiene.py                  # vendor binaries, credentials, version drift
```

`constraints.txt` holds the versions the suites last passed with. If your change
needs a newer one, raise it there and say why in the pull request.

The gallery suite needs `tesseract` for the sharepic test (`apt-get install
tesseract-ocr`, `brew install tesseract`). The booth suite never prints on a
real printer, even on a machine that has one set up (`booth/tests/conftest.py`).
CI runs the same, plus a dependency audit and a secret scan.

## The CLA, step by step

1. Read [`CLA.md`](CLA.md) once. It is short and in plain words.
2. Put `I have read CLA.md and I agree to it.` in the description of your
   first pull request. That covers all later ones.
3. Sign off your commits (`git commit -s`).
4. If you contribute for an employer, the employer has to agree, not you —
   say so in the pull request.

A pull request without the line is reviewed like any other, but not merged
until the line is there.

## Commit messages

First line: what changes for a user, in plain words. Then, if it needs it, a
paragraph on why, and what you decided against. Not a list of files — the diff
already knows the files.

```
Print the QR in the corner the operator picked

It was nailed to the bottom right, which collided with the logo on
landscape cards. Position and size now come from the profile; the old
fixed values became the defaults, so existing events look unchanged.
```

## Reporting bugs

Use the issue template. What we need: what you did, what happened, what you
expected, and whether the booth had network at the time. Booth logs live in
`<data>/logs/booth.log`; the last 200 lines are usually enough. If a gallery is
involved, its version is at `/ueber`.

**Do not** paste device keys, API keys or a guest's photo into an issue. If a
bug only reproduces with real guest data, say so and we will find another way.

## Security

Do not open a public issue for a vulnerability. See [`SECURITY.md`](SECURITY.md).

## Licence of your contribution

Your work is published under [AGPL-3.0-or-later](LICENSE), the project's
licence, and stays in this public repository. All code stays complete and open; money is made
with operation (managed hosting, set-up, support), not with a closed feature
set, so nothing your code could disappear into.

Contributions need one step beyond the sign-off: read [`CLA.md`](CLA.md) and
add this line to your pull request description:

```
I have read CLA.md and I agree to it.
```

You keep your copyright. The CLA gives the maintainer the rights needed to keep
the licence situation clean for the whole codebase — the permission for the
Canon EDSDK in [`LICENSE-EXCEPTION`](LICENSE-EXCEPTION) is the main example.
`CLA.md` explains the reasoning; if you disagree with it, a precise issue or an
AGPL fork are both welcome alternatives.
