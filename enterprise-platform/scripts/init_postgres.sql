CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE INDEX IF NOT EXISTS idx_schema_embedding ON schema_metadata USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_chunk_embedding ON document_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_chunk_doc ON document_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunk_content_fts ON document_chunks USING gin (to_tsvector('simple', content));
CREATE INDEX IF NOT EXISTS idx_schema_fts ON schema_metadata USING gin (to_tsvector('simple', description));
