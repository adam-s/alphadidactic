# Drizzle Migration Traps

I like Drizzle. Type-safe queries, schema-as-code, readable generated SQL. But the migration system has sharp edges that don't show up until you're in CI, in Docker, or deploying to production. I hit all of them. The journey from the first deploy to a reliable migration pipeline took six attempts.

## push --force

The first deploy used `drizzle-kit push --force`. It compares your TypeScript schema to the live database and applies changes directly — no migration file, no journal entry, no review step. This felt convenient.

Rename a column and Drizzle doesn't see a rename. It sees one column to drop and one to create. Your data is gone. I lost a column this way — not in production, but close enough to make the lesson stick. The `--force` flag exists to skip confirmation prompts, which means it skips the only safety check between your schema diff and a destructive DDL operation.

## Interactive prompts that hang forever

After abandoning `push`, I switched to `drizzle-kit migrate`. It runs SQL migration files from the journal. But it prompts interactively — "Are you sure?" — when it encounters anything it considers destructive. In Docker, in CI, in any non-interactive context, this blocks on stdin forever. The container sits there waiting for input that will never come.

I tried six fixes in succession:
1. Piping `yes` into the command
2. Setting `CI=true`
3. `TERM=dumb`
4. Wrapping in `expect`
5. Using `script -c`
6. Running under `unbuffer`

None worked reliably. The Drizzle CLI wasn't designed for non-interactive environments.

## The programmatic API

The actual fix was dropping the CLI entirely and using Drizzle's programmatic migration runner:

```typescript
import { drizzle } from 'drizzle-orm/postgres-js';
import { migrate } from 'drizzle-orm/postgres-js/migrator';
import postgres from 'postgres';

const client = postgres(process.env.DATABASE_URL!, { max: 1 });
const db = drizzle(client);

try {
  await migrate(db, { migrationsFolder: './drizzle' });
  console.log('Migrations complete.');
} catch (error) {
  console.error('Migration failed:', error);
  process.exit(1);
} finally {
  await client.end();
}
```

No prompts. Reads the journal, applies pending migrations, exits. The `max: 1` connection is deliberate — migrations need exclusive access, not a pool. And `client.end()` is required or the script hangs forever: postgres.js maintains a connection pool with active handles, and Node's event loop won't exit while the pool holds open TCP sockets.

In production, this runs as a Docker init container before the API starts. The API container depends on the migration container completing successfully.

## The orphan migration

Drizzle tracks migrations in `drizzle/meta/_journal.json`. The `drizzle-kit generate` command creates both the SQL file and the journal entry atomically. But I hand-wrote a migration — a `jsonb` column addition that `generate` didn't pick up — and forgot to register it in the journal.

The file existed on disk. It was committed to git. CI ran "apply migrations" and reported success. The migration runner said "all migrations applied" because the journal didn't know the file existed. The column was missing for two weeks. CI passed every time.

The rule: never hand-write migration files without adding the journal entry. Or better, use `drizzle-kit generate` and edit the generated SQL afterward.

## Regenerating from scratch

Early migrations were incomplete — they'd been created piecemeal as features were added, and some tables were missing. The schema had 37 tables but the migrations only covered 30. The fix was to regenerate the entire initial migration from scratch, covering all 37 tables in one coherent SQL file. This meant dropping and recreating the journal, which is fine for a project that hasn't shipped yet but would be terrifying in production.

## TimescaleDB DDL never goes in Drizzle migrations

Drizzle generates standard PostgreSQL DDL. It knows nothing about `create_hypertable()`, `add_compression_policy()`, or continuous aggregates. Put those in a Drizzle migration and three things break: `drizzle-kit generate` produces nonsensical diffs next time (it sees hypertable metadata it doesn't understand), the migrations aren't idempotent (running `create_hypertable` twice throws), and test databases require TimescaleDB even when the tests don't touch time-series features.

The directory split:

```
drizzle/                     ← Drizzle owns this
  0000_initial.sql           ← CREATE TABLE, ALTER TABLE, indexes
  0001_add_events.sql        ← Standard PostgreSQL DDL
  meta/_journal.json         ← Migration tracking

scripts/db-setup/            ← You own this
  setup-hypertables.ts       ← create_hypertable(), if_not_exists
  enable-compression.sql     ← ALTER TABLE SET compress, policies
  create-aggregates.sql      ← Continuous aggregates, refresh policies
```

Twenty-four Drizzle migrations. All standard DDL. Zero TimescaleDB SQL in any of them. The separation is deliberate and makes CI possible — CI uses plain `postgres:16` without the TimescaleDB extension, and all 24 migrations apply cleanly.

## Lazy connections

One more trap that isn't specific to migrations but bites hardest in a monorepo with a shared database package. If importing your database module opens a connection at load time, every file that transitively imports it — test files, CLI tools, scripts that share types — needs `DATABASE_URL` set or the import throws immediately.

```typescript
// Lazy initialization behind a Proxy — only connects on first query
export const db = new Proxy({} as DbType, {
  get(_target, prop) {
    return (getDb() as Record<string | symbol, unknown>)[prop];
  },
});
```

Import freely. The connection opens only when you actually call a method on `db`. Tests that mock the database never connect. CLI tools that share types with the API don't need database credentials.

## The pattern

`strict: true` in `drizzle.config.ts` makes `drizzle-kit generate` fail loudly on destructive diffs instead of prompting. The programmatic `migrate()` function applies migrations non-interactively. Every SQL file is tracked in the journal. TimescaleDB features live in separate scripts that run after migrations. Each concern has a clear owner and a clear boundary.

Drizzle is a good ORM with a migration system built for humans sitting at terminals. In automated environments — Docker, CI, production deploys — use the programmatic API, enforce strict mode, and keep anything the ORM doesn't understand out of its pipeline entirely.

---

*TimescaleDB + Drizzle series:*
1. [The Two-Layer Trick](./01-the-two-layer-trick.md)
2. [Choosing Chunk Intervals](./02-choosing-chunk-intervals.md)
3. [Compression as Survival](./03-compression-as-survival.md)
4. [Continuous Aggregates](./04-continuous-aggregates.md)
5. [Bulk Ingestion](./05-bulk-ingestion.md)
6. [Query Patterns That Matter](./06-query-patterns-that-matter.md)
7. **Drizzle Migration Traps** *(you are here)*
8. [The Things That Bite in Production](./08-production-lessons.md)
