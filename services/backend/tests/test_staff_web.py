from dataclasses import replace

from fastapi.testclient import TestClient
import pytest

from xueban_backend import staff_web
from xueban_backend.app import create_app


def web_build(tmp_path, name='dist', title='学伴 · 坐班工作台'):
    root = tmp_path / name
    root.mkdir()
    (root / 'assets').mkdir()
    (root / 'index.html').write_text(f'<!doctype html><title>{title}</title>')
    (root / 'assets/app.js').write_text('export const boot = true;')
    (root / '.env').write_text('must not be served')
    (root / 'assets/app.js.map').write_text('must not be served')
    private = tmp_path / f'{name}-private.txt'
    private.write_text('must not be served')
    (root / 'outside.txt').symlink_to(private)
    return root


@pytest.fixture
def no_default_build(monkeypatch, tmp_path):
    monkeypatch.delenv('STAFF_WEB_DIST', raising=False)
    monkeypatch.setattr(staff_web, 'DEFAULT_DIST', tmp_path / 'absent')


def test_staff_web_absent_without_build_and_rejects_invalid_directory(settings, tmp_path, no_default_build, monkeypatch):
    with TestClient(create_app(settings)) as client:
        assert client.get('/staff/').status_code == 404
    monkeypatch.setenv('STAFF_WEB_DIST', str(tmp_path))
    with pytest.raises(RuntimeError, match='STAFF_WEB_DIST'):
        create_app(settings)


def test_staff_build_shares_origin_but_not_api_authorization(settings, tmp_path, no_default_build, monkeypatch):
    monkeypatch.setenv('STAFF_WEB_DIST', str(web_build(tmp_path)))
    with TestClient(create_app(settings)) as client:
        page = client.get('/staff/')
        assert page.status_code == 200 and '坐班工作台' in page.text
        assert page.headers['cache-control'] == 'no-store'
        policy = page.headers['content-security-policy']
        assert "connect-src 'self'" in policy and "script-src 'self';" in policy and 'unsafe-eval' not in policy
        assert "frame-ancestors 'none'" in policy
        assert page.headers['referrer-policy'] == 'no-referrer'
        assert page.headers['x-content-type-options'] == 'nosniff'
        assert page.headers['x-xueban-web'] == 'staff-v1-accounts'
        assert 'camera=()' in page.headers['permissions-policy']
        assert client.get('/staff', follow_redirects=False).headers['location'] == '/staff/'
        assert client.get('/staff/assets/app.js').status_code == 200
        assert client.get('/v1/me').status_code == 401
        assert 'content-security-policy' not in client.get('/v1/me').headers
        assert client.get('/staff/v1/me').status_code == 404
        for path in ['/staff/.env', '/staff/assets/app.js.map', '/staff/outside.txt', '/staff/%2e%2e/private.txt', '/staff/%2e%2e/dist-private.txt']:
            response = client.get(path)
            assert response.status_code == 404
            assert 'must not be served' not in response.text


def test_repository_build_is_default_when_present(settings, tmp_path, monkeypatch):
    monkeypatch.delenv('STAFF_WEB_DIST', raising=False)
    monkeypatch.setattr(staff_web, 'DEFAULT_DIST', web_build(tmp_path))
    with TestClient(create_app(settings)) as client:
        assert client.get('/staff/').status_code == 200


def test_staff_and_student_webs_are_separate(settings, tmp_path, no_default_build, monkeypatch):
    monkeypatch.setenv('STAFF_WEB_DIST', str(web_build(tmp_path, 'staff')))
    student = web_build(tmp_path, 'student', '学伴')
    with TestClient(create_app(replace(settings, student_web_dist=student))) as client:
        assert client.get('/').url.path == '/learn/'
        learn = client.get('/learn/')
        assert learn.headers['x-xueban-web'] == 'student-v2-accounts'
        assert '坐班工作台' not in learn.text
        staff = client.get('/staff/')
        assert staff.headers['x-xueban-web'] == 'staff-v1-accounts'
        assert '坐班工作台' in staff.text
