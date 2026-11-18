# Code signing with the SignPath Foundation: what the project prepares, what the owner sets up

**Status: not active.** The project has prepared everything that lives in the
repository. The application to the SignPath Foundation, the SignPath
organisation and the settings below are done by the owner, by hand. Until then
every Windows file of this project is unsigned. The public policy is in
[`CODE-SIGNING-POLICY.md`](CODE-SIGNING-POLICY.md); the German background page
with Certum and SmartScreen is [`CODE-SIGNATUR.md`](CODE-SIGNATUR.md).

Read on 18 Nov 2026: <https://signpath.org/terms>, the SignPath documentation
on GitHub as a trusted build system, artifact configurations, origin
verification and pipeline policies (<https://docs.signpath.io/>), and the
inputs of `signpath/github-action-submit-signing-request` v3.0 (its
`action.yml`). Check them again before you rely on a detail here.

## What is in the repository

| File | What it does |
|---|---|
| `.github/workflows/windows-release.yml` | Builds on GitHub-hosted Windows runners with `packaging/windows/build.ps1`, runs the Canon guard, uploads the unsigned files, and, when SignPath is set up, submits two signing requests, checks the signatures, writes `SHA256SUMS.txt` over the signed files and makes a **draft** release. |
| `tools/check_hygiene.py --build-output … --pyinstaller …` | The guard: fails the run when a file of Canon's SDK is in the build tree or inside `PhotoboxBooth.exe` / `PhotoboxSync.exe`. Only the free edition is ever built or signed in CI. |
| `packaging/windows/camera_sdk_edition.py`, `build.ps1 -WithCameraSdk` | The edition with camera SDK (website only, setup only; its setup packs the same booth program and adds Canon's libraries in `EDSDK\`). **Never used in CI and never signed**: build.ps1, the spec, the setup script and the helper refuse to run on a CI runner, and `tests/test_camera_sdk_edition.py` fails if any workflow names the switch. Signing covers the free edition only. |
| `packaging/windows/version_resource.py` | Product name `Photobox`, product and file version from `VERSION`, publisher, copyright with the AGPL note, original file name, for both programs. |
| `packaging/windows/installer/photobox.iss` | The same for the setup (`VersionInfo*`), including the version as text. |
| `packaging/windows/check_release_files.ps1` | Product name and version in every file; with `-Signed` also a valid signature by "SignPath Foundation". |

No `.signpath/` folder: SignPath's documentation keeps artifact
configurations in the SignPath web interface. Pipeline policies may be stored
in a repository, but they are optional; the suggestion below is pasted into
the web interface instead.

## Why two signing requests per release

SignPath can sign the files *inside* MSI, CAB, APPX and MSIX packages ("deep
signing"). An Inno Setup `.exe` is not among them: for SignPath it is one
`<pe-file>`, nothing it can open. So the workflow:

1. builds booth and sync, and sends `PhotoboxBooth.exe` and `PhotoboxSync.exe`
   (artifact `programs-unsigned`) to SignPath;
2. after approval, builds the setup with `build.ps1 -InstallerOnly` **from the
   signed programs**, and sends `Photobox-Setup-<version>.exe` (artifact
   `setup-unsigned`) to SignPath;
3. after approval, checks every signature and writes `SHA256SUMS.txt`.

Each request waits for a person to approve it in SignPath (up to five hours
per job, then the job fails and nothing is released). The uninstaller that
Inno Setup writes at install time (`unins000.exe`) stays unsigned: Inno can
only sign it during compilation with a local signing tool, which this setup
does not have.

## Settings the workflow reads

Repository → Settings → Secrets and variables → Actions:

| Kind | Name | Value |
|---|---|---|
| Secret | `SIGNPATH_API_TOKEN` | API token of a SignPath user with submitter rights in the project |
| Variable | `SIGNPATH_ORGANIZATION_ID` | the organisation id from SignPath |
| Variable | `SIGNPATH_PROJECT_SLUG` | the project's slug, e.g. `photobox` |
| Variable | `SIGNPATH_SIGNING_POLICY_SLUG` | the slug of the signing policy that uses the SignPath Foundation certificate (shown in the project in SignPath) |
| Variable | `SIGNPATH_ARTIFACT_CONFIGURATION_PROGRAMS` | slug of the configuration "programs" below |
| Variable | `SIGNPATH_ARTIFACT_CONFIGURATION_SETUP` | slug of the configuration "setup" below |

If one of them is missing, both signing jobs are skipped and a tag ends in a
draft pre-release titled "… (unsigned)". The token never reaches a job that
builds anything: a separate job only answers "set up or not".

## Artifact configurations to create in SignPath

Both artifacts arrive as ZIP files (`actions/upload-artifact` zips by
default), so the root element is `<zip-file>`. The workflow passes one
parameter, `version`, read from `VERSION`; both configurations must declare
it. SignPath's terms require product name and product version restrictions
on every signed file; the files carry exactly these values
(`version_resource.py`, `photobox.iss`).

**"programs"** (booth and sync):

```xml
<?xml version="1.0" encoding="utf-8"?>
<artifact-configuration xmlns="http://signpath.io/artifact-configuration/v1">
  <parameters>
    <parameter name="version" required="true" />
  </parameters>
  <zip-file>
    <pe-file-set product-name="Photobox" product-version="${version}">
      <include path="PhotoboxBooth.exe" original-filename="PhotoboxBooth.exe" />
      <include path="PhotoboxSync.exe" original-filename="PhotoboxSync.exe" />
      <for-each>
        <authenticode-sign />
      </for-each>
    </pe-file-set>
  </zip-file>
</artifact-configuration>
```

**"setup"**:

```xml
<?xml version="1.0" encoding="utf-8"?>
<artifact-configuration xmlns="http://signpath.io/artifact-configuration/v1">
  <parameters>
    <parameter name="version" required="true" />
  </parameters>
  <zip-file>
    <pe-file path="Photobox-Setup-${version}.exe" product-name="Photobox" product-version="${version}">
      <authenticode-sign />
    </pe-file>
  </zip-file>
</artifact-configuration>
```

`company-name="Till Heidrich"` and
`copyright="Copyright (C) Till Heidrich. AGPL-3.0-or-later"` would also
match and can be added. Not verified without a SignPath account: whether
SignPath compares `product-version` with the text version (`0.9.1`, what the
files carry as text) or with the four-number version (`0.9.1.0`). If a
request fails on it, the message names the value SignPath read.

## Signing policy and origin

* Trusted build system: the predefined "GitHub.com", added to the
  organisation and linked to the project (SignPath documentation,
  "Trusted Build Systems → GitHub"). The SignPath GitHub App is optional
  (audit log evaluation).
* "Verify origin" on. All jobs of this workflow run on GitHub-hosted runners,
  which SignPath checks for open-source projects.
* Allowed branches: the workflow signs builds of tags `vX.Y.Z` and of runs
  started by hand. Which ref name SignPath shows for a tag build is not
  verified here; set the restriction after the first request shows it.
* Approval: one approver, Till Heidrich (see the policy). Every request is
  approved by hand.
* Optional pipeline policy, pasted as an internal definition:

  ```yaml
  github-build-policies:
    version: '1.0'
    disallow_reruns: false
    runners:
      require_github_hosted: true
  ```

## What could not be checked here

* No Windows build ran for this change, on a runner or anywhere else. The
  workflow was checked with `actionlint` and the static tests in
  `tests/test_windows_release_workflow.py`, the version resource with
  `tests/test_windows_version_resource.py`, the guard against real
  PyInstaller programs built on macOS (same archive format).
* No SignPath account: the artifact configurations above follow the
  documentation, they were not run.
