from dataclasses import replace

from fastapi.testclient import TestClient
import pytest

from xueban_backend.app import create_app
from scripts.mac_web import local_origin


def web_build(tmp_path):
    root = tmp_path / 'dist'
    root.mkdir()
    (root / 'assets').mkdir()
    (root / 'index.html').write_text('<!doctype html><title>学伴</title>')
    (root / 'assets/app.js').write_text('export const boot = true;')
    (root / '.env').write_text('must not be served')
    (root / 'assets/app.js.map').write_text('must not be served')
    private = tmp_path / 'private.txt'
    private.write_text('must not be served')
    (root / 'outside.txt').symlink_to(private)
    return root


def test_student_web_is_opt_in_and_requires_build(settings, tmp_path):
    with TestClient(create_app(settings)) as client:
        assert client.get('/learn/').status_code == 404
    with pytest.raises(RuntimeError, match='STUDENT_WEB_DIST'):
        create_app(replace(settings, student_web_dist=tmp_path))


def test_student_build_shares_origin_but_not_api_authorization(settings, tmp_path):
    with TestClient(create_app(replace(settings, student_web_dist=web_build(tmp_path)))) as client:
        page = client.get('/learn/')
        assert page.status_code == 200 and '<title>学伴</title>' in page.text
        assert page.headers['cache-control'] == 'no-store'
        assert "connect-src 'self'" in page.headers['content-security-policy']
        assert "script-src 'self'" in page.headers['content-security-policy']
        assert page.headers['referrer-policy'] == 'no-referrer'
        assert client.get('/').url.path == '/learn/'
        assert client.get('/learn/assets/app.js').status_code == 200
        assert client.get('/v1/me').status_code == 401
        assert client.get('/learn/v1/me').status_code == 404
        for path in ['/learn/.env', '/learn/assets/app.js.map', '/learn/outside.txt', '/learn/%2e%2e/private.txt']:
            response = client.get(path)
            assert response.status_code == 404
            assert 'must not be served' not in response.text


@pytest.mark.parametrize('origin', ['https://example.com', 'http://192.168.1.20:8000', 'http://127.0.0.1:8000/learn', 'http://user@localhost:8000', 'http://localhost:8000?token=test', 'http://localhost:8000#pair=test'])
def test_automatic_browser_pairing_refuses_nonlocal_or_embedded_credentials(origin):
    with pytest.raises(ValueError):
        local_origin(origin)


def test_automatic_browser_pairing_accepts_clean_loopback_origin():
    assert local_origin('http://127.0.0.1:8000/') == 'http://127.0.0.1:8000'


def test_web_launcher_opens_login_without_issuing_identity(monkeypatch, tmp_path):
    from scripts import mac_web

    (tmp_path / 'apps/student-web/node_modules').mkdir(parents=True)
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    config = {'host': '127.0.0.1', 'environment': {'PUBLIC_BASE_URL': 'http://127.0.0.1:8000'}}
    calls, opened = [], []
    monkeypatch.setattr(mac_web, 'REPO', tmp_path)
    monkeypatch.setattr(mac_web, 'load', lambda path: (config, {}))
    monkeypatch.setattr(mac_web, 'ready', lambda origin: True)
    monkeypatch.setattr(mac_web.subprocess, 'run', lambda args, **kwargs: calls.append(args))
    monkeypatch.setattr(mac_web.webbrowser, 'open', lambda url: opened.append(url) or True)
    monkeypatch.setattr(mac_web.sys, 'argv', ['mac_web.py', '--runtime', str(runtime)])
    mac_web.main()
    assert opened == ['http://127.0.0.1:8000/learn/']
    assert not list(runtime.iterdir())
    assert len(calls) == 2
    assert all('pair' not in command and 'admin' not in command for command in calls)
