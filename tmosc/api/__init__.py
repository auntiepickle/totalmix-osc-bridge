"""HTTP + WebSocket API (FastAPI). The application object lives in tmosc.api.app
(`create_app()` + lifespan; run it with `uvicorn tmosc.api.app:app` or
`python -m tmosc`). Routes are one APIRouter per area under routes/, config
persistence in persistence.py, the opt-in token gate in auth.py. Deliberately
no re-export here: a package attribute named `app` would shadow the submodule."""
