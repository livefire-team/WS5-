import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "app"))
os.environ.setdefault("AUTH_MODE", "passcode")
os.environ.setdefault("SESSION_SECRET", "test-secret-" + "x" * 40)
os.environ.setdefault("PASSCODE_SHA256", __import__("hashlib").sha256(b"open-sesame").hexdigest())
