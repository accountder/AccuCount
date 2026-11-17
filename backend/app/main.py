"""应用入口。"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .db import Base, SessionLocal, engine
from .migrate import ensure_defaults, ensure_schema
from .seed import seed

DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    ensure_schema()
    db = SessionLocal()
    try:
        seed(db)
        ensure_defaults(db)
    finally:
        db.close()
    yield


app = FastAPI(title="AccuCount 账务系统", version="0.2.0", lifespan=lifespan)
app.include_router(router)


@app.get("/api/health")
def health():
    return {"ok": True, "version": app.version, "frontend": DIST.exists()}


if DIST.exists():
    assets = DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        target = DIST / full_path
        if full_path and target.is_file():
            return FileResponse(target)
        return FileResponse(DIST / "index.html")
