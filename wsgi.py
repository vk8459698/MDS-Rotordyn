"""WSGI entry point for serving the web interface behind a real server.

Windows (the ADRE host):

    waitress-serve --listen=0.0.0.0:8050 wsgi:server

Linux:

    gunicorn -w 4 -b 0.0.0.0:8050 wsgi:server

Parsed runs are cached in the worker process and keyed by a token held in the
browser.  waitress serves every request from one process, so that cache is
shared and nothing further is needed.  gunicorn with several workers needs
sticky sessions, or the cache in ``rotordyn.webapp`` moved to Redis.

See DEPLOY.md for the firewall, service and cost details.
"""

from __future__ import annotations

import os

from rotordyn.webapp import create_app

app = create_app(os.environ.get("ROTORDYN_DATA", "."))
server = app.server
