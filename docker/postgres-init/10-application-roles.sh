#!/bin/sh
set -eu

# The official image executes *.sh init hooks with every POSTGRES_* variable
# available. Passing them as psql variables preserves quoting for passwords.
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 \
  --set migration_user="$POSTGRES_MIGRATION_USER" \
  --set migration_password="$POSTGRES_MIGRATION_PASSWORD" \
  --set app_user="$POSTGRES_APP_USER" \
  --set app_password="$POSTGRES_APP_PASSWORD" <<'SQL'
CREATE ROLE :"migration_user" LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD :'migration_password';
CREATE ROLE :"app_user" LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD :'app_password';
ALTER DATABASE :"DBNAME" OWNER TO :"migration_user";
GRANT CONNECT ON DATABASE :"DBNAME" TO :"app_user";
SQL
