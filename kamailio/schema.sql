-- Kamailio permissions tables (Postgres) — carrier source-IP allow-list.
-- The dashboard writes CIDRs into `address` (grp 1); Kamailio's permissions module
-- loads them (allow_source_address("1")) and reloads on demand.
CREATE TABLE IF NOT EXISTS version (
    id SERIAL PRIMARY KEY, table_name VARCHAR(32) NOT NULL, table_version INTEGER NOT NULL DEFAULT 0);
INSERT INTO version (table_name, table_version)
  SELECT 'address',6 WHERE NOT EXISTS (SELECT 1 FROM version WHERE table_name='address');
INSERT INTO version (table_name, table_version)
  SELECT 'trusted',6 WHERE NOT EXISTS (SELECT 1 FROM version WHERE table_name='trusted');
CREATE TABLE IF NOT EXISTS address (
    id SERIAL PRIMARY KEY, grp INTEGER NOT NULL DEFAULT 1, ip_addr VARCHAR(50) NOT NULL,
    mask INTEGER NOT NULL DEFAULT 32, port INTEGER NOT NULL DEFAULT 0, tag VARCHAR(64));
CREATE TABLE IF NOT EXISTS trusted (
    id SERIAL PRIMARY KEY, src_ip VARCHAR(50) NOT NULL, proto VARCHAR(4) NOT NULL,
    from_pattern VARCHAR(64), ruri_pattern VARCHAR(64), tag VARCHAR(64), priority INTEGER NOT NULL DEFAULT 0);
