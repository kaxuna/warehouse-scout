#!/bin/bash
# Runs once, on first start of an empty Oracle volume, after the image has
# created the schema owner (APP_USER) in the FREEPDB1 pluggable database.
#
# Creates the read-only account the agent connects with. It can read every
# table in the TPC-DS schema — including tables created later — and nothing else.
set -euo pipefail

sqlplus -s / as sysdba <<SQL
WHENEVER SQLERROR EXIT SQL.SQLCODE
ALTER SESSION SET CONTAINER = FREEPDB1;

CREATE USER ${ORACLE_RO_USER} IDENTIFIED BY "${ORACLE_RO_PASSWORD}";
GRANT CREATE SESSION TO ${ORACLE_RO_USER};

-- Schema-level privilege (Oracle 23ai): covers present and future tables.
GRANT SELECT ANY TABLE ON SCHEMA ${APP_USER} TO ${ORACLE_RO_USER};

-- The loader needs room to store data.
ALTER USER ${APP_USER} QUOTA UNLIMITED ON USERS;
EXIT;
SQL
