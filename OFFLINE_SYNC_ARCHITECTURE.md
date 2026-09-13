# Black Rhino PostgreSQL + Offline Synchronization

## Source of truth
PostgreSQL is the authoritative database. The browser never becomes the master inventory database.

## Connected operation
1. Cashier logs in against the FastAPI server.
2. `/api/offline/bootstrap` provides the current active catalogue, stock snapshot and open shift.
3. The POS keeps that snapshot in IndexedDB.
4. Online sales are committed directly to PostgreSQL.

## Offline operation
1. The service worker supplies the cached POS shell.
2. The POS reads the latest successful bootstrap from IndexedDB.
3. Each offline sale gets a cryptographically random `client_id` UUID.
4. The local stock snapshot is decremented immediately so the same till cannot repeatedly sell the same cached units.
5. The sale is placed in an IndexedDB queue with `PENDING` status.
6. A local 80mm receipt is printable and explicitly marked `PENDING SYNC`.

## Reconnection
1. Browser connectivity returns.
2. The queue is posted to `/api/sync/sales`.
3. PostgreSQL checks the idempotency key, cashier identity, role, shift and current inventory.
4. Inventory rows are locked with `FOR UPDATE`.
5. The sale, payment rows, stock movements and audit entry commit atomically.
6. Successful queue items are removed.
7. A stock/shift/security conflict is retained as `REJECTED` for manager review instead of being silently retried forever.

## Why this is safer than a second server database
There is no competing SQLite server database to reconcile. Offline state is explicitly a client queue, while PostgreSQL remains the single authoritative ledger.

## Important business rule
Offline inventory is a last-known snapshot. If another till sells the same item while this till is offline, the server rejects the conflicting offline sale when it synchronizes. This avoids negative inventory and prevents silent stock corruption.
