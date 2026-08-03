-- Runs once on a FRESH postgres volume (docker-entrypoint-initdb.d).
-- Prefect server needs its own database (see PREFECT_API_DATABASE_CONNECTION_URL
-- in docker-compose.yml); POSTGRES_DB only creates the `isbe` facts DB.
-- Existing deployments must run this once by hand:
--   docker exec isbe-postgres psql -U isbe -c "CREATE DATABASE prefect;"
CREATE DATABASE prefect;
