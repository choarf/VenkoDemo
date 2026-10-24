# VenkoDemo

End-to-end demo built on the **Akvo Green** edge gateway:

```
Raspberry Pi (Akvo_Green, unchanged) ──MQTT/mTLS──▶ AWS IoT Core
   IoT Rule  <data topic>   ─▶ Firehose ─▶ S3 raw/data/dt=YYYY-MM-DD/   (history, Athena)
                         └▶ S3 latest/data.json                      (live view)
   IoT Rule  <system topic> ─▶ Firehose ─▶ S3 raw/system/…  + latest/system.json
API Gateway + Lambda (Python) ◀── Web dashboard (Highcharts, S3 + CloudFront)
DynamoDB: test events + operator comments
EventBridge (Mon 06:00 local) ─▶ report Lambda ─▶ S3 reports/weekly/<YYYY-Www>/
```

What the dashboard does:

| View | What it shows |
|---|---|
| **Resumen** (free run) | One dial per sensor from `config.json` with its limit scale (grey / yellow warning / red alarm) and state in text, KPIs, process indicators (if the site defines them), a short system card, active alarms. Monitoring only; nothing is recorded as a test. |
| **Prueba (HMI)** | Start/Stop test events, elapsed time, live trend since start (warning/alarm lines), comments per sensor or general, recent tests with report links. |
| **Alarmas** | Active alarms; for 24 h / 7 d / 30 d: totals (alarms, warnings, read errors), the sensor with most events, events per day, the episode table (click a row to see the sensor around the event) and Excel export. |
| **Históricos** | Any time range + any sensors → chart (one axis per unit, warning/alarm lines) and a per-sensor summary (min/avg/max/last, readings in warning or alarm); export to Excel or CSV. |
| **Reportes** | Test reports (created on Stop) and weekly reports, as HTML (printable to PDF) and Excel, with the process indicators' averages. |
| **Sistema** | Gateway and hardware data, CPU / RAM / disk against `system_limits` with their 24 h min/avg/max, connections (AWS, Modbus, WiFi, OLED), uptime. |

The layout follows `docs/propuesta-tablero/` (navy header band with tabs, light theme with the water-waves background; **Fondo** switches to the dark theme). It adapts to desktop, tablet and phone: dials become bars and tables become one card per row on narrow screens.

