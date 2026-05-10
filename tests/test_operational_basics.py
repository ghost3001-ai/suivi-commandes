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
    app_module.app.config['DASHBOARD_PURCHASE_BUDGET'] = 50000
    app_module.app.config['DASHBOARD_BUDGET_WARNING_PCT'] = 85

    response = authenticated_client.get('/dashboard')
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'Tableau de bord analytique' in body
    assert 'Cockpit Data Analyst achats' in body
    assert 'Analyse des dépenses (Spend Analysis)' in body
    assert 'Prévisions & planification' in body
    assert 'Risques & anomalies' in body
    assert 'Optimisation du processus achats' in body
    assert 'Aide à la décision stratégique' in body
    assert 'Workflow achats piloté par la donnée' in body
    assert 'Budget achats sous tension' in body

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


def test_advanced_admin_is_available_only_to_admins(authenticated_client, app_module):
    seed_paginated_data(app_module)

    response = authenticated_client.get('/admin/system')
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'Admin avancé' in body
    assert 'Commandes' in body
    assert 'Produits' in body

    response = authenticated_client.get('/admin/system/produits')
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'Produit 000' in body
    assert 'Ajouter' in body

    spectator_client = build_role_client(app_module, 'spectateur', 'spectateur_admin_blocked')
    response = spectator_client.get('/admin/system', follow_redirects=False)
    assert response.status_code == 302
    assert '/dashboard' in response.headers['Location']


def test_reference_options_feed_business_forms(authenticated_client, app_module):
    response = authenticated_client.post('/admin/system/referentiels/ajouter', data={
        'groupe': 'commande_acheteur',
        'cle': '',
        'libelle': 'ACHETEUR LIBRE',
        'ordre': '10',
        'actif': 'on',
    }, follow_redirects=False)
    assert response.status_code == 302

    response = authenticated_client.post('/admin/system/referentiels/ajouter', data={
        'groupe': 'produit_famille',
        'cle': '',
        'libelle': 'Famille libre',
        'ordre': '10',
        'actif': 'on',
    }, follow_redirects=False)
    assert response.status_code == 302

    response = authenticated_client.post('/admin/system/referentiels/ajouter', data={
        'groupe': 'produit_categorie',
        'cle': '',
        'libelle': 'Catégorie libre',
        'parent_cle': 'Famille libre',
        'ordre': '10',
        'actif': 'on',
    }, follow_redirects=False)
    assert response.status_code == 302

    response = authenticated_client.post('/admin/system/referentiels/ajouter', data={
        'groupe': 'produit_sous_famille',
        'cle': '',
        'libelle': 'Sous-famille libre',
        'parent_cle': 'Catégorie libre',
        'ordre': '10',
        'actif': 'on',
    }, follow_redirects=False)
    assert response.status_code == 302

    response = authenticated_client.get('/commande/ajouter')
    assert response.status_code == 200
    assert 'ACHETEUR LIBRE' in response.get_data(as_text=True)

    response = authenticated_client.get('/stock/produit/ajouter')
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'Famille libre' in body
    assert 'Catégorie libre' in body
    assert 'Sous-famille libre' in body


def test_role_restrictions_for_achats_stock_manager_and_engineer(app_module):
    seed_paginated_data(app_module)

    with app_module.app.app_context():
        commande = app_module.Commande.query.order_by(app_module.Commande.id.desc()).first()
        assert commande is not None
        commande_id = commande.id

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
    response = stock_client.get(f'/commande/modifier/{commande_id}')
    assert response.status_code == 200
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

    comptable_client = build_role_client(app_module, 'service_comptable', 'compta_user')
    response = comptable_client.get('/commandes')
    assert response.status_code == 200
    response = comptable_client.get('/commande/ajouter', follow_redirects=False)
    assert response.status_code == 302
    assert '/commandes' in response.headers['Location']
    response = comptable_client.get(f'/commande/modifier/{commande_id}')
    assert response.status_code == 200
    response = comptable_client.get('/stocks', follow_redirects=False)
    assert response.status_code == 302
    assert '/commandes' in response.headers['Location']

    marketing_client = build_role_client(app_module, 'service_marketing', 'marketing_user')
    response = marketing_client.get('/commandes')
    assert response.status_code == 200
    response = marketing_client.get(f'/commande/modifier/{commande_id}', follow_redirects=False)
    assert response.status_code == 302
    assert '/commandes' in response.headers['Location']
    response = marketing_client.get('/dashboard', follow_redirects=False)
    assert response.status_code == 302
    assert '/commandes' in response.headers['Location']


