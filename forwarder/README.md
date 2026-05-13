# Tabarjal Forecasting — OPC UA → API Forwarder

Reads the 77 green-highlighted tags in `RequiredDocument.xlsx` from the plant's
OPC UA servers and pushes them to the FastAPI ingest endpoints
(`/api/v1/{pqm,wms,sacu}/readings/ingest`) on a fixed cadence.

## Tag set (auto-derived from the Excel sheet)

| Category | Sources | Component keys per source | Total |
|---|---|---|---|
| PQM  | PQM01, PQM02 | `total_active_power` | 2 |
| WMS  | MVPS02, MVPS14, MVPS22, MVPS39, MVPS48 | `dni_wm2`, `front_soil_sensor_1/2`, `front_tr_loss_sensor_1/2`, `ghi_w`, `module_temperature_1`, `poa1_w`, `poa2_w`, `wind_direction`, `wind_speed` | 55 |
| SACU (selected inverters) | MVPS02, MVPS14, MVPS22, MVPS39, MVPS48 | `sacu_dc_curr`, `sacu_active_power`, `sacu_plant_status_2`, `sacu_inv_efficiency` | 20 |

All `component_key` values match `tabarjal_schema.sql` 1:1, so no schema
migration is needed.

### OPC UA endpoints

| Endpoint | Used for |
|---|---|
| `opc.tcp://172.16.51.1:4840` | PQM-01, PQM-02, all 5 WMS sources (57 tags) |
| `opc.tcp://172.16.2.1:4840`  | SACU MVPS02 (4 tags) |
| `opc.tcp://172.16.14.1:4840` | SACU MVPS14 (4 tags) |
| `opc.tcp://172.16.22.1:4840` | SACU MVPS22 (4 tags) |
| `opc.tcp://172.16.39.1:4840` | SACU MVPS39 (4 tags) |
| `opc.tcp://172.16.48.1:4840` | SACU MVPS48 (4 tags) |

## Install

**Use Python 3.10 – 3.13.** Python 3.14 changed `issubclass()` semantics in a
way that breaks asyncua's typing internals (`TypeError: issubclass() arg 1
must be a class` on every `client.connect()`). asyncua does not yet ship a
3.14-compatible release.

```bash
cd forwarder
python3.12 -m venv .venv && source .venv/bin/activate   # Linux/macOS
# Windows: py -3.12 -m venv .venv && .venv\Scripts\activate
python --version                                        # confirm 3.10–3.13
pip install -U pip
pip install -r requirements.txt
cp .env.example .env   # edit if API host / interval differ
```

## Refresh the tag manifest (only if Excel changes)

```bash
python extract_tags.py           # writes tags.json
```

This validates every green tag resolves to a `component_key` and that every
row has an OPC node id and endpoint; it aborts non-zero otherwise.

## Run

```bash
# normal: poll every POLL_INTERVAL_SECONDS, push forever
python opcua_forwarder.py

# one cycle then exit — useful for cron or smoke tests
python opcua_forwarder.py --once

