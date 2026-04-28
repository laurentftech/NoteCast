import sys
from pathlib import Path
from unittest.mock import MagicMock

# Stub heavy/unavailable dependencies so harvester.py can be imported without Docker
for mod in ("podgen", "playwright", "notebooklm"):
    sys.modules.setdefault(mod, MagicMock())

# Suppress module-level EPISODES_DIR.mkdir() which tries to create /public/episodes
_orig_mkdir = Path.mkdir
def _noop_mkdir(self, *args, **kwargs):
    if str(self).startswith("/public") or str(self).startswith("/data") or str(self).startswith("/root"):
        return
    _orig_mkdir(self, *args, **kwargs)
Path.mkdir = _noop_mkdir

# Stub _load_or_generate_feed_token so _build_users() doesn't write to /data at import time
import unittest.mock as _mock
_orig_write_text = Path.write_text
def _safe_write_text(self, data, *args, **kwargs):
    if str(self).startswith("/data") or str(self).startswith("/root"):
        return
    return _orig_write_text(self, data, *args, **kwargs)
Path.write_text = _safe_write_text

_orig_read_text = Path.read_text
def _safe_read_text(self, *args, **kwargs):
    if str(self).startswith("/data") or str(self).startswith("/root"):
        raise FileNotFoundError
    return _orig_read_text(self, *args, **kwargs)
Path.read_text = _safe_read_text

_orig_exists = Path.exists
def _safe_exists(self):
    if str(self) in ('/data/.feed_token', '/root/.notebooklm/storage_state.json'):
        return False
    return _orig_exists(self)
Path.exists = _safe_exists
