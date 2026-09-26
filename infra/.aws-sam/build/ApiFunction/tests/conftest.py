import os
import sys
from pathlib import Path

# Lambda code root (backend/) is the import root, as in the deployed package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
