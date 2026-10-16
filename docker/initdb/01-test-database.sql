-- Runs once when the docker-compose PostgreSQL volume is first created.
-- The integration suite needs a second, disposable database.
CREATE DATABASE newtonite_test OWNER newtonite;
