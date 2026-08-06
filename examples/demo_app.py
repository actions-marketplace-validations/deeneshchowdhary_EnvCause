import os
import sys

# Simulate a bug that only occurs when two config changes interact.
if os.getenv("FEATURE_NEW_AUTH") == "true" and os.getenv("JWT_ALGORITHM") == "RS256":
    print("Authentication startup failure", file=sys.stderr)
    raise SystemExit(1)

print("OK")
