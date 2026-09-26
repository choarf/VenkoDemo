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
- Python 3.12+ locally (for `sync_config.py`; `boto3` needed for the upload).
- The Akvo_Green gateway already publishing to AWS IoT Core (thing, certificate and policy exist). **No gateway code changes are required.**

---

## How to deploy

### 1. Deploy the AWS stack

```bash
cd infra
sam build --template template.yaml
sam deploy --guided --stack-name venko-demo --capabilities CAPABILITY_IAM
```

Accept the defaults, or set the parameters:

| Parameter | Default | Meaning |
|---|---|---|
| `DataTopic` / `SystemTopic` | `AKVO/data` / `AKVO/system` | Must match `aws.topic_pub` / `aws.topic_system` in the gateway `config.json` |
| `PartitionStartDate` | `2026-01-01` | First day Athena looks for data |
| `AllowedOrigin` | `*` | CORS origin; set to the dashboard URL after the first deploy (step 4) |
| `ReportTimezone` | `America/Mexico_City` | Time zone for weekly report boundaries |
| `GlueDatabaseName` | `venko_demo` | Athena database name |

`sam deploy` prints the outputs `ApiUrl`, `ApiKeyId`, `DataBucketName`, `WebBucketName` and `DashboardUrl`.

### 2. Check that data is arriving

With the gateway running (or by publishing a sample from **AWS IoT → MQTT test client** to `AKVO/data`):

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name venko-demo \
  --query "Stacks[0].Outputs[?OutputKey=='DataBucketName'].OutputValue" --output text)
aws s3 cp s3://$BUCKET/latest/data.json - | head -c 400; echo
aws s3 ls s3://$BUCKET/raw/data/ --recursive | tail -3     # appears within ~60 s (Firehose buffer)
```

If nothing appears, look in `s3://$BUCKET/errors/`, and check that the gateway's IoT policy allows `iot:Publish` on those topics.

Athena check (workgroup `venko-demo-wg`, database `venko_demo`):

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
tools/deploy_web.sh venko-demo
```

The script:
1. Runs `tools/sync_config.py`, which reads `Akvo_Green/config_data/config.json`, keeps only the gateway timing and sensors (the `aws`/`modbus` sections with certificate paths are **never** uploaded), merges `config/dashboard_overrides.json`, and uploads `s3://<data bucket>/config/config.json`.
2. Writes `web/app-config.js` with the API URL and API key (git-ignored).
3. Syncs `web/` to the web bucket and prints the dashboard URL.

### 4. Lock CORS to the dashboard URL (recommended)

```bash
cd infra
sam deploy --stack-name venko-demo --capabilities CAPABILITY_IAM \
  --parameter-overrides AllowedOrigin=https://dxxxxxxxxxxxx.cloudfront.net
```

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
aws lambda invoke --function-name venko-demo-report \
  --cli-binary-format raw-in-base64-out --payload '{"kind":"weekly","week":"2026-W39"}' /dev/stdout
```

---

## Changing sensors or labels

- **Add, remove or re-limit sensors:** edit the gateway config in Akvo_Green (its CSVs + `config_manager.py build`), deploy it to the Pi, then run `tools/deploy_web.sh` (or just `python3 tools/sync_config.py --bucket <data bucket>`). The API caches the config for up to 5 minutes.
- **Change display names, units, grouping or warn margin:** edit `config/dashboard_overrides.json` and run the same sync. Keys are `DEVICE_ID.SensorName`, e.g. `DEV_5.PhSensor`.
- **Preview the generated config without uploading:** `python3 tools/sync_config.py --out build/config.json`

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
| History/alarms error "query failed" | Athena console → workgroup `venko-demo-wg` → recent queries for the message |
| Report stuck on *Generando reporte…* | CloudWatch logs of `venko-demo-report`; use **Reintentar** |
| CORS error in browser console | `AllowedOrigin` must equal the exact dashboard origin |

**Data retention:** raw data moves to S3 Infrequent Access after 90 days and is never deleted automatically. Athena results and exports expire after 7 days. Reports are kept.

**Cost (demo scale, 1 gateway @ 20 s):** a few USD/month. Firehose, S3, Athena (a few MB scanned per query), Lambda and DynamoDB on-demand all stay within or near the free tiers.

**Security notes:**
- The API key is visible to anyone who can load the page. It only gates and throttles casual access. For a public demo, add Cognito login or CloudFront access restrictions.
- Highcharts requires a commercial license for commercial use.

**Remove everything:** empty both buckets, then `sam delete --stack-name venko-demo`.
