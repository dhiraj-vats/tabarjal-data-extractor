"""
Live OPC UA -> Forecasting API forwarder.

For each entry in tags.json this service:
  1. Subscribes to / polls the OPC UA node on its endpoint
  2. Buckets the latest reading by (category, source_code) with the
     schema-defined component_key
  3. POSTs a single ingest payload per category per cycle to
       POST {API_BASE_URL}/api/v1/pqm/readings/ingest
       POST {API_BASE_URL}/api/v1/wms/readings/ingest
       POST {API_BASE_URL}/api/v1/sacu/readings/ingest

The forwarder maintains one OPC UA client per endpoint, reconnects on
failure with exponential backoff, and never blocks the whole pipeline on
one bad server. block_ts is the time the OPC UA value was sampled.

Run:
    python opcua_forwarder.py            # honours .env
    python opcua_forwarder.py --once     # one cycle then exit (smoke test)
    python opcua_forwarder.py --dry-run  # log payloads, do not POST
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from asyncua import Client, ua

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass


LOG = logging.getLogger("forwarder")
API_LOG = logging.getLogger("forwarder.api")

DEFAULT_TAGS_PATH = Path(__file__).resolve().parent / "tags.json"

API_BODY_PREVIEW_CHARS = 500


@dataclass(frozen=True)
class Tag:
    category: str          # PQM | WMS | SACU
    source_code: str       # PQM01, MVPS02, ...
    component_key: str     # total_active_power, dni_wm2, ...
    tagname: str
    opc_node_id: str
    opc_endpoint: str


@dataclass
class Reading:
    value: float
    sampled_at: datetime


def load_tags(path: Path) -> list[Tag]:
    raw = json.loads(path.read_text())
    return [Tag(**r) for r in raw]


def group_by_endpoint(tags: list[Tag]) -> dict[str, list[Tag]]:
    out: dict[str, list[Tag]] = defaultdict(list)
    for t in tags:
        out[t.opc_endpoint].append(t)
    return out


def _coerce_value(raw: Any) -> float | None:
    """OPC UA values can be int/float/bool/None. Coerce to float; drop None."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return float(int(raw))
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


class EndpointPoller:
    """Owns one OPC UA client; reads its tags on every cycle.

    Reconnects with bounded exponential backoff. A failure on one endpoint
    only blanks that endpoint's readings for the current cycle.
    """

    def __init__(
        self,
        endpoint: str,
        tags: list[Tag],
        connect_timeout: float,
        request_timeout: float,
    ) -> None:
        self.endpoint = endpoint
        self.tags = tags
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        self._client: Client | None = None
        self._nodes: list[Any] = []
        self._backoff = 1.0

    async def _connect(self) -> None:
        client = Client(url=self.endpoint, timeout=self.connect_timeout)
        await client.connect()
        try:
            nodes = [client.get_node(t.opc_node_id) for t in self.tags]
        except Exception:
            await client.disconnect()
            raise
        self._client = client
        self._nodes = nodes
        self._backoff = 1.0
        LOG.info("connected: %s (%d tags)", self.endpoint, len(self.tags))

    async def _disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception as exc:
                LOG.debug("disconnect error on %s: %s", self.endpoint, exc)
            self._client = None
            self._nodes = []

    async def ensure_connected(self) -> bool:
        if self._client is not None:
            return True
        try:
            await self._connect()
            return True
        except Exception as exc:
            LOG.warning(
                "connect failed: %s (%s: %s) — backoff %.1fs",
                self.endpoint, type(exc).__name__, exc or "<no message>",
                self._backoff,
            )
            LOG.debug("connect traceback for %s:", self.endpoint, exc_info=True)
            await asyncio.sleep(self._backoff)
            self._backoff = min(self._backoff * 2, 60.0)
            return False

    async def poll_once(self) -> dict[Tag, Reading]:
        if not await self.ensure_connected():
            return {}
        assert self._client is not None
        try:
            data_values = await asyncio.wait_for(
                self._client.read_values(self._nodes),
                timeout=self.request_timeout,
            )
        except Exception as exc:
            LOG.warning("read failed on %s: %s — will reconnect", self.endpoint, exc)
            await self._disconnect()
            return {}

        sampled_at = datetime.now(timezone.utc)
        out: dict[Tag, Reading] = {}
        for tag, raw in zip(self.tags, data_values):
            v = _coerce_value(raw)
            if v is None:
                LOG.debug("skip %s.%s — non-numeric value %r",
                          tag.source_code, tag.component_key, raw)
                continue
            out[tag] = Reading(value=v, sampled_at=sampled_at)
        return out

    async def close(self) -> None:
        await self._disconnect()


