"""Routers composed by tmosc.api.app.create_app().

Each module exposes one `APIRouter` named `router` with full paths (no
prefix), imports the bridge singleton from tmosc.bridge (routes read it at
call time - tests mutate it; `request.app.state.bridge` is phase 4b), and
keeps every handler's sync/async choice as commented on the handler (sync =
threadpool for blocking device I/O, async = event-loop-only mutation of
bridge.mappings). Config persistence helpers are called through
`persistence.<name>` so a test that patches tmosc.api.persistence is seen.
No module here imports tmosc.api.app.
"""
