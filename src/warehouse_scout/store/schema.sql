-- Scout's own tables. Applied by store.snapshot.ensure_schema(); every
-- statement is safe to run again.

-- One row per table, view or function found in the source warehouse.
CREATE TABLE IF NOT EXISTS scout.schema_object (
    object_name text PRIMARY KEY,   -- as the source catalog spells it (upper case)
    object_type text NOT NULL CHECK (object_type IN ('TABLE', 'VIEW', 'FUNCTION')),
    comment     text,
    row_count   bigint,             -- tables only
    definition  text                -- view query or function source; NULL for tables
);

CREATE TABLE IF NOT EXISTS scout.schema_column (
    object_name text NOT NULL REFERENCES scout.schema_object ON DELETE CASCADE,
    position    integer NOT NULL,
    column_name text NOT NULL,
    data_type   text NOT NULL,      -- e.g. NUMBER(7,2), VARCHAR2(4000), DATE
    nullable    boolean NOT NULL,
    PRIMARY KEY (object_name, position)
);

-- The query history exported from the source.
CREATE TABLE IF NOT EXISTS scout.query_log (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at timestamp NOT NULL,
    user_name  text NOT NULL,
    sql        text NOT NULL,
    elapsed_ms integer NOT NULL
);

CREATE INDEX IF NOT EXISTS query_log_started_at ON scout.query_log (started_at);

-- Searchable chunks of the snapshot with their embedding vectors.
-- `embedding` has no fixed length so different embedders can be stored;
-- `embedder` says which model produced each vector, and a search only
-- compares vectors of the same embedder.
CREATE TABLE IF NOT EXISTS scout.chunk (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    embedder    text NOT NULL,
    kind        text NOT NULL CHECK (kind IN ('table', 'view', 'function', 'query')),
    object_name text NOT NULL,
    content     text NOT NULL,
    metadata    jsonb NOT NULL DEFAULT '{}',
    embedding   vector NOT NULL,
    UNIQUE (embedder, kind, object_name)
);
