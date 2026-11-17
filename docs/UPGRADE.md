# Upgrading a running gallery

From the old production line (`main`, around 17.09.2026, before the backup
command existed) to this version, on Docker or Coolify. Read it once to the
end before you start; the steps that cannot be undone are marked.

What the upgrade does to the data: the schema is extended on the first
start (new tables and columns, never a destructive change; about 20 new
tables). This was rehearsed with a database written by the old code
(`tools/migration_rehearsal.py`): no row lost, a second start changes
nothing, and the old code still runs against the migrated database. The way
back you should rely on is nevertheless the backup from step 1.

## 0. What you need

A shell on the Docker host (with Coolify: the server, *Terminal* or SSH),
and the gallery's container and volume:

```bash
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}'      # find the gallery
C=<container name>
docker inspect -f '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}' "$C"
```

The line ending in `-> /data` is the data volume. Note down, before you
change anything: the commit that runs now (Coolify: *Deployments*, or the
image tag) and every environment variable of the application — above all
whether `SECRET_KEY` is set, and to what.

## 1. Back up the old version (required)

The old version has no backup command. Three ways, best first. Each gives
a **confidential** file: it holds the admin password hash, every device
key, the mail account. `chmod 600` it, copy it off the server, keep it
where only you can read it.

**a) The new backup command, run inside the old container.** It is one
file without dependencies, reads only, and does not migrate anything; the
database is taken through SQLite's backup API, so the gallery keeps running.

```bash
git show origin/ecosystem:app/backup.py > photobox_backup.py      # in a checkout of this repository
                                                                   # (until merged: origin/work/backup-restore)
docker cp photobox_backup.py "$C":/tmp/photobox_backup.py
docker exec -e DATA_DIR=/data "$C" python /tmp/photobox_backup.py create --out /data/backups
docker exec "$C" python /tmp/photobox_backup.py list --dir /data/backups
docker exec "$C" python /tmp/photobox_backup.py verify /data/backups/photobox-backup-<stamp>.tar
docker cp "$C":/data/backups/photobox-backup-<stamp>.tar .
chmod 600 photobox-backup-<stamp>.tar
```

The command checks free space first and stops with a clear message instead
of filling the disk; if space is short, add `--no-media` and back up
`storage/` separately (way c). The old gallery does not serve
`/data/backups/` (since the hotfix 07bce6e, `/data` serves only `storage/`,
`assets/`, `faq/`). After the upgrade the daily run prunes this archive
like its own after a week — the copy you took off the server is the one
that counts.

**b) Without new code: database via the backup API, the rest with tar.**
The image has no `sqlite3` program (python:3.12-slim), Python has the API:

```bash
docker exec "$C" python -c "import sqlite3; s=sqlite3.connect('file:/data/gallery.sqlite3?mode=ro', uri=True); d=sqlite3.connect('/tmp/gallery-pre-upgrade.sqlite3'); s.backup(d); print(d.execute('PRAGMA integrity_check').fetchone()[0]); d.close()"
docker cp "$C":/tmp/gallery-pre-upgrade.sqlite3 . && docker exec "$C" rm /tmp/gallery-pre-upgrade.sqlite3
docker exec "$C" tar -cf - -C /data --exclude=./cache --exclude=./gallery.sqlite3 --exclude='./gallery.sqlite3-*' . > photobox-files-pre-upgrade.tar
chmod 600 gallery-pre-upgrade.sqlite3 photobox-files-pre-upgrade.tar
```

It must print `ok`. Do not copy `gallery.sqlite3` with `cp` while the
container runs: a copy taken during a write is not a database. If the host
has `sqlite3`, `sqlite3 <volume>/gallery.sqlite3 ".backup '/root/gallery-pre-upgrade.sqlite3'"`
is equivalent.

**c) Volume copy with the container stopped** (a minute of downtime):

```bash
docker stop "$C"          # Coolify: Stop
tar -cf photobox-volume-pre-upgrade.tar -C <volume source> .
docker start "$C"         # Coolify: Start
```

## 2. Rehearse with the real backup (strongly recommended)

On your own computer, in a checkout of this version:

```bash
python tools/migration_rehearsal.py --from-backup photobox-backup-<stamp>.tar
```

