"""Serve a prebuilt student client on the API origin, without a second web server."""
from pathlib import Path

from fastapi.responses import RedirectResponse
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles


CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' blob: data:; connect-src 'self'; font-src 'self'; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)


class StudentFiles(StaticFiles):
    async def get_response(self, path, scope):
        # Dist files only; a mistaken dotfile or source map is never a web resource.
        if (path not in {'', '.'} and any(part.startswith('.') for part in Path(path).parts)) or path.endswith('.map'):
            raise HTTPException(status_code=404)
        return await super().get_response(path, scope)


def install_student_web(app, directory: Path | None):
    if directory is None:
        return
    directory = directory.resolve()
    if not directory.is_dir() or not (directory / 'index.html').is_file():
        raise RuntimeError('STUDENT_WEB_DIST must contain the built student web index.html')

    @app.middleware('http')
    async def student_headers(request, call_next):
        response = await call_next(request)
        if request.url.path == '/learn' or request.url.path.startswith('/learn/'):
            response.headers['Content-Security-Policy'] = CSP
            response.headers['Referrer-Policy'] = 'no-referrer'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['Permissions-Policy'] = 'camera=(self), microphone=(), geolocation=()'
            response.headers['X-Xueban-Web'] = 'student-v2-accounts'
        return response

    @app.get('/', include_in_schema=False)
    def student_entry():
        return RedirectResponse('/learn/', status_code=307)

    app.mount('/learn', StudentFiles(directory=directory, html=True, follow_symlink=False), name='student-web')
