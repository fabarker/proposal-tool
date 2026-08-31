"""Port configuration - STAND-IN for the host's ``dashboardConfig.py``.

The host binds 0.0.0.0; this mirror defaults to 127.0.0.1 so a dev laptop does
not prompt about accepting external connections. Both are environment config.
This file is NOT transplanted - the host already has its own.
"""

import os

FLASK_FRONTEND_PORT = int(os.getenv('FRONTEND_PORT', 8001))
DASHBOARD_HOST = os.getenv('DASHBOARD_HOST', '127.0.0.1')
