# The daily maintenance run

Retention periods, the NAS offload and deletions on the NAS only happen when
`app/maintenance.py` runs. Until 11.11.2026 that needed a cron job, and the
production gallery had none: "30 days" was a field in a form (gap L3 in
`business_logic/index.md`).

## What runs

`python -m app.maintenance` (or `--dry-run`) does four things and carries
on if one of them fails:

0. write a backup (`backup.scheduled`, see "Backups" below) — first, so
   that retention and the offload act on a state that is already saved,
1. offload originals to the NAS (`storage.offload_pending`) — an original is
   deleted locally only after its copy was read back from the target and,
   by default, once a backup holds it,
2. delete on the NAS what was deleted here (`storage.process_remote_deletions`),
3. empty galleries whose retention has passed, with a line in the deletion
   log (`retention.sweep`).

## Without a scheduler: the gallery starts it itself

Under a real server (Gunicorn in the Docker image, or the Werkzeug dev
server) every worker looks at most every ten minutes, on the next request,
whether the last run is older than `PHOTOBOX_MAINTENANCE_HOURS` (default
`24`). One atomic `UPDATE` on the `settings` table decides which worker takes
it; the others, and a cron job running at the same time, see the claim and
do nothing. The run itself happens in a background thread, so no guest
waits for it. The container's health check (`/healthz` every 30 s) is
enough traffic to trigger it.

The dashboard shows when the run last finished (UTC) and turns red when that
is more than 36 hours ago or has never happened.

| Variable | Default | Meaning |
|----------|---------|---------|
| `PHOTOBOX_MAINTENANCE` | `on` | `off` stops the self-start; only an external scheduler runs it |
| `PHOTOBOX_MAINTENANCE_HOURS` | `24` | minimum hours between two runs (self-start and claim) |

Limits: if the worker is killed while the run is in progress, the claim
stays and the next attempt comes one interval later. The dashboard shows it.

## With Coolify: a scheduled task (recommended in addition)

A scheduled task runs at a fixed time at night and does not depend on
traffic. Both together still run once a day, because the command records its
run and the self-start sees it.

1. Coolify → the gallery application → **Scheduled Tasks** → **Add**.
2. Name: `maintenance`. Command: `python -m app.maintenance`.
   Frequency: `30 3 * * *` (03:30 every night, in the time zone set for
   the server in Coolify). Container: the gallery's web container.
3. Save, then **Run now** once and check the log: "Sicherung", "Ausgelagert",
   "Auf dem Auslagerungsziel gelöscht", "Aufbewahrung".
4. The dashboard should now show the run.

Elsewhere (plain Docker, systemd): the same command in the container, e.g.
`docker compose exec web python -m app.maintenance` from the host's crontab.

The `pruner` service in `docker-compose.yml` is older and only calls part of
this; with the self-start or a scheduled task it is not needed.

## Backups

Every run writes one archive with `python -m app.backup` logic into
`PHOTOBOX_BACKUP_DIR` (default `DATA_DIR/backups`): the database (SQLite
backup API, consistent while the gallery runs), the media, operator
uploads, `.secret_key` and the inbox's host keys, and a manifest with a
SHA-256 for every file. The archive is **confidential** (password hashes,
device keys, mail account): mode 600, folder mode 700, never served by
`/data`. A backup on the same disk protects against mistakes and failed
upgrades, not against losing the server — for that, set up the offload
target (the backup is then copied there as well) or copy
`DATA_DIR/backups` off the server yourself.

| Variable | Default | Meaning |
|----------|---------|---------|
| `PHOTOBOX_BACKUP` | `on` | `off`: the run writes no backup (the dashboard keeps warning) |
| `PHOTOBOX_BACKUP_DIR` | `DATA_DIR/backups` | where archives go |
| `PHOTOBOX_BACKUP_KEEP` | `7` | archives kept; the newest one with media is kept in addition |
| `PHOTOBOX_BACKUP_MEDIA_DAILY_MB` | `500` | media up to this size go into every backup; above it (or with `0`) only weekly, the other days database + settings + uploads |
| `PHOTOBOX_BACKUP_MEDIA_EVERY_DAYS` | `7` | interval for the backup with media when they are large |
| `PHOTOBOX_BACKUP_OFFSITE` | `auto` | copy each backup to the offload target (SFTP/WebDAV) if one is set up; `off` never |
| `PHOTOBOX_OFFLOAD_REQUIRE_BACKUP` | `on` | delete an offloaded original locally only once a backup holds exactly these bytes; with `PHOTOBOX_BACKUP=off` set this to `off` too, or nothing is offloaded |

By hand: `python -m app.backup create [--out DIR] [--no-media]`, `verify
ARCHIVE`, `list`, `prune --keep N`, `restore ARCHIVE --into DIR [--force]`.
Deleted galleries live on in backups until those are pruned — with the
defaults about a week, two at most for the weekly copy with media.
The dashboard warns when the newest backup is older than 48 hours, and
shows a failed copy to the offload target.
