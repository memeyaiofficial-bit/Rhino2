# PostgreSQL deployment checklist

## Required

1. Create PostgreSQL 16+ database/user.
2. Set `DATABASE_URL=postgresql+psycopg://...`.
3. Set a long random `APP_SECRET`.
4. Run `python scripts/seed.py` for a new database, or run `migrations.sql` before upgrading an existing Black Rhino database.
5. Put the FastAPI app behind HTTPS. Set `SESSION_HTTPS_ONLY=1`.
6. Open the POS while connected on every till once, then keep the browser/site available so the service worker and IndexedDB snapshot are populated.

## Multi-till layout

Use one PostgreSQL instance and one central Black Rhino application server. Each till browser has its own offline queue. All queues synchronize to the same PostgreSQL ledger. Do not run a separate SQLite server database per till.

## Offline limits

Offline mode is intended for short network outages. It uses the last synchronized inventory snapshot. The authoritative stock check happens in PostgreSQL when a queued sale synchronizes. Conflicts are rejected instead of allowing negative inventory.

## Backups

Back up PostgreSQL regularly and test restoration. Do not rely on the browser's IndexedDB as a backup; it is a temporary synchronization queue, not the accounting ledger.
