#!/usr/bin/env python3
"""Smoke test minimal de l'application Flask."""

import importlib
import os
import re
import sys
import tempfile


RESETTABLE_MODULES = ('app', 'config', 'models')


def extract_csrf_token(html):
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if not match:
        raise RuntimeError("Champ csrf_token introuvable dans le formulaire")
    return match.group(1)


def load_smoke_app():
    temp_dir = tempfile.TemporaryDirectory()
    database_path = os.path.join(temp_dir.name, 'smoke.sqlite')

    os.environ['DATABASE_URL'] = f'sqlite:///{database_path}'
    os.environ['APP_ENV'] = 'development'
    os.environ['SECRET_KEY'] = 'smoke-test-secret-key'
    os.environ['ADMIN_PASSWORD'] = 'admin123'
    os.environ['DASHBOARD_SCHEDULER_ENABLED'] = '0'

    for module_name in RESETTABLE_MODULES:
        sys.modules.pop(module_name, None)

    importlib.invalidate_caches()
    app_module = importlib.import_module('app')
    app_module.app.config.update(TESTING=True)
    app_module.database_bootstrap_uri = None
    app_module.dashboard_scheduler = None
    return temp_dir, app_module


def main():
    temp_dir, app_module = load_smoke_app()
    app = app_module.app

    with app.app_context():
        app_module.init_db()

    client = app.test_client()

    try:
        login_page = client.get('/login')
        if login_page.status_code != 200:
            raise RuntimeError(f"GET /login a échoué avec {login_page.status_code}")

        token = extract_csrf_token(login_page.get_data(as_text=True))
        login_response = client.post(
            '/login',
            data={
                'username': 'admin',
                'password': 'admin123',
                'csrf_token': token,
            },
            follow_redirects=False,
        )
        if login_response.status_code != 302:
            raise RuntimeError(f"POST /login a échoué avec {login_response.status_code}")

        routes = [
            '/dashboard',
            '/commandes',
            '/fournisseurs',
            '/performances',
            '/admin/utilisateurs',
            '/admin/profil',
            '/api/commandes',
            '/api/dashboard/kpi',
            '/api/performances/global',
            '/exporter/excel',
        ]

        failures = []
        for route in routes:
            response = client.get(route)
            if response.status_code != 200:
                failures.append((route, response.status_code))

        logout_get = client.get('/logout')
        if logout_get.status_code != 405:
            failures.append(('/logout [GET]', logout_get.status_code))

        if failures:
            for route, status_code in failures:
                print(f"FAIL {route}: {status_code}")
            return 1

        print("Smoke test OK")
        return 0
    finally:
        temp_dir.cleanup()


if __name__ == '__main__':
    sys.exit(main())
