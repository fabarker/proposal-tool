"""Service configuration - STAND-IN for the host's ``pmgService/config.py``.

The host uses pydantic-settings; a plain object keeps the same attribute
surface (``settings.port``) without pulling settings machinery into the mirror.
This file is NOT transplanted - the host already has its own. The one setting
the transplant adds to the host's config is SCENARIO_ADAPTER (see
``scenario/registry.py``).
"""

import os


class Settings:
    """Runtime settings for the pmgService mirror."""

    def __init__(self):
        self.port = int(os.getenv('PMG_SVC_PORT', 8002))
        self.host = os.getenv('PMG_SVC_HOST', '127.0.0.1')


settings = Settings()
