"""Compatibility shim - the server moved into the tmosc package.

Docker images built before the move start the server with
`uvicorn web.web_client:app` (the container is NOT rebuilt on a normal deploy),
so this module keeps exporting `app` until every image is rebuilt. New code and
the Dockerfile use `uvicorn tmosc.api.app:app` / `python -m tmosc`.
"""
from tmosc.api.app import app  # noqa: F401