class ApiPusher:
    def __init__(
        self,
        base_url: str,
        http_timeout: float,
        retries: int,
        dry_run: bool,
        audit_log_path: Path | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.http_timeout = http_timeout
        self.retries = retries
        self.dry_run = dry_run
        self.audit_log_path = audit_log_path
        self._client: httpx.AsyncClient | None = None
        self._audit_fp = None

    async def __aenter__(self) -> "ApiPusher":
        self._client = httpx.AsyncClient(timeout=self.http_timeout)
        if self.audit_log_path is not None:
            self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            self._audit_fp = self.audit_log_path.open("a", buffering=1, encoding="utf-8")
            API_LOG.info("api audit log -> %s", self.audit_log_path)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client is not None:
            await self._client.aclose()
        if self._audit_fp is not None:
            self._audit_fp.close()
            self._audit_fp = None

    def _write_audit(self, record: dict[str, Any]) -> None:
        if self._audit_fp is None:
            return
        try:
            self._audit_fp.write(json.dumps(record, default=str) + "\n")
        except Exception as exc:
            LOG.warning("audit-log write failed: %s", exc)

    @staticmethod
    def _fmt_ts(ts: datetime) -> str:
        # API examples use "YYYY-MM-DD HH:MM:SS". Send UTC seconds.
        return ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def _build_payload(
        self,
        readings: list[tuple[Tag, Reading]],
    ) -> dict[str, Any]:
        by_source: dict[str, dict[str, dict[str, float]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        for tag, r in readings:
            ts_key = self._fmt_ts(r.sampled_at)
            by_source[tag.source_code][ts_key][tag.component_key] = r.value

        sources_out: list[dict[str, Any]] = []
        for source_code, blocks in by_source.items():
            sources_out.append({
                "source_code": source_code,
                "blocks": [
                    {"block_ts": ts, "components": comps}
                    for ts, comps in sorted(blocks.items())
                ],
            })
        return {"source": "opcua", "sources": sources_out}

    @staticmethod
    def _preview(body: str) -> str:
        if len(body) <= API_BODY_PREVIEW_CHARS:
            return body
        return body[:API_BODY_PREVIEW_CHARS] + f"...[+{len(body) - API_BODY_PREVIEW_CHARS} chars]"

    async def push(
        self,
        category: str,
        readings: list[tuple[Tag, Reading]],
    ) -> None:
        if not readings:
            return
        endpoint = f"{self.base_url}/api/v1/{category.lower()}/readings/ingest"
        payload = self._build_payload(readings)
        if self.dry_run:
            API_LOG.info(
                "[dry-run] POST %s payload=%s",
                endpoint, json.dumps(payload),
            )
            self._write_audit({
                "ts": datetime.now(timezone.utc).isoformat(),
                "dry_run": True,
                "category": category,
                "method": "POST",
                "url": endpoint,
                "request": payload,
            })
            return
        assert self._client is not None
        for attempt in range(1, self.retries + 1):
            sent_at = datetime.now(timezone.utc)
            t0 = asyncio.get_event_loop().time()
            try:
                resp = await self._client.post(endpoint, json=payload)
            except httpx.HTTPError as exc:
                duration_ms = int((asyncio.get_event_loop().time() - t0) * 1000)
                API_LOG.warning(
                    "POST %s -> NETWORK_ERROR (%s: %s) attempt=%d/%d duration_ms=%d",
                    endpoint, type(exc).__name__, exc, attempt, self.retries,
                    duration_ms,
                )
                self._write_audit({
                    "ts": sent_at.isoformat(),
                    "category": category,
                    "method": "POST",
                    "url": endpoint,
                    "attempt": attempt,
                    "duration_ms": duration_ms,
                    "error": {"type": type(exc).__name__, "message": str(exc)},
                    "request": payload,
                })
                if attempt < self.retries:
                    await asyncio.sleep(min(2 ** attempt, 30))
                continue

            duration_ms = int((asyncio.get_event_loop().time() - t0) * 1000)
            body_text = resp.text or ""
            try:
                body_json: Any = resp.json()
            except ValueError:
                body_json = None

            # Always log status + (truncated) response body for visibility.
            log_method = (
                API_LOG.error if resp.status_code >= 400
                else API_LOG.warning if resp.status_code >= 300
                else API_LOG.info
            )
            log_method(
                "POST %s -> %s (%d sources, %d readings, %d ms) body=%s",
                endpoint, resp.status_code,
                len(payload["sources"]), len(readings), duration_ms,
                self._preview(body_text) if body_text else "<empty>",
            )

            self._write_audit({
                "ts": sent_at.isoformat(),
                "category": category,
                "method": "POST",
                "url": endpoint,
                "attempt": attempt,
                "duration_ms": duration_ms,
                "status_code": resp.status_code,
                "request": payload,
                "response_body": body_json if body_json is not None else body_text,
            })

            if resp.status_code >= 500 and attempt < self.retries:
                LOG.warning(
                    "retrying POST %s after 5xx (attempt %d/%d)",
                    endpoint, attempt, self.retries,
                )
                await asyncio.sleep(min(2 ** attempt, 30))
                continue
            return
        LOG.error("POST %s exhausted %d retries", endpoint, self.retries)


class Forwarder:
    def __init__(
        self,
        tags: list[Tag],
        api_base_url: str,
        poll_interval: float,
        opc_connect_timeout: float,
        opc_request_timeout: float,
        http_timeout: float,
        retries: int,
        dry_run: bool,
        api_log_path: Path | None = None,
    ) -> None:
        self.poll_interval = poll_interval
        self.api_base_url = api_base_url
        self.http_timeout = http_timeout
        self.retries = retries
        self.dry_run = dry_run
        self.api_log_path = api_log_path

        self.pollers = [
            EndpointPoller(ep, tlist, opc_connect_timeout, opc_request_timeout)
            for ep, tlist in group_by_endpoint(tags).items()
        ]
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self, once: bool = False) -> int:
        try:
            async with ApiPusher(
                self.api_base_url, self.http_timeout, self.retries,
                self.dry_run, self.api_log_path,
            ) as pusher:
                while not self._stop.is_set():
                    cycle_started = asyncio.get_event_loop().time()
                    await self._one_cycle(pusher)
                    if once:
                        return 0
                    elapsed = asyncio.get_event_loop().time() - cycle_started
                    sleep_for = max(self.poll_interval - elapsed, 0.0)
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=sleep_for)
                    except asyncio.TimeoutError:
                        pass
            return 0
        finally:
            await asyncio.gather(
                *(p.close() for p in self.pollers),
                return_exceptions=True,
            )

    async def _one_cycle(self, pusher: ApiPusher) -> None:
        results = await asyncio.gather(
            *(p.poll_once() for p in self.pollers),
            return_exceptions=True,
        )
        merged: dict[Tag, Reading] = {}
        for poller, res in zip(self.pollers, results):
            if isinstance(res, Exception):
                LOG.error("poller %s raised: %s", poller.endpoint, res)
                continue
            merged.update(res)
        if not merged:
            LOG.warning("cycle produced 0 readings — skipping push")
            return

        by_cat: dict[str, list[tuple[Tag, Reading]]] = defaultdict(list)
        for tag, reading in merged.items():
            by_cat[tag.category].append((tag, reading))

        await asyncio.gather(*(
            pusher.push(cat, items) for cat, items in by_cat.items()
        ))


def _env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return float(v)


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return int(v)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tags", default=str(DEFAULT_TAGS_PATH),
                    help="Path to tags.json")
    ap.add_argument("--api-base-url", default=os.getenv("API_BASE_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--poll-interval", type=float,
                    default=_env_float("POLL_INTERVAL_SECONDS", 60.0),
                    help="Seconds between read cycles")
    ap.add_argument("--opc-connect-timeout", type=float,
                    default=_env_float("OPC_CONNECT_TIMEOUT", 10.0))
    ap.add_argument("--opc-request-timeout", type=float,
                    default=_env_float("OPC_REQUEST_TIMEOUT", 10.0))
    ap.add_argument("--http-timeout", type=float,
                    default=_env_float("HTTP_TIMEOUT", 15.0))
    ap.add_argument("--retries", type=int,
                    default=_env_int("HTTP_RETRIES", 3))
    ap.add_argument("--once", action="store_true",
                    help="Run a single cycle and exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="Log payloads instead of POSTing")
    ap.add_argument("--log-level",
                    default=os.getenv("LOG_LEVEL", "INFO"))
    ap.add_argument("--api-log",
                    default=os.getenv("API_LOG_PATH", ""),
                    help="If set, append every API request/response as JSON "
                         "lines to this file (audit trail).")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    tags = load_tags(Path(args.tags))
    if not tags:
        print("error: no tags loaded", file=sys.stderr)
        return 2
    LOG.info("loaded %d tags across %d endpoints",
             len(tags), len({t.opc_endpoint for t in tags}))

    api_log_path = Path(args.api_log).expanduser() if args.api_log else None

    fwd = Forwarder(
        tags=tags,
        api_base_url=args.api_base_url,
        poll_interval=args.poll_interval,
        opc_connect_timeout=args.opc_connect_timeout,
        opc_request_timeout=args.opc_request_timeout,
        http_timeout=args.http_timeout,
        retries=args.retries,
        dry_run=args.dry_run,
        api_log_path=api_log_path,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _handle_signal(_signum, _frame):
        LOG.info("shutdown signal received")
        loop.call_soon_threadsafe(fwd.stop)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        return loop.run_until_complete(fwd.run(once=args.once))
    finally:
        loop.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