def test_command_payment_and_reception_flow_updates_statuses(app_module):
    with app_module.app.app_context():
        db = app_module.db
        fournisseur = app_module.Fournisseur.query.first()
        if fournisseur is None:
            fournisseur = app_module.Fournisseur(
                nom='Workflow fournisseur',
                pays='Cameroun',
                categorie='Test',
            )
            db.session.add(fournisseur)
            db.session.flush()

        commande = app_module.Commande(
            nr=999,
            date_cde=date.today(),
            entite='AFRILUX',
            demandeur='Demandeur workflow',
            service_demandeur='Marketing',
            acheteur='GILLES',
            fournisseur_id=fournisseur.id,
            affaire='Commande workflow',
            bon_commande='BC-WORKFLOW-001',
            date_livraison=date.today(),
            montant=1000,
            avance=200,
        )
        commande.calculer_solde()
        db.session.add(commande)
        db.session.commit()
        commande_id = commande.id

    comptable_client = build_role_client(app_module, 'service_comptable', 'workflow_compta')
    response = comptable_client.post(
        f'/commande/modifier/{commande_id}',
        data={
            'avance': '1000',
            'date_paiement': date.today().isoformat(),
            'facture': 'FAC-001',
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app_module.app.app_context():
        commande = app_module.db.session.get(app_module.Commande, commande_id)
        assert commande is not None
        assert commande.statut == app_module.Commande.STATUT_PAYE
        assert commande.date_paiement == date.today()
        assert commande.facture == 'FAC-001'
        assert not commande.est_achevee()

    stock_client = build_role_client(app_module, 'gestionnaire_stock', 'workflow_stock')
    response = stock_client.post(
        f'/commande/modifier/{commande_id}',
        data={
            'date_reception': date.today().isoformat(),
            'bon_livraison': 'BL-001',
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app_module.app.app_context():
        commande = app_module.db.session.get(app_module.Commande, commande_id)
        assert commande is not None
        assert commande.date_reception == date.today()
        assert commande.bon_livraison == 'BL-001'
        assert commande.est_achevee()
        assert commande.get_statut_avancement() == app_module.Commande.PHASE_ACHEVEE


def test_partial_purchase_reception_updates_stock_and_backorder(app_module):
    with app_module.app.app_context():
        db = app_module.db
        fournisseur = app_module.Fournisseur(nom='Fournisseur pneus', pays='Cameroun', categorie='Test')
        produit = app_module.Produit(
            nom='Pneu atelier',
            code='PNEU-001',
            stock_actuel=10,
            stock_minimum=1,
            prix_unitaire=20,
            actif=True,
        )
        db.session.add_all([fournisseur, produit])
        db.session.flush()

        commande = app_module.Commande(
            nr=1200,
            date_cde=date.today(),
            entite='AFRILUX',
            demandeur='Magasin',
            service_demandeur='Magasin',
            acheteur='GILLES',
            fournisseur_id=fournisseur.id,
            affaire='Commande pneus',
            bon_commande='BC-PART-001',
            date_livraison=date.today(),
            montant=2000,
            avance=2000,
            date_paiement=date.today(),
            facture='FAC-PART-001',
        )
        commande.calculer_solde()
        db.session.add(commande)
        db.session.flush()
        ligne = app_module.CommandeProduit(
            commande=commande,
            produit=produit,
            quantite=100,
            prix_unitaire=20,
        )
        ligne.calculer_montant()
        db.session.add(ligne)
        db.session.commit()
        commande_id = commande.id
        ligne_id = ligne.id
        produit_id = produit.id

    stock_client = build_role_client(app_module, 'gestionnaire_stock', 'partial_stock')
    response = stock_client.post(
        f'/commande/modifier/{commande_id}',
        data={
            'date_reception': date.today().isoformat(),
            'bon_livraison': 'BL-PART-001',
            'reception_ligne_id[]': [str(ligne_id)],
            'reception_quantite_recue[]': ['60'],
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app_module.app.app_context():
        commande = app_module.db.session.get(app_module.Commande, commande_id)
        produit = app_module.db.session.get(app_module.Produit, produit_id)
        ligne = app_module.db.session.get(app_module.CommandeProduit, ligne_id)
        assert produit.stock_actuel == 70
        assert ligne.quantite_recue == 60
        assert ligne.quantite_arriere == 40
        assert commande.est_reception_partielle()
        assert not commande.est_achevee()
        assert commande.get_statut_avancement() == app_module.Commande.PHASE_RECEPTION_PARTIELLE

    response = stock_client.post(
        f'/commande/modifier/{commande_id}',
        data={
            'date_reception': date.today().isoformat(),
            'bon_livraison': 'BL-PART-002',
            'reception_ligne_id[]': [str(ligne_id)],
            'reception_quantite_recue[]': ['100'],
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app_module.app.app_context():
        commande = app_module.db.session.get(app_module.Commande, commande_id)
        produit = app_module.db.session.get(app_module.Produit, produit_id)
        ligne = app_module.db.session.get(app_module.CommandeProduit, ligne_id)
        assert produit.stock_actuel == 110
        assert ligne.quantite_recue == 100
        assert ligne.quantite_arriere == 0
        assert commande.est_achevee()
        assert app_module.MouvementStock.query.count() == 2


def test_supplier_performance_filters_and_exports(authenticated_client, app_module):
    seed_paginated_data(app_module)

    response = authenticated_client.get('/performances/fournisseurs?period=month&include_inactive=1')
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'Filtres fournisseurs' in body
    assert 'Vue active' in body
    assert 'Classement Data Analyst' in body

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
    body = response.get_data(as_text=True)
    assert 'Filtres période' in body
    assert 'Score valeur' in body

    response = authenticated_client.get(f'/performances/fournisseur/{fournisseur_id}/export/excel?period=month')
    assert response.status_code == 200
    assert response.mimetype == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

    response = authenticated_client.get(f'/performances/fournisseur/{fournisseur_id}/export/pdf?period=month')
    assert response.status_code == 200
    assert response.mimetype == 'application/pdf'

def test_supplier_performance_page_highlights_best_value_supplier(authenticated_client, app_module):
    with app_module.app.app_context():
        db = app_module.db
        supplier_a = app_module.Fournisseur(nom='Fournisseur A', pays='Cameroun', categorie='Test')
        supplier_b = app_module.Fournisseur(nom='Fournisseur B', pays='France', categorie='Test')
        db.session.add_all([supplier_a, supplier_b])
        db.session.flush()

        good_order = app_module.Commande(
            nr=7001,
            date_cde=date.today(),
            entite='AFRILUX',
            demandeur='Analyste',
            acheteur='GILLES',
            fournisseur_id=supplier_a.id,
            affaire='Lot performant',
            bon_commande='BC-ANA-001',
            date_livraison=date.today(),
            date_reception=date.today(),
            montant=900,
            avance=900,
            solde=0,
            statut=app_module.Commande.STATUT_PAYE,
            prix_reference_marche=1000,
            commande_conforme=True,
            rupture_fournisseur=False,
            note_fournisseur=4.8,
            note_service=4.6,
            date_paiement=date.today(),
        )
        risky_order = app_module.Commande(
            nr=7002,
            date_cde=date.today(),
            entite='SMART',
            demandeur='Analyste',
            acheteur='ALAIN',
            fournisseur_id=supplier_b.id,
            affaire='Lot à négocier',
            bon_commande='BC-ANA-002',
            date_livraison=date.today() - timedelta(days=10),
            montant=1500,
            avance=200,
            solde=1300,
            statut=app_module.Commande.STATUT_A_PAYER,
            prix_reference_marche=1000,
            commande_conforme=False,
            rupture_fournisseur=True,
            note_fournisseur=2.5,
            note_service=2.8,
        )
        db.session.add_all([good_order, risky_order])
        db.session.commit()

    response = authenticated_client.get('/performances/fournisseurs')
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'Fournisseur A = meilleur rapport qualité/prix' in body
    assert 'Fournisseur B à +50.0% vs marché' in body


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


def test_product_form_exposes_stock_management_fields(authenticated_client):
    response = authenticated_client.get('/stock/produit/ajouter')
    assert response.status_code == 200

    body = unescape(response.get_data(as_text=True))
    assert 'Type de stock' in body
    assert 'Méthode de réapprovisionnement' in body
    assert 'Méthode de valorisation' in body
    assert 'Stock de sécurité' in body
    assert 'Consommation moyenne / jour' in body
    assert 'Taux de possession annuel' in body


def test_product_model_computes_replenishment_metrics(app_module):
    produit = app_module.Produit(
        nom='Pompe de circulation',
        type_stock=app_module.Produit.TYPE_MRO,
        methode_reappro=app_module.Produit.REAPPRO_POINT_COMMANDE,
        methode_valorisation=app_module.Produit.VALORISATION_CUMP,
        prix_unitaire=100,
        stock_actuel=20,
        stock_minimum=5,
        stock_securite=10,
        delai_approvisionnement_jours=4,
        consommation_moyenne_journaliere=3,
        cout_passation_commande=5000,
        taux_possession_annuel=25,
        actif=True,
    )

    assert produit.get_type_stock_label() == 'Maintenance / MRO'
    assert produit.get_methode_reappro_label() == 'Point de commande'
    assert produit.get_methode_valorisation_label() == 'CUMP'
    assert round(produit.point_commande, 2) == 22
    assert round(produit.couverture_stock_jours, 2) == round(20 / 3, 2)
    assert produit.quantite_economique_commande is not None
    assert produit.doit_etre_reapprovisionne()
    assert produit.get_quantite_reappro_recommandee() is not None
    assert produit.get_etat_stock() == 'A_REAPPROVISIONNER'


def test_catalog_uses_repo_root_workbook_by_default(app_module):
    catalog_path = app_module.app.config['CATEGORY_CATALOG_FILE']
    expected_path = os.path.join(app_module.app.root_path, 'Projet Suivi Commande ASS.xlsx')

    assert catalog_path == expected_path
    assert os.path.basename(catalog_path) == 'Projet Suivi Commande ASS.xlsx'


def test_supplier_reference_bootstrap_uses_source_workbook(app_module, monkeypatch, tmp_path):
    workbook_path = tmp_path / 'fournisseurs.xlsx'
    workbook_path.write_bytes(b'test')

    app_module.app.config['SUPPLIER_SOURCE_WORKBOOK_FILE'] = str(workbook_path)
    app_module.app.config['TESTING'] = False
    app_module.synchronized_supplier_reference_key = None

    import import_fournisseurs_workbook as supplier_importer
    calls = []

    def fake_import_suppliers(path, replace_existing=False):
        calls.append((path, replace_existing))
        assert path == str(workbook_path)
        assert replace_existing is False
        fournisseur = app_module.Fournisseur(nom='Fournisseur bootstrap')
        app_module.db.session.add(fournisseur)
        app_module.db.session.commit()
        return ([{'nom': 'Fournisseur bootstrap'}], 1, 0)

    monkeypatch.setattr(supplier_importer, 'import_suppliers', fake_import_suppliers)

    with app_module.app.app_context():
        app_module.db.session.add(app_module.Fournisseur(nom='Fournisseur déjà présent'))
        app_module.db.session.commit()

        imported = app_module.ensure_supplier_reference_data()
        imported_again = app_module.ensure_supplier_reference_data()

        assert imported == 1
        assert imported_again == 0
        assert len(calls) == 1
        assert app_module.Fournisseur.query.count() == 2


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
