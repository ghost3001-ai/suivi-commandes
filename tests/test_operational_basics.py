import os
from html import unescape

from datetime import date, timedelta


def seed_paginated_data(app_module):
    with app_module.app.app_context():
        db = app_module.db
        admin_user = app_module.Utilisateur.query.filter_by(role='admin').first()
        fournisseur = app_module.Fournisseur.query.first()
        if fournisseur is None:
            fournisseur = app_module.Fournisseur(
                nom='Fournisseur test',
                pays='Cameroun',
                categorie='Test',
            )
            db.session.add(fournisseur)
            db.session.flush()

        for index in range(30):
            db.session.add(
                app_module.Produit(
                    nom=f'Produit {index:03d}',
                    code=f'P-{index:03d}',
                    famille='Famille test',
                    categorie='Categorie test',
                    prix_unitaire=100 + index,
                    stock_actuel=50 + index,
                    stock_minimum=10,
                    actif=True,
                )
            )

        for index in range(30):
            vente = app_module.Vente(
                reference=f'VE-{index:03d}',
                client_nom=f'Client {index:03d}',
                date_vente=date.today() - timedelta(days=index),
                montant_total=1000 + index,
                montant_paye=500,
                solde=500 + index,
                created_by=admin_user.id,
            )
            db.session.add(vente)

        for index in range(30):
            db.session.add(
                app_module.Commande(
                    nr=index + 1,
                    date_cde=date.today() - timedelta(days=index),
                    entite='AFRILUX',
                    demandeur=f'Demandeur {index:03d}',
                    acheteur='Acheteur test',
                    fournisseur_id=fournisseur.id if fournisseur else None,
                    affaire=f'Affaire {index:03d}',
                    bon_commande=f'BC-{index:03d}',
                    montant=2500 + index,
                    avance=500,
                    solde=2000 + index,
                    statut=app_module.Commande.STATUT_A_PAYER,
                )
            )

        for index in range(60):
            db.session.add(
                app_module.LogAction(
                    utilisateur_id=admin_user.id,
                    action='TEST',
                    table='tests',
                    record_id=index + 1,
                    details=f'Log de test {index:03d}',
                    ip_address='127.0.0.1',
                )
            )

        db.session.commit()


def build_role_client(app_module, role, username):
    client = app_module.app.test_client()
    with app_module.app.app_context():
        utilisateur = app_module.Utilisateur(
            username=username,
            email=f'{username}@example.com',
            role=role,
            actif=True,
        )
        utilisateur.set_password('secret123')
        app_module.db.session.add(utilisateur)
        app_module.db.session.commit()
        user_id = utilisateur.id

    with client.session_transaction() as session_data:
        session_data['_user_id'] = str(user_id)
        session_data['_fresh'] = True

    return client


def test_health_and_readiness_endpoints(client):
    health_response = client.get('/healthz')
    assert health_response.status_code == 200
    assert health_response.get_json()['status'] == 'ok'

    ready_response = client.get('/readyz')
    assert ready_response.status_code == 200
    assert ready_response.get_json()['status'] == 'ready'


def test_stale_session_is_cleared_when_database_bootstraps(empty_app_module):
    client = empty_app_module.app.test_client()
    with client.session_transaction() as session_data:
        session_data['_user_id'] = '1'
        session_data['_fresh'] = True

    response = client.get('/login', follow_redirects=False)
    assert response.status_code == 200

    with client.session_transaction() as session_data:
        assert '_user_id' not in session_data
        assert '_fresh' not in session_data


def test_paginated_views_are_accessible_for_authenticated_admin(authenticated_client, app_module):
    seed_paginated_data(app_module)

    response = authenticated_client.get('/dashboard')
    assert response.status_code == 200
    assert 'Tableau de bord analytique' in response.get_data(as_text=True)

    response = authenticated_client.get('/stocks?page=2')
    assert response.status_code == 200
    assert 'Précédent' in response.get_data(as_text=True)
    assert response.headers['X-Content-Type-Options'] == 'nosniff'

    response = authenticated_client.get('/commandes?page=2')
    assert response.status_code == 200
    assert 'Précédent' in response.get_data(as_text=True)

    response = authenticated_client.get('/ventes?page=2')
    assert response.status_code == 200
    assert 'Précédent' in response.get_data(as_text=True)

    response = authenticated_client.get('/admin/logs?page=2')
    assert response.status_code == 200
    assert 'Précédent' in response.get_data(as_text=True)


def test_role_restrictions_for_achats_stock_manager_and_engineer(app_module):
    seed_paginated_data(app_module)

    achats_client = build_role_client(app_module, 'achats', 'achats_user')
    response = achats_client.get('/commandes')
    assert response.status_code == 200
    response = achats_client.get('/dashboard', follow_redirects=False)
    assert response.status_code == 302
    assert '/commandes' in response.headers['Location']
    response = achats_client.get('/stocks', follow_redirects=False)
    assert response.status_code == 302
    assert '/commandes' in response.headers['Location']

    stock_client = build_role_client(app_module, 'gestionnaire_stock', 'stock_user')
    response = stock_client.get('/stocks')
    assert response.status_code == 200
    response = stock_client.get('/commandes')
    assert response.status_code == 200
    response = stock_client.get('/commande/ajouter', follow_redirects=False)
    assert response.status_code == 302
    assert '/commandes' in response.headers['Location']
    response = stock_client.get('/stock/produit/ajouter')
    assert response.status_code == 200

    ingenieur_client = build_role_client(app_module, 'ingenieur', 'ingenieur_user')
    response = ingenieur_client.get('/performances')
    assert response.status_code == 200
    response = ingenieur_client.get('/commandes')
    assert response.status_code == 200
    response = ingenieur_client.get('/stocks')
    assert response.status_code == 200
    response = ingenieur_client.get('/commande/ajouter', follow_redirects=False)
    assert response.status_code == 302
    assert '/commandes' in response.headers['Location']
    response = ingenieur_client.get('/ventes', follow_redirects=False)
    assert response.status_code == 302
    assert '/commandes' in response.headers['Location']


