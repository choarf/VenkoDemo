#!/usr/bin/env python3
"""Set (or change) a site's password for the dashboard's Sistema view.

Only a salted PBKDF2 hash is stored, in the site's app table (pk=AUTH, sk=SYSTEM);
changing it signs everyone out of Sistema. See backend/common/sysauth.py.

  python3 tools/set_system_password.py <key>              # asks for it twice (terminal)
  python3 tools/set_system_password.py <key> --generate   # random one, printed once
  printf '%s\\n' "$PW" | python3 tools/set_system_password.py <key> --stdin
"""
import argparse
import getpass
import secrets
import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from common.sysauth import hash_password  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("key", help="site key, e.g. cmd4 (stack venko-<key>)")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--generate", action="store_true", help="create a random password and print it once")
    src.add_argument("--stdin", action="store_true", help="read the password from the first line of stdin")
    args = ap.parse_args()

    if args.generate:
        password = secrets.token_urlsafe(12)
    elif args.stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass(f"Nueva contraseña de Sistema para {args.key}: ")
        if getpass.getpass("Repítela: ") != password:
            sys.exit("error: las contraseñas no coinciden")
    try:
        stored = hash_password(password)
    except ValueError as e:
        sys.exit(f"error: {e}")

    stack = f"venko-{args.key}"
    table = boto3.client("cloudformation").describe_stack_resource(
        StackName=stack, LogicalResourceId="AppTable")["StackResourceDetail"]["PhysicalResourceId"]
    boto3.resource("dynamodb").Table(table).put_item(Item={"pk": "AUTH", "sk": "SYSTEM", "hash": stored})
    print(f"Contraseña de Sistema guardada para {stack} (tabla {table}). Las sesiones abiertas se cierran.")
    if args.generate:
        print(f"Contraseña: {password}")


if __name__ == "__main__":
    main()
