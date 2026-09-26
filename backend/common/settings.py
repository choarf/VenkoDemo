"""Runtime settings from the Lambda environment (see infra/template.yaml Globals)."""
import os

BUCKET = os.environ.get("BUCKET", "")
TABLE = os.environ.get("TABLE", "")
GLUE_DB = os.environ.get("GLUE_DB", "")
WORKGROUP = os.environ.get("WORKGROUP", "primary")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
REPORT_TZ = os.environ.get("REPORT_TZ", "America/Mexico_City")
REPORT_FUNCTION = os.environ.get("REPORT_FUNCTION", "")

DATA_TABLE = "readings_raw"
SYSTEM_TABLE = "system_raw"

CONFIG_KEY = "config/config.json"
LATEST_DATA_KEY = "latest/data.json"
LATEST_SYSTEM_KEY = "latest/system.json"

# Hard limits so a careless query can't run a huge Athena scan from the dashboard.
MAX_RANGE_DAYS = 31
