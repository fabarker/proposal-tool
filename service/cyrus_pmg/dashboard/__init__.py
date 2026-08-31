"""
Dashboard module - Frontend only.

Mirror of ``cyrus_pmg.dashboard``: a thin Flask process that serves the static
page folders and reverse-proxies /api/* to the FastAPI backend. The backend
service lives in ``cyrus_pmg.pmgService``.

This package only contains:
    - dashboardFrontend.py  - Flask server for HTML/JS/CSS + API proxy
    - dashboardConfig.py    - Port configuration
    - static/               - Shared frontend assets (accessGate.js, globals.js)
    - proposalTool/         - The Proposal Tool page folder (generated)
"""
