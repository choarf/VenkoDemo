# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Cloud side + web dashboard for the **Akvo_Green** Raspberry Pi Modbus gateway (sibling repo `../Akvo_Green`). The gateway publishes MQTT to AWS IoT Core; everything else (storage, API, reports, dashboard) lives in this repo. UI text and report text are in Spanish. `README.md` is the authoritative operator guide (deploy, adding sites, sensor changes, troubleshooting) — keep it in sync when behavior changes.

## Working across both repos

Most changes touch both repos (a new `system.csv` column → `config.json` → this dashboard; site config; deploys). Akvo_Green must be a working directory of the session, or reads/edits there are refused (`blockReadsOutsideWorkingDirectories`):

```
/add-dir /home/chris/git_reps/claude/projects/Venko/Akvo_Green
```

It's saved in `.claude/settings.local.json` (`permissions.additionalDirectories`), but a session started from inside Akvo_Green doesn't load this repo's settings - starting `claude` from the parent folder `~/git_reps/claude/projects/Venko` covers both. Commit each repo separately.

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

# deploy stack (each site = stack venko-<key>, samconfig env <key>)
cd infra && sam build --template template.yaml && sam deploy --config-env <key>

# publish dashboard config + web/ to a site
tools/deploy_web.sh <stack> <gateway-config.json> <overrides.json>

# per-site summary page (AWS resources, URLs, IoT, live status) -> docs/sites/<key>.md
python3 tools/site_summary.py <samconfig-env> [--pi user@host]

# AWS cost per site by category (Cost Explorer, stack-name/Site/Component tags; README "Cost per site")
python3 tools/site_costs.py [<key>...] [--by service|component|usage] [--months N]

# preview dashboard config built from a site's gateway config (no args = site cmd4)
python3 tools/sync_config.py --source ../Akvo_Green/sites/<key>/config_data/config.json \
    --overrides config/sites/<key>.json --out build/config.json
# new site: write a starter overrides file (labels reused from other sites; refuses to overwrite)
python3 tools/sync_config.py --init --source ../Akvo_Green/sites/<key>/config_data/config.json \
    --overrides config/sites/<key>.json
```

Python 3.14 (Lambda runtime `python3.14`). `.gitignore` lists `infra/.aws-sam/` and `__pycache__/`, but both were committed earlier and are still tracked, so they show as modified after any build/test run — never stage them with source changes (stage files by name, not `git add -A`). `web/app-config.js` and `build/` are ignored. No linter/formatter or JS build step is configured; the web app is plain ES modules loaded directly by the browser, with Highcharts from CDN.

## Architecture

**Data flow.** IoT rules on `DataTopic`/`SystemTopic` (default `AKVO/data`, `AKVO/system`) fan each message to Firehose → `s3://<data bucket>/raw/{data,system}/dt=YYYY-MM-DD/` (queried by Athena via Glue tables `readings_raw` / `system_raw`) **and** overwrite `latest/data.json` / `latest/system.json` (the live view). All of this is defined in `infra/template.yaml`; there is no custom ingestion code.

