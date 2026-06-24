import hashlib
import hmac
import time
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Allow login/logout paths without auth
        if request.url.path in ('/login', '/api/auth/login', '/api/auth/logout'):
            return await call_next(request)

        settings = request.app.state.settings
        token = request.cookies.get('vuln_session')

        if not token or not verify_token(token, settings.auth_secret):
            if request.url.path.startswith('/api/'):
                return JSONResponse({'detail': 'Unauthorized'}, status_code=401)
            return RedirectResponse('/login')

        return await call_next(request)


def create_token(secret: str) -> str:
    ts = str(int(time.time()))
    mac = hmac.new(secret.encode(), ts.encode(), hashlib.sha256).hexdigest()
    return f'{ts}:{mac}'


def verify_token(token: str, secret: str) -> bool:
    try:
        ts_str, mac = token.split(':', 1)
        expected = hmac.new(secret.encode(), ts_str.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(mac, expected)
    except Exception:
        return False
