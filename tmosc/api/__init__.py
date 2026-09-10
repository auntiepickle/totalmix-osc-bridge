"""HTTP + WebSocket API (FastAPI). The application object lives in tmosc.api.app
(run it with `uvicorn tmosc.api.app:app` or `python -m tmosc`). Deliberately no
re-export here: a package attribute named `app` would shadow the submodule."""
