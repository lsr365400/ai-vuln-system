import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from src.database import init_db
from src.config import load_settings
from src.event_bus import bus
from src.scheduler import Scheduler
from src.auth import AuthMiddleware, create_token
from src.api.routes_sessions import router as sessions_router
from src.api.routes_reports import router as reports_router
from src.api.websocket_handler import router as ws_router

settings = load_settings()
scheduler = Scheduler(max_concurrent=5, max_per_project=3)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db = await init_db(settings.database_path)
    await app.state.db.execute(
        "UPDATE sessions SET status='error', error_msg='server restarted', finished_at=datetime('now') WHERE status='running'"
    )
    await app.state.db.commit()
    app.state.settings = settings
    app.state.scheduler = scheduler
    app.state.event_bus = bus
    asyncio.create_task(scheduler.start())
    yield
    await app.state.db.close()


app = FastAPI(title='AI Vulnerability Scanner', version='2.0', lifespan=lifespan)

app.add_middleware(AuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(sessions_router)
app.include_router(reports_router)
app.include_router(ws_router)

frontend_dir = Path('frontend')


@app.get('/login', response_class=HTMLResponse)
async def login_page():
    login_html = frontend_dir / 'login.html'
    if login_html.exists():
        return login_html.read_text(encoding='utf-8')
    return HTMLResponse('<h2>Login page not found</h2>', status_code=404)


@app.get('/', response_class=HTMLResponse)
async def main_page():
    index_html = frontend_dir / 'index.html'
    if index_html.exists():
        return index_html.read_text(encoding='utf-8')
    return HTMLResponse('<h2>App not found</h2>', status_code=404)


@app.post('/api/auth/login')
async def login(request: Request):
    try:
        body = await request.json()
        password = body.get('password', '')
    except Exception:
        return JSONResponse({'ok': False, 'error': 'Invalid request'}, status_code=400)

    if password != request.app.state.settings.auth_password:
        return JSONResponse({'ok': False, 'error': '密码错误'}, status_code=401)

    token = create_token(request.app.state.settings.auth_secret)
    response = JSONResponse({'ok': True})
    response.set_cookie(
        key='vuln_session',
        value=token,
        httponly=True,
        samesite='strict',
        max_age=86400,
    )
    return response


@app.post('/api/auth/logout')
async def logout():
    response = JSONResponse({'ok': True})
    response.delete_cookie('vuln_session')
    return response


@app.get('/api/auth/check')
async def check_auth():
    return {'ok': True, 'authenticated': True}
