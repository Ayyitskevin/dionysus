# Dionysus Backup And Restore

Dionysus stores production state in SQLite. Backups cover the application
database only: organizations, users, password hashes, member rows, invite state,
content packs, share tokens, subscriptions, Stripe IDs, jobs, and audit events.

Backups do not include `.env`, Cloudflare tunnel credentials, service units, logs,
or local virtualenv files. Keep env files backed up separately and never commit
them.

## Create A Verified Backup

Run the CLI from the Flow checkout with the same environment used by the service:

```bash
cd /home/kevin-lee/ai-workspace/dionysus
set -a
. /home/kevin-lee/ai-workspace/dionysus/.env
set +a
.venv/bin/python -m app.cli backup /home/kevin-lee/dionysus-backups
```

Expected output shape:

```text
backup  /home/kevin-lee/dionysus-backups/dionysus-YYYYMMDD-HHMMSSZ.db
restore_check  ok  integrity=ok  migrations=5  tables=...
```

The command uses SQLite's backup API, so committed WAL data is included even
while the service is running. The backup directory is set to `0700`, and each
backup file is written as `0600`.

## Verify An Existing Backup

```bash
cd /home/kevin-lee/ai-workspace/dionysus
set -a
. /home/kevin-lee/ai-workspace/dionysus/.env
set +a
.venv/bin/python -m app.cli verify-backup /home/kevin-lee/dionysus-backups/<backup>.db
```

Verification restores the backup into a temporary SQLite DB, then checks:

- `PRAGMA integrity_check` returns `ok`.
- `schema_migrations` exists.
- Every migration file currently in `migrations/` is present in the backup.

## Restore Production

This is intentionally manual because restore replaces production state.

1. Pick and verify the backup with `verify-backup`.
2. Stop the app:

   ```bash
   systemctl --user stop dionysus.service
   ```

3. Preserve the current DB before replacing it:

   ```bash
   mkdir -p /home/kevin-lee/dionysus-backups/restore-preimage
   preimage="/home/kevin-lee/dionysus-backups/restore-preimage/dionysus-pre-restore-$(date -u +%Y%m%d-%H%M%SZ).db"
   cp "$DIONYSUS_DATA_DIR/dionysus.db" "$preimage"
   chmod 600 "$preimage"
   ```

4. Copy the verified backup into place:

   ```bash
   cp /home/kevin-lee/dionysus-backups/<backup>.db "$DIONYSUS_DATA_DIR/dionysus.db"
   chmod 600 "$DIONYSUS_DATA_DIR/dionysus.db"
   ```

5. Start and smoke:

   ```bash
   systemctl --user start dionysus.service
   systemctl --user is-active dionysus.service
   curl -fsS http://127.0.0.1:8450/healthz
   curl -fsS http://127.0.0.1:8450/readiness
   ```

6. Check a signed owner session and the Mise bridge before considering the
   restore complete.

## Restore Drill

For a non-production drill, never overwrite the live DB. Verify the artifact and
then run a temporary data directory:

```bash
tmpdir=$(mktemp -d)
cp /home/kevin-lee/dionysus-backups/<backup>.db "$tmpdir/dionysus.db"
DIONYSUS_DATA_DIR="$tmpdir" .venv/bin/python -m app.cli verify-backup "$tmpdir/dionysus.db"
```

Delete the temporary directory after inspection.
