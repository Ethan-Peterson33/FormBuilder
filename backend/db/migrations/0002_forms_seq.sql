-- 0002_forms_seq: insertion order for forms + migration checksums.
--
-- * forms.seq: GET /forms lists forms in creation order (the order the JSON
--   file had). Existing rows are numbered by (created_at, id) so the current
--   order is kept; new rows get the next identity value.
-- * schema_migrations.checksum: lets `python -m db.migrate` detect a migration
--   file that was edited after it had been applied.
--
-- Written to be safe on both a plain 0001 database and on one whose 0001 was
-- created from a build that already contained forms.seq: every step checks
-- the catalog first.

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'forms' AND column_name = 'seq'
    ) THEN
        ALTER TABLE forms ADD COLUMN seq bigint;

        UPDATE forms AS f
           SET seq = numbered.rn
          FROM (SELECT id, row_number() OVER (ORDER BY created_at, id) AS rn FROM forms) AS numbered
         WHERE f.id = numbered.id;

        ALTER TABLE forms ALTER COLUMN seq SET NOT NULL;
        ALTER TABLE forms ALTER COLUMN seq ADD GENERATED ALWAYS AS IDENTITY;

        -- Continue numbering after the back-filled rows.
        PERFORM setval(
            pg_get_serial_sequence('forms', 'seq'),
            COALESCE((SELECT max(seq) FROM forms), 0) + 1,
            false
        );
    END IF;
END
$$;

COMMENT ON COLUMN forms.seq IS 'Insertion order; GET /forms lists forms by it.';

DROP INDEX IF EXISTS forms_active_name_idx;
CREATE INDEX IF NOT EXISTS forms_active_seq_idx ON forms (seq) WHERE deleted_at IS NULL;

ALTER TABLE schema_migrations ADD COLUMN IF NOT EXISTS checksum text;
COMMENT ON COLUMN schema_migrations.checksum IS 'sha256 of the migration file when applied by db.migrate; NULL when applied another way.';

INSERT INTO schema_migrations (version) VALUES ('0002_forms_seq');

COMMIT;
