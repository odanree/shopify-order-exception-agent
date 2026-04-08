# Learnings

Debugging surprises, non-obvious gotchas, and operational lessons. Complements ADRs
(which capture *decisions*) by capturing *things that burned time* so they don't repeat.

---

## L001 — Introducing Alembic to a live database bootstrapped with `create_all`

**Date:** 2026-04-08  
**Area:** Database migrations / Alembic

### What happened

The project originally used `Base.metadata.create_all()` in `init_db()` to create tables at
startup. When Alembic was introduced later, running `alembic upgrade head` failed with:

```
sqlalchemy.exc.ProgrammingError: relation "audit_logs" already exists
```

Attempting to stamp the existing DB with `alembic stamp head` via `docker compose run` also
failed — the env var override in `env.py` (`os.getenv("DATABASE_URL")`) was not taking effect
because the container image was stale (built before the `env.py` change was committed).

A second failure: `alembic_version` was manually created by the `postgres` superuser, so
`portfolio_user` got `permission denied for table alembic_version` on the next startup.

### Root causes

1. **`docker compose restart` reuses the existing image.** Code changes are not picked up until
   `docker compose up -d --build`. Restart ≠ rebuild.
2. **`docker compose run` uses the service image**, which may be stale if not rebuilt after
   pushing new commits. Use `--build` or rebuild first.
3. **Tables created via `create_all` are invisible to Alembic** — it has no record that
   migration `001` was already applied. The DB must be stamped before Alembic can take over.
4. **Manually created DB objects default to the creating role's ownership.** Running psql as
   `postgres` to create `alembic_version` means `portfolio_user` can't read it.

### Fix (one-time for existing DBs)

```sql
-- Run as postgres superuser
CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) PRIMARY KEY);
INSERT INTO alembic_version VALUES ('002') ON CONFLICT DO NOTHING;
GRANT ALL ON TABLE alembic_version TO portfolio_user;
```

Then rebuild (not restart) the container. Alembic will find the version table, see it's
already at `002`, and run no migrations.

### Prevention going forward

- **New projects:** Add Alembic from the start. Never use `create_all` in production.
- **Existing projects:** When adding Alembic mid-life, stamp first via SQL, then rebuild.
- **Always `--build`** when deploying code changes: `docker compose up -d --build <service>`.
- **Grant to app user immediately** when creating any table manually: `GRANT ALL ON TABLE ... TO portfolio_user`.
- **Use `docker exec <running-container>`** rather than `docker compose run` when you need
  the exact environment the live service has, including already-resolved env vars.

---

## L002 — `docker compose run` env vars can be shadowed by service definition

**Date:** 2026-04-08  
**Area:** Docker / deployment

### What happened

Passing `-e DATABASE_URL=...` to `docker compose run` appeared to have no effect — the
container still used the value from the `docker-compose.yml` `environment:` block.

### Root cause

`docker compose run -e KEY=value` merges with the service's environment. If `KEY` is already
defined in `environment:` (or resolved from `.env`), the service definition wins.

### Fix

Either unset the key in `docker-compose.yml` for the run, or use
`docker exec <already-running-container>` which inherits the live service environment directly.