# print the payloads without POSTing (offline debugging)
python opcua_forwarder.py --once --dry-run
```

The service handles SIGINT/SIGTERM cleanly and disconnects all OPC UA clients
before exit, so it is safe to run under systemd, supervisord, or as a Docker
sidecar.

## API request/response logging

Every POST to the ingest API is logged. Two levels of detail are available.

**Always on — stdout (`forwarder.api` logger):**

```
2026-05-13 21:13:12,432 INFO forwarder.api: POST http://127.0.0.1:8000/api/v1/pqm/readings/ingest -> 200 (2 sources, 2 readings, 14 ms) body={"ok":true,...}
2026-05-13 21:13:12,464 INFO forwarder.api: POST http://127.0.0.1:8000/api/v1/sacu/readings/ingest -> 200 (2 sources, 8 readings, 9 ms) body={"ok":true,...}
```

Bodies are truncated to ~500 characters in stdout. Non-2xx responses are
logged at `ERROR` and include the (truncated) body, so failures explain
themselves without DEBUG.

**Opt-in JSONL audit trail — `API_LOG_PATH` env var or `--api-log FILE`:**

When set, every request/response is appended as one JSON object per line:

```bash
python opcua_forwarder.py --api-log logs/api.jsonl
# or set API_LOG_PATH=logs/api.jsonl in .env
```

Schema (one record per attempt):

```json
{
  "ts": "2026-05-13T21:13:12.432+00:00",
  "category": "PQM",
  "method": "POST",
  "url": "http://127.0.0.1:8000/api/v1/pqm/readings/ingest",
  "attempt": 1,
  "duration_ms": 14,
  "status_code": 200,
  "request":  { "source": "opcua", "sources": [...] },
  "response_body": { "ok": true, "ingested": 2 }
}
```

Network failures (no HTTP response) get the same record shape with `"error":
{"type": "...", "message": "..."}` and no `status_code`. Bodies are stored
**untruncated** in the JSONL file — handy for diffing the API contract or
replaying a payload.

## Operational behaviour

- **Per-endpoint isolation.** Each OPC UA endpoint has its own client. If
  one inverter's PLC is unreachable, the others still publish that cycle.
- **Bounded backoff.** Failed endpoints retry with exponential backoff
  (1s → 60s cap) until they reconnect; no manual intervention needed.
- **`block_ts`.** UTC timestamp of the OPC UA read, formatted
  `YYYY-MM-DD HH:MM:SS` to match the Postman examples. All readings in a
  cycle from the same endpoint share one timestamp.
- **HTTP retries.** Idempotent retries with exponential backoff on 5xx and
  network errors (`HTTP_RETRIES` cap). 4xx responses are logged and not
  retried — they signal a contract bug, not a transient fault.
- **Type coercion.** Non-numeric OPC values (None, strings) are skipped
  with a DEBUG log; booleans coerce to 0/1 (the `sacu_plant_status_2`
  tag is a status flag).
- **Schema-validated.** Source codes (`PQM01`/`PQM02`/`MVPS*`) and
  component keys exactly match the `tb_data_sources` / `tb_source_components`
  seed rows in `tabarjal_schema.sql`.

## Troubleshooting

### `connect failed: ... (TypeError: issubclass() arg 1 must be a class)`

This is a known bug in **asyncua ≤ 1.1.5** triggered during the OPC UA
secure-channel handshake on certain servers (the parser passes an instance
where a class is expected). Fix:

```bash
pip install -U "asyncua>=1.1.6"
```

`requirements.txt` is already pinned to `asyncua>=1.1.6`.

### See the full traceback for a connect failure

```bash
python opcua_forwarder.py --log-level DEBUG
```

DEBUG includes the asyncua call stack for every failed connect, so you can
tell whether the failure is in `connect()`, `open_secure_channel()`,
endpoint negotiation, or `get_node()`.

### Empty exception messages (`connect failed: opc.tcp://... ()`)

After upgrading asyncua, these become `connect failed: ... (TimeoutError:
...)` or similar — the new log line above shows the exception **type** even
when the message is empty, which is enough to tell connection refused
(`ConnectionRefusedError`) from timeout (`TimeoutError` / `asyncio.TimeoutError`)
from a server-side reject.

### `ModuleNotFoundError: No module named '_cffi_backend'`

`cryptography` 45.x calls into Rust through a `cffi` shim; if pip resolves
without pulling the `cffi` wheel (sometimes happens on Windows with a stale
cache), every `import asyncua` fails before connect. Fix:

```bat
pip install --force-reinstall --no-cache-dir cffi cryptography
```

`cffi` is listed in `requirements.txt` so a clean `pip install -r requirements.txt`
in a fresh venv should pick it up.

## Smoke test without the live PLCs

If you want to verify the API contract end-to-end before the OPC UA
servers are reachable, the easiest path is to run the FastAPI service
locally and point a stub OPC UA server (e.g. `asyncua`'s `examples/server-example.py`)
at `127.0.0.1:4840`, edit `tags.json` to use that endpoint, and run with
`--once`. Otherwise `--dry-run` confirms the payload shape.
