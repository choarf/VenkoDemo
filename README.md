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
config/dashboard_overrides.json  display labels, units, groups, warn margin per sensor (site 1)
config/sites/<key>.json        the same, for each additional site (see Adding a site)
tools/sync_config.py           Akvo_Green config.json → dashboard config (uploads to S3)
tools/deploy_web.sh            uploads the dashboard config + app-config.js and publishes web/ to S3/CloudFront
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
| `DataBucketName` | `venko-demo-<account>-<region>` | Name of the data bucket. Leave empty for site 1; every additional site sets its own. **Never change it on a running stack**: the old bucket (and its history) is retained and the stack starts over with an empty one |

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
tools/deploy_web.sh [stack] [gateway-config.json] [overrides.json]
tools/deploy_web.sh                      # site 1: sam-app, Akvo_Green/config_data/config.json, config/dashboard_overrides.json
```

The script:
1. Builds the dashboard config with `tools/sync_config.py`: reads the gateway `config.json`, keeps only the gateway timing and sensors (the `aws`/`modbus` sections with certificate paths are **never** uploaded) and merges the overrides.
2. **Checks the topics:** if the config's `topic_pub`/`topic_system` differ from the stack's `DataTopic`/`SystemTopic`, it stops and uploads nothing. This catches publishing the wrong site's (or a half-edited) config.
3. Uploads `s3://<data bucket>/config/config.json`.
4. Uploads `app-config.js` (API URL + key for that stack) straight to the web bucket. Nothing is written into `web/`, so sites can't overwrite each other.
5. Syncs `web/` to the web bucket and prints the dashboard URL.

Browsers may keep old dashboard files cached after an update; a hard reload (Ctrl+Shift+R) picks up the new version.

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

## Adding a site (another Pi + its own AWS stack and dashboard)

Each site is self-contained: **one Raspberry Pi, one AWS stack, one dashboard**. Sites share the AWS account, the IoT endpoint and the code (one Akvo_Green repo, one VenkoDemo repo); they share no data, buckets, API keys or URLs.

```
Site 1 (existing)                          Site "akvo"
Pi 1  client AKVO_Gateway                  Pi 2  client AKVO_Akvo
      topics AKVO/data, AKVO/system              topics VENKO/akvo/data, VENKO/akvo/system
Stack sam-app                              Stack venko-akvo
      data  venko-demo-884520769610-…            data  venko-akvo-884520769610-us-east-1
      web   https://ddlxtxblzvu22…               web   https://dsm04m3n65m8j.cloudfront.net
```

### Sites in this account

| | Site 1 | akvo |
|---|---|---|
| Gateway config | `Akvo_Green/config_data/` | `Akvo_Green/sites/akvo/config_data/` |
| Certificates | `Akvo_Green/gateway/certs/` | `Akvo_Green/sites/akvo/certs/` (git-ignored) |
| MQTT client / gateway id | `AKVO_Gateway` | `AKVO_Akvo` |
| Topics | `AKVO/data`, `AKVO/system` | `VENKO/akvo/data`, `VENKO/akvo/system` |
| IoT policy | `AllIoTAcessPolicy` (allows everything) | `venko-akvo-gateway` (only its client id + `VENKO/akvo/*`) |
| Stack / samconfig env | `sam-app` / `default` | `venko-akvo` / `akvo` |
| Data bucket | `venko-demo-884520769610-us-east-1` | `venko-akvo-884520769610-us-east-1` |
| Athena DB / workgroup | `venko_demo` / `sam-app-wg` | `venko_akvo` / `venko-akvo-wg` |
| Dashboard labels | `config/dashboard_overrides.json` | `config/sites/akvo.json` |
| Dashboard | https://ddlxtxblzvu22.cloudfront.net | https://dsm04m3n65m8j.cloudfront.net |

### Naming convention

Pick a short **site key** (lowercase, e.g. `akvo`) and derive every name from it:

| Item | Value |
|---|---|
| MQTT `client_id` = `gateway_id` | `AKVO_<Key>` - **must be unique**: two Pis with the same client id keep disconnecting each other |
| Topics | `VENKO/<key>/data`, `VENKO/<key>/system` |
| IoT policy / thing | `venko-<key>-gateway` / `AKVO_<Key>` |
| Stack / samconfig env | `venko-<key>` / `<key>` |
| Data bucket | `venko-<key>-<account>-<region>` |
| Web bucket | `venko-<key>-web-<account>` |
| Athena DB | `venko_<key>` |

### Step by step (example: key `plant2`)

**1. Gateway config** (dev machine, in Akvo_Green):

```bash
cd Akvo_Green
mkdir -p sites/plant2/config_data
cp config_data/modbus.csv sites/plant2/config_data/
cp config_data/Akvo_devices.csv sites/plant2/config_data/devices.csv   # or the site's own sensor list
# system.csv: gateway_id AKVO_Plant2 · aws.csv: client_id AKVO_Plant2, topics VENKO/plant2/data, VENKO/plant2/system
#   (copy them from sites/akvo/config_data/ and change those values)
cd gateway && python3 config_manager.py build --config-dir ../sites/plant2/config_data
```