It restores the archive into a temporary folder, runs the migration twice,
checks every table, page, media file, alias, share link and device key, then
starts the old code against the migrated copy. The archive is not changed,
the network is blocked during the run (the database holds real mail and
notification settings), and the report shows counts and findings only.
Exit code 0: nothing lost. Read the `Finding:` lines, especially the
retention line (step 3).

From way b or c, make an archive first:

```bash
mkdir pre && tar -xf photobox-files-pre-upgrade.tar -C pre && cp gallery-pre-upgrade.sqlite3 pre/gallery.sqlite3
python -m app.backup create --data-dir pre --out .
```

## 3. Decide before you deploy

* **Retention starts working.** The old version never deleted anything
  (there was no maintenance run, gap L3). The new one starts its daily run
  on its own, and the **first run empties every gallery whose retention
  period has passed** (period from the event date, default 30 days; the old
  form suggested 90). The rehearsal prints how many galleries that would
  be. To look first: deploy with `PHOTOBOX_MAINTENANCE=off`, then
  `docker exec "$C" python -m app.maintenance --dry-run`, extend the periods
  you want to keep in each event, and only then remove the variable. Not
  undoable once it ran — except from the backup.
* **Boxes need an assigned event.** In the old version any device key could
  upload into any event. Since the security review (S4) a box only uploads
  into its own events, and the old data does not record which box used
  which event. Before the upgrade, for every box still in use (also the old
  Windows uploader): *Admin → Devices → edit → assigned event*. Otherwise
  its uploads are refused with 403 after the upgrade.
* **New booth software before the gallery.** Seven built-in looks were
  renamed; a gallery of this version stores the new names, which an older
  booth does not know. The old Windows uploader is not affected.
* **Events saved "unpublished" in the old form are public.** The old form
  stored `published = 1` for them (column default) and the gallery showed
  them; the new code does the same. Check *Admin → Events* if that matters.
* **Everyone is logged out once** if `SECRET_KEY` was not set (see below).

## 4. Environment variables

Read from the code of this version. Nothing is required; what is missing
falls back as described.

