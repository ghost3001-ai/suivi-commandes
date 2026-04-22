from __future__ import annotations

import importlib
import sys
from contextlib import suppress
from pathlib import Path

import pytest


RESETTABLE_MODULES = ('app', 'config', 'models')
PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_application_module(monkeypatch, tmp_path, initialize_db=True):
    database_path = tmp_path / 'pytest.sqlite'
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{database_path}')
    monkeypatch.setenv('APP_ENV', 'development')
    monkeypatch.setenv('SECRET_KEY', 'pytest-secret-key')
    monkeypatch.setenv('DASHBOARD_SCHEDULER_ENABLED', '0')
    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)

    for module_name in RESETTABLE_MODULES:
        sys.modules.pop(module_name, None)

    importlib.invalidate_caches()
    app_module = importlib.import_module('app')
    app_module.app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
    )
    app_module.database_bootstrap_uri = None
    app_module.dashboard_scheduler = None

    lock_handle = getattr(app_module, 'scheduler_lock_handle', None)
    if lock_handle is not None and not lock_handle.closed:
        lock_handle.close()
    app_module.scheduler_lock_handle = None

    if initialize_db:
        with app_module.app.app_context():
            app_module.init_db()

    return app_module


def unload_application_module(app_module):
    with suppress(Exception):
        app_module.db.session.remove()

    with suppress(Exception):
        app_module.db.engine.dispose()

    lock_handle = getattr(app_module, 'scheduler_lock_handle', None)
    if lock_handle is not None and not lock_handle.closed:
        with suppress(Exception):
            lock_handle.close()
    app_module.scheduler_lock_handle = None

    for module_name in RESETTABLE_MODULES:
        sys.modules.pop(module_name, None)


@pytest.fixture
def app_module(monkeypatch, tmp_path):
    module = load_application_module(monkeypatch, tmp_path, initialize_db=True)
    try:
        yield module
    finally:
        unload_application_module(module)


@pytest.fixture
def empty_app_module(monkeypatch, tmp_path):
    module = load_application_module(monkeypatch, tmp_path, initialize_db=False)
    try:
        yield module
    finally:
        unload_application_module(module)


@pytest.fixture
def client(app_module):
    return app_module.app.test_client()


@pytest.fixture
def admin_user_id(app_module):
    with app_module.app.app_context():
        admin_user = app_module.Utilisateur.query.filter_by(role='admin').first()
        assert admin_user is not None
        return admin_user.id


@pytest.fixture
def authenticated_client(client, admin_user_id):
    with client.session_transaction() as session_data:
        session_data['_user_id'] = str(admin_user_id)
        session_data['_fresh'] = True
    return client