`--config-dir` makes `build`/`export` use that folder; without it they use `config_data/` as always.

**2. Certificate, restricted policy and thing** (AWS IoT). The private key is only available at creation time; it goes into `sites/plant2/certs/` (git-ignored) and onto the Pi, nowhere else.

```bash
KEY=plant2; CLIENT=AKVO_Plant2; ACCT=884520769610
mkdir -p ../sites/$KEY/certs && cd ../sites/$KEY/certs && umask 077
aws iot create-keys-and-certificate --set-as-active \
  --certificate-pem-outfile certificate.pem.crt --private-key-outfile private.pem.key \
  --query certificateArn --output text > cert.arn
cp ../../../gateway/certs/AmazonRootCA1.pem .
cat > policy.json <<EOF
{ "Version": "2012-10-17", "Statement": [
  { "Effect": "Allow", "Action": "iot:Connect", "Resource": "arn:aws:iot:us-east-1:$ACCT:client/$CLIENT" },
  { "Effect": "Allow", "Action": "iot:Publish", "Resource": "arn:aws:iot:us-east-1:$ACCT:topic/VENKO/$KEY/*" } ] }
EOF
aws iot create-policy --policy-name venko-$KEY-gateway --policy-document file://policy.json
aws iot attach-policy --policy-name venko-$KEY-gateway --target "$(cat cert.arn)"
aws iot create-thing --thing-name $CLIENT
aws iot attach-thing-principal --thing-name $CLIENT --principal "$(cat cert.arn)"
```

With this policy the Pi can only connect as its own client id and only publish to its own topics; a wrong `aws.csv` shows up as a connection error in the gateway log instead of data landing in another site.

**3. AWS stack** (VenkoDemo). Add a section to `infra/samconfig.toml` (copy `[akvo.*]` and change the key), then:

```bash
cd infra
sam build --template template.yaml
sam deploy --config-env plant2
```

**4. Dashboard labels and publish.** Create `config/sites/plant2.json` (copy `config/sites/akvo.json`: title, units, one entry per `DEVICE_ID.SensorName`), then:

```bash
bash tools/deploy_web.sh venko-plant2 ../Akvo_Green/sites/plant2/config_data/config.json config/sites/plant2.json
```

A brand-new CloudFront URL can return 403 for a few minutes until it has propagated.

**5. Lock CORS** to the new `DashboardUrl`: set `AllowedOrigin` in the `[plant2.deploy.parameters]` section, then

```bash
sam deploy --config-env plant2
API_ID=$(aws cloudformation describe-stack-resource --stack-name venko-plant2 \
  --logical-resource-id Api --query StackResourceDetail.PhysicalResourceId --output text)
aws apigateway create-deployment --rest-api-id "$API_ID" --stage-name prod
```

**6. Install the Pi** (Raspberry Pi OS, network + SSH working, RS-485 adapter on `/dev/ttyUSB0`). From the dev machine:

```bash
cd Akvo_Green
tools/push_site.sh plant2 pi@<pi-address> --certs      # code + config.json + certs
ssh pi@<pi-address> 'cd Akvo_Green && ./install.sh --skip-certs && sudo systemctl start akvo-green'
```

`push_site.sh` syncs the code without touching the Pi's `config_data/`, `data/`, `logs/`, `.venv/` or certs, copies the site's `config.json`, and (with `--certs`) its certificates. `install.sh` sets up packages, the venv, serial-port access and the `akvo-green` service; it only needs to run once per Pi.

**7. Check** (within ~1 minute of the service starting):

```bash
aws s3 cp s3://venko-plant2-884520769610-us-east-1/latest/data.json - | head -c 300; echo
```

Then open the dashboard: **Gateway en línea**, dials filling in. If nothing arrives: `journalctl -u akvo-green -f` on the Pi (a policy refusal shows as a connection error), the certificate is ACTIVE, and `aws.csv`'s client id/topics match the policy and the stack.

### Day to day with several sites

| Task | Site 1 | Another site (`<key>`) |
|---|---|---|
| Sensor/label change | edit `Akvo_Green/config_data/*.csv` → `build` | edit `sites/<key>/config_data/*.csv` → `build --config-dir ../sites/<key>/config_data` |
| Send config/code to the Pi | `tools/push_site.sh root pi@<pi1>` | `tools/push_site.sh <key> pi@<pi>` |
| Dashboard config | `bash tools/deploy_web.sh` | `bash tools/deploy_web.sh venko-<key> <its config.json> config/sites/<key>.json` |
| Stack/template change | `sam deploy` | `sam deploy --config-env <key>` |
| Dashboard code change (`web/`) | run `deploy_web.sh` for **every** site | |
| Disable a lost Pi | `aws iot update-certificate --certificate-id <id> --new-status INACTIVE` | same, with that Pi's certificate id |

### Removing a site

```bash
aws s3 rm s3://venko-<key>-web-<account> --recursive
sam delete --stack-name venko-<key>          # the data bucket is retained (DeletionPolicy: Retain)
```

Then detach and delete the IoT policy, thing and certificate if the Pi is gone for good, and delete the `[<key>.*]` samconfig section. Empty and delete the data bucket only if its history is no longer needed.

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
