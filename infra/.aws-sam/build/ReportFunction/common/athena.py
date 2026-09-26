"""Run an Athena query and return rows as dicts.

Results are read from the CSV Athena writes to S3 rather than paged through
GetQueryResults (1000 rows/call), which is much faster for report-sized results.
"""
import csv
import io
import time
from urllib.parse import urlparse

import boto3

from . import settings

_athena = boto3.client("athena")
_s3 = boto3.client("s3")


class QueryError(RuntimeError):
    pass


def run(sql: str, timeout_s: float = 25.0) -> list[dict]:
    qid = _athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": settings.GLUE_DB},
        WorkGroup=settings.WORKGROUP,
    )["QueryExecutionId"]

    deadline = time.time() + timeout_s
    delay = 0.3
    while True:
        ex = _athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        state = ex["Status"]["State"]
        if state == "SUCCEEDED":
            break
        if state in ("FAILED", "CANCELLED"):
            raise QueryError(ex["Status"].get("StateChangeReason", state))
        if time.time() > deadline:
            _athena.stop_query_execution(QueryExecutionId=qid)
            raise QueryError("query timed out; narrow the time range")
        time.sleep(delay)
        delay = min(delay * 1.5, 2.0)

    loc = urlparse(ex["ResultConfiguration"]["OutputLocation"])
    body = _s3.get_object(Bucket=loc.netloc, Key=loc.path.lstrip("/"))["Body"].read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(body)))


def num(v):
    """Athena CSV cells are strings; '' means NULL."""
    if v is None or v == "":
        return None
    f = float(v)
    return int(f) if f.is_integer() and "." not in v and "e" not in v.lower() else f
