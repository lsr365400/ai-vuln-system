import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from src.database import init_db
from src.config import load_settings
from src.event_bus import bus
from src.scheduler import Scheduler
from src.api.routes_sessions import router as sessions_router
from src.api.routes_reports import router as reports_router
from src.api.websocket_handler import router as ws_router

settings = load_settings()
scheduler = Scheduler(max_concurrent=5, max_per_project=3)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    app.state.db = await init_db(settings.database_path)
    # Mark any lingering "running" sessions as error (server restart = lost tasks)
    await app.state.db.execute(
        "UPDATE sessions SET status='error', error_msg='server restarted', finished_at=datetime('now') WHERE status='running'"
    )
    await app.state.db.commit()
    app.state.settings = settings
    app.state.scheduler = scheduler
    app.state.event_bus = bus
    asyncio.create_task(scheduler.start())
    yield
    # Shutdown
    await app.state.db.close()


app = FastAPI(title="AI Vulnerability Scanner", version="2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessions_router)
app.include_router(reports_router)
app.include_router(ws_router)

# Serve frontend static files
frontend_dir = Path("frontend")
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