| Variable | Default | If missing or wrong |
|----------|---------|---------------------|
| `DATA_DIR` | `./data` (`/data` in the image) | Where database and media live; must be the mounted volume. |
| `SECRET_KEY` | — | Missing or the docker-compose placeholder `change-me-in-production`: a random key is generated once in `DATA_DIR/.secret_key` (it is in every backup) and the log warns on every start. The old version signed with the placeholder itself, so after the upgrade sessions end once (admin login, gallery passwords). Shorter than 16 characters: used, with a warning. Recommended: set 32+ random characters in Coolify; if it was set before, keep the value. |
| `BASE_URL` | — | The public address (`https://…`). Used for links in mails and QR codes when *Settings → app base URL* is empty, and with `https://` the session cookie is marked Secure. Set it. Since 15.11.2026 password-reset mails and pairing codes are **refused** when neither this nor the admin setting is set and the request's host is not loopback or in `ALLOWED_HOSTS`. |
| `ALLOWED_HOSTS` | — | Comma-separated host names that may appear in reset and pairing links when no base address is set. |
| `TRUST_PROXY` | `0` (was `1` until 15.11.2026) | **Opt-in.** `1` trusts one proxy's `X-Forwarded-For/Proto/Host`. **Behind Coolify/Traefik set `TRUST_PROXY=1` before deploying this version**, or every visitor shows up with the proxy's address (login brakes then count all visitors together, logs show the proxy). Leave it `0` when the container is reachable without a proxy (the NAS variant), or the brakes could be dodged with a forged header. |
| `PHOTOBOX_MAX_UPLOAD_MB` | `450` | Hard cap for any request body (the old version had none). |
| `PHOTOBOX_GUEST_UPLOAD_MB` | `25` | Cap for guest uploads. |
| `PHOTOBOX_MAINTENANCE` | `on` | `off` stops the self-start of the daily run (see step 3). |
| `PHOTOBOX_MAINTENANCE_HOURS` | `24` | Minimum hours between two runs. |
| `PHOTOBOX_SILENCE_CHECK` | `on` | `off` stops the per-worker thread that checks boxes with "Warnen, wenn die Box nicht mehr antwortet" switched on (every 60 s); then run `python -m app.silence_alarm` from cron instead. Without any box switched on the check does nothing. |
| `PHOTOBOX_BACKUP`, `PHOTOBOX_BACKUP_DIR`, `PHOTOBOX_BACKUP_KEEP`, `PHOTOBOX_BACKUP_MEDIA_DAILY_MB`, `PHOTOBOX_BACKUP_MEDIA_EVERY_DAYS`, `PHOTOBOX_BACKUP_OFFSITE` | `on`, `DATA_DIR/backups`, `7`, `500`, `7`, `auto` | The daily backup, see [`MAINTENANCE.md`](MAINTENANCE.md#backups). |
| `PHOTOBOX_OFFLOAD_REQUIRE_BACKUP` | `on` | An offloaded original is deleted locally only once a backup holds it. |
| `PHOTOBOX_SOURCE_URL` | the project's repository | Source address on the About page (AGPL §13). A fork sets its own; anything but `http(s)://` is ignored. |
| `SOURCE_COMMIT` / `PHOTOBOX_COMMIT`, `PHOTOBOX_VERSION` | —, `VERSION` file | The commit and version shown on the About page; Coolify passes `SOURCE_COMMIT` as build argument. |
| `ADMIN_USER`, `ADMIN_PASSWORD` | `admin`, random | Only used when the database has no admin password yet — an existing installation keeps its login. |
| `ORIGINALS_BACKEND`, `ORIGINALS_SFTP_*`, `ORIGINALS_WEBDAV_*`, `ORIGINALS_OFFLOAD_AFTER_MIN` | `local`, …, `30` | Unchanged; with a target set, backups are copied there too. |
| `UMAMI_SRC`, `UMAMI_WEBSITE_ID` | — | Optional analytics script; its origin is allowed by the content security policy. |
| `TG_TOKEN`, `TG_CHAT_ID`, `DISCORD_WEBHOOK`, `QUIET_FROM`, `QUIET_TO` | — | Legacy fallbacks; the admin settings win. |

## 5. Deploy

Coolify: point the application at the new branch or image, set the
variables, *Deploy*. Plain Docker: `docker compose pull && docker compose up -d`
(or build). Then watch the log of the first start:

* no traceback from `init_db` / `init_schema`;
* `SECRET_KEY is not set …` only if you meant to rely on the generated key;
* **`Erste Anmeldung: Benutzer …` must not appear.** It means the database
  had no admin password — the gallery started on an empty volume. Stop and
  check the volume mount before anyone uses it.

## 6. Checks afterwards

1. Admin login with the old password; the dashboard shows the cards
   *Daily maintenance run* and *Backups*.
2. One gallery as a guest, one with its password, one old link (`/s/…` or
   a renamed gallery's old address).
3. One upload from a box (or the Windows uploader) into its event.
4. Maintenance: after the first run (or `docker exec "$C" python -m app.maintenance`)
   the dashboard shows the run and a backup; `docker exec "$C" python -m app.backup list`.
5. `/setup` answers 404 to a visitor:
   `curl -s -o /dev/null -w '%{http_code}\n' https://<gallery>/setup`.
6. Security headers on every answer:
   `curl -sI https://<gallery>/ | grep -iE 'content-security-policy|x-content-type-options|referrer-policy|x-frame-options'`.

## 7. The way back

If something is wrong and you need the old version back:

1. Stop the new container.
2. Put the old image or commit back (Coolify: redeploy the commit noted in
   step 0, or the branch `main`).
3. Restore the backup from step 1 into the volume — not the migrated
   database. With way a, on the host:
   `docker run --rm -v <volume>:/data -v "$PWD":/in python:3.12-slim sh -c "cd /in && python photobox_backup.py restore /in/photobox-backup-<stamp>.tar --into /data --force"`
   (`--force` backs up the current content into `/data/backups/` first).
   With way b or c: empty the volume and unpack the tar files into it, the
   database as `gallery.sqlite3`.
4. Start the old container and log in.

The old code does start against the migrated database (rehearsed: pages,
media, logins, device keys, uploads work; the new tables are ignored). That
is a way to get the site up quickly, but everything written by the new
version in the meantime (its new tables) is then only half used. Restoring
the backup is the clean way; everything uploaded between upgrade and
rollback is in the newer backups the new version wrote, not in the old one.
