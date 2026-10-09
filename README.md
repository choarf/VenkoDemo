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
tools/site_summary.py          writes docs/sites/<key>.md: one site's AWS resources, URLs, IoT, live status, commands
docs/sites/<key>.md            generated site summaries, plus README.md overview of all sites (see Adding a site, step 8)
web/                           dashboard (index.html, css/, js/)
```

## S3 bucket contents

Each site has two buckets (names per site in [Adding a site](#adding-a-site-another-pi--its-own-aws-stack-and-dashboard)); every site uses the same layout.

### Data bucket (`venko-demo-<account>-<region>` for site 1)

| Prefix | Written by | Contents |
|---|---|---|
| `raw/data/dt=YYYY-MM-DD/` | IoT rule -> Firehose | Every `AKVO/data` message, one JSON line per message, tagged with `gw` (MQTT client id) and `rx_ms` (receive time) - the Athena `readings_raw` table's source |
| `raw/system/dt=YYYY-MM-DD/` | IoT rule -> Firehose | Every `AKVO/system` message, same tagging - the `system_raw` table's source |
| `latest/data.json` / `latest/system.json` | IoT rule's direct S3 action | The single most recent message of each, overwritten every time - what **Resumen**'s live dials and the online/offline pill read, without an Athena query |
| `errors/iot/data/` / `errors/iot/system/` | IoT rule's `ErrorAction` | A message lands here if the Firehose/S3 action rejected it. Empty means no ingestion failures |
| `athena-results/` | Athena (workgroup `<stack>-wg`) | Raw query result files. Expires after 7 days |
| `config/config.json` | `tools/sync_config.py` | The sensor/gateway config the API and dashboard read - see [Changing sensors or labels](#changing-sensors-or-labels) |
| `exports/<epoch-ms>/` | the API (`GET /export`) | Históricos' Excel/CSV export files, served via a presigned URL. Expires after 7 days |
| `reports/tests/T<timestamp>/` | report Lambda, on test stop | `meta.json` + `report.html` + `report.xlsx` for that test |
| `reports/weekly/<YYYY-Www>/` | report Lambda, Monday cron | Same three files for that ISO week |

`raw/` additionally moves to Infrequent Access after 90 days and is kept forever; reports are kept forever too. See **Data retention** under [Operations & troubleshooting](#operations--troubleshooting) for the full policy.

### Web bucket (`venko-demo-web-884520769610` for site 1)

Just the dashboard's static files: `index.html`, `css/`, `js/`, plus `app-config.js` (that site's API URL and key, written by `tools/deploy_web.sh` - see step 3 under [How to deploy](#how-to-deploy)). The bucket blocks all public access; only that site's CloudFront distribution can read it (`WebBucketPolicy` scopes `s3:GetObject` to its Origin Access Control).

---

## No hardware yet? Demo with virtual Modbus

Akvo_Green has an optional, config-driven simulate mode (`modbus.csv`'s `simulate_enabled` column) - the gateway generates synthetic, bounded-random-walk readings for every sensor in its own `devices.csv` (occasionally spiking out of range to exercise alarms) instead of reading a real serial port. No `socat`, no mock process, works on a bare Pi, a plain VM, or a container.

Nothing on this side (VenkoDemo) needs to change - IoT Core, Firehose, S3, Glue/Athena and the API are all topic-based and don't care whether the gateway behind them is real or simulated. The only difference reaching here is one extra field: every simulated payload carries `"simulated": true`, preserved all the way through to `raw/data/`/`raw/system/` and Athena, so synthetic and real history can always be told apart later, not just at a glance on the dashboard. (The dashboard itself has no visual "DEMO DATA" badge yet - worth adding to `js/modules/overview.js` if this gets used for a live demo in front of people who might mistake it for a running plant.)

See Akvo_Green's `docs/edge_node_config/CONFIGURATION.md` ("Virtual Modbus") for the full field reference. In short: set `simulate_enabled=1` in that site's `modbus.csv`, rebuild, restart the gateway (it's read once at startup, like `database.*`) - everything from here down, including this README's deploy steps, works completely unchanged.

Every site's `modbus.csv` has the column explicitly, so a site's mode is visible at a glance (current values in [Sites in this account](#sites-in-this-account)):

```csv
port,baudrate,timeout,parity,stopbits,bytesize,simulate_enabled
/dev/ttyUSB0,9600,1.0,N,1,8,0
```

- `0` (or blank, or the column missing in an older file) = read the real sensors on `port`. `1`/`true`/`yes`/`on` = simulate.
- **Simulated sites show alarms constantly** - the simulator deliberately pushes values past `min`/`max`. That's not a limit or wiring problem.
- **Is a site simulated right now?** Check what it's actually publishing, not just the CSV (the Pi may run an older build):

  ```bash
  aws s3 cp s3://<data bucket>/latest/data.json - | python3 -c "import json,sys; print(json.load(sys.stdin).get('simulated', False))"
  ```

- **Switching a site to real sensors:** set `simulate_enabled=0`, `build --config-dir ../sites/<key>/config_data`, `tools/push_site.sh <key> pi@<pi>`, then `sudo systemctl restart akvo-green` on the Pi (a config reload alone doesn't change the mode). History recorded while simulated stays tagged `"simulated": true` in S3/Athena; reports over that period include it, so start real tests after the switch.
- Watch out: site 1's `config_data/modbus.csv` uses Windows (CRLF) line endings. Edit it in an editor, not with a blind `sed '…s/$/…/'`, or the new value lands after the hidden `\r` and `build` fails with `invalid literal for int()`.

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
| `WebBucketName` | *(auto-generated)* | Name of the dashboard bucket. This stack uses `venko-demo-web-884520769610` (set in `samconfig.toml`). Changing it creates a new, empty bucket, so rerun `tools/deploy_web.sh` straight after. **Must be different from `DataBucketName`** - see note below |
| `DataBucketName` | `venko-demo-<account>-<region>` | Name of the data bucket. Leave empty for site 1; every additional site sets its own. **Never change it on a running stack**: the old bucket (and its history) is retained and the stack starts over with an empty one. **Must be different from `WebBucketName`** - see note below |

`sam deploy` prints the outputs `ApiUrl`, `ApiKeyId`, `DataBucketName`, `WebBucketName` and `DashboardUrl`.

`WebBucketName` and `DataBucketName` create two separate S3 buckets - giving them the same value makes the template try to create two buckets with one name, which `sam deploy` rejects while building the changeset, before you even reach the confirmation prompt (`Failed to create changeset ... AWS::EarlyValidation::ResourceExistenceCheck`). Leave both blank for the defaults above, or set two distinct, globally-unique names.

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
| `modbus.csv` | Serial port settings, plus `simulate_enabled` (`0` = read the real sensors, `1` = synthetic demo data - see [No hardware yet?](#no-hardware-yet-demo-with-virtual-modbus)) |
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

| | Site 1 | akvo | cmd4 |
|---|---|---|---|
| Gateway config | `Akvo_Green/config_data/` | `Akvo_Green/sites/akvo/config_data/` | `Akvo_Green/sites/cmd4/config_data/` |
| Certificates | `Akvo_Green/gateway/certs/` | `Akvo_Green/sites/akvo/certs/` (git-ignored) | `Akvo_Green/sites/cmd4/certs/` (git-ignored) |
| MQTT client / gateway id | `AKVO_Gateway` | `AKVO_Akvo` | `AKVO_cmd4` |
| Topics | `AKVO/data`, `AKVO/system` | `VENKO/akvo/data`, `VENKO/akvo/system` | `VENKO/cmd4/data`, `VENKO/cmd4/system` |
| IoT policy | `AllIoTAcessPolicy` (allows everything) | `venko-akvo-gateway` (only its client id + `VENKO/akvo/*`) | `venko-cmd4-gateway` (only its client id + `VENKO/cmd4/*`) |
| Stack / samconfig env | `sam-app` / `default` | `venko-akvo` / `akvo` | `venko-cmd4` / `cmd4` |
| Data bucket | `venko-demo-884520769610-us-east-1` | `venko-akvo-884520769610-us-east-1` | `venko-cmd4-884520769610-us-east-1` |
| Athena DB / workgroup | `venko_demo` / `sam-app-wg` | `venko_akvo` / `venko-akvo-wg` | `venko_cmd4` / `venko-cmd4-wg` |
| Dashboard labels | `config/dashboard_overrides.json` | `config/sites/akvo.json` | `config/sites/cmd4.json` |
| Dashboard | https://ddlxtxblzvu22.cloudfront.net | https://dsm04m3n65m8j.cloudfront.net | https://d29127v2virrqh.cloudfront.net |
| Gateway on a Pi | yes - publishing | **no data received yet** (`latest/` empty, checked 2026-10-05) | yes - Pi `192.168.68.129`, publishing |
| `modbus.csv` `simulate_enabled` | `0` - real sensors | `0` - real sensors | **`1` - SIMULATED data** (every payload tagged `"simulated": true`) |
| Local dashboard on the Pi (`web_enabled`) | off | off | http://192.168.68.129:8080 |

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

### Step by step (worked example: site `cmd4`)

Every step below is the actual sequence run for a real site in this account, `cmd4`, with the exact parameters and the result each step left behind - not a hypothetical. Steps 1-7 are done; the Pi (`192.168.68.129`) runs in **simulate mode** (`simulate_enabled=1`), so this site's dashboard shows synthetic data until it's switched to real sensors.

**1. Gateway config** (dev machine, in Akvo_Green):

```bash
cd Akvo_Green
mkdir -p sites/cmd4/config_data
cp config_data/modbus.csv sites/cmd4/config_data/     # then set simulate_enabled: 1 for a demo without sensors, 0 for real ones
cp config_data/Akvo_devices.csv sites/cmd4/config_data/devices.csv
# system.csv: gateway_id AKVO_cmd4 · aws.csv: client_id AKVO_cmd4, topics VENKO/cmd4/data, VENKO/cmd4/system
cd gateway && python3 config_manager.py build --config-dir ../sites/cmd4/config_data
```

- **Parameters used:** site key `cmd4`; sensor set `Akvo_devices.csv` reused as-is (the same 15 sensors `akvo` uses - `Pt100Sensor`, `Ambient`, `Humedad`, `ORPSensor`, `PhSensor`, `TempPhSensor`, `DOSensor`, `CODSensor`, `TempCODSensor`, `TurbCODSensort`, `BODSensor`, `TOCSensor`, `CondSensor`, `TempCondSensor`, `TDSCondSensor`); `gateway_id`/`client_id` = `AKVO_cmd4`; topics = `VENKO/cmd4/data` / `VENKO/cmd4/system`.
- **Pitfall hit:** the four CSVs were first copied into `sites/cmd4/` directly instead of `sites/cmd4/config_data/` - `build --config-dir ../sites/cmd4/config_data` then fails with `FileNotFoundError: .../sites/cmd4/config_data/devices.csv` because that folder was empty. Fix: `mv sites/cmd4/{devices,modbus,system,aws}.csv sites/cmd4/config_data/`.
- **Outcome:** `sites/cmd4/config_data/config.json` built - `Loaded 15 devices`, `Validation successful` (with the same duplicate-slave-ID *warnings* `akvo`/site 1 also get, since several sensors share one physical Modbus unit - harmless).

**2. Certificate, restricted policy and thing** (AWS IoT). The private key is only available at creation time; it goes into `sites/cmd4/certs/` (git-ignored) and onto the Pi, nowhere else.

```bash
KEY=cmd4; CLIENT=AKVO_cmd4; ACCT=884520769610
mkdir -p sites/$KEY/certs && cd sites/$KEY/certs && umask 077
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

Run from the repo root here (not from inside `gateway/`, unlike the original walkthrough), so the first two paths drop their `../`.

- **Parameters used:** `KEY=cmd4` (must match the topics set in step 1: `VENKO/cmd4/*`), `CLIENT=AKVO_cmd4` (must match step 1's `aws.csv` `client_id` / `system.csv` `gateway_id`), `ACCT=884520769610`.
- **Outcome:** certificate `arn:...:cert/e494e73e...ff9066f`, status `ACTIVE`; policy `venko-cmd4-gateway` attached to it (`iot:Connect` scoped to client `AKVO_cmd4`, `iot:Publish` scoped to `VENKO/cmd4/*`); thing `AKVO_cmd4` created with that certificate as its principal. Five files in `sites/cmd4/certs/`, all mode `600`: `certificate.pem.crt`, `private.pem.key`, `AmazonRootCA1.pem`, `cert.arn`, `policy.json`.

**3. AWS stack** (VenkoDemo). Add a section to `infra/samconfig.toml` (copy `[akvo.*]` and change the key):

```toml
[cmd4.deploy.parameters]
stack_name = "venko-cmd4"
resolve_s3 = true
s3_prefix = "venko-cmd4"
region = "us-east-1"
confirm_changeset = true
capabilities = "CAPABILITY_IAM"
parameter_overrides = "DataTopic=\"VENKO/cmd4/data\" SystemTopic=\"VENKO/cmd4/system\" PartitionStartDate=\"2026-10-04\" AllowedOrigin=\"*\" ReportTimezone=\"America/Mexico_City\" GlueDatabaseName=\"venko_cmd4\" DataBucketName=\"venko-cmd4-884520769610-us-east-1\" WebBucketName=\"venko-cmd4-web-884520769610\""
image_repositories = []

[cmd4.global.parameters]
region = "us-east-1"
```

then:

```bash
cd infra
sam build --template template.yaml
sam deploy --config-env cmd4
```

- **Parameters used:** `DataTopic`/`SystemTopic` matching step 1 exactly; `PartitionStartDate` = today; `AllowedOrigin` left as `*` for now (locked down in step 5); `GlueDatabaseName=venko_cmd4`, `DataBucketName=venko-cmd4-884520769610-us-east-1`, `WebBucketName=venko-cmd4-web-884520769610` - following the [Naming convention](#naming-convention) table exactly.
- **Pitfalls hit:**
  - `sam build --template cmd4.yaml` fails - `Template file not found`. There is only ever **one** `template.yaml`, shared by every site; only `samconfig.toml`'s per-env section differs. Use `sam build --template template.yaml`.
  - `sam deploy --config-env cmd4` fails with `Missing option '--stack-name'` until the `[cmd4.*]` section above actually exists in `samconfig.toml` - `--config-env` has nothing to read otherwise.
  - Even with that fixed, deploy can fail mid-upload with `Error: Unable to upload artifact ... S3 Bucket does not exist`, despite the upload progress bar showing 100%. This means the account's shared SAM artifact bucket (`aws-sam-cli-managed-default-samclisourcebucket-...`) was deleted outside of CloudFormation, while the `aws-sam-cli-managed-default` stack that "owns" it still reports `CREATE_COMPLETE` - CloudFormation doesn't notice the drift on its own. Fix (affects every site's next deploy, not just this one): delete that stale stack and let SAM recreate it fresh on the next deploy -
    ```bash
    aws cloudformation delete-stack --stack-name aws-sam-cli-managed-default --region us-east-1
    aws cloudformation wait stack-delete-complete --stack-name aws-sam-cli-managed-default --region us-east-1
    ```
- **Outcome:** stack `venko-cmd4` → `CREATE_COMPLETE`. Outputs: `ApiUrl=https://4f9o3ozqu2.execute-api.us-east-1.amazonaws.com/prod`, `ApiKeyId=6wrs4s8nh3`, `DataBucketName=venko-cmd4-884520769610-us-east-1`, `WebBucketName=venko-cmd4-web-884520769610`, `DashboardUrl=https://d29127v2virrqh.cloudfront.net`.

**4. Dashboard labels and publish.** `cmd4` uses the exact same 15 sensors as `akvo`, so `config/sites/cmd4.json` was built by copying `config/sites/akvo.json` verbatim and changing only `site.title` (to tell the two dashboards apart) - for a site with a different sensor set, edit the `sensors`/`groups`/`units` too, one entry per `DEVICE_ID.SensorName`. Then:

```bash
bash tools/deploy_web.sh venko-cmd4 ../Akvo_Green/sites/cmd4/config_data/config.json config/sites/cmd4.json
```

- **Parameters used:** stack `venko-cmd4`, gateway config `sites/cmd4/config_data/config.json`, overrides `config/sites/cmd4.json`.
- **Outcome:** topic-match safety check passed (config's `topic_pub`/`topic_system` matched the stack's `DataTopic`/`SystemTopic`); `15 sensors, gateway AKVO_cmd4` uploaded to `s3://venko-cmd4-884520769610-us-east-1/config/config.json`; `web/` (13 files) synced to the web bucket; `app-config.js` written with this stack's API URL/key. Verified directly: `curl https://d29127v2virrqh.cloudfront.net` → `HTTP 200`, real page; `GET /config` with the API key → `HTTP 200`, `"site":{"title":"Venko Demo – Cmd4"}`, 15 sensors.

**5. Lock CORS** to the real `DashboardUrl` from step 3: set `AllowedOrigin` in `[cmd4.deploy.parameters]`, then

```bash
sam deploy --config-env cmd4
API_ID=$(aws cloudformation describe-stack-resource --stack-name venko-cmd4 \
  --logical-resource-id Api --query StackResourceDetail.PhysicalResourceId --output text)
aws apigateway create-deployment --rest-api-id "$API_ID" --stage-name prod
```

- **Parameters used:** `AllowedOrigin="https://d29127v2virrqh.cloudfront.net"` (replacing `*`).
- **Outcome:** stack updated (`UPDATE_COMPLETE`, parameter-only change - much faster than the first deploy, no CloudFront recreation); API stage redeployed. Verified with a CORS preflight: the response's `Access-Control-Allow-Origin` is now the fixed string `https://d29127v2virrqh.cloudfront.net` for *every* request regardless of the caller's actual `Origin` header - which is exactly what makes a browser loaded from any other origin reject the response, while the real dashboard (whose own origin matches) works normally.

**6. Install the Pi** (Raspberry Pi OS, network + SSH working, RS-485 adapter on `/dev/ttyUSB0`). From the dev machine:

```bash
cd Akvo_Green
tools/push_site.sh cmd4 pi@<pi-address> --certs      # code + config.json + certs
ssh pi@<pi-address> 'cd Akvo_Green && ./install.sh --skip-certs && sudo systemctl start akvo-green'
```

- **Status: done** - Pi `192.168.68.129`, user `pi`, service `akvo-green` enabled and running. `push_site.sh` syncs the code without touching the Pi's `config_data/`, `data/`, `logs/`, `.venv/` or certs, copies the site's `config.json`, and (with `--certs`) its certificates. It also installs the AKVO Modbus Tool desktop app (`akvo-modbus`, from `../Tools/AkvoModbus`) when it changed; `--no-modbus-tool` skips it. `install.sh` sets up packages, the venv, serial-port access and the `akvo-green` service; it only needs to run once per Pi.

**7. Check** (within ~1 minute of the service starting):

```bash
aws s3 cp s3://venko-cmd4-884520769610-us-east-1/latest/data.json - | head -c 300; echo
```

- **Status: done (2026-10-05)** - `latest/data.json` updates every poll, `raw/data/` fills via Firehose, payloads carry `"simulated": true`. Once a Pi is publishing, open the dashboard and confirm **Gateway en línea**, dials filling in. If nothing arrives: `journalctl -u akvo-green -f` on the Pi (a policy refusal shows as a connection error), the certificate is `ACTIVE`, and `aws.csv`'s client id/topics match the policy (step 2) and the stack (step 3).

**8. Write the site summary** - one page with everything about the site: dashboard and API URLs, API key id, S3 buckets, IoT thing/certificate/policy/rules, Firehose, Lambdas, DynamoDB, Athena, gateway settings, live status (last data, simulated or real) and ready-to-run commands:

```bash
python3 tools/site_summary.py cmd4 --pi pi@192.168.68.129     # → docs/sites/cmd4.md
python3 tools/site_summary.py default                          # site 1 (stack sam-app)
```

- `<key>` is the `samconfig.toml` env. It reads the stack from AWS and the site's files in Akvo_Green, so run it after steps 1-7.
- `--pi` is the Pi's SSH target (not stored anywhere in AWS). It's remembered from the previous summary, so it only needs to be given once.
- **No secrets** are written: the API key appears as its id plus the command that prints its value; certificates only as id, status and folder. The file is safe to commit and share.
- It's a snapshot - **rerun it after any change to the site** (redeploy, new certificate, switch to real sensors) rather than editing it by hand.
- Each run also rebuilds [`docs/sites/README.md`](docs/sites/README.md): every site side by side (client id, topics, Pi, Modbus mode, status, web page, S3 data/web buckets, stack), taken from the site pages - no extra AWS calls.
- Example: [`docs/sites/cmd4.md`](docs/sites/cmd4.md).

### Local dashboard on each Pi

Besides the cloud dashboard, each Pi can serve a small read-only page over its own local SQLite history at `http://<pi-address>:8080`: live values, trends, alarm events, CSV export and host health. It needs **no internet and no AWS**, so it's the way to look at a site when the cloud can't - e.g. during a network outage, or on a site with no uplink. No password: anyone on the Pi's network can view it.

It lives entirely in Akvo_Green (`gateway/history_web.py`, service `akvo-history-web`) - nothing in this repo or AWS changes. Turn it on per site with `web_enabled=1` / `web_port=8080` in that site's `system.csv` (`database_enabled=1` must already be on), rebuild, `tools/push_site.sh <key> pi@<pi>`, and the first time on each Pi run `./install.sh --skip-certs` there. Full steps: Akvo_Green `sites/README.md`; reference: `docs/edge_node_config/CONFIGURATION.md`, "Local web dashboard". Running now on `cmd4` only.

### Day to day with several sites

| Task | Site 1 | Another site (`<key>`) |
|---|---|---|
| Sensor/label change | edit `Akvo_Green/config_data/*.csv` → `build` | edit `sites/<key>/config_data/*.csv` → `build --config-dir ../sites/<key>/config_data` |
| Send config/code to the Pi | `tools/push_site.sh root pi@<pi1>` | `tools/push_site.sh <key> pi@<pi>` |
| Dashboard config | `bash tools/deploy_web.sh` | `bash tools/deploy_web.sh venko-<key> <its config.json> config/sites/<key>.json` |
| Stack/template change | `sam deploy` | `sam deploy --config-env <key>` |
| Dashboard code change (`web/`) | run `deploy_web.sh` for **every** site | |
| Disable a lost Pi | `aws iot update-certificate --certificate-id <id> --new-status INACTIVE` | same, with that Pi's certificate id |
| Site summary (after any change) | `python3 tools/site_summary.py default` | `python3 tools/site_summary.py <key>` |
| Local dashboard on the Pi | `web_enabled=1` in `config_data/system.csv` → `build` → `push_site.sh root` | same in `sites/<key>/config_data/system.csv` → `push_site.sh <key>` (first time: `./install.sh --skip-certs` on the Pi) |

### Removing a site

Stop the Pi's gateway **before** touching AWS - otherwise it spends its
last moments retrying a now-invalid MQTT connection instead of a clean
shutdown:

```bash
ssh pi@<pi-address> '
  sudo systemctl stop akvo-green
  sudo systemctl disable akvo-green
  sudo rm -f /etc/systemd/system/akvo-green.service
  sudo systemctl daemon-reload
'
```

Skip this if the site never had a Pi (a [virtual-Modbus](#no-hardware-yet-demo-with-virtual-modbus)-only demo, or the Pi is already gone/dead).

Then the AWS side - empty the web bucket before `sam delete`, since
CloudFormation won't delete a non-empty bucket, then delete the stack
(removes everything `template.yaml` created - Firehose, the IoT topic
rules, Glue/Athena, the Lambdas/API, CloudFront - **except** `DataBucket`,
which has `DeletionPolicy: Retain` on purpose and is left behind,
orphaned, until you remove it explicitly in the last step below):

```bash
aws s3 rm s3://venko-<key>-web-<account> --recursive
sam delete --stack-name venko-<key>
```

The IoT certificate/thing/policy are **not** part of the stack (they were
created by hand in [Adding a site](#adding-a-site-another-pi--its-own-aws-stack-and-dashboard)
step 2, not by `template.yaml`) - detach and delete them if the Pi is
gone for good:

```bash
aws iot detach-thing-principal --thing-name AKVO_<Key> --principal <cert-arn>
aws iot detach-policy --policy-name venko-<key>-gateway --target <cert-arn>
aws iot update-certificate --certificate-id <cert-id> --new-status INACTIVE
aws iot delete-certificate --certificate-id <cert-id>
aws iot delete-thing --thing-name AKVO_<Key>
aws iot delete-policy --policy-name venko-<key>-gateway
```

Finally: delete the `[<key>.*]` section in `infra/samconfig.toml`, and
`Akvo_Green/sites/<key>/` (config + certs) if the site is gone for good.
Empty and delete the retained data bucket
(`aws s3 rb s3://venko-<key>-<account>-<region> --force`) only as a
separate, deliberate step if its history really isn't needed - it's kept
by default specifically so a `sam delete` alone can never lose it.

**If the Pi is being reused for a different site** instead of
decommissioned, skip the systemd-service-removal and sudoers cleanup
above - `install.sh`'s system-level setup (packages, venv, `dialout`
group, the passwordless-`reboot` sudoers rule) is reusable as-is. Just
overwrite its identity with the new site's and restart:

```bash
tools/push_site.sh <new-key> pi@<pi-address> --certs
ssh pi@<pi-address> 'sudo systemctl restart akvo-green'
```

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
| `sam deploy` fails with `Failed to create changeset ... AWS::EarlyValidation::ResourceExistenceCheck` before the confirmation prompt | `WebBucketName` and `DataBucketName` were given the same value - they must be two distinct bucket names (or left blank for the defaults) |
| `sam build --template <key>.yaml` says `Template file not found` | There is only one template, `template.yaml`, shared by every site - only `samconfig.toml`'s per-env section differs |
| `sam deploy --config-env <key>` fails with `Missing option '--stack-name'` | `samconfig.toml` has no `[<key>.*]` section yet - add one (copy `[akvo.*]`) before using `--config-env` |
| `sam deploy` fails mid-upload with `Unable to upload artifact ... S3 Bucket does not exist`, even after the progress bar reaches 100% | The account's shared `aws-sam-cli-managed-default` SAM artifact bucket was deleted outside CloudFormation, but its stack still reports `CREATE_COMPLETE`. Delete that stale stack (`aws cloudformation delete-stack --stack-name aws-sam-cli-managed-default`) and redeploy - SAM recreates it fresh. Affects every site's next deploy, not just the one that hit it |

**Data retention:** raw data moves to S3 Infrequent Access after 90 days and is never deleted automatically. Athena results and exports expire after 7 days. Reports are kept.

**Cost (demo scale, 1 gateway @ 20 s):** a few USD/month. Firehose, S3, Athena (a few MB scanned per query), Lambda and DynamoDB on-demand all stay within or near the free tiers.

**Security notes:**
- The API key is visible to anyone who can load the page. It only gates and throttles casual access. For a public demo, add Cognito login or CloudFront access restrictions.
- Highcharts requires a commercial license for commercial use.

**Remove everything:** empty the web bucket, then `sam delete --stack-name sam-app`. The data bucket (`venko-demo-<account>-<region>`) has `DeletionPolicy: Retain`, so the stack delete leaves it and its sensor history in place. Empty and delete it by hand only if you really want the data gone.
