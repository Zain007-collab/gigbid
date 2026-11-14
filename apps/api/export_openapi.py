"""Write FastAPI's OpenAPI schema to a file (the web client is generated from it)."""
import json
import os
import sys

os.environ.setdefault("GIGBID_OFFLINE", "1")
from apps.api.main import app  # noqa: E402

json.dump(app.openapi(), open(sys.argv[1] if len(sys.argv) > 1 else "openapi.json", "w"), indent=1)
