#!/bin/sh
set -eu

SQL_FILE=/bootstrap/metabase_envdata.sql
MARKER_TABLE=rollout_studio_seed_marker

if [ ! -s "$SQL_FILE" ]; then
  echo "Missing $SQL_FILE. Add the supplied environment SQL before starting Metabase." >&2
  exit 1
fi

SEED_ID=$(sha256sum "$SQL_FILE" | awk '{print $1}')
database_exists=$(psql --dbname=postgres --tuples-only --no-align --command \
  "SELECT 1 FROM pg_database WHERE datname = 'root_db'")

write_marker() {
  psql --dbname=root_db --set=ON_ERROR_STOP=1 --command \
    "BEGIN; CREATE TABLE IF NOT EXISTS public.${MARKER_TABLE} (seed_id text PRIMARY KEY, seeded_at timestamptz NOT NULL DEFAULT now()); INSERT INTO public.${MARKER_TABLE} (seed_id) VALUES ('${SEED_ID}') ON CONFLICT (seed_id) DO NOTHING; COMMIT;"
}

if [ "$database_exists" = "1" ]; then
  marker_exists=$(psql --dbname=root_db --tuples-only --no-align --command \
    "SELECT CASE WHEN to_regclass('public.${MARKER_TABLE}') IS NULL THEN 0 ELSE 1 END")
  if [ "$marker_exists" = "1" ]; then
    marker_seed=$(psql --dbname=root_db --tuples-only --no-align --command \
      "SELECT seed_id FROM public.${MARKER_TABLE} LIMIT 1")
    if [ "$marker_seed" != "$SEED_ID" ]; then
      echo "root_db was seeded from a different archive; refusing to overwrite it." >&2
      exit 1
    fi
    echo "Supplied Metabase environment is already seeded."
    exit 0
  fi

  # Adopt volumes created before the marker existed only when they clearly
  # contain the supplied Metabase baseline. Never overwrite an ambiguous DB.
  required_tables=$(psql --dbname=root_db --tuples-only --no-align --command \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name IN ('core_user', 'databasechangelog', 'metabase_database', 'report_card')")
  baseline_rows=0
  if [ "$required_tables" = "4" ]; then
    baseline_rows=$(psql --dbname=root_db --tuples-only --no-align --command \
      "SELECT CASE WHEN EXISTS (SELECT 1 FROM public.core_user WHERE lower(email) = 'daksh@deeptune.com') AND EXISTS (SELECT 1 FROM public.databasechangelog) AND EXISTS (SELECT 1 FROM public.metabase_database) THEN 1 ELSE 0 END")
  fi
  if [ "$baseline_rows" = "1" ]; then
    write_marker
    echo "Existing supplied Metabase baseline was marked as seeded."
    exit 0
  fi

  echo "root_db already exists without a valid seed marker; refusing to overwrite it." >&2
  echo "Use an explicit Docker volume reset only if you intend to discard that database." >&2
  exit 1
fi

echo "Restoring supplied Metabase environment baseline..."
pg_restore --create --exit-on-error --dbname=postgres "$SQL_FILE"
write_marker
echo "Metabase environment restore complete."