def test_supplier_performance_filters_and_exports(authenticated_client, app_module):
    seed_paginated_data(app_module)

    response = authenticated_client.get('/performances/fournisseurs?period=month&include_inactive=1')
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'Filtres fournisseurs' in body
    assert 'Vue active' in body

    response = authenticated_client.get('/performances/fournisseurs/export/excel?period=month&include_inactive=1')
    assert response.status_code == 200
    assert response.mimetype == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

    response = authenticated_client.get('/performances/fournisseurs/export/pdf?period=month&include_inactive=1')
    assert response.status_code == 200
    assert response.mimetype == 'application/pdf'

    with app_module.app.app_context():
        fournisseur = app_module.Fournisseur.query.first()
        assert fournisseur is not None
        fournisseur_id = fournisseur.id

    response = authenticated_client.get(f'/performances/fournisseur/{fournisseur_id}?period=month')
    assert response.status_code == 200
    assert 'Filtres période' in response.get_data(as_text=True)

    response = authenticated_client.get(f'/performances/fournisseur/{fournisseur_id}/export/excel?period=month')
    assert response.status_code == 200
    assert response.mimetype == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

    response = authenticated_client.get(f'/performances/fournisseur/{fournisseur_id}/export/pdf?period=month')
    assert response.status_code == 200
    assert response.mimetype == 'application/pdf'


def test_product_performance_page_exposes_catalog_categories(authenticated_client, app_module):
    catalog = app_module.get_product_category_catalog()
    expected_total = sum(
        len(catalog['categories_by_family'].get(famille, []))
        for famille in catalog.get('families', [])
    )
    assert expected_total > 0

    expected_family = catalog['families'][0]
    expected_category = catalog['categories_by_family'][expected_family][0]

    response = authenticated_client.get('/performances/produits')
    assert response.status_code == 200

    body = unescape(response.get_data(as_text=True))
    assert 'Catégories du référentiel' in body
    assert f'{expected_total} catégories' in body
    assert expected_family in body
    assert expected_category in body


def test_product_form_preloads_catalog_categories(authenticated_client, app_module):
    catalog = app_module.get_product_category_catalog()
    expected_total = sum(
        len(catalog['categories_by_family'].get(famille, []))
        for famille in catalog.get('families', [])
    )
    expected_category = catalog['categories_by_family'][catalog['families'][0]][0]

    response = authenticated_client.get('/stock/produit/ajouter')
    assert response.status_code == 200

    body = unescape(response.get_data(as_text=True))
    assert 'Les 49 catégories sont préchargées.' in body
    assert expected_category in body
    assert body.count('<option value="') >= expected_total


def test_catalog_uses_repo_root_workbook_by_default(app_module):
    catalog_path = app_module.app.config['CATEGORY_CATALOG_FILE']
    expected_path = os.path.join(app_module.app.root_path, 'Projet Suivi Commande ASS.xlsx')

    assert catalog_path == expected_path
    assert os.path.basename(catalog_path) == 'Projet Suivi Commande ASS.xlsx'


def test_manual_stock_movement_supports_multiple_products(authenticated_client, app_module):
    with app_module.app.app_context():
        produit_a = app_module.Produit(
            nom='Produit A',
            code='MP-A',
            famille='Famille test',
            categorie='Categorie test',
            stock_actuel=10,
            stock_minimum=1,
            actif=True,
        )
        produit_b = app_module.Produit(
            nom='Produit B',
            code='MP-B',
            famille='Famille test',
            categorie='Categorie test',
            stock_actuel=4,
            stock_minimum=1,
            actif=True,
        )
        app_module.db.session.add_all([produit_a, produit_b])
        app_module.db.session.commit()
        produit_a_id = produit_a.id
        produit_b_id = produit_b.id

    response = authenticated_client.post('/stock/mouvement/ajouter', data={
        'type_mouvement': 'ENTREE',
        'motif': 'Réception lot',
        'produit_id[]': [str(produit_a_id), str(produit_b_id)],
        'quantite[]': ['5', '3'],
    }, follow_redirects=True)
    assert response.status_code == 200
    assert '2 mouvement(s) de stock enregistré(s)' in response.get_data(as_text=True)

    with app_module.app.app_context():
        produit_a = app_module.Produit.query.get(produit_a_id)
        produit_b = app_module.Produit.query.get(produit_b_id)
        assert produit_a.stock_actuel == 15
        assert produit_b.stock_actuel == 7
        assert app_module.MouvementStock.query.count() == 2
