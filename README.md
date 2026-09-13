# Black Rhino Liquor Store POS

A PostgreSQL-first liquor-store point-of-sale system with browser-level offline synchronization:

- Admin, Manager and Cashier roles
- PostgreSQL as the authoritative server database
- Browser IndexedDB offline queue with automatic synchronization
- Products, variants, categories and brands
- Inventory and stock movement audit trail
- POS sales with cash/card/M-Pesa/manual payment records
- Cashier shifts and cash drawer reconciliation
- Customers
- Suppliers, purchases and goods receiving
- Expenses
- Discounts with role-based limits
- Dashboard and sales/inventory reports
- Audit log
- Printable 80mm receipts
- Barcode/SKU lookup
- Low-stock and out-of-stock indicators
- Configurable store settings
- Supplied Black Rhino logo
- No external CDN or runtime internet dependency

## Quick start

Python 3.10+ is recommended.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
python scripts/seed.py
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

Default accounts created by the seed script:

| Role | Username | Password |
|---|---|---|
| Admin | admin | admin123 |
| Manager | manager | manager123 |
| Cashier | cashier | cashier123 |

Change these credentials immediately in a real deployment.

## Production database and offline synchronization

**PostgreSQL is the only server database. SQLite is not used by the production application.** Set `DATABASE_URL` to a PostgreSQL connection string before starting the server.

The POS is offline-capable at the **till/browser layer**. While connected, the browser downloads the current product/stock snapshot and the cashier's open-shift information into IndexedDB. If the PostgreSQL-backed application becomes unreachable, the cached POS shell can continue taking sales. Offline sales receive a client UUID and are queued locally. When connectivity returns, the browser sends the queue to `/api/sync/sales`.

The server treats `client_id` as an idempotency key, locks inventory rows in PostgreSQL during sale processing, verifies the cashier's shift and role, and commits each synchronized sale atomically. This prevents duplicate synchronization and protects stock from concurrent tills.

Important operational behavior:

- Offline inventory is a **last-synchronized snapshot**; it is not authoritative while disconnected.
- A sale that conflicts with current PostgreSQL stock is rejected during synchronization rather than creating negative stock.
- Offline receipts are marked **PENDING SYNC**. After synchronization, the sale receives its official Black Rhino receipt number.
- If a cashier's server-side shift is closed before queued sales synchronize, those sales are rejected for manager review rather than silently posted to another shift.
- Offline mode requires the POS to have been opened successfully online at least once so the product/shift snapshot is cached.

### First PostgreSQL deployment

1. Create a PostgreSQL database and user.
2. Set `DATABASE_URL`.
3. For an existing Black Rhino database, run `migrations.sql`.
4. Run `python scripts/seed.py` for a new installation.
5. Start the FastAPI application.
6. Open `/pos` while connected and keep the POS browser installed/available on the till device so the offline shell and snapshot are cached.

Docker Compose includes PostgreSQL 16 with a health check. Replace all `CHANGE_THIS_*` values before production use.

## Catalogue import

The supplied stock-taking pages have been transcribed into `scripts/catalog.csv` (122 rows) and `scripts/catalog_seed.sql`. The Python seed script imports them into PostgreSQL. Size normalization is explicit: `1/2` and `HALF` become `500ml`, while `1/4` becomes `250ml`. Selling prices are loaded as provided; missing source prices remain `0` until confirmed. Opening stock is `0` because the photographed pages do not contain stock quantities.

Run `python scripts/seed.py` after PostgreSQL schema setup to import the catalogue.

## Receipt printing

After completing a sale, click **Print receipt**. The receipt is sized for 80mm thermal paper. The receipt opens as a dedicated 80mm print layout using the supplied logo. It uses the browser's normal print system, so it works with ordinary printers and thermal printers exposed to the operating system.

For direct ESC/POS printing, integrate the generated receipt payload with the printer service for the specific Windows/Linux printer model.

## Production notes

This package is a complete working starter POS, but a production deployment should additionally configure:

- HTTPS/reverse proxy
- scheduled database backups
- stronger per-user credentials
- OS/device access controls
- real Safaricom Daraja credentials if M-Pesa STK Push is required
- a tested direct thermal-printer driver if silent printing is required
- business/legal configuration such as tax rates and minimum legal purchase age

The core sale transaction uses a database transaction and writes stock movements and audit entries together.
