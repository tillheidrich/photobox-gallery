# Contributor Licence Agreement

Photobox Gallery is published under the AGPL-3.0-or-later, and only under it.
There is no second edition. Keeping the licence situation clean still needs
one person who can speak for the whole codebase — to grant a licence exception,
to answer a legal question, to fix a licence problem without asking every
contributor first. That is what this document is for.

It is written to be read, not to be skipped. If a sentence here bothers you, say
so in the pull request — that is a fair conversation to have before you write
code, and a bad reason to walk away silently.

> This is the project's plain-language agreement, not legal advice. If you
> contribute on behalf of an employer, check with them first: in most
> employment contracts the code you write belongs to the company, not to you,
> and then the company has to agree, not you.

## What you are agreeing to

By contributing to this repository you state that:

1. **The work is yours to give.** You wrote it, or you have the right to submit
   it. It is not copied out of a proprietary product, and it does not carry a
   licence that conflicts with this agreement. Code written with the help of an
   AI assistant is fine — you are the one who reviewed it and stands behind it.
2. **You grant a licence.** You give Till Heidrich a perpetual, worldwide,
   non-exclusive, irrevocable, royalty-free right to use, reproduce, modify,
   publish and distribute your contribution, and to sublicense it — **including
   under licence terms other than the AGPL**, for example to add a licence
   exception like the one for the Canon EDSDK in `LICENSE-EXCEPTION`.
3. **You keep your copyright.** This is a licence, not a transfer. You may use
   your own contribution anywhere else, for anything, without asking.
4. **You grant patent rights.** If you hold a patent that your contribution
   would otherwise infringe, you grant the same licence for it, so nobody
   downstream is ambushed later.
5. **No warranty is expected of you.** You contribute as-is. Nobody may sue you
   because your patch had a bug.

## Why one rights holder

There is no paid edition. The maintainer earns money by
running the software for others as a managed service — hosting galleries,
setting up and looking after booths — which anyone else may do under the same
licence. The CLA is not there to take your code anywhere else. It is there
because a few things can only be done by someone who holds the rights to every
line:

* **The Canon SDK permission.** The booth can talk to Canon cameras through the
  Canon EDSDK, which is proprietary and under Canon's own agreement. Nobody
  ships it — every user gets it from Canon — but a build that loads it combines
  AGPL code with a proprietary library. The AGPL allows the rights holders to
  permit that explicitly (section 7); the permission is in `LICENSE-EXCEPTION`.
  Code from contributors who did not grant the right to add such permissions
  could not be covered by it.
* **Fixing a licence problem.** If a future camera SDK, a court decision or a
  new version of the AGPL raises a question, it can be answered for the whole
  codebase at once, instead of one contributor at a time.
* **Defending the licence.** Someone who takes the code and breaks the AGPL can
  be addressed by one person who holds the necessary rights.

Contributions are never taken private: every line you contribute stays in the
public AGPL repository. The right to sublicense exists for the cases above, not
so the code can disappear from the open one.

## How to agree

Put this line in your pull request description:

```
I have read CLA.md and I agree to it.
```

and sign off your commits (`git commit -s`), which is the
[Developer Certificate of Origin](https://developercertificate.org/).

One agreement covers all your future contributions to this repository. You do
not have to repeat it in every pull request — though the checkbox in the pull
request template is there to remind you.

## If you would rather not

That is a legitimate position, and plenty of people hold it. Two things you can
still do: open an issue describing the fix precisely (a well-written bug report
is worth more than most patches), or publish your change as a fork under the
AGPL. Forks are explicitly welcome — that is what the licence is for.
