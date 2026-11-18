# Product

A self-hosted photo booth: software for the box at the party, a gallery the
guests open afterwards, and the sync that carries pictures from one to the
other. Built to replace rented booth licences with something the operator
owns, and to be honest about data.

**Register: product.** The design serves the job. Nobody admires the booth; they
use it for forty seconds and walk away with a print.

## Who uses it

**The guest**, for under a minute, at a party. Often holding something in one
hand. Sometimes tipsy, frequently in a queue, never trained. They will not read
instructions. Anything that needs explaining has already failed.

**The operator**, before and during the event. That is Till, or a colleague who
rented the box. They set it up in a room they have never seen, ten minutes
before the first guest, often on a phone, sometimes over a hotel wifi that
barely works. They cannot fix code at 11pm.

**The host**, afterwards. They want the pictures and they want to know that
nothing is being done with them behind their back.

## What the product promises

* It works without internet. Everything that can be done offline is done
  offline, and what needs the network queues until the network returns.
* Nothing leaves the box that the operator did not switch on.
* In the data-minimal mode, prints happen and nothing is kept. That mode exists
  for kindergartens, schools and works councils, and it is enforced where data
  would leave, not in the interface.
* No cloud AI, no biometrics, no face recognition. A local segmentation model
  that separates foreground from background is allowed and named as such; it
  recognises nobody and stores nothing.

## Tone

German in the interface, plain and short. The booth speaks to guests the way a
friendly person would: "Tippen & lächeln", not "Bitte betätigen Sie den
Auslöser". The admin speaks to an operator under time pressure: what is wrong,
what to do, in one sentence, without error codes.

Error messages name a next step. "Kamera verbinden" beats "device not found".

## Anti-references

* **Established commercial booth software**: the functional benchmark, the aesthetic warning. Dense option
  screens, borrowed Windows-dialog look, a wall of switches with no hierarchy.
* **Anything that looks like a SaaS landing page**: big gradient number, three
  identical feature cards, a purple glow. This is a tool, not a pitch.
* **Photo apps that treat pictures as content to be processed**: the guests in
  the picture are people at a party, not data.

## Strategic principles

* The operator decides, not the software. Every guest-facing choice can be
  switched off, and switching it off removes a screen rather than leaving an
  empty one.
* Fail visibly to the operator, quietly to the guest. A failed print is a red
  line in the admin and a friendly sentence at the booth.
* Prefer a setting in the profile over a setting in someone's head. If it can be
  configured remotely, it should be, because the booth is usually not where the
  operator is.
