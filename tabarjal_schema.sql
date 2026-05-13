-- Database: re_tabrajhal_shv
-- Clean PostgreSQL schema + master seed data.
-- Relation columns are plain integer columns, without DB-level relation constraints.

CREATE TABLE IF NOT EXISTS tb_data_sources (
    id SERIAL PRIMARY KEY,
    source_code VARCHAR(50) NOT NULL,
    source_name VARCHAR(150) NOT NULL,
    category VARCHAR(50) NOT NULL,
    location VARCHAR(150),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tb_data_sources_category_code
ON tb_data_sources (category, source_code);

CREATE TABLE IF NOT EXISTS tb_source_components (
    id SERIAL PRIMARY KEY,
    source_id INTEGER NOT NULL,
    component_key VARCHAR(100) NOT NULL,
    display_name VARCHAR(200) NOT NULL,
    external_key VARCHAR(200) NOT NULL,
    unit VARCHAR(30),
    data_type VARCHAR(30) NOT NULL DEFAULT 'float',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tb_source_components_source_key
ON tb_source_components (source_id, component_key);

CREATE INDEX IF NOT EXISTS idx_tb_source_components_source_id
ON tb_source_components (source_id);

CREATE INDEX IF NOT EXISTS idx_tb_source_components_external_key
ON tb_source_components (external_key);

CREATE TABLE IF NOT EXISTS tb_pqm_readings (
    id SERIAL PRIMARY KEY,
    src_component_id INTEGER NOT NULL,
    block_ts TIMESTAMPTZ NOT NULL,
    value NUMERIC(18, 6) NOT NULL,
    source VARCHAR(50) NOT NULL DEFAULT 'api',
    raw_key VARCHAR(200),
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tb_pqm_readings_active
ON tb_pqm_readings (src_component_id, block_ts)
WHERE is_deleted = FALSE;

CREATE INDEX IF NOT EXISTS idx_tb_pqm_readings_active_block_ts
ON tb_pqm_readings (block_ts)
WHERE is_deleted = FALSE;

CREATE INDEX IF NOT EXISTS idx_tb_pqm_readings_deleted_lookup
ON tb_pqm_readings (src_component_id, block_ts, is_deleted);

CREATE TABLE IF NOT EXISTS tb_wms_readings (
    id SERIAL PRIMARY KEY,
    src_component_id INTEGER NOT NULL,
    block_ts TIMESTAMPTZ NOT NULL,
    value NUMERIC(18, 6) NOT NULL,
    source VARCHAR(50) NOT NULL DEFAULT 'api',
    raw_key VARCHAR(200),
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tb_wms_readings_active
ON tb_wms_readings (src_component_id, block_ts)
WHERE is_deleted = FALSE;

CREATE INDEX IF NOT EXISTS idx_tb_wms_readings_active_block_ts
ON tb_wms_readings (block_ts)
WHERE is_deleted = FALSE;

CREATE INDEX IF NOT EXISTS idx_tb_wms_readings_deleted_lookup
ON tb_wms_readings (src_component_id, block_ts, is_deleted);

CREATE TABLE IF NOT EXISTS tb_sacu_readings (
    id SERIAL PRIMARY KEY,
    src_component_id INTEGER NOT NULL,
    block_ts TIMESTAMPTZ NOT NULL,
    value NUMERIC(18, 6) NOT NULL,
    source VARCHAR(50) NOT NULL DEFAULT 'api',
    raw_key VARCHAR(200),
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tb_sacu_readings_active
ON tb_sacu_readings (src_component_id, block_ts)
WHERE is_deleted = FALSE;

CREATE INDEX IF NOT EXISTS idx_tb_sacu_readings_active_block_ts
ON tb_sacu_readings (block_ts)
WHERE is_deleted = FALSE;

CREATE INDEX IF NOT EXISTS idx_tb_sacu_readings_deleted_lookup
ON tb_sacu_readings (src_component_id, block_ts, is_deleted);

CREATE TABLE IF NOT EXISTS tb_inverter_readings (
    id SERIAL PRIMARY KEY,
    src_component_id INTEGER NOT NULL,
    block_ts TIMESTAMPTZ NOT NULL,
    value NUMERIC(18, 6) NOT NULL,
    source VARCHAR(50) NOT NULL DEFAULT 'api',
    raw_key VARCHAR(200),
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tb_inverter_readings_active
ON tb_inverter_readings (src_component_id, block_ts)
WHERE is_deleted = FALSE;

CREATE INDEX IF NOT EXISTS idx_tb_inverter_readings_active_block_ts
ON tb_inverter_readings (block_ts)
WHERE is_deleted = FALSE;

CREATE INDEX IF NOT EXISTS idx_tb_inverter_readings_deleted_lookup
ON tb_inverter_readings (src_component_id, block_ts, is_deleted);

-- Master data sources .


INSERT INTO tb_data_sources (source_code, source_name, category, location)
VALUES
('PQM01', 'PQM - 01', 'PQM', 'Main Plant'),
('PQM02', 'PQM - 02', 'PQM', 'Main Plant')
ON CONFLICT (category, source_code) DO UPDATE
SET source_name = EXCLUDED.source_name,
    location = EXCLUDED.location,
    is_active = TRUE,
    updated_at = NOW();

INSERT INTO tb_data_sources (source_code, source_name, category, location)
VALUES
('MVPS02', 'MVPS02', 'WMS', 'Main Plant'),
('MVPS14', 'MVPS14', 'WMS', 'Main Plant'),
('MVPS22', 'MVPS22', 'WMS', 'Main Plant'),
('MVPS39', 'MVPS39', 'WMS', 'Main Plant'),
('MVPS48', 'MVPS48', 'WMS', 'Main Plant')
ON CONFLICT (category, source_code) DO UPDATE
SET source_name = EXCLUDED.source_name,
    location = EXCLUDED.location,
    is_active = TRUE,
    updated_at = NOW();

INSERT INTO tb_data_sources (source_code, source_name, category, location)
VALUES
('MVPS02', 'SACU MVPS02', 'SACU', 'Main Plant'),
('MVPS14', 'SACU MVPS14', 'SACU', 'Main Plant'),
('MVPS22', 'SACU MVPS22', 'SACU', 'Main Plant'),
('MVPS39', 'SACU MVPS39', 'SACU', 'Main Plant'),
('MVPS48', 'SACU MVPS48', 'SACU', 'Main Plant')
ON CONFLICT (category, source_code) DO UPDATE
SET source_name = EXCLUDED.source_name,
    location = EXCLUDED.location,
    is_active = TRUE,
    updated_at = NOW();

-- PQM components.

INSERT INTO tb_source_components (
    source_id,
    component_key,
    display_name,
    external_key,
    unit,
    data_type
)
SELECT
    s.id,
    'total_active_power',
    s.source_name || ' Total Active Power',
    'ns=6;s=Arp.Plc.Eclr/MCR_' || s.source_code || '_TOTAL_ACTIVE_POWER',
    'kW',
    'float'
FROM tb_data_sources s
WHERE s.category = 'PQM'
ON CONFLICT (source_id, component_key) DO UPDATE
SET display_name = EXCLUDED.display_name,
    external_key = EXCLUDED.external_key,
    unit = EXCLUDED.unit,
    data_type = EXCLUDED.data_type,
    is_active = TRUE,
    updated_at = NOW();

-- WMS components.

WITH wms_components(component_key, label, unit, data_type) AS (
    VALUES
    ('dni_wm2', 'DNI WM2', 'W/m2', 'float'),
    ('front_soil_sensor_1', 'FRONT SOIL SENSOR 1', NULL, 'float'),
    ('front_soil_sensor_2', 'FRONT SOIL SENSOR 2', NULL, 'float'),
    ('front_tr_loss_sensor_1', 'FRONT TR LOSS SENSOR 1', NULL, 'float'),
    ('front_tr_loss_sensor_2', 'FRONT TR LOSS SENSOR 2', NULL, 'float'),
    ('ghi_w', 'GHI W', 'W/m2', 'float'),
    ('module_temperature_1', 'MODULE TEMPERATURE 1', 'C', 'float'),
    ('poa1_w', 'POA1 W', 'W/m2', 'float'),
    ('poa2_w', 'POA2 W', 'W/m2', 'float'),
    ('wind_direction', 'WIND DIRECTION', 'deg', 'float'),
    ('wind_speed', 'WIND SPEED', 'm/s', 'float')
)
INSERT INTO tb_source_components (
    source_id,
    component_key,
    display_name,
    external_key,
    unit,
    data_type
)
SELECT
    s.id,
    c.component_key,
    s.source_code || ' ' || c.label,
    s.source_code || ' ' || c.label,
    c.unit,
    c.data_type
FROM tb_data_sources s
CROSS JOIN wms_components c
WHERE s.category = 'WMS'
ON CONFLICT (source_id, component_key) DO UPDATE
SET display_name = EXCLUDED.display_name,
    external_key = EXCLUDED.external_key,
    unit = EXCLUDED.unit,
    data_type = EXCLUDED.data_type,
    is_active = TRUE,
    updated_at = NOW();

-- SACU components.

WITH sacu_components(component_key, label, unit, data_type) AS (
    VALUES
    ('sacu_dc_curr', 'SACU DC CURR', 'A', 'float'),
    ('sacu_active_power', 'SACU ACTIVE POWER', 'kW', 'float'),
    ('sacu_plant_status_2', 'SACU PLANT STATUS 2', NULL, 'float'),
    ('sacu_inv_efficiency', 'SACU INV EFFICIENCY', '%', 'float')
)
INSERT INTO tb_source_components (
    source_id,
    component_key,
    display_name,
    external_key,
    unit,
    data_type
)
SELECT
    s.id,
    c.component_key,
    s.source_code || ' ' || c.label,
    s.source_code || ' ' || c.label,
    c.unit,
    c.data_type
FROM tb_data_sources s
CROSS JOIN sacu_components c
WHERE s.category = 'SACU'
ON CONFLICT (source_id, component_key) DO UPDATE
SET display_name = EXCLUDED.display_name,
    external_key = EXCLUDED.external_key,
    unit = EXCLUDED.unit,
    data_type = EXCLUDED.data_type,
    is_active = TRUE,
    updated_at = NOW();

-- Quick checks:
-- SELECT category, COUNT(*) FROM tb_data_sources GROUP BY category ORDER BY category;
-- SELECT s.category, COUNT(*) FROM tb_source_components c JOIN tb_data_sources s ON s.id = c.source_id GROUP BY s.category ORDER BY s.category;





UPDATE tb_source_components c
SET
    external_key = 'ns=6;s=Arp.Plc.Eclr/MCR_' || s.source_code || '_TOTAL_ACTIVE_POWER',
    updated_at = NOW()
FROM tb_data_sources s
WHERE s.id = c.source_id
  AND s.category = 'PQM'
  AND c.component_key = 'total_active_power';
