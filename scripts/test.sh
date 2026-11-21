#!/bin/sh
# Run the full suite in a fresh database alongside the local Compose demo.
set -eu
test_db="merchant_test_$(date +%s)_$$"
docker compose exec -T db createdb -U merchant "$test_db"
trap 'docker compose exec -T db dropdb -U merchant --if-exists "$test_db"' EXIT
test_url="postgresql+psycopg://merchant:demo-local@db:5432/$test_db"
docker compose run --rm --no-deps -T -e "DATABASE_URL=$test_url" -e "TEST_DATABASE_URL=$test_url" \
  api sh -c 'alembic upgrade head && python -m pytest -q -p no:cacheprovider'
