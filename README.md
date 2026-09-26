# VenkoDemo

End-to-end demo built on the **Akvo Green** edge gateway:

```
Raspberry Pi (Akvo_Green, unchanged) ──MQTT/mTLS──▶ AWS IoT Core
   IoT Rule  AKVO/data   ─▶ Firehose ─▶ S3 raw/data/dt=YYYY-MM-DD/   (history, Athena)
                         └▶ S3 latest/data.json                      (live view)
   IoT Rule  AKVO/system ─▶ Firehose ─▶ S3 raw/system/…  + latest/system.json
API Gateway + Lambda (Python) ◀── Web dashboard (Highcharts, S3 + CloudFront)
DynamoDB: test events + operator comments
EventBridge (Mon 06:00 local) ─▶ report Lambda ─▶ S3 reports/weekly/<YYYY-Www>/
```

What the dashboard does:

| View | What it shows |
|---|---|
| **Resumen** (free run) | One dial per sensor from `config.json` (limits, warn/alarm colours), KPIs, system data table, active alarms. Monitoring only; nothing is recorded as a test. |
| **Prueba (HMI)** | Start/Stop test events, elapsed time, live trend since start, comments per sensor or general, recent tests with report links. |
| **Alarmas** | Active alarms, alarm history (episodes) for 24 h / 7 d / 30 d, click a row to see the sensor around the event. |
| **Históricos** | Any time range + any sensors → chart; export to Excel or CSV. |
| **Reportes** | Test reports (created on Stop) and weekly reports, as HTML (printable to PDF) and Excel. |

