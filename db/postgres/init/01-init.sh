#!/bin/bash
# Runs once, on first start of an empty Postgres volume.
#
#   scout   schema — Scout's own tables (snapshots, embeddings, LLM call log, eval history)
#   target  schema — where migrated tables land; the agent may only read it
#
# The read-only role is the guardrail: the database refuses writes from the
# agent, regardless of what SQL the model produces.
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
     -v ro_user="$POSTGRES_RO_USER" \
     -v ro_password="$POSTGRES_RO_PASSWORD" \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS scout;
CREATE SCHEMA IF NOT EXISTS target;

CREATE ROLE :"ro_user" LOGIN PASSWORD :'ro_password';

GRANT CONNECT ON DATABASE :"DBNAME" TO :"ro_user";
GRANT USAGE ON SCHEMA target TO :"ro_user";
GRANT SELECT ON ALL TABLES IN SCHEMA target TO :"ro_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA target GRANT SELECT ON TABLES TO :"ro_user";

-- Belt and braces: even a SELECT cannot run away with the server.
ALTER ROLE :"ro_user" SET default_transaction_read_only = on;
ALTER ROLE :"ro_user" SET statement_timeout = '15s';
ALTER ROLE :"ro_user" SET search_path = target;
SQL
