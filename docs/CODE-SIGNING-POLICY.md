# Code signing policy

Applies to the Windows files of Photobox: `PhotoboxBooth-<version>.exe`,
`PhotoboxSync-<version>.exe` and `Photobox-Setup-<version>.exe`. The same
policy, in German and English, is on the project site:
<https://photobox-gallery.com/en/code-signing-policy/>.

## Status

**Not active yet.** Free code signing through the SignPath Foundation is
being prepared for this project. Until it is active, every Windows file of
Photobox is **unsigned**, and Windows SmartScreen warns about an unknown
publisher. Compare the file with `SHA256SUMS.txt` before running it. This
section changes when the first signed release is published; until then no
release of this project carries a SignPath Foundation signature.

For signed releases:

Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by [SignPath Foundation](https://signpath.org)

## What is signed, and what never is

* Only files built from the source code in this repository, by the
  repository's own workflow (`.github/workflows/windows-release.yml`) on
  GitHub-hosted runners. SignPath checks that each file comes from that build.
* Only the **free edition**: the booth, the sync program and the setup that
  contains both. The workflow stops if a file of Canon's camera SDK (EDSDK) is
  anywhere in the build or inside a program (`tools/check_hygiene.py`), so a
  signed file can never contain it.
* **Never signed through SignPath:** the edition with camera SDK, which
  contains the runtime libraries of Canon's EDSDK. The SDK is proprietary and
  not under the AGPL. That edition is offered free of charge on the project
  website only, as a setup only (no portable program), built by the maintainer
  outside this repository and outside CI. The setup of the edition with camera
  SDK is itself never signed, with the SignPath Foundation certificate or any
  other. It packs the same booth program and sync program as the free edition
  of the same build, plus Canon's libraries in a separate `EDSDK` folder and
  their terms; Canon's files are never inside a signed file.
* Not signed: builds anyone makes elsewhere, the macOS build, the gallery
  server. Files from other projects are not signed with this project's
  certificate; the programs contain unsigned libraries of upstream open-source
  projects, as listed in `THIRD-PARTY-NOTICES.txt`.

Every signing request is approved by hand. The workflow never publishes a
release; it prepares a draft, which the maintainer publishes by hand.

## Team roles

Photobox is maintained by one person. All roles are held by:

* **Authors (committers):** Till Heidrich ([@tillheidrich](https://github.com/tillheidrich))
* **Reviewers:** Till Heidrich ([@tillheidrich](https://github.com/tillheidrich)).
  Every change proposed by someone who is not a committer (a pull request) is
  reviewed by him before it is merged, including changes to build scripts and
  CI configuration.
* **Approvers:** Till Heidrich ([@tillheidrich](https://github.com/tillheidrich)).
  He approves each signing request in SignPath.

Everyone in these roles uses multi-factor authentication for GitHub and for
SignPath.

## Privacy

This program will not transfer any information to other networked systems
unless specifically requested by the user or the person installing or
operating it.

In detail, as the code does it (the full list, with the setting that switches
each connection on, is in [`PRIVACY-BOOTH.md`](PRIVACY-BOOTH.md) and is
generated from `tools/outbound_connections.py`; a test fails when a source
file opens a connection that is not on that list):

* **The gallery the operator chose.** When the operator pairs the booth or
  the sync program with a gallery (or enters its address), photos and videos
  of each run, status reports and log excerpts go to that gallery, and the
  booth fetches a program update from the address that gallery offers when
  the operator or the gallery starts the update. Off until the operator sets it
  up. A short pairing code is resolved to `https://<code>.photobox-gallery.com`,
  the gallery service the code names. Remote control by that gallery (which
  can include the camera's live image and a screenshot) is a separate switch,
  off by default.
* **Further targets the operator configured:** SFTP/FTPS/FTP servers, an SMTP
  server for e-mails, webhook addresses, a network printer, a camera on the
  local network, a download page for guests' phones on the booth's local
  network. Each is off by default.
* **Nothing else.** No telemetry, no usage statistics to the maintainer, no
  advertising, no update check of its own without a paired gallery. Guests'
  photos never reach the maintainer unless the operator uses a gallery the
  maintainer runs for them.
* **During installation** the setup shows this information on its own page
  ("what this program does") before anything is installed. If Microsoft's
  WebView2 Runtime is missing, the setup offers to download it from Microsoft
  (a task the installing person can untick); Microsoft's privacy statement
  applies to that component: <https://privacy.microsoft.com/privacystatement>.

Privacy policy of the project site: <https://photobox-gallery.com/en/privacy/>
(German: <https://photobox-gallery.com/datenschutz/>). Operators of a booth
are responsible for their guests' data; see [`PRIVACY-BOOTH.md`](PRIVACY-BOOTH.md).

## Uninstalling

* **Setup:** Windows Settings → Apps → "Photobox" → Uninstall, or "Uninstall
  Photobox" in the Start menu. The uninstaller removes the start at logon and
  puts back every Windows setting the setup changed; it asks before deleting
  the data folder `%LOCALAPPDATA%\PhotoboxBooth` (default: keep it).
* **Portable `PhotoboxBooth-<version>.exe`:** if you ran it with `--install`,
  run it once with `--uninstall`; then delete the file and the folder
  `%LOCALAPPDATA%\PhotoboxBooth`.
* **Portable `PhotoboxSync-<version>.exe`:** if you ran it with `--install`,
  run it once with `--uninstall`; then delete the file and the folder
  `%LOCALAPPDATA%\PhotoboxSync`.
