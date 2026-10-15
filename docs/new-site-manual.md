# Akvo Green – Manual: setting up a new site

As of 2026-10-13. Living copy: [Claude doc](https://claude.ai/code/artifact/8f138779-36aa-4cdf-ae01-26904ca52ea6).

## Overview

A new site takes 9 steps and ends with one Raspberry Pi publishing to its own AWS stack and its own web dashboard. Sites share the AWS account and the code (repos `Akvo_Green` and `VenkoDemo`), never data, buckets, API keys or URLs.

Everything is named after one **site key**: short, lowercase, typed the same way everywhere. The examples in this manual set up a site called `plant5`; the reference site is `cmd4`.

| Item | Rule | Example (`plant5`) |
| --- | --- | --- |
| Site key | lowercase, no spaces | `plant5` |
| Gateway id = MQTT client id | `AKVO_<key>`, unique per Pi | `AKVO_plant5` |
| MQTT topics | `VENKO/<key>/data`, `VENKO/<key>/system` | `VENKO/plant5/data` |
| Gateway config folder | `Akvo_Green/sites/<key>/config_data/` | `sites/plant5/config_data/` |
| Certificates (git-ignored) | `Akvo_Green/sites/<key>/certs/` | `sites/plant5/certs/` |
| IoT policy / thing | `venko-<key>-gateway` / `AKVO_<key>` | `venko-plant5-gateway` |
| AWS stack / samconfig env | `venko-<key>` / `<key>` | `venko-plant5` / `plant5` |
| Data bucket | `venko-<key>-<account>-<region>` | `venko-plant5-884520769610-us-east-1` |
| Web bucket | `venko-<key>-web-<account>` | `venko-plant5-web-884520769610` |
| Athena / Glue database | `venko_<key>` | `venko_plant5` |
| Dashboard labels | `VenkoDemo/config/sites/<key>.json` | `config/sites/plant5.json` |

Case matters: folder names, topics, IoT policy resources and bucket names are all case-sensitive. A folder `sites/Plant5` with topics `VENKO/plant5/*` doesn't fail loudly; the Pi is just not allowed to publish.

Where each step happens:

```
 Raspberry Pi                 Your computer: Akvo_Green      AWS: VenkoDemo
 ───────────────────────      ─────────────────────────      ─────────────────────────
 1 Write the SD card
 2 Update Pi, check RS-485 ─┐
                            └─▶ 3 Gateway CSVs + build
                                4 WiFi passwords ──────────┐
                                                           └─▶ 5 IoT certificate + policy
                                                               6 AWS stack (sam deploy)
 ┌──────────────────────────────────────────────────────────── 7 Dashboard labels + CORS
 └▶ 8 push_site + install.sh ─┐
                              └─▶ 9 Check and record
```

You work mostly from your computer: the Pi is touched at the start (SD card) and the end (install), AWS in the middle. Steps 3, 4 and 8 run from the Akvo_Green repo.

Set these shell variables once on your computer; every command below uses them:

```bash
KEY=plant5; CLIENT=AKVO_plant5; ACCT=884520769610
```

## Requirements

Have everything below ready before step 1; the information in the first table is what most often stalls a setup halfway.

**Information to collect first**

| Item | Example | Used in |
| --- | --- | --- |
| Site key (lowercase) | `plant5` | everywhere |
| Pi's IP address, reserved on the site router | `192.168.68.140` | steps 2, 8, 9 |
| WiFi networks: up to 2 SSIDs + passwords (none if on Ethernet) | `Planta`, `Oficina` | step 4 |
| Sensor list, from each sensor's Modbus manual: slave address, register address and count, data type, scale/offset, unit, alarm min/max | `PhSensor`: slave 25, reg 0, 2 regs, float, pH 0–14 | step 3 (`devices.csv`) |
| Bus serial settings: baud rate, parity, stop bits, data bits | 9600, N, 1, 8 | step 3 (`modbus.csv`) |
| Site time zone | `America/Mexico_City` | steps 1, 3, 6 |
| Dashboard title and Spanish sensor labels | `Venko – Plant5`, `Temperatura Pt100` | step 7 |

**Hardware at the site**

| Part | Notes |
| --- | --- |
| Raspberry Pi 4, 3B+ or CM5 + 16 GB+ SD card | Pi 4 if it will drive a monitor |
| Pi power supply, 5 V 2.4 A or more | Short, thick cable; the official 5.1 V 3 A gives the most margin |
| RS-485 interface | USB-RS485 adapter (`/dev/ttyUSB0`) or Waveshare 2-CH isolated RS485 HAT (`/dev/ttySC0`) |
| RS-485 cabling | Twisted pair A/B plus GND reference; 120 Ω termination at both ends of the line |
| Sensor power supply, 12 V | Separate from the Pi; for a bus like cmd4's (7 devices, one with a UV lamp and wiper) 2.5–3 A |
| Network | Ethernet cable or WiFi coverage at the cabinet |
| Optional: 1.54" I2C OLED (SSD1309), monitor + HDMI cable, keyboard/mouse | The monitor needs its own 5 V 3 A supply, never a Pi USB port |

**Network access from the Pi**

- Outbound internet: TCP **8883** to AWS IoT (`a2zzu55sjawy4x-ats.iot.us-east-1.amazonaws.com`, MQTT over TLS) and 443 for updates.
- DNS, and TCP 53 to `8.8.8.8` (the gateway's internet check).
- SSH (22) from your computer to the Pi on the local network.

**Software on your computer**

| Tool | Tested version | Used for |
| --- | --- | --- |
| Git + both repos side by side: `~/git_reps/claude/projects/Venko/Akvo_Green` and `.../VenkoDemo` | — | everything |
| Python 3 | 3.14.4 | config build, dashboard tools |
| AWS CLI v2, logged in (`aws sts get-caller-identity` works) | 2.37.4 | steps 5–7, 9 |
| AWS SAM CLI | 1.166.2 | step 6 |
| SSH client + key (`~/.ssh/id_*.pub`), `rsync` | — | steps 1, 8 |
| Raspberry Pi Imager | — | step 1 |
| Optional: `dpkg-deb` and the `Tools/AkvoModbus` repo next to the others | — | AKVO Modbus Tool install (skipped if missing) |

**Python libraries**

| Where | Libraries (pinned version) | Installed by |
| --- | --- | --- |
| Pi – gateway (`gateway/requirements.txt`) | `pymodbus` 3.15.0, `pyserial` 3.5, `psutil` 7.2.2, `awsiotsdk` 1.31.0, `awscrt` 0.36.1 | `install.sh`; `push_site.sh` updates them when the file changes |
| Pi – OLED, optional (`gateway/requirements-oled.txt`) | `luma.oled` 3.16.0 (+ `Pillow`, `smbus2`) | same |
| Pi – AKVO Modbus Tool | `customtkinter`, `darkdetect`, `Pillow`, `pymodbus`, `pyserial`, `openpyxl`, `pandas` | `akvo-modbus-setup` (run by `push_site.sh`) |
| Your computer – VenkoDemo tools and tests | `boto3`, `openpyxl`, `jinja2`, `tzdata`, `pytest` | you: `pip install -r backend/requirements.txt boto3 pytest` in a venv |
| AWS Lambda | `openpyxl`, `jinja2`, `tzdata` (`backend/requirements.txt`); `boto3` comes with the runtime | `sam build` |

The local dashboard and `config_manager.py` need only the Python standard library.

**AWS account and permissions**

- Account `884520769610`, region `us-east-1`.
- Your AWS user needs to create CloudFormation stacks with IAM roles (`CAPABILITY_IAM`), S3 buckets, IoT certificates/policies/things, Lambda, API Gateway, Firehose, Glue/Athena, DynamoDB, CloudFront and EventBridge. Simplest: an admin user for whoever sets up sites.

**Installed on the Pi automatically** (no action needed)

- By `install.sh`: `python3-venv`, `python3-pip`, `socat`, `i2c-tools`; a Python venv with `pymodbus`, `pyserial`, `psutil`, `awsiotsdk`, `luma.oled`; the services; serial-port access.
- By `push_site.sh`: the code, the site config, certificates, WiFi profiles and the AKVO Modbus Tool.
- The Pi needs only Raspberry Pi OS 64-bit (step 1) and internet access during install.

## Step 1 – Write the SD card

In Raspberry Pi Imager, on any computer:

1. **OS:** *Raspberry Pi OS (64-bit) with desktop*. The desktop is needed for the AKVO Modbus Tool and the dashboard kiosk; *Lite* works if you need neither.
2. **Edit settings:**
   - hostname `plant5`;
   - user `pi` and a password;
   - WiFi SSID, password and country (skip on Ethernet);
   - time zone `America/Mexico_City`;
   - **Services:** enable SSH, *public-key only*, paste your `~/.ssh/id_*.pub`.
3. Write the card, boot the Pi, find its address (`ping plant5.local` or the router's client list), and reserve that IP on the router.

## Step 2 – Prepare the Pi

```bash
ssh pi@192.168.68.140            # your Pi's address
sudo apt update && sudo apt full-upgrade -y && sudo reboot
```

After the reboot, check the RS-485 interface:

- **USB adapter:** `ls /dev/ttyUSB*` shows `/dev/ttyUSB0`.
- **Waveshare 2-CH HAT:** set its DIP switch to *auto*, add this line under `[all]` in `/boot/firmware/config.txt`, reboot, then `ls /dev/ttySC*` shows `ttySC0` and `ttySC1`:

```
dtoverlay=sc16is75x-spi,sc16is752,spi1-1cs,spi1-0,int_pin=24
```

- **OLED (optional):** also add `dtparam=i2c_arm=on`, reboot, and `i2cdetect -y 1` shows `3c`.

## Step 3 – Gateway config (Akvo_Green)

Start from cmd4's four CSV files, edit them, then build `config.json`:

```bash
cd ~/git_reps/claude/projects/Venko/Akvo_Green
mkdir -p sites/$KEY/config_data
cp sites/cmd4/config_data/{devices,modbus,system,aws}.csv sites/$KEY/config_data/
```

The files must be inside `sites/$KEY/config_data/`, not `sites/$KEY/`.

**`system.csv`** – gateway identity, timing and optional features:

```csv
gateway_id,city,poll_interval,system_interval,watchdog_timeout,database_enabled,database_retention_days,web_enabled,web_port,wifi1_ssid,wifi2_ssid
AKVO_plant5,America/Mexico_City,20,60,120,1,30,1,8080,Planta,Oficina
```

- `gateway_id` = `$CLIENT`. `poll_interval` = seconds between sensor reads.
- `database_enabled=1` keeps a local history on the Pi; `web_enabled=1` serves it at `http://<pi>:8080`.
- `wifi1_ssid` / `wifi2_ssid` = WiFi networks to join, preferred first (step 4). Leave the columns out for none.
- Optional OLED: add `display_enabled,display_driver,display_address` = `1,ssd1309,0x3C`.

**`modbus.csv`** – the RS-485 bus:

```csv
port,baudrate,timeout,parity,stopbits,bytesize,simulate_enabled
/dev/ttyUSB0,9600,1.0,N,1,8,1
```

- `port`: `/dev/ttyUSB0` (USB adapter) or `/dev/ttySC0` (HAT channel 1).
- `simulate_enabled`: `1` = synthetic demo data, `0` = real sensors. See the real-sensor known issue under *Common problems* before using `0`.

**`devices.csv`** – one row per sensor; rows with the same `device_id` form one device:

```csv
device_id,slave,sensor_name,addr,count,scale,offset,unit,type,min,max,enabled
DEV_1,2,Pt100Sensor,0,1,1,0,°C,float,0,55,1
DEV_3,3,Humedad,1,1,1,0,%HR,float,0,80,1
DEV_5,25,PhSensor,0,2,1,0,unit,float,0,14,1
```

- `slave` = Modbus address; `addr` / `count` = first holding register and how many.
- `type`: `float`, `float32`, `int`, `uint16`, `uint32`, `int32`; only the float types apply `scale` and `offset`.
- `min` / `max` = alarm limits (also the OLED's ▲/▼). `enabled=0` skips the row.
- Several sensors on one physical unit share a `slave`; the build then warns *Duplicate slave ID*, which is harmless.

**`aws.csv`** – where to publish:

```csv
host,client_id,ca,cert,key,topic_pub,topic_system,dashboard_url,data_bucket,web_bucket
a2zzu55sjawy4x-ats.iot.us-east-1.amazonaws.com,AKVO_plant5,./certs/AmazonRootCA1.pem,./certs/certificate.pem.crt,./certs/private.pem.key,VENKO/plant5/data,VENKO/plant5/system,,venko-plant5-884520769610-us-east-1,venko-plant5-web-884520769610
```

- `host` is the same for every site; keep the three cert paths as they are.
- Leave `dashboard_url` empty until step 6 gives it.

Build and validate:

```bash
cd gateway && python3 config_manager.py build --config-dir ../sites/$KEY/config_data && cd ..
```

Expected: `Loaded N devices` and `Validation successful`.

## Step 4 – WiFi networks (optional)

The Pi can know two WiFi networks, preferred first; you change them from your computer instead of re-imaging the SD card. Only the SSIDs go in `system.csv` (step 3). The passwords go in `sites/$KEY/wifi.csv` — next to `config_data/`, not inside it — which is git-ignored like the certificates and never reaches `config.json`. Step 3 doesn't copy it; create it from the Akvo_Green root:

```bash
cp sites/cmd4/wifi.csv sites/$KEY/wifi.csv     # same networks as cmd4: keeps their passwords
# or start empty for other networks:
printf 'ssid,password\n' > sites/$KEY/wifi.csv
chmod 600 sites/$KEY/wifi.csv
```

Then edit it with a text editor so it has one row per SSID in `system.csv`:

```csv
ssid,password
Planta,your-planta-password
Oficina,your-oficina-password
```

- One row per SSID in `system.csv`; an empty password means an open network.
- WPA passwords are 8–63 characters. A placeholder like `CHANGE_ME` is refused on push.
- On the next push (step 8) they become NetworkManager profiles `akvo-wifi1` (priority 20) and `akvo-wifi2` (priority 10). The current connection isn't dropped; the Pi switches when its network goes away or at the next reboot.
- To change WiFi later: edit `system.csv` and `wifi.csv`, rebuild, push.

## Step 5 – IoT certificate, policy and thing (AWS)

Each Pi gets its own certificate and a policy that only lets it connect as `$CLIENT` and publish to `VENKO/$KEY/*`. The private key can only be downloaded now; it goes in `sites/$KEY/certs/` (git-ignored) and on the Pi, nowhere else. Run from the Akvo_Green root:

```bash
mkdir -p sites/$KEY/certs && cd sites/$KEY/certs && umask 077
aws iot create-keys-and-certificate --set-as-active \
  --certificate-pem-outfile certificate.pem.crt --private-key-outfile private.pem.key \
  --query certificateArn --output text > cert.arn
cp ../../cmd4/certs/AmazonRootCA1.pem .
cat > policy.json <<JSON
{ "Version": "2012-10-17", "tatement": [
  { "Effect": "Allow", "Action": "iot:Connect", "Resource": "arn:aws:iot:us-east-1:$ACCT:client/$CLIENT" },
  { "Effect": "Allow", "Action": "iot:Publish", "Resource": "arn:aws:iot:us-east-1:$ACCT:topic/VENKO/$KEY/*" } ] }
JSON
aws iot create-policy --policy-name venko-$KEY-gateway --policy-document file://policy.json
aws iot attach-policy --policy-name venko-$KEY-gateway --target "$(cat cert.arn)"
aws iot create-thing --thing-name $CLIENT
aws iot attach-thing-principal --thing-name $CLIENT --principal "$(cat cert.arn)"
cd ../../..
```

Result: five files in `sites/plant5/certs/`, all mode `600` — `certificate.pem.crt`, `private.pem.key`, `AmazonRootCA1.pem`, `cert.arn`, `policy.json`. `CLIENT` must equal `client_id` in `aws.csv`, and `KEY` the key in its topics.

## Step 6 – AWS stack (VenkoDemo)

One CloudFormation stack per site holds the storage, API, reports and dashboard. Add the site's section to `VenkoDemo/infra/samconfig.toml` (copy `[cmd4.*]`, replace every `cmd4`, set `PartitionStartDate` to today, keep `AllowedOrigin` at `*` until step 7):

```toml
[plant5.deploy.parameters]
stack_name = "venko-plant5"
resolve_s3 = true
s3_prefix = "venko-plant5"
region = "us-east-1"
confirm_changeset = true
capabilities = "CAPABILITY_IAM"
parameter_overrides = "DataTopic=\"VENKO/plant5/data\" SystemTopic=\"VENKO/plant5/system\" PartitionStartDate=\"2026-10-13\" AllowedOrigin=\"*\" ReportTimezone=\"America/Mexico_City\" GlueDatabaseName=\"venko_plant5\" DataBucketName=\"venko-plant5-884520769610-us-east-1\" WebBucketName=\"venko-plant5-web-884520769610\""
image_repositories = []

[plant5.global.parameters]
region = "us-east-1"
```

```bash
cd ~/git_reps/claude/projects/Venko/VenkoDemo/infra
sam build --template template.yaml
sam deploy --config-env $KEY
```

- Bucket names are lowercase only.
- When it ends with `CREATE_COMPLETE`, copy the `DashboardUrl` output (e.g. `https://d1f1ymj0tb4k52.cloudfront.net`) into `aws.csv`'s `dashboard_url` and rerun the build from step 3.
- Never change `DataBucketName` on an existing stack: the old bucket is kept and a new empty one is created.

## Step 7 – Dashboard labels, publish and CORS

The dashboard reads the sensor list from the gateway's `config.json`; a small overrides file adds Spanish labels, groups and decimals. Generate a starter file, edit it, then publish:

```bash
cd ~/git_reps/claude/projects/Venko/VenkoDemo
python3 tools/sync_config.py --init --source ../Akvo_Green/sites/$KEY/config_data/config.json \
    --overrides config/sites/$KEY.json
```

`--init` reuses labels from other sites where the sensor key matches and never overwrites an existing file. Example of the result after editing:

```json
{
  "site": { "title": "Venko – Plant5", "brand": "Ghotrix - Semca" },
  "units": { "unit": "pH", "uS/cm": "µS/cm" },
  "defaults": { "warn_margin": 0.1, "decimals": 2 },
  "groups": ["Temperatura", "Ambiente", "Química"],
  "sensors": {
    "DEV_1.Pt100Sensor": { "label": "Temperatura Pt100", "group": "Temperatura", "decimals": 1 },
    "DEV_3.Humedad":     { "label": "Humedad", "group": "Ambiente" },
    "DEV_5.PhSensor":    { "label": "pH", "group": "Química" }
  }
}
```

Keys are `DEVICE_ID.SensorName`, exactly as in `devices.csv` (case-sensitive). Publish config and web app to the site:

```bash
bash tools/deploy_web.sh venko-$KEY ../Akvo_Green/sites/$KEY/config_data/config.json config/sites/$KEY.json
```

It refuses to upload if the config's topics don't match the stack's. Then **lock CORS** to the dashboard URL: set `AllowedOrigin=\"https://<DashboardUrl host>\"` in `[plant5.deploy.parameters]` and run:

```bash
cd infra && sam deploy --config-env $KEY && cd ..
API_ID=$(aws cloudformation describe-stack-resource --stack-name venko-$KEY \
  --logical-resource-id Api --query StackResourceDetail.PhysicalResourceId --output text)
aws apigateway create-deployment --rest-api-id "$API_ID" --stage-name prod
```

SAM doesn't republish the API stage when only a parameter changes, hence the `create-deployment`.

## Step 8 – Install on the Pi

From your computer, in Akvo_Green. The first push carries the certificates; `install.sh` runs once per Pi:

```bash
cd ~/git_reps/claude/projects/Venko/Akvo_Green
tools/push_site.sh $KEY pi@192.168.68.140 --certs        # add --no-modbus-tool on Raspberry Pi OS Lite
ssh -t pi@192.168.68.140 'cd Akvo_Green && ./install.sh --skip-certs && sudo systemctl start akvo-green'
```

- `push_site.sh` copies the code, the site's `config.json`, the certificates (with `--certs`), the WiFi profiles and the AKVO Modbus Tool. It asks for the Pi's sudo password for the WiFi step.
- `install.sh` sets up packages, the Python venv, serial-port access and the services `akvo-green` and `akvo-history-web` (and `akvo-oled`).
- **Every later change** is the same push without `--certs`: edit the CSVs, rebuild (step 3), `tools/push_site.sh $KEY pi@<pi>`. The gateway reloads `config.json` within 5 s.
- Push to the right Pi: pushing one site's config to another site's Pi makes that Pi publish under the wrong name and stops its own site.

**Optional extras on the Pi**

| Extra | How |
| --- | --- |
| Dashboard full screen at boot | `ssh pi@<pi> 'cd Akvo_Green && tools/setup_kiosk.sh'` (`--window` for a normal window, `--off` to remove) |
| OLED status screen | `display_enabled=1` in `system.csv` + I2C on (step 2), rebuild, push |
| AKVO Modbus Tool | On the Pi's desktop or VNC: `sudo systemctl stop akvo-green`, then `akvo-modbus`; start `akvo-green` again when done |

## Step 9 – Check and record

Within about a minute of starting the gateway:

```bash
ssh pi@192.168.68.140 'journalctl -u akvo-green -n 30 --no-pager'     # "MQTT connected", no Modbus errors
aws s3 cp s3://venko-$KEY-$ACCT-us-east-1/latest/data.json - | head -c 300; echo
ssh pi@192.168.68.140 'vcgencmd get_throttled'                        # throttled=0x0 = power OK
```

Open the dashboard URL and confirm **Gateway en línea** with the dials filling in. Then write the site summary page and commit both repos (certificates and `wifi.csv` are git-ignored; check `git status` anyway):

```bash
cd ~/git_reps/claude/projects/Venko/VenkoDemo
python3 tools/site_summary.py $KEY --pi pi@192.168.68.140     # writes docs/sites/plant5.md
```

**Checklist**

- [ ] Site key chosen, lowercase, used identically everywhere
- [ ] SD card written, SSH by key, IP reserved on the router
- [ ] RS-485 port visible (`/dev/ttyUSB0` or `/dev/ttySC0`)
- [ ] Four CSVs edited, `build` says *Validation successful*
- [ ] `wifi.csv` filled (if WiFi), mode 600
- [ ] Certificate ACTIVE, policy and thing attached
- [ ] Stack `CREATE_COMPLETE`, `dashboard_url` in `aws.csv`, rebuilt
- [ ] Overrides labelled, `deploy_web.sh` done, CORS locked
- [ ] Pushed with `--certs`, `install.sh` run, `akvo-green` active
- [ ] Data in `latest/data.json`, dashboard shows *Gateway en línea*
- [ ] `site_summary.py` run, site added to both READMEs, repos committed

## Removing a site

Remove in this order: the Pi first (so it stops publishing), then AWS, then the repos. The data bucket survives the stack on purpose; deleting it is a separate, deliberate step that erases the site's history. Set the variables first, as in the overview:

```bash
KEY=plant5; CLIENT=AKVO_plant5; ACCT=884520769610; PI=pi@192.168.68.140
```

**R1 – Back up the Pi's local history (optional)**

```bash
ssh $PI "tar czf ~/akvo-$KEY-backup-$(date +%F).tgz -C ~ Akvo_Green/data Akvo_Green/config_data"
scp "$PI:~/akvo-$KEY-backup-*.tgz" .
```

**R2 – Clean the Raspberry Pi**

If the Pi will become another site, skip this: push the new site over it with `tools/push_site.sh <new-key> $PI --certs` and restart `akvo-green`. To decommission it:

```bash
ssh -t $PI '
  for s in akvo-green akvo-history-web akvo-oled; do
    sudo systemctl disable --now $s 2>/dev/null
    sudo rm -f /etc/systemd/system/$s.service
  done
  sudo systemctl daemon-reload
  rm -f ~/.config/autostart/akvo-kiosk.desktop       # dashboard kiosk at boot
  sudo rm -f /etc/sudoers.d/akvo-green-reboot
  sudo apt-get remove -y akvo-modbus; rm -rf ~/.local/share/akvo-modbus
  rm -rf ~/Akvo_Green ~/.config/akvo-kiosk
'
```

The WiFi profiles `akvo-wifi1`/`akvo-wifi2` are left on purpose: removing the one the Pi is using cuts your SSH session. Remove them only from a Pi on a cable, or at the site: `sudo nmcli connection delete akvo-wifi1 akvo-wifi2`.

**R3 – Delete the AWS stack**

Empty the web bucket first (CloudFormation won't delete a non-empty bucket), then delete the stack. This removes Firehose, the IoT rules, Glue/Athena, the Lambdas and API, CloudFront and the web bucket — everything except the data bucket.

```bash
aws s3 rm s3://venko-$KEY-web-$ACCT --recursive
aws cloudformation delete-stack --stack-name venko-$KEY
aws cloudformation wait stack-delete-complete --stack-name venko-$KEY
```

Use `aws cloudformation delete-stack` rather than `sam delete`: `sam delete` crashes with `NoSuchBucket` when a bucket is already gone.

**R4 – Delete the IoT certificate, thing and policy**

They aren't part of the stack (step 5 made them by hand). Run from the Akvo_Green root:

```bash
ARN=$(cat sites/$KEY/certs/cert.arn); CERT_ID=${ARN##*/}
aws iot detach-thing-principal --thing-name $CLIENT --principal "$ARN"
aws iot detach-policy --policy-name venko-$KEY-gateway --target "$ARN"
aws iot update-certificate --certificate-id $CERT_ID --new-status INACTIVE
aws iot delete-certificate --certificate-id $CERT_ID
aws iot delete-thing --thing-name $CLIENT
for v in $(aws iot list-policy-versions --policy-name venko-$KEY-gateway \
    --query 'policyVersions[?!isDefaultVersion].versionId' --output text); do
  aws iot delete-policy-version --policy-name venko-$KEY-gateway --policy-version-id $v
done
aws iot delete-policy --policy-name venko-$KEY-gateway
```

The loop removes older policy versions; `delete-policy` refuses a policy that still has them.

**R5 – Delete the data bucket (deliberate: erases all history)**

Only when the site's readings, reports and history are really no longer needed. Download what you want to keep first:

```bash
aws s3 sync s3://venko-$KEY-$ACCT-us-east-1 ./venko-$KEY-archive     # optional archive
aws s3 rb s3://venko-$KEY-$ACCT-us-east-1 --force
```

**R6 – Clean the repos and commit**

| Repo | Remove |
| --- | --- |
| VenkoDemo | the `[plant5.*]` sections in `infra/samconfig.toml`; `config/sites/plant5.json`; `docs/sites/plant5.md` and its row in `docs/sites/README.md`; the site in README's *Sites in this account* |
| Akvo_Green | the folder `sites/plant5/` (config, certificates, `wifi.csv`); the site's rows in `sites/README.md` |

Commit and push both repos. Check that nothing points at the old site: `grep -rn plant5` in both.

**Removal checklist**

- [ ] History backed up (if wanted)
- [ ] Pi services, kiosk, sudoers rule, Modbus Tool and `~/Akvo_Green` removed — or Pi re-pushed as a new site
- [ ] Web bucket emptied, stack `venko-plant5` deleted
- [ ] Certificate, thing and policy deleted
- [ ] Data bucket deleted, or kept on purpose
- [ ] samconfig, overrides, docs and `sites/plant5/` removed, both repos committed

## Common problems

| Symptom | Cause | Fix |
| --- | --- | --- |
| `build`: `FileNotFoundError: .../config_data/devices.csv` | CSVs in `sites/$KEY/` | Move them into `sites/$KEY/config_data/` |
| `sam deploy`: `Missing option '--stack-name'` | `KEY` unset or no `[$KEY.*]` section | Set `KEY`, add the samconfig section |
| Stack ends in `ROLLBACK_COMPLETE` | Bucket or Glue database name already exists (left over, or another stack's) | Delete the old stack/bucket (`aws cloudformation delete-stack --stack-name venko-$KEY`), then deploy again |
| `sam deploy`: `S3 Bucket does not exist` | SAM's artifact bucket was deleted | Delete stack `aws-sam-cli-managed-default`, deploy again |
| Pi log: `No such file … certificate.pem.crt` | First push without `--certs` | `tools/push_site.sh $KEY pi@<pi> --certs` |
| Pi connects but nothing reaches AWS | Client id, topics or policy differ (often upper vs lower case) | Compare `aws.csv`, `policy.json` and `samconfig.toml`; all must use the same lowercase key |
| Dashboard: `deploy_web.sh` refuses to upload | Config topics ≠ stack topics | Fix `aws.csv` topics, rebuild |
| Real sensors: `unexpected keyword argument 'simulate'` | Known gateway bug with `simulate_enabled=0` | Stay in simulate mode until it's fixed in Akvo_Green |
| `akvo-modbus`: `no $DISPLAY` | Started from SSH | Run it in the Pi's desktop/VNC, or `DISPLAY=:0 akvo-modbus` |
| Random reboots, SD errors | Under-voltage | `vcgencmd get_throttled` ≠ `0x0` → better cable or official supply |

Full reference: `VenkoDemo/README.md` ("Adding a site") and `Akvo_Green/docs/edge_node_config/CONFIGURATION.md`.
