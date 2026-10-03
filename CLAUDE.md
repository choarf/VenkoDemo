# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Cloud side + web dashboard for the **Akvo_Green** Raspberry Pi Modbus gateway (sibling repo `../Akvo_Green`, not modified from here). The gateway publishes MQTT to AWS IoT Core; everything else (storage, API, reports, dashboard) lives in this repo. UI text and report text are in Spanish. `README.md` is the authoritative operator guide (deploy, adding sites, sensor changes, troubleshooting) — keep it in sync when behavior changes.

## Commands

```bash
# setup
python3 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt boto3 pytest

# tests (run from backend/, which is the import root — no AWS needed)
cd backend && python -m pytest -q
cd backend && python -m pytest -q tests/test_router.py::test_conflict_maps_to_409   # single test

# dashboard against a mock API with SYNTHETIC data (UI work only; no Excel/CSV export)
python3 tools/mock_api.py            # http://localhost:8787

# deploy stack (site 1 = stack sam-app, samconfig env "default"; other sites use --config-env <key>)
cd infra && sam build --template template.yaml && sam deploy [--config-env <key>]

# publish dashboard config + web/ to a site
tools/deploy_web.sh [stack] [gateway-config.json] [overrides.json]

# preview dashboard config built from the gateway config
python3 tools/sync_config.py --out build/config.json
```

Python 3.14 (Lambda runtime `python3.14`). There is no `.gitignore`, and `infra/.aws-sam/` (SAM build output) is committed — don't hand-edit it and avoid staging it with source changes. No linter/formatter or JS build step is configured; the web app is plain ES modules loaded directly by the browser, with Highcharts from CDN.

## Architecture

**Data flow.** IoT rules on `DataTopic`/`SystemTopic` (default `AKVO/data`, `AKVO/system`) fan each message to Firehose → `s3://<data bucket>/raw/{data,system}/dt=YYYY-MM-DD/` (queried by Athena via Glue tables `readings_raw` / `system_raw`) **and** overwrite `latest/data.json` / `latest/system.json` (the live view). All of this is defined in `infra/template.yaml`; there is no custom ingestion code.

**Two Lambdas, one code package** (`CodeUri: ../backend/`, so `backend/` is the import root — imports look like `from common import ...`):
- `api.router.handler` — API Gateway REST proxy. `ROUTES` in `backend/api/router.py` maps method + path regex to handlers in `api/data.py`, `api/testruns.py`, `api/reports.py`. Route order matters (`/tests/active` must precede `/tests/{id}`). Handlers return plain dicts; HTTP status comes from exceptions: `ValueError`/`KeyError`→400, `ddb.NotFound`→404, `ddb.Conflict`→409, `athena.QueryError`→502, `LookupError`→503.
- `api.reports.report_handler` — builds test reports (invoked async by `POST /tests/{id}/stop` and regenerate) and weekly reports (EventBridge `cron(0 12 ? * MON *)` — fixed UTC, i.e. 06:00 only for the default `America/Mexico_City`; changing `ReportTimezone` moves the report's week boundaries but not the trigger time — or `POST /reports/weekly`). Writes HTML (Jinja `backend/templates/report.html.j2`) + XLSX (openpyxl) to `reports/…` in S3 via `common/report_builder.py`.

**`backend/common/`**:
- `queries.py` — SQL builders. Readings are `devices map<device, map<sensor, struct<val,status,alarm,err>>>`, flattened with `CROSS JOIN UNNEST`. Values are string-interpolated, so any new input must be an int or pass `parse_sensor_keys` (`DEVICE.Sensor` regex) — this is the injection guard.
- `ddb.py` — single-table DynamoDB: `pk=TEST` (tests), `pk=STATE sk=ACTIVE` (the one running test; enforced with conditional writes → `Conflict`), `pk=COMMENT#<date>` (comments, GSI `by_test`).
- `storage.py` — S3 helpers; `get_config()` caches `config/config.json` per container for 5 min and raises `LookupError` if missing.
- `settings.py` — all runtime config from Lambda env vars (set in the template's `Globals`); `MAX_RANGE_DAYS = 31` caps Athena scans.

**Sensor config pipeline.** Sensors are not hard-coded anywhere. `tools/sync_config.py` reads the gateway's `config.json` (from `../Akvo_Green`), strips the `aws`/`modbus` sections (cert paths must never be uploaded), merges display overrides (`config/dashboard_overrides.json` for site 1, `config/sites/<key>.json` for others; keys are case-sensitive `DEVICE_ID.SensorName`), and uploads to `s3://<data bucket>/config/config.json`. The API, reports and dashboard all read that. New sensors need no stack redeploy.

**Multi-site.** One Pi = one stack = one dashboard; sites share code and AWS account only. Per-site params live in `infra/samconfig.toml` sections (`[default.*]`, `[akvo.*]`, …). `deploy_web.sh` refuses to upload if the gateway config's topics don't match the stack's, and writes `app-config.js` (API URL + key) straight to that site's web bucket — never into `web/` — so sites can't overwrite each other. A change to `web/` must be deployed to **every** site. Don't change `DataBucketName` on an existing stack (bucket is `Retain`; a new empty one would be created).

**Web (`web/`).** `js/main.js` does hash routing between views (`overview`, `test`, `alarms`, `history`, `reports`), polls `/live` + `/tests/active` every 10 s, and dispatches to `js/modules/*.js`. Most views have a same-named module; the `test` view is `hmi.js` (start/stop, comments) using `trend.js` for the live chart, and it receives every live poll via `hmi.onLive`. `api.js` does fetch with `X-Api-Key` from `window.VENKO` in `app-config.js` (generated per site, not in the repo). Charts created in hidden views must be reflowed on show.

**CORS gotcha.** After changing `AllowedOrigin`, SAM doesn't redeploy the API stage; run `aws apigateway create-deployment --rest-api-id <id> --stage-name prod`. Also persist it in `samconfig.toml` or a plain `sam deploy` resets it to `*`.
