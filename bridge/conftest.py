import sys
from pathlib import Path
from unittest.mock import MagicMock

# Stub heavy/unavailable dependencies so harvester.py can be imported without Docker
for mod in ("podgen", "aiohttp", "aiohttp.web", "playwright", "notebooklm"):
    sys.modules.setdefault(mod, MagicMock())

import aiohttp
aiohttp.web = sys.modules["aiohttp.web"]

# Suppress module-level EPISODES_DIR.mkdir() which tries to create /public/episodes
_orig_mkdir = Path.mkdir
def _noop_mkdir(self, *args, **kwargs):
    if str(self).startswith("/public") or str(self).startswith("/data"):
        return
    _orig_mkdir(self, *args, **kwargs)
Path.mkdir = _noop_mkdir
