#!/bin/sh
set -eu

SQL_FILE=/bootstrap/metabase_envdata.sql

if [ ! -s "$SQL_FILE" ]; then
  echo "Missing $SQL_FILE. Add the supplied environment SQL before starting Metabase." >&2
  exit 1
fi

echo "Restoring supplied Metabase environment baseline..."
pg_restore --clean --create --if-exists --exit-on-error --dbname=postgres "$SQL_FILE"
echo "Metabase environment restore complete."
