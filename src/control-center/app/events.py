"""The Control Center's view of the event store.

The real code lives in src/shared/livefire_events.py, so the Control Center, WS2's Ops Service,
WS1's load generator and WS4's workflow all write events the same way. In the container image
that file is copied next to this one; in the repo it is found in src/shared.
"""
import sys
from pathlib import Path

try:
    from livefire_events import *  # noqa: F401,F403
    from livefire_events import DRILL_ID_RE, live, make_event, now_ts, parse_ts, replay, store_from_env, validate_drill_id  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
    from livefire_events import *  # noqa: F401,F403
    from livefire_events import DRILL_ID_RE, live, make_event, now_ts, parse_ts, replay, store_from_env, validate_drill_id  # noqa: F401
