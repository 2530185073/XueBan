"""Serve the prebuilt sitting-teacher workbench at /staff/ on the API origin.

The build directory comes from ``STAFF_WEB_DIST``; without it the repository's
``apps/staff-web/dist`` is used only when a build is present.
"""
import os
from pathlib import Path

from fastapi.responses import RedirectResponse
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles


DEFAULT_DIST = Path(__file__).resolve().parents[3] / 'apps' / 'staff-web' / 'dist'

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' blob: data:; connect-src 'self'; font-src 'self'; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)


class StaffFiles(StaticFiles):
    async def get_response(self, path, scope):
        # Dist files only; a mistaken dotfile or source map is never a web resource.
        if (path not in {'', '.'} and any(part.startswith('.') for part in Path(path).parts)) or path.endswith('.map'):
            raise HTTPException(status_code=404)
        return await super().get_response(path, scope)


def staff_web_directory() -> Path | None:
    configured = os.environ.get('STAFF_WEB_DIST')
    if configured:
        return Path(configured)
    return DEFAULT_DIST if (DEFAULT_DIST / 'index.html').is_file() else None


def install_staff_web(app, directory: Path | None = None):
    directory = directory or staff_web_directory()
    if directory is None:
        return
    directory = directory.resolve()
    if not directory.is_dir() or not (directory / 'index.html').is_file():
        raise RuntimeError('STAFF_WEB_DIST must contain the built staff web index.html')

    @app.middleware('http')
    async def staff_headers(request, call_next):
        response = await call_next(request)
        if request.url.path == '/staff' or request.url.path.startswith('/staff/'):
            response.headers['Content-Security-Policy'] = CSP
            response.headers['Referrer-Policy'] = 'no-referrer'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
            response.headers['X-Xueban-Web'] = 'staff-v1-accounts'
        return response

    @app.get('/staff', include_in_schema=False)
    def staff_entry():
        return RedirectResponse('/staff/', status_code=307)

    app.mount('/staff', StaffFiles(directory=directory, html=True, follow_symlink=False), name='staff-web')