All data shown comes from the real gateway through AWS IoT. The only exception is the local mock server (see [Local development](#local-development)), which is clearly marked as synthetic.

---

## Repository layout

```
infra/template.yaml            SAM template: S3, Firehose, IoT rules, Glue/Athena, DynamoDB, Lambdas, API, CloudFront
backend/api/                   Lambda handlers (router.py = API entry, reports.py = report Lambda)
backend/common/                Athena, S3, DynamoDB helpers, SQL builders, report builder (HTML + XLSX)
backend/templates/             report.html.j2
backend/tests/                 pytest unit tests (no AWS needed)
config/dashboard_overrides.json  display labels, units, groups, warn margin per sensor
tools/sync_config.py           Akvo_Green config.json → dashboard config (uploads to S3)
tools/deploy_web.sh            writes web/app-config.js and publishes web/ to S3/CloudFront
tools/mock_api.py              local UI server with SYNTHETIC data (development only)
web/                           dashboard (index.html, css/, js/)
```

---

## Prerequisites

- An AWS account in the same region as the gateway's IoT endpoint (`us-east-1`, from `config.json` → `aws.host`).
- AWS CLI v2 with credentials that can create IAM roles, and the **AWS SAM CLI**.
- Python 3.14 locally (matches the Lambda runtime, which `sam build` requires) (for `sync_config.py`; `boto3` needed for the upload).
- The Akvo_Green gateway already publishing to AWS IoT Core (thing, certificate and policy exist). **No gateway code changes are required.**

---

## How to deploy

### 1. Deploy the AWS stack

```bash
cd infra
sam build --template template.yaml
sam deploy --guided --stack-name sam-app --capabilities CAPABILITY_IAM
```

Accept the defaults, or set the parameters:

| Parameter | Default | Meaning |
|---|---|---|
| `DataTopic` / `SystemTopic` | `AKVO/data` / `AKVO/system` | Must match `aws.topic_pub` / `aws.topic_system` in the gateway `config.json` |
| `PartitionStartDate` | `2026-01-01` | First day Athena looks for data |
| `AllowedOrigin` | `*` | CORS origin; set to the dashboard URL after the first deploy (step 4) |
| `ReportTimezone` | `America/Mexico_City` | Time zone for weekly report boundaries |
| `GlueDatabaseName` | `venko_demo` | Athena database name |
| `WebBucketName` | *(auto-generated)* | Name of the dashboard bucket. This stack uses `venko-demo-web-884520769610` (set in `samconfig.toml`). Changing it creates a new, empty bucket, so rerun `tools/deploy_web.sh` straight after |

`sam deploy` prints the outputs `ApiUrl`, `ApiKeyId`, `DataBucketName`, `WebBucketName` and `DashboardUrl`.

### 2. Check that data is arriving

With the gateway running (or by publishing a sample from **AWS IoT → MQTT test client** to `AKVO/data`):

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name sam-app \
  --query "Stacks[0].Outputs[?OutputKey=='DataBucketName'].OutputValue" --output text)
aws s3 cp s3://$BUCKET/latest/data.json - | head -c 400; echo
aws s3 ls s3://$BUCKET/raw/data/ --recursive | tail -3     # appears within ~60 s (Firehose buffer)
```

If nothing appears, look in `s3://$BUCKET/errors/`, and check that the gateway's IoT policy allows `iot:Publish` on those topics.

Athena check (workgroup `sam-app-wg`, database `venko_demo`):

```sql
SELECT from_unixtime(rx_ms/1000) AS t, d.device, s.sensor, r.val, r.status, r.alarm
FROM readings_raw
CROSS JOIN UNNEST(devices) AS d(device, sensors)
CROSS JOIN UNNEST(d.sensors) AS s(sensor, r)
WHERE dt = cast(current_date AS varchar)
ORDER BY rx_ms DESC LIMIT 20;
```

### 3. Publish the sensor config and the dashboard

```bash
tools/deploy_web.sh sam-app
```

The script:
1. Runs `tools/sync_config.py`, which reads `Akvo_Green/config_data/config.json`, keeps only the gateway timing and sensors (the `aws`/`modbus` sections with certificate paths are **never** uploaded), merges `config/dashboard_overrides.json`, and uploads `s3://<data bucket>/config/config.json`.
2. Writes `web/app-config.js` with the API URL and API key (git-ignored).
3. Syncs `web/` to the web bucket and prints the dashboard URL.

### 4. Lock CORS to the dashboard URL (recommended)

```bash
cd infra
sam deploy --stack-name sam-app --capabilities CAPABILITY_IAM \
  --parameter-overrides AllowedOrigin=https://dxxxxxxxxxxxx.cloudfront.net

# SAM does not republish the API stage when only a parameter changes, so the
# CORS preflight keeps the old origin until the stage is redeployed:
API_ID=$(aws cloudformation describe-stack-resource --stack-name sam-app \
  --logical-resource-id Api --query StackResourceDetail.PhysicalResourceId --output text)
aws apigateway create-deployment --rest-api-id "$API_ID" --stage-name prod
```

Put the same `AllowedOrigin` in `infra/samconfig.toml` (`parameter_overrides`), otherwise a plain `sam deploy` resets it to `*`.

Open `DashboardUrl`. The header pill should show **Gateway en línea**.

---

## How to use the dashboard

### Free-run monitoring
Open **Resumen**. The dials update every 10 s (the gateway publishes every `poll_interval` = 20 s).
- **Blue** is normal. **Orange** means the value is within the warn margin (default 10 % of the range) of a limit.
- **Red** with a red outline means alarm: the gateway flagged LOW/HIGH against `min`/`max` in `config.json`.
- **ERR** with a dashed outline means a read error (`BUS_ERROR`/`EXCEPTION`).
- If no message arrives for 3 poll intervals, the pill turns red (**Gateway sin datos**).

### Running a test
1. Go to **Prueba (HMI)** and fill in *Nombre*, *Operador* and optional *Notas*.
2. Click **▶ Iniciar prueba**. The header shows *Prueba en curso* with the elapsed time on every view and every browser. Only one test can run at a time.
3. While it runs, add **comments**. Choose a sensor, or *General*, for notes such as "valve adjusted" or "sample taken". Comments are linked to the running test automatically.
4. Click **■ Detener prueba**, then click again to confirm.
5. The report is generated in the background (about 30–90 s). When it's ready, *Pruebas recientes* and **Reportes** show **HTML · Excel** links. If generation fails, a **Reintentar** link appears.

The test report covers the test window and includes: per-sensor statistics (samples, availability, min/avg/max/std, first/last, % time in alarm), trends, alarm events, comments, data gaps and system health.

### Alarms
**Alarmas** lists active alarms and the history of alarm *episodes*. Consecutive out-of-range samples are merged into one event with start, end, duration and peak. Click a row to open the sensor's trend around that event.

### Querying stored data
In **Históricos**, pick a range (or 1 h / 6 h / 24 h / 7 d), select sensors, then click **Consultar**. Points are averaged per bucket, sized automatically for the range. **Excel** / **CSV** exports the same range. Ranges are limited to 31 days.

### Weekly report
Generated automatically every Monday at 06:00 (Mexico City) for the previous ISO week (Mon–Sun), in HTML and Excel. To create one on demand, go to **Reportes**, enter a week (e.g. `2026-W39`, or leave it empty for last week) and click **Generar**. Or run:

```bash
aws lambda invoke --function-name sam-app-report \
  --cli-binary-format raw-in-base64-out --payload '{"kind":"weekly","week":"2026-W39"}' /dev/stdout
```

---

## Changing sensors or labels

When Akvo_Green changes (more sensors, a new unit, new limits), the AWS stack does **not** need redeploying. Athena stores each message's sensors as a map, so new sensors appear in history, alarms and reports automatically. Only the gateway config and the dashboard config need updating.

### The config pipeline

```
Akvo_Green/config_data/*.csv ──config_manager.py build──▶ Akvo_Green/config_data/config.json ──▶ Pi (gateway)
                                                                      │
config/dashboard_overrides.json ──────────────────────────────────────┤
                                                                      ▼
                                              tools/sync_config.py ──▶ s3://<data bucket>/config/config.json
                                                                                  (read by the API and reports)
```

| Input | Sets |
|---|---|
| `devices.csv` | One row per sensor (grouped by `device_id`): Modbus slave/register, `scale`/`offset`, `unit`, `type`, alarm `min`/`max`, `enabled` |
| `modbus.csv` | Serial port settings |
| `system.csv` | `gateway_id`, time zone, poll/system intervals, watchdog |
| `aws.csv` | IoT endpoint, MQTT `client_id`, cert paths, topics |
| `config/dashboard_overrides.json` | Display only: site title, unit display names, per-sensor label/group/decimals |

`Akvo_devices.csv` / `Osmosis_devices.csv` are saved sensor sets and are **not** read by the build. To switch sets, copy one over `devices.csv` and rebuild. The `aws` and `modbus` sections are never uploaded to S3.

### Every change: three steps

**1. Edit the CSV and rebuild the gateway config:**

```bash
cd ~/git_reps/claude/projects/Venko/Akvo_Green/gateway
# edit ../config_data/devices.csv
python3 config_manager.py build --dry-run   # validate and print
python3 config_manager.py build             # writes config_data/config.json
```

**2. Copy the new `config.json` to the Pi's `config_data/`** (e.g. with `scp`). The gateway reloads it within 5 s without a restart.

**3. Update the dashboard labels and publish the dashboard config:**

```bash
cd ~/git_reps/claude/projects/Venko/VenkoDemo
# edit config/dashboard_overrides.json (only if labels/units/groups changed)
python3 tools/sync_config.py --out build/config.json   # preview
python3 tools/sync_config.py --bucket venko-demo-884520769610-us-east-1
```

- The API and reports cache the config for up to **5 minutes**. Reload the dashboard after that.
- `tools/deploy_web.sh` also runs this step, but it re-uploads `web/` as well. It's only needed when the dashboard code changes.
- `sync_config.py` reads Akvo_Green's `config.json` **on this machine**, so always build it here (step 1), even if you edited it on the Pi.

### Common changes

**Add a sensor.** Add a row to `devices.csv`:

```
device_id,slave,sensor_name,addr,count,scale,offset,unit,type,min,max,enabled
DEV_6,4,Caudal,0,1,1,0,L/min,float,0,200,1
```

and its entry in `dashboard_overrides.json`:

```json
"DEV_6.Caudal": { "label": "Caudal", "group": "Flujo", "decimals": 1 }
```

Keys are always `DEVICE_ID.SensorName` and are case-sensitive. A sensor without an override still shows up, with its raw name and unit.

**Only rename a displayed unit or label** (e.g. `Mpa` → `kPa`): step 3 only. Edit `units` or the sensor's entry in the overrides. The gateway and the stored values are unchanged.

**Actually change the unit or scale** (e.g. show 20.3 °C instead of 203):

```
DEV_2,3,TempAgua,0,1,0.1,0,Celsius,float,0,50,1
```

- Change `scale`, `unit` **and** `min`/`max` together. Alarm limits apply to the value *after* scaling.
- Keep `type` as `float`. `int`/`uint16` ignore `scale`.
- Update the override too, e.g. `"decimals": 1`. `"Celsius": "°C"` is already in `units`.
- Stored data **keeps the old scale**, so a chart or report covering the change mixes 203 and 20.3. Make the change between tests.

**Disable a sensor.** Set `enabled` to `0`. It leaves the dashboard after step 3. Its old data stays in S3 and is still queryable in Athena.

**Gateway name, intervals or time zone.** Edit `system.csv` and run the three steps. The report's "Gateway" field is `gateway_id` from `system.csv`. The `gw` column on stored messages is the MQTT `client_id` from `aws.csv`, added by the IoT rule. Don't change `client_id` unless the IoT policy allows the new name.

**MQTT topics** (`topic_pub`/`topic_system` in `aws.csv`) are the one change that needs `sam deploy`:

```bash
cd infra
sam deploy --parameter-overrides DataTopic=NEW/data SystemTopic=NEW/system
```

Also update the topics in `infra/samconfig.toml`, or the next plain `sam deploy` puts the old ones back.

### Check afterwards

- **Resumen:** the new or changed dial shows the right label and unit.
- **Históricos:** the new sensor's chip is there. Its history starts when the Pi began sending it.
- A sensor showing its raw name means the override key doesn't match. `sync_config.py` lists unmatched keys as `warning: overrides for sensors not in config: …`. Entries kept for another sensor set also appear there, which is expected.
- Finished test reports keep the labels they were built with. Use **Reintentar** in Reportes to rebuild one with the current config.

---

## Local development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt boto3 pytest

# unit tests (SQL builders, time helpers, alarm episodes, HTML/XLSX reports, API routing)
cd backend && python -m pytest -q && cd ..

# dashboard against a MOCK API with synthetic data - UI work only
python3 tools/mock_api.py        # → http://localhost:8787
```

The mock serves `web/` and fakes every API route, including Start/Stop and report generation with the real report builder. **Excel/CSV export** from Históricos is not available in the mock.

To point a locally served dashboard at the real API instead, copy `web/app-config.example.js` to `web/app-config.js`, fill in `apiBase`/`apiKey`, and run `python3 -m http.server -d web 8000`. `AllowedOrigin` must allow `http://localhost:8000`.

---

## API reference

All routes require the header `X-Api-Key`. Times are epoch ms or ISO-8601.

| Method & path | Description |
|---|---|
| `GET /config` | Sensor/gateway config used by the dashboard |
| `GET /live` | Latest values, active alarms, system data, `online`, `age_s` |
| `GET /history?from&to&sensors=DEV_5.PhSensor,…&bucket_ms` | Bucketed avg/min/max per sensor |
| `GET /alarms?from&to&sensors` | Alarm episodes |
| `GET /export?from&to&sensors&format=xlsx\|csv` | Presigned download URL |
| `GET /tests` · `GET /tests/active` · `GET /tests/{id}` | Tests (with comments) |
| `POST /tests/start` `{name, operator, notes}` | Start a test (409 if one is running) |
| `POST /tests/{id}/stop` `{operator}` | Stop a test and generate its report |
| `POST /tests/{id}/report` | Regenerate a test report |
| `GET /comments?test_id` or `?from&to` · `POST /comments` `{text, author, sensor?, test_id?}` | Comments |
| `GET /reports` · `GET /reports/url?key=reports/…` · `POST /reports/weekly` `{week?}` | Reports |

---

## Operations & troubleshooting

| Symptom | Check |
|---|---|
| Dashboard shows "No se pudo cargar la configuración" | `web/app-config.js` values; `config/config.json` missing → run `sync_config.py` |
| Pill says *Gateway sin datos* | Gateway running and connected? `latest/data.json` timestamp; `errors/iot/` in the bucket |
| History empty but live works | Firehose buffers ~60 s; check `raw/data/dt=…/` exists; `PartitionStartDate` not after your data |
| History/alarms error "query failed" | Athena console → workgroup `sam-app-wg` → recent queries for the message |
| Report stuck on *Generando reporte…* | CloudWatch logs of `sam-app-report`; use **Reintentar** |
| CORS error in browser console | `AllowedOrigin` must equal the exact dashboard origin |

**Data retention:** raw data moves to S3 Infrequent Access after 90 days and is never deleted automatically. Athena results and exports expire after 7 days. Reports are kept.

**Cost (demo scale, 1 gateway @ 20 s):** a few USD/month. Firehose, S3, Athena (a few MB scanned per query), Lambda and DynamoDB on-demand all stay within or near the free tiers.

**Security notes:**
- The API key is visible to anyone who can load the page. It only gates and throttles casual access. For a public demo, add Cognito login or CloudFront access restrictions.
- Highcharts requires a commercial license for commercial use.

**Remove everything:** empty the web bucket, then `sam delete --stack-name sam-app`. The data bucket (`venko-demo-<account>-<region>`) has `DeletionPolicy: Retain`, so the stack delete leaves it and its sensor history in place. Empty and delete it by hand only if you really want the data gone.
