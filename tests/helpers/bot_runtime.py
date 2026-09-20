"""Import one bot singleton with isolated paths and dummy provider settings.

The temporary directory lives as long as this module. Environment overrides are
limited to the import and restored even if the bot fails to load. Tests retain
the original source defaults before enabling their shared reply configuration.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
import tempfile

UNIT_BASE_DIRECTORY = tempfile.TemporaryDirectory(
    prefix=f"mrsMThatcher-unit-import-{os.getpid()}-"
)
UNIT_BASE = Path(UNIT_BASE_DIRECTORY.name)

IMPORT_ENV = {
    "MRS_TEST_MODE": "1",
    "MRS_BASE_DIR": str(UNIT_BASE),
    "MRS_LOG_FILE": str(UNIT_BASE / "unit-test.log"),
    "X_API_BASE_URL": "http://127.0.0.1:9",
    "X_UPLOAD_BASE_URL": "http://127.0.0.1:9",
    "OPENAI_API_BASE_URL": "http://127.0.0.1:9/v1",
    "X_CONSUMER_KEY": "dummy",
    "X_CONSUMER_SECRET": "dummy",
    "X_ACCESS_TOKEN": "dummy",
    "X_ACCESS_SECRET": "dummy",
    "X_MY_USER_ID": "12345",
    "OPENAI_API_KEY": "dummy",
    "X_BEARER_TOKEN": "dummy",
}
ORIGINAL_ENV = {key: os.environ.get(key) for key in IMPORT_ENV}
os.environ.update(IMPORT_ENV)

try:
    import mrsMThatcher2 as bot

    SOURCE_DEFAULT_SINGLE_CALL_REPLY = copy.deepcopy(bot.single_call_reply)
    SOURCE_GET_TWEET_BY_ID = bot.get_tweet_by_id
    SOURCE_TWEET_LOOKUP_FETCH = bot._tweet_lookup_cache.TweetLookupCache.fetch
    bot.single_call_reply = {
        **bot.single_call_reply,
        "enabled": True,
    }
finally:
    for key, value in ORIGINAL_ENV.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

SCENARIOS = Path(__file__).resolve().parents[1] / "fixtures" / "scenarios"