All data shown comes from the real gateway through AWS IoT. The only exception is the local mock server (see [Local development](#local-development)), which is clearly marked as synthetic.

---

## Repository layout

```
infra/template.yaml            SAM template: S3, Firehose, IoT rules, Glue/Athena, DynamoDB, Lambdas, API, CloudFront
backend/api/                   Lambda handlers (router.py = API entry, reports.py = report Lambda)
backend/common/                Athena, S3, DynamoDB helpers, SQL builders, report builder (HTML + XLSX)
backend/templates/             report.html.j2
backend/tests/                 pytest unit tests (no AWS needed)
config/sites/<key>.json        display labels, units, groups, warn margin per sensor, one file per site (see Adding a site)
tools/sync_config.py           Akvo_Green config.json → dashboard config (uploads to S3)
tools/deploy_web.sh            uploads the dashboard config + app-config.js and publishes web/ to S3/CloudFront
tools/mock_api.py              local UI server with SYNTHETIC data (development only)
tools/site_summary.py          writes docs/sites/<key>.md: one site's AWS resources, URLs, IoT, live status, commands
docs/sites/<key>.md            generated site summaries, plus README.md overview of all sites (see Adding a site, step 10)
web/                           dashboard (index.html, css/, js/)
```

## S3 bucket contents

Each site has two buckets (names per site in [Adding a site](#adding-a-site-another-pi--its-own-aws-stack-and-dashboard)); every site uses the same layout.

### Data bucket (`venko-<key>-<account>-<region>`)

| Prefix | Written by | Contents |
|---|---|---|
| `raw/data/dt=YYYY-MM-DD/` | IoT rule -> Firehose | Every data-topic message, one JSON line per message, tagged with `gw` (MQTT client id) and `rx_ms` (receive time) - the Athena `readings_raw` table's source |
| `raw/system/dt=YYYY-MM-DD/` | IoT rule -> Firehose | Every system-topic message, same tagging - the `system_raw` table's source |
| `latest/data.json` / `latest/system.json` | IoT rule's direct S3 action | The single most recent message of each, overwritten every time - what **Resumen**'s live dials and the online/offline pill read, without an Athena query |
| `errors/iot/data/` / `errors/iot/system/` | IoT rule's `ErrorAction` | A message lands here if the Firehose/S3 action rejected it. Empty means no ingestion failures |
| `athena-results/` | Athena (workgroup `<stack>-wg`) | Raw query result files. Expires after 7 days |
| `config/config.json` | `tools/sync_config.py` | The sensor/gateway config the API and dashboard read - see [Changing sensors or labels](#changing-sensors-or-labels) |
| `exports/<epoch-ms>/` | the API (`GET /export`) | Históricos' Excel/CSV export files, served via a presigned URL. Expires after 7 days |
| `reports/tests/T<timestamp>/` | report Lambda, on test stop | `meta.json` + `report.html` + `report.xlsx` for that test |
| `reports/weekly/<YYYY-Www>/` | report Lambda, Monday cron | Same three files for that ISO week |

`raw/` additionally moves to Infrequent Access after 90 days and is kept forever; reports are kept forever too. See **Data retention** under [Operations & troubleshooting](#operations--troubleshooting) for the full policy.

### Web bucket (`venko-<key>-web-<account>`)

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

## Prerequisites

- An AWS account in the same region as the gateway's IoT endpoint (`us-east-1`, from `config.json` → `aws.host`).
- AWS CLI v2 with credentials that can create IAM roles, and the **AWS SAM CLI**.
- Python 3.14 locally (matches the Lambda runtime, which `sam build` requires) (for `sync_config.py`; `boto3` needed for the upload).
- A Raspberry Pi with an RS-485 adapter (or none, in simulate mode). [Adding a site](#step-by-step-from-a-blank-sd-card-to-a-working-dashboard) covers everything from a blank SD card, including the AWS IoT certificate and policy. No gateway code changes are required, except the known issue for real sensors noted there.

---

## How to deploy

### 1. Deploy the AWS stack

```bash
cd infra
sam build --template template.yaml
sam deploy --config-env <key>     # needs a [<key>.*] section in samconfig.toml - see Adding a site
```

Each site sets these in its `samconfig.toml` section (`parameter_overrides`):

| Parameter | Default | Meaning |
|---|---|---|
| `DataTopic` / `SystemTopic` | `AKVO/data` / `AKVO/system` | Must match `aws.topic_pub` / `aws.topic_system` in the gateway `config.json` |
| `PartitionStartDate` | `2026-01-01` | First day Athena looks for data |
| `AllowedOrigin` | `*` | CORS origin; set to the dashboard URL after the first deploy (step 4) |
| `ReportTimezone` | `America/Mexico_City` | Time zone for weekly report boundaries |
| `GlueDatabaseName` | `venko_demo` | Athena database name |
| `WebBucketName` | *(auto-generated)* | Name of the dashboard bucket. Sites use `venko-<key>-web-<account>`. Changing it creates a new, empty bucket, so rerun `tools/deploy_web.sh` straight after. **Must be different from `DataBucketName`** - see note below |
| `DataBucketName` | `venko-demo-<account>-<region>` | Name of the data bucket. Every site sets its own, `venko-<key>-<account>-<region>`. **Never change it on a running stack**: the old bucket (and its history) is retained and the stack starts over with an empty one. **Must be different from `WebBucketName`** - see note below |

`sam deploy` prints the outputs `ApiUrl`, `ApiKeyId`, `DataBucketName`, `WebBucketName` and `DashboardUrl`.

`WebBucketName` and `DataBucketName` create two separate S3 buckets - giving them the same value makes the template try to create two buckets with one name, which `sam deploy` rejects while building the changeset, before you even reach the confirmation prompt (`Failed to create changeset ... AWS::EarlyValidation::ResourceExistenceCheck`). Leave both blank for the defaults above, or set two distinct, globally-unique names.

### 2. Check that data is arriving

With the gateway running (or by publishing a sample from **AWS IoT → MQTT test client** to the site's data topic, e.g. `VENKO/<key>/data`):

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name venko-<key> \
  --query "Stacks[0].Outputs[?OutputKey=='DataBucketName'].OutputValue" --output text)
aws s3 cp s3://$BUCKET/latest/data.json - | head -c 400; echo
aws s3 ls s3://$BUCKET/raw/data/ --recursive | tail -3     # appears within ~60 s (Firehose buffer)
```

If nothing appears, look in `s3://$BUCKET/errors/`, and check that the gateway's IoT policy allows `iot:Publish` on those topics.

Athena check (workgroup `venko-<key>-wg`, database `venko_<key>`):

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
tools/deploy_web.sh <stack> <gateway-config.json> <overrides.json>
tools/deploy_web.sh venko-cmd4 ../Akvo_Green/sites/cmd4/config_data/config.json config/sites/cmd4.json
```

The script:
1. Builds the dashboard config with `tools/sync_config.py`: reads the gateway `config.json`, keeps only the gateway timing and sensors (the `aws`/`modbus` sections with certificate paths are **never** uploaded) and merges the overrides.
2. **Checks the topics:** if the config's `topic_pub`/`topic_system` differ from the stack's `DataTopic`/`SystemTopic`, it stops and uploads nothing. This catches publishing the wrong site's (or a half-edited) config.
3. Uploads `s3://<data bucket>/config/config.json`.
4. Uploads `app-config.js` (API URL + key for that stack) straight to the web bucket. Nothing is written into `web/`, so sites can't overwrite each other.
5. Syncs `web/` to the web bucket and prints the dashboard URL.

Browsers may keep old dashboard files cached after an update; a hard reload (Ctrl+Shift+R) picks up the new version.

### 4. Lock CORS to the dashboard URL (recommended)

Set `AllowedOrigin=\"https://dxxxxxxxxxxxx.cloudfront.net\"` in the site's `parameter_overrides` in `infra/samconfig.toml` (passing `--parameter-overrides` on the command line replaces the whole list), then:

```bash
cd infra
sam deploy --config-env <key>

# SAM does not republish the API stage when only a parameter changes, so the
# CORS preflight keeps the old origin until the stage is redeployed:
API_ID=$(aws cloudformation describe-stack-resource --stack-name venko-<key> \
  --logical-resource-id Api --query StackResourceDetail.PhysicalResourceId --output text)
aws apigateway create-deployment --rest-api-id "$API_ID" --stage-name prod
```

Open `DashboardUrl`. The header pill should show **Gateway en línea**.

---

## How to use the dashboard

### Free-run monitoring
Open **Resumen**. The dials update every 10 s (the gateway publishes every `poll_interval` = 20 s).
- **Normal** (cyan needle, grey frame). **Advertencia** (yellow frame) means the value is within the warn margin (`warn_margin`, default 10 % of the range) of a limit.
- **Alarma** (red frame) means the gateway flagged LOW/HIGH against `min`/`max` in `config.json`.
- **Error de lectura** (dashed frame, value `ERR`) means `BUS_ERROR`/`EXCEPTION`.
- If no message arrives for 3 poll intervals, the pill turns red (**Gateway sin datos**).
- **Indicadores del proceso** (only on sites with a `derived` section, see [Process indicators](#process-indicators-and-resource-thresholds)) are computed in the browser from the live values, with the same states.

### Running a test
1. Go to **Prueba (HMI)** and fill in *Nombre*, *Operador* and optional *Notas*.
2. Click **▶ Iniciar prueba**. The header shows *Prueba en curso* with the elapsed time on every view and every browser. Only one test can run at a time.
3. While it runs, add **comments**. Choose a sensor, or *General*, for notes such as "valve adjusted" or "sample taken". Comments are linked to the running test automatically.
4. Click **■ Detener prueba**, then click again to confirm.
5. The report is generated in the background (about 30–90 s). When it's ready, *Pruebas recientes* and **Reportes** show **HTML · Excel** links. If generation fails, a **Reintentar** link appears.

The test report covers the test window and includes: per-sensor statistics (samples, availability, min/avg/max/std, first/last, % time in alarm), trends, alarm events, comments, data gaps and system health.

### Alarms
**Alarmas** lists active alarms and the history of *episodes* for 24 h / 7 d / 30 d. Consecutive samples of the same kind are merged into one event with start, end, duration and peak. Kinds: **alarm** (LOW/HIGH from the gateway), **warning** (`WARN_LOW`/`WARN_HIGH`: within `warn_margin` of a limit, computed by the API from each sensor's limits) and **read error**. Above the table: totals per kind, the sensor with most events and a stacked chart of events per day. Click a row to open the sensor's trend around that event; **Excel** downloads the episodes. Reports keep listing alarms and read errors only.

### Querying stored data
In **Históricos**, pick a range (or 1 h / 6 h / 24 h / 7 d), choose sensors in the **Sensores** drop-down, then click **Consultar**. Points are averaged per bucket, sized automatically for the range. Below the chart, **Resumen del rango** shows each sensor's min / average / max / last value and how many readings were in warning or alarm. **Excel** / **CSV** exports the same range. Ranges are limited to 31 days.

### System
**Sistema** shows the gateway (client, id, last reading, local time, sampling), the hardware (model, serial, hostname, IP, OS), CPU / RAM / disk with a yellow / red frame at the site's `system_limits` (default 80 % / 90 %) and their min / avg / max over the last 24 h, and the connections: AWS (since when), Modbus (port or simulated), WiFi (network, profile `akvo-wifi1`/`2`, signal; *No se usa* on a wired Pi) and the OLED. Uptime, WiFi, Modbus and OLED come from gateway code of 2026-10-23 or later; an older Pi shows "—".

### Weekly report
Generated automatically every Monday at 06:00 (Mexico City) for the previous ISO week (Mon–Sun), in HTML and Excel. To create one on demand, go to **Reportes**, enter a week (e.g. `2026-W39`, or leave it empty for last week) and click **Generar**. Or run:

```bash
aws lambda invoke --function-name venko-<key>-report \
  --cli-binary-format raw-in-base64-out --payload '{"kind":"weekly","week":"2026-W39"}' /dev/stdout
```

---

## Changing sensors or labels

When Akvo_Green changes (more sensors, a new unit, new limits), the AWS stack does **not** need redeploying. Athena stores each message's sensors as a map, so new sensors appear in history, alarms and reports automatically. Only the gateway config and the dashboard config need updating.

### The config pipeline

```
Akvo_Green/sites/<key>/config_data/*.csv ──config_manager.py build──▶ …/config_data/config.json ──▶ Pi (gateway)
                                                                      │
config/sites/<key>.json ──────────────────────────────────────────────┤
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
| `config/sites/<key>.json` | Display only: site title, unit display names, per-sensor label/group/decimals |

`Akvo_devices.csv` / `Osmosis_devices.csv` are saved sensor sets and are **not** read by the build. To switch sets, copy one over `devices.csv` and rebuild. The `aws` and `modbus` sections are never uploaded to S3.

### Every change: three steps

**1. Edit the CSV and rebuild the gateway config:**

```bash
cd ~/git_reps/claude/projects/Venko/Akvo_Green/gateway
# edit ../sites/<key>/config_data/devices.csv
python3 config_manager.py build --config-dir ../sites/<key>/config_data --dry-run   # validate and print
python3 config_manager.py build --config-dir ../sites/<key>/config_data             # writes its config.json
```

**2. Send the new `config.json` to the Pi:** `tools/push_site.sh <key> pi@<pi>` (from Akvo_Green). The gateway reloads it within 5 s without a restart.

**3. Update the dashboard labels and publish the dashboard config:**

```bash
cd ~/git_reps/claude/projects/Venko/VenkoDemo
# edit config/sites/<key>.json (only if labels/units/groups changed)
SRC="--source ../Akvo_Green/sites/<key>/config_data/config.json --overrides config/sites/<key>.json"
python3 tools/sync_config.py $SRC --out build/config.json   # preview
python3 tools/sync_config.py $SRC --bucket venko-<key>-884520769610-us-east-1
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

and its entry in `config/sites/<key>.json`:

```json
"DEV_6.Caudal": { "label": "Caudal", "group": "Flujo", "decimals": 1 }
```

Keys are always `DEVICE_ID.SensorName` and are case-sensitive. A sensor without an override still shows up, with its raw name and unit.

### Process indicators and resource thresholds

Both live in the site's overrides file and are published with step 3 (`deploy_web.sh`); no stack or gateway change.

**Process indicators** (`derived`): values computed from existing sensors, shown in **Resumen** and in the reports (period average). Only three fixed operations exist - nothing in the file is run as code: `ratio` (a ÷ b × `scale`), `diff` (a − b) and `balance` (a − each of `minus`). `sync_config.py` refuses unknown sensors or operations and `min` ≥ `max`. `provisional: true` labels the limits as *provisionales* on the page and in the reports. Example (site `cmd4`, reverse osmosis):

```json
"derived": [
  {"key": "REC", "label": "Recuperación", "unit": "%", "op": "ratio", "a": "DEV_7.Fj2_flujo_de_permeado",
   "b": "DEV_6.Fj1_flujo_de_alimentacion", "scale": 100, "min": 50, "max": 85, "decimals": 0},
  {"key": "DPF", "label": "ΔP del filtro", "unit": "psi", "op": "diff", "a": "DEV_1.P1_pre_filtro",
   "b": "DEV_2.P2_pos_filtro", "min": 0, "max": 15},
  {"key": "BAL", "label": "Balance de flujos", "unit": "gpm", "op": "balance", "a": "DEV_6.Fj1_flujo_de_alimentacion",
   "minus": ["DEV_7.Fj2_flujo_de_permeado", "DEV_8.Fj3_flujo_de_rechazo"], "min": -1, "max": 1}
]
```

The warning band is `warn_margin` of the indicator's range (from `defaults`, or per entry). A site without `derived` doesn't show the card.

**Gateway resource thresholds** (`system_limits`): `[warning, alarm]` in % for the **Sistema** view, default `{"cpu": [80, 90], "ram": [80, 90], "disk": [80, 90]}`; set only the ones that differ, e.g. `"system_limits": {"disk": [70, 85]}`.

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

Change `DataTopic`/`SystemTopic` in the site's `parameter_overrides` in `infra/samconfig.toml`, then:

```bash
cd infra
sam deploy --config-env <key>
```

### Check afterwards

- **Resumen:** the new or changed dial shows the right label and unit.
- **Históricos:** the new sensor's chip is there. Its history starts when the Pi began sending it.
- A sensor showing its raw name means the override key doesn't match. `sync_config.py` lists unmatched keys as `warning: overrides for sensors not in config: …`. Entries kept for another sensor set also appear there, which is expected.
- Finished test reports keep the labels they were built with. Use **Reintentar** in Reportes to rebuild one with the current config.

---

## Adding a site (another Pi + its own AWS stack and dashboard)

Each site is self-contained: **one Raspberry Pi, one AWS stack, one dashboard**. Sites share the AWS account, the IoT endpoint and the code (one Akvo_Green repo, one VenkoDemo repo); they share no data, buckets, API keys or URLs.

```
Site "cmd4"                                Site "<key>"
Pi    client AKVO_cmd4                     Pi    client AKVO_<Key>
      topics VENKO/cmd4/data, …/system           topics VENKO/<key>/data, …/system
Stack venko-cmd4                           Stack venko-<key>
      data  venko-cmd4-884520769610-…            data  venko-<key>-<account>-<region>
      web   https://d29127v2virrqh…              web   its own CloudFront URL
```

### Sites in this account

| | cmd4 |
|---|---|
| Gateway config | `Akvo_Green/sites/cmd4/config_data/` |
| Certificates | `Akvo_Green/sites/cmd4/certs/` (git-ignored) |
| MQTT client / gateway id | `AKVO_cmd4` |
| Topics | `VENKO/cmd4/data`, `VENKO/cmd4/system` |
| IoT policy | `venko-cmd4-gateway` (only its client id + `VENKO/cmd4/*`) |
| Stack / samconfig env | `venko-cmd4` / `cmd4` |
| Data bucket | `venko-cmd4-884520769610-us-east-1` |
| Athena DB / workgroup | `venko_cmd4` / `venko-cmd4-wg` |
| Dashboard labels | `config/sites/cmd4.json` |
| Dashboard | https://d29127v2virrqh.cloudfront.net |
| Gateway on a Pi | yes - Pi `192.168.68.129`, publishing |
| `modbus.csv` `simulate_enabled` | **`1` - SIMULATED data** (every payload tagged `"simulated": true`) |
| Local dashboard on the Pi (`web_enabled`) | http://192.168.68.129:8080 |

### Naming convention

Pick a short **site key** (lowercase, e.g. `cmd4`) and derive every name from it:

| Item | Value |
|---|---|
| MQTT `client_id` = `gateway_id` | `AKVO_<Key>` - **must be unique**: two Pis with the same client id keep disconnecting each other |
| Topics | `VENKO/<key>/data`, `VENKO/<key>/system` |
| IoT policy / thing | `venko-<key>-gateway` / `AKVO_<Key>` |
| Stack / samconfig env | `venko-<key>` / `<key>` |
| Data bucket | `venko-<key>-<account>-<region>` |
| Web bucket | `venko-<key>-web-<account>` |
| Athena DB | `venko_<key>` |

### Step by step: from a blank SD card to a working dashboard

The full sequence for a new site, starting from an empty SD card. Commands use shell variables, so set them once (example: a new site `plant5`):

```bash
KEY=plant5; CLIENT=AKVO_plant5; ACCT=884520769610
```

Keep `KEY` **lowercase** and type it the same way everywhere. Folder names, MQTT topics, IoT policy resources and S3 bucket names are all case-sensitive. A `sites/Demo` folder with topics `VENKO/demo/*` and a policy for `VENKO/Demo/*` doesn't fail loudly; the Pi just isn't allowed to publish.

`cmd4` is the reference site: it was set up with these steps (Pi `192.168.68.129`, simulate mode), and its files are the ones copied below. The *cmd4* notes under each step are what happened there.

> **Known issue - real sensors:** with `simulate_enabled=0` the gateway currently can't open the serial port: `edge_node_improved.py` passes the whole `modbus` config, including the `simulate` key, to `ModbusSerialClient(**self.cfg)`, which fails with `unexpected keyword argument 'simulate'` and retries forever. Simulate mode is unaffected. Fix that in Akvo_Green before installing a site on real sensors.

**1. Write the SD card** (Raspberry Pi Imager, on any computer):

- **OS:** *Raspberry Pi OS (64-bit) with desktop* - the desktop is needed for the AKVO Modbus Tool and the local-dashboard kiosk. *Lite* also works if you need neither (then use `--no-modbus-tool` in step 8).
- **Edit settings:**
  - hostname, e.g. `plant5`;
  - user `pi` and a password;
  - Wi-Fi SSID, password and country (skip for Ethernet);
  - time zone `America/Mexico_City`;
  - **Services:** enable SSH, *public-key only*, and paste your `~/.ssh/id_*.pub`.
- Write the card, boot the Pi, then find its address (`ping plant5.local` or the router's client list). Reserve that IP on the router so it doesn't change.

**2. Prepare the Pi:**

```bash
ssh pi@<pi-address>
sudo apt update && sudo apt full-upgrade -y && sudo reboot
# after the reboot, with the RS-485 adapter plugged in:
ls /dev/ttyUSB*        # expect /dev/ttyUSB0
```

**3. Gateway config** (dev machine, in Akvo_Green). Start from cmd4's files:

```bash
cd ~/git_reps/claude/projects/Venko/Akvo_Green
mkdir -p sites/$KEY/config_data
cp sites/cmd4/config_data/{devices,modbus,system,aws}.csv sites/$KEY/config_data/
```

Then edit the copies:

| File | Set |
|---|---|
| `system.csv` | `gateway_id` = `$CLIENT`. Optionally `web_enabled=1` for the [local dashboard](#local-dashboard-on-each-pi) on port 8080, and `wifi1_ssid` / `wifi2_ssid` for the WiFi networks the Pi should join (preferred, fallback) |
| `../wifi.csv` | Only with `wifi*_ssid`: the passwords, one `ssid,password` row per network, in `sites/$KEY/wifi.csv`. Git-ignored like the certs; never in `config.json` |
| `aws.csv` | `client_id` = `$CLIENT`; `topic_pub` / `topic_system` = `VENKO/$KEY/data` / `VENKO/$KEY/system`; `data_bucket` / `web_bucket` = the names from the [Naming convention](#naming-convention); `dashboard_url` - fill in after step 5 |
| `modbus.csv` | `simulate_enabled`: `1` = synthetic demo data, `0` = real sensors (see the known issue above) |
| `devices.csv` | The sensor list - keep cmd4's 15 sensors or replace them |

Build and validate:

```bash
cd gateway && python3 config_manager.py build --config-dir ../sites/$KEY/config_data && cd ..
```

- *cmd4:* `Loaded 15 devices`, `Validation successful`, plus duplicate-slave-ID *warnings* (several sensors share one physical Modbus unit - harmless).
- **Pitfall:** the CSVs must be inside `sites/$KEY/config_data/`, not `sites/$KEY/`. Otherwise `build` fails with `FileNotFoundError: .../config_data/devices.csv`.

**4. Certificate, restricted policy and thing** (AWS IoT, from the Akvo_Green root). The private key can only be downloaded now. It goes into `sites/$KEY/certs/` (git-ignored) and onto the Pi, nowhere else.

```bash
mkdir -p sites/$KEY/certs && cd sites/$KEY/certs && umask 077
aws iot create-keys-and-certificate --set-as-active \
  --certificate-pem-outfile certificate.pem.crt --private-key-outfile private.pem.key \
  --query certificateArn --output text > cert.arn
cp ../../cmd4/certs/AmazonRootCA1.pem .
cat > policy.json <<JSON
{ "Version": "2012-10-17", "Statement": [
  { "Effect": "Allow", "Action": "iot:Connect", "Resource": "arn:aws:iot:us-east-1:$ACCT:client/$CLIENT" },
  { "Effect": "Allow", "Action": "iot:Publish", "Resource": "arn:aws:iot:us-east-1:$ACCT:topic/VENKO/$KEY/*" } ] }
JSON
aws iot create-policy --policy-name venko-$KEY-gateway --policy-document file://policy.json
aws iot attach-policy --policy-name venko-$KEY-gateway --target "$(cat cert.arn)"
aws iot create-thing --thing-name $CLIENT
aws iot attach-thing-principal --thing-name $CLIENT --principal "$(cat cert.arn)"
cd ../../..
```

- `KEY` must match the topics from step 3, and `CLIENT` must match `aws.csv`'s `client_id`.
- *cmd4:* certificate `e494e73e…` `ACTIVE`, policy `venko-cmd4-gateway`, thing `AKVO_cmd4`. That left five files in `sites/cmd4/certs/`, all mode `600`: `certificate.pem.crt`, `private.pem.key`, `AmazonRootCA1.pem`, `cert.arn`, `policy.json`.

**5. AWS stack** (VenkoDemo). In `infra/samconfig.toml`, copy the `[cmd4.*]` sections to `[$KEY.*]` and replace every `cmd4` with the new key. Set `PartitionStartDate` to today and `AllowedOrigin` to `*` for now (locked down in step 7):

```toml
[plant5.deploy.parameters]
stack_name = "venko-plant5"
tags = "Project=\"Venko\" Site=\"plant5\" Client=\"<client>\""
resolve_s3 = true
s3_prefix = "venko-plant5"
region = "us-east-1"
confirm_changeset = true
capabilities = "CAPABILITY_IAM"
parameter_overrides = "DataTopic=\"VENKO/plant5/data\" SystemTopic=\"VENKO/plant5/system\" PartitionStartDate=\"2026-10-09\" AllowedOrigin=\"*\" ReportTimezone=\"America/Mexico_City\" GlueDatabaseName=\"venko_plant5\" DataBucketName=\"venko-plant5-884520769610-us-east-1\" WebBucketName=\"venko-plant5-web-884520769610\""
image_repositories = []

[plant5.global.parameters]
region = "us-east-1"
```

```bash
cd ~/git_reps/claude/projects/Venko/VenkoDemo/infra
sam build --template template.yaml
sam deploy --config-env $KEY
```

Note the `DashboardUrl` output. Put it in `aws.csv`'s `dashboard_url`, then rerun the build from step 3.

- **Pitfalls:**
  - There is only one template. `sam build --template $KEY.yaml` fails with `Template file not found`.
  - `--config-env $KEY` fails with `Missing option '--stack-name'` until the `[$KEY.*]` section exists.
  - If the upload fails with `S3 Bucket does not exist` (even at 100 %), the shared SAM artifact bucket was deleted outside CloudFormation. Delete the stale stack and redeploy, and SAM recreates it:
    ```bash
    aws cloudformation delete-stack --stack-name aws-sam-cli-managed-default --region us-east-1
    aws cloudformation wait stack-delete-complete --stack-name aws-sam-cli-managed-default --region us-east-1
    ```
- *cmd4:* `CREATE_COMPLETE`, `DashboardUrl=https://d29127v2virrqh.cloudfront.net`.

**6. Dashboard labels and publish** (VenkoDemo root):

```bash
cd ~/git_reps/claude/projects/Venko/VenkoDemo
python3 tools/sync_config.py --init --source ../Akvo_Green/sites/$KEY/config_data/config.json --overrides config/sites/$KEY.json
# edit config/sites/$KEY.json: site.title, and the labels/groups of any sensor it couldn't reuse
bash tools/deploy_web.sh venko-$KEY ../Akvo_Green/sites/$KEY/config_data/config.json config/sites/$KEY.json
```

- `--init` writes one entry per enabled sensor in the site's `config.json`. Sensors and units that another site already labels (same `DEVICE_ID.SensorName`) get that site's label, group and decimals. The rest get their raw name and an empty group, and the command prints how many need labelling by hand. It never overwrites an existing file.
- Override keys are `DEVICE_ID.SensorName` and are case-sensitive.
- `deploy_web.sh` refuses to upload if the config's topics don't match the stack's.
- *cmd4:* `15 sensors, gateway AKVO_cmd4` uploaded, `web/` synced, `app-config.js` written. The dashboard returned `HTTP 200`.

**7. Lock CORS** to the `DashboardUrl`: set `AllowedOrigin` in `[$KEY.deploy.parameters]`, then:

```bash
cd infra && sam deploy --config-env $KEY && cd ..
API_ID=$(aws cloudformation describe-stack-resource --stack-name venko-$KEY \
  --logical-resource-id Api --query StackResourceDetail.PhysicalResourceId --output text)
aws apigateway create-deployment --rest-api-id "$API_ID" --stage-name prod
```

SAM doesn't republish the API stage when only a parameter changes, hence the `create-deployment`.

**8. Install the gateway on the Pi** (dev machine, Akvo_Green):

```bash
cd ~/git_reps/claude/projects/Venko/Akvo_Green
tools/push_site.sh $KEY pi@<pi-address> --certs        # add --no-modbus-tool on Lite
ssh pi@<pi-address> 'cd Akvo_Green && ./install.sh --skip-certs && sudo systemctl start akvo-green'
```

- `push_site.sh` syncs the code and copies the site's `config.json` and, with `--certs`, its certificates. It never touches the Pi's `config_data/`, `data/`, `logs/`, `.venv/` or certs. It also installs the AKVO Modbus Tool (`akvo-modbus`, from `../Tools/AkvoModbus`) when that changed.
- `install.sh` sets up packages, the venv, serial-port access, the reboot sudo rule and the `akvo-green` / `akvo-history-web` services. It only needs to run once per Pi.
- With `wifi1_ssid`/`wifi2_ssid` set, every push also saves those networks on the Pi (NetworkManager profiles `akvo-wifi1`/`akvo-wifi2`). It doesn't drop the current connection; the Pi switches when its current network goes away or at the next reboot. To change a site's WiFi later: edit `system.csv` and `wifi.csv`, rebuild, push. Details: Akvo_Green `docs/edge_node_config/CONFIGURATION.md`, "WiFi networks".

**9. Check** (within about a minute):

```bash
ssh pi@<pi-address> 'journalctl -u akvo-green -n 30 --no-pager'    # "MQTT connected", no Modbus errors
aws s3 cp s3://venko-$KEY-$ACCT-us-east-1/latest/data.json - | head -c 300; echo
```

Open the dashboard and confirm **Gateway en línea** with the dials filling in. If nothing arrives, check:
- the certificate is `ACTIVE` (a policy refusal shows as a connection error in the journal);
- `aws.csv`'s client id and topics match the policy (step 4) and the stack (step 5);
- `s3://<data bucket>/errors/iot/`.

*cmd4:* `latest/data.json` updates every poll, `raw/data/` fills via Firehose, and payloads carry `"simulated": true`.

**10. Record the site** - write the site summary: one page with the site's URLs, API key id, buckets, IoT resources, Lambdas, Athena, gateway settings, live status and ready-to-run commands.

```bash
cd ~/git_reps/claude/projects/Venko/VenkoDemo
python3 tools/site_summary.py $KEY --pi pi@<pi-address>     # → docs/sites/$KEY.md, and rebuilds docs/sites/README.md
```

- `--pi` is remembered from the previous summary, so it only needs to be given once.
- **No secrets** are written: the API key appears as its id plus the command that prints it, and certificates only as id, status and folder.
- It's a snapshot, so **rerun it after any change to the site** instead of editing it.
- Add the site to [Sites in this account](#sites-in-this-account) above and to both tables in Akvo_Green's `sites/README.md`.
- Commit both repos. The certificates are git-ignored; check `git status` anyway before committing.
- Example: [`docs/sites/cmd4.md`](docs/sites/cmd4.md).

### Local dashboard on each Pi

Besides the cloud dashboard, each Pi can serve a small read-only page over its own local SQLite history at `http://<pi-address>:8080`: live values, trends, alarm events, CSV export and host health. It needs **no internet and no AWS**, so it's the way to look at a site when the cloud can't - e.g. during a network outage, or on a site with no uplink. No password: anyone on the Pi's network can view it.

It lives entirely in Akvo_Green (`gateway/history_web.py`, service `akvo-history-web`) - nothing in this repo or AWS changes. Turn it on per site with `web_enabled=1` / `web_port=8080` in that site's `system.csv` (`database_enabled=1` must already be on), rebuild, `tools/push_site.sh <key> pi@<pi>`, and the first time on each Pi run `./install.sh --skip-certs` there. Full steps: Akvo_Green `sites/README.md`; reference: `docs/edge_node_config/CONFIGURATION.md`, "Local web dashboard". Running now on `cmd4` only.

### Day to day with several sites

| Task | Command (site `<key>`) |
|---|---|
| Sensor/label change | edit `sites/<key>/config_data/*.csv` → `build --config-dir ../sites/<key>/config_data` |
| Send config/code to the Pi | `tools/push_site.sh <key> pi@<pi>` |
| Dashboard config | `bash tools/deploy_web.sh venko-<key> <its config.json> config/sites/<key>.json` |
| Stack/template change | `sam deploy --config-env <key>` |
| Dashboard code change (`web/`) | run `deploy_web.sh` for **every** site |
| Disable a lost Pi | `aws iot update-certificate --certificate-id <id> --new-status INACTIVE` |
| Site summary (after any change) | `python3 tools/site_summary.py <key>` |
| AWS cost of a site, by category | `python3 tools/site_costs.py <key> [--by service\|component\|usage]` |
| Local dashboard on the Pi | `web_enabled=1` in `sites/<key>/config_data/system.csv` → `build` → `push_site.sh <key>` (first time: `./install.sh --skip-certs` on the Pi) |

### Cost per site

Every resource of a site's stack is tagged three ways, so Cost Explorer can split the bill per site and per category:

- `aws:cloudformation:stack-name` = `venko-<key>`, set by CloudFormation. It's active as a cost allocation tag, and backfilled from 2026-09-01.
- `Project` / `Site` / `Client`, the stack tags from `samconfig.toml`'s `tags` line. Every new site needs this line.
- `Component` per resource (`template.yaml`):

| Component | Resources |
|---|---|
| `ingest` | IoT rules, Firehose streams |
| `storage` | data bucket |
| `api` | API Gateway, API Lambda, DynamoDB table, API logs |
| `query` | Athena workgroup (history, alarms, reports) |
| `reports` | report Lambda, report logs |
| `web` | web bucket, CloudFront |

The user tags can only be activated for billing a few hours after a resource first carries them: `python3 tools/site_costs.py --activate-tags`. Grouping by `Component` works from then on.

```bash
python3 tools/site_costs.py                       # all sites by service, last 3 months, plus the account total
python3 tools/site_costs.py cmd4 --by component   # one site by category
python3 tools/site_costs.py --by usage --months 1 # finest grain (requests, GB-month, ...)
python3 tools/site_costs.py --budgets <email> --limit 5   # monthly budget per site (rerun for a new site)
```

- **Budgets:** each site has a budget, `venko-<key>-monthly` (USD 5). It emails at 80% and 100% of actual spend and at 100% of forecast.
- **Not split per site:** IoT Core messaging and rule charges are billed per account. They show only in the account total, as "AWS IoT". One message every 20 s plus one system message per minute is about 175,000 messages a month, which costs cents.
- **Other projects:** the account total includes the account's other projects (S3, EC2, VPC). Cost Explorer requests cost USD 0.01 each.
- **Lambda logs:** they go to `/venko/venko-<key>/{api,report}`, kept 30 days. The older `/aws/lambda/venko-<key>-*` groups that Lambda created itself have no retention and can be deleted.

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
step 4, not by `template.yaml`) - detach and delete them if the Pi is
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
| `GET /alarms?from&to&sensors` | Episodes (alarms, warnings near a limit, read errors; default 24 h) and `summary` (totals per kind, top sensor, events per day) |
| `GET /alarms/export?from&to&sensors` | Episodes as XLSX; presigned download URL |
| `GET /history/stats?from&to&sensors` | Per sensor: min/avg/max/last, readings in alarm / warning / read error |
| `GET /system/stats?from&to` | CPU / RAM / disk min/avg/max (default 24 h) |
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
| Page unstyled, tabs do nothing, values "Sin datos" right after a deploy | The browser kept an older CSS/JS. Reload with Ctrl+Shift+R once. `deploy_web.sh` uploads everything with `Cache-Control: no-cache` (since 2026-10-23), so later deploys show at once |
| Red banner "No se pudo cargar la librería de gráficas" | Highcharts (from `cdn.jsdelivr.net`) didn't load: offline or blocked network. Data, dials and tables still work; only charts are missing. `code.highcharts.com` was dropped because it answered 403 on some networks |
| Dashboard shows "No se pudo cargar la configuración" | `web/app-config.js` values; `config/config.json` missing → run `sync_config.py` |
| Pill says *Gateway sin datos* | Gateway running and connected? `latest/data.json` timestamp; `errors/iot/` in the bucket |
| History empty but live works | Firehose buffers ~60 s; check `raw/data/dt=…/` exists; `PartitionStartDate` not after your data |
| History/alarms error "query failed" | Athena console → workgroup `venko-<key>-wg` → recent queries for the message |
| Report stuck on *Generando reporte…* | CloudWatch log group `/venko/venko-<key>/report`; use **Reintentar** |
| CORS error in browser console | `AllowedOrigin` must equal the exact dashboard origin |
| `sam deploy` fails with `Failed to create changeset ... AWS::EarlyValidation::ResourceExistenceCheck` before the confirmation prompt | `WebBucketName` and `DataBucketName` were given the same value - they must be two distinct bucket names (or left blank for the defaults) |
| `sam build --template <key>.yaml` says `Template file not found` | There is only one template, `template.yaml`, shared by every site - only `samconfig.toml`'s per-env section differs |
| `sam deploy --config-env <key>` fails with `Missing option '--stack-name'` | `samconfig.toml` has no `[<key>.*]` section yet - add one (copy `[cmd4.*]`) before using `--config-env` |
| `sam deploy` fails mid-upload with `Unable to upload artifact ... S3 Bucket does not exist`, even after the progress bar reaches 100% | The account's shared `aws-sam-cli-managed-default` SAM artifact bucket was deleted outside CloudFormation, but its stack still reports `CREATE_COMPLETE`. Delete that stale stack (`aws cloudformation delete-stack --stack-name aws-sam-cli-managed-default`) and redeploy - SAM recreates it fresh. Affects every site's next deploy, not just the one that hit it |

**Data retention:** raw data moves to S3 Infrequent Access after 90 days and is never deleted automatically. Athena results and exports expire after 7 days. Reports are kept.

**Cost (demo scale, 1 gateway @ 20 s):** a few USD/month. Firehose, S3, Athena (a few MB scanned per query), Lambda and DynamoDB on-demand all stay within or near the free tiers.

**Security notes:**
- The API key is visible to anyone who can load the page. It only gates and throttles casual access. For a public demo, add Cognito login or CloudFront access restrictions.
- Highcharts (loaded from jsDelivr, `cdn.jsdelivr.net/npm/highcharts@12.1.2`) requires a commercial license for commercial use.

**Remove a site's stack:** see [Removing a site](#removing-a-site). The data bucket (`venko-<key>-<account>-<region>`) has `DeletionPolicy: Retain`, so the stack delete leaves it and its sensor history in place. Empty and delete it by hand only if you really want the data gone.
