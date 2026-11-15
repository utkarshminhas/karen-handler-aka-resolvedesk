#!/bin/bash
# Creates the two login roles that enforce zone ownership at the database level:
#   store_ops      -> the store-ops MCP server only (store zone + read policy vectors)
#   resolvedesk_app -> FastAPI / LangGraph (app, vectors, graph zones; NO store access)
# Runs once, on first container start, as the admin superuser.
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v store_pw="$STORE_OPS_DB_PASSWORD" -v app_pw="$APP_DB_PASSWORD" <<'EOSQL'
CREATE ROLE store_ops LOGIN PASSWORD :'store_pw';
CREATE ROLE resolvedesk_app LOGIN PASSWORD :'app_pw';
EOSQL