**Two Lambdas, one code package** (`CodeUri: ../backend/`, so `backend/` is the import root — imports look like `from common import ...`):
- `api.router.handler` — API Gateway REST proxy. `ROUTES` in `backend/api/router.py` maps method + path regex to handlers in `api/data.py`, `api/testruns.py`, `api/reports.py`. Route order matters (`/tests/active` must precede `/tests/{id}`). Handlers return plain dicts; HTTP status comes from exceptions: `ValueError`/`KeyError`→400, `ddb.NotFound`→404, `ddb.Conflict`→409, `athena.QueryError`→502, `LookupError`→503.
- `api.reports.report_handler` — builds test reports (invoked async by `POST /tests/{id}/stop` and regenerate) and weekly reports (EventBridge `cron(0 12 ? * MON *)` — fixed UTC, i.e. 06:00 only for the default `America/Mexico_City`; changing `ReportTimezone` moves the report's week boundaries but not the trigger time — or `POST /reports/weekly`). Writes HTML (Jinja `backend/templates/report.html.j2`) + XLSX (openpyxl) to `reports/…` in S3 via `common/report_builder.py`.

**`backend/common/`**:
- `queries.py` — SQL builders. Readings are `devices map<device, map<sensor, struct<val,status,alarm,err>>>`, flattened with `CROSS JOIN UNNEST`. Values are string-interpolated, so any new input must be an int or pass `parse_sensor_keys` (`DEVICE.Sensor` regex) — this is the injection guard. Warning bands (`warn_bands`, used by `/alarms` and `/history/stats`) come from config limits as floats and keys re-checked against the same regex.
- `ddb.py` — single-table DynamoDB: `pk=TEST` (tests), `pk=STATE sk=ACTIVE` (the one running test; enforced with conditional writes → `Conflict`), `pk=COMMENT#<date>` (comments, GSI `by_test`).
- `storage.py` — S3 helpers; `get_config()` caches `config/config.json` per container for 5 min and raises `LookupError` if missing.
- `settings.py` — all runtime config from Lambda env vars (set in the template's `Globals`); `MAX_RANGE_DAYS = 31` caps Athena scans.

**Sensor config pipeline.** Sensors are not hard-coded anywhere. `tools/sync_config.py` reads the gateway's `config.json` (from `../Akvo_Green`), strips the `aws`/`modbus` sections (cert paths must never be uploaded), merges display overrides (`config/sites/<key>.json`; keys are case-sensitive `DEVICE_ID.SensorName`), and uploads to `s3://<data bucket>/config/config.json`. The API, reports and dashboard all read that. Overrides may also carry `derived` (process indicators: fixed ops `ratio`/`diff`/`balance` in `backend/common/derived.py`, mirrored in `overview.js`; validated by `sync_config.derived_entries`) and `system_limits` (CPU/RAM/disk warning/alarm % for the Sistema view). New sensors need no stack redeploy.

**Multi-site.** One Pi = one stack = one dashboard; sites share code and AWS account only. Current sites: `cmd4`, `demo`. A site key is lowercase and is used everywhere: samconfig env `<key>`, stack `venko-<key>`, topics `VENKO/<key>/{data,system}`, buckets `venko-<key>-…`, Glue DB `venko_<key>`, overrides `config/sites/<key>.json`, gateway folder `../Akvo_Green/sites/<key>/` (its `aws.csv` topics and `certs/policy.json` must use the same lowercase key — IoT policy resources are case-sensitive). Per-site params live in `infra/samconfig.toml` sections (`[cmd4.*]`, …; there is no `[default.*]`, so always pass `--config-env`). The README's "Step by step: from a blank SD card…" section is the full procedure for adding a site. `deploy_web.sh` refuses to upload if the gateway config's topics don't match the stack's, and writes `app-config.js` (API URL + key) straight to that site's web bucket — never into `web/` — so sites can't overwrite each other. A change to `web/` must be deployed to **every** site. Every samconfig section needs a `tags = "Project=... Site=<key> Client=..."` line and every new resource a `Component` tag (cost per site). Don't change `DataBucketName` on an existing stack (bucket is `Retain`; a new empty one would be created).

**Web (`web/`).** `js/main.js` does hash routing between views (`overview`, `test`, `alarms`, `history`, `reports`, `system`), polls `/live` + `/tests/active` every 10 s, and dispatches to `js/modules/*.js`. Most views have a same-named module; the `test` view is `hmi.js` (start/stop, comments) using `trend.js` for the live chart, and it receives every live poll via `hmi.onLive`. `api.js` does fetch with `X-Api-Key` from `window.VENKO` in `app-config.js` (generated per site, not in the repo). Charts created in hidden views must be reflowed on show (`main.js` also reflows every chart when `main` changes width). The overview dials are plain SVG (`overview.js`), not Highcharts. Highcharts loads from jsDelivr (`code.highcharts.com` returns 403 on some networks); `main.js` swaps in a no-op stand-in when it's missing so the page still works without charts. The layout follows `docs/propuesta-tablero/` (prototype + implementation plan); `deploy_web.sh` uploads a copy of `web/` with `Cache-Control: no-cache` and every CSS/JS URL and `from "./x.js"` import stamped `?v=<hash>` - keep imports in that relative form or they won't be stamped.

**CORS gotcha.** After changing `AllowedOrigin`, SAM doesn't redeploy the API stage; run `aws apigateway create-deployment --rest-api-id <id> --stage-name prod`. Also persist it in `samconfig.toml` or a plain `sam deploy` resets it to `*`.

**Teardown gotcha.** `DataBucket` is `Retain`, so deleting a stack leaves the bucket behind. A new stack whose `DataBucketName`/`WebBucketName`/`GlueDatabaseName` already exist (left over, or owned by another stack) ends in `ROLLBACK_COMPLETE`; remove the old stack/bucket first. If the bucket was already removed, `sam delete` fails with `NoSuchBucket`; use `aws cloudformation delete-stack --stack-name venko-<key>` instead.
