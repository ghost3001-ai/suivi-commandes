#!/usr/bin/env python3
"""Smoke test minimal de l'application Flask."""

import re
import sys

from app import app, init_db


def extract_csrf_token(html):
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if not match:
        raise RuntimeError("Champ csrf_token introuvable dans le formulaire")
    return match.group(1)


def main():
    with app.app_context():
        init_db()

    client = app.test_client()

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


if __name__ == '__main__':
    sys.exit(main())
