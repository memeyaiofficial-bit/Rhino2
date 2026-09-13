-- Black Rhino PostgreSQL migration for offline synchronization.
-- Run against an existing production database before starting the new build.
BEGIN;
ALTER TABLE sales ADD COLUMN IF NOT EXISTS client_id VARCHAR(64);
ALTER TABLE sales ADD COLUMN IF NOT EXISTS shift_id INTEGER;
CREATE UNIQUE INDEX IF NOT EXISTS ix_sales_client_id ON sales(client_id) WHERE client_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_sales_shift_id ON sales(shift_id);
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_sales_shift_id_shifts') THEN
    ALTER TABLE sales ADD CONSTRAINT fk_sales_shift_id_shifts FOREIGN KEY (shift_id) REFERENCES shifts(id);
  END IF;
END $$;
COMMIT;
