# Glossary

The pages in `current/` are German. These are their terms, what they mean, and
the name each one has in the code. The distinctions matter more than the
words: "tidy up" is not "delete", "unpublished" is not "silent".

| German term | English | Meaning | In the code |
|-------------|---------|---------|-------------|
| **Durchgang** | run | One visit at the booth: one or more shots, one result, one QR code | `Session`, `session_uid` |
| **Kennung** | id | Random string of a run; sub-items append `.1`, `.f1`, `.gif`, `.boom` | `uid` |
| **Fassung** | variant | `web` (downscaled, uploaded first) or `original` | `variant` |
| **Signaturschlüssel** | signing key | The event's secret the booth signs QR codes with. It is the identity of the event — not the slug | `share_key` |
| **Profil** | profile | All settings of a booth as JSON with a version number; travels from gallery to booth | `profile_json`, `profile_version` |
| **Auftrag** | job | One of the allowed remote actions the booth collects during sync | `device_jobs` |
| **Abgleich** | sync | The booth's call to the gallery: state out, profile and jobs back | `POST /api/v2/booth/sync` |
| **Warteschlange** | queue, outbox | The booth's upload list, one per target | `Outbox` |
| **Datensparsamer Betrieb** | data-minimal mode | Print, delete, done: nothing leaves the booth, nothing stays | `privacy.mode = print_only`, `ephemeral` |
| **Stiller Betrieb** | silent mode | Upload yes, show no: to the outside the gallery does not exist | `silent`, `silent_mode` |
| **Veröffentlicht** | published | The shared gallery is readable for everyone who knows the address. Unpublished does **not** mean "the guest gets nothing" | `events.published` |
| **Freigabe** | approval, moderation | New items become visible only after the operator confirms them | `moderation`, `review_state` |
| **Aufräumen** | tidy up | The booth removes files that are provably uploaded. Not a deletion request; the entry and its print counter stay | `aufgeraeumt` |
| **Löschen** | delete | The guest or the operator wants the item gone. Travels booth → gallery as a deletion slip, and since `948e076` gallery → booth as well | `deleted`, `deletions`, `deleted_sessions` |
| **Löschzettel** | deletion slip | The booth's note "this run was deleted here", sent with the next sync | `deletions` |
| **Gelöschte Kennung** | tombstone | An id the gallery will never accept again (since `0bc51bc`) | `deleted_uids` |
| **Betreiber** | operator | Whoever runs the installation and is logged in to the admin | — |
| **Bremse** | brake | A limit on failed attempts at a door (login, password, PIN) | `sharelinks.too_often`, `booth/boothcore/pinbrake.py` |
| **Schranke** | gate | One of the checks a gallery request passes in order (schedule, published, silent, key, password, expiry) | `app/blueprints/public.py` |
| **Ampel** | traffic light | The gallery's green/yellow/red rating of a booth's heartbeat | `app/booth.py` |
| **Abzug** | print | One sheet out of the printer; the unit that allowances count | `add_prints`, `event_prints` |
