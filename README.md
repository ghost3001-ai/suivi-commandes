# README - Suivi Commandes AFRILUX SMART

Application Flask de suivi et gestion des commandes avec authentification, auditing et analytics.

## 🚀 Démarrage Rapide

### Option 1: Script automatisé (Recommandé)
```bash
./run.sh
```

### Option 2: Manuel
```bash
# 1. Créer l'env
python3.12 -m venv venv
source venv/bin/activate

# 2. Installer
pip install -r requirements.txt

# 3. Outils qualité / migrations (recommandé)
pip install -r requirements-dev.txt

# 4. Initialiser
python init_app.py

# 5. Lancer
python app.py
```

**L'app est accessible à:** `http://localhost:5000`
**Credentials:** `admin` / `admin123`

---

## 📋 Fonctionnalités

### 🔐 Sécurité
- ✅ Authentification avec Flask-Login
- ✅ Protection CSRF sur tous les formulaires
- ✅ Validation des données côté backend
- ✅ Hachage sécurisé des mots de passe
- ✅ Logging d'audit complet

### 📊 Gestion Commandes
- ✅ CRUD complet (Ajouter/Modifier/Afficher/Supprimer)
- ✅ Filtrage avancé (entité, statut, acheteur, fournisseur)
- ✅ Import/Export Excel
- ✅ Tracking des statuts (À PAYER, PAYÉ)
- ✅ Calcul automatique du solde

### 🏢 Gestion Fournisseurs
- ✅ Annuaire complet
- ✅ Contact et détails juridiques
- ✅ Historique des commandes

### 👥 Gestion Utilisateurs
- ✅ Contrôle d'accès par rôle (admin/spectateur)
- ✅ Création d'utilisateurs
- ✅ Gestion des permissions
- ✅ Profil personnel

### 📈 Analytics & Performances
- ✅ Dashboard KPI
- ✅ Performances par acheteur
- ✅ Performances par fournisseur
- ✅ Évolution par produit/affaire
- ✅ Taux de retard / Délais moyens
- ✅ Historique des dépenses achats
- ✅ Alertes de dépassement budget
- ✅ Recommandations Data Analyst par étape du cycle achats
- ✅ Scoring qualité/prix/délais des fournisseurs
- ✅ Détection d'anomalies prix, livraison et facturation

### 📋 Audit
- ✅ Logs complets de toutes les actions
- ✅ IP source enregistrée
- ✅ Consultation des historiques

---

## 🧠 Vision Data Analyst

L'application ne sert pas seulement à saisir des commandes. Elle joue le rôle de cockpit achats orienté décision :

- centraliser les données d'achats
- analyser automatiquement les dépenses et les risques
- recommander des actions concrètes au service achats, à la logistique et à la comptabilité

En pratique, le dashboard agit comme un "cerveau" du service achats.

### Cycle achats piloté par la donnée

| Étape | Acteur principal | Ce que fait le Data Analyst |
|-------|------------------|-----------------------------|
| Analyse des dépenses | Direction / Achats | suit l'historique, compare budget vs réel, déclenche les alertes de dépassement |
| Recherche fournisseur | Service achats | classe automatiquement les fournisseurs selon prix, qualité, délais |
| Négociation & décision | Acheteur / Responsable | met en avant les écarts vs marché et les marges de renégociation |
| Création commande | Service achats | signale les anomalies de cohérence sur les montants et références |
| Livraison | Logistique / Réception | mesure retards, conformité et ruptures fournisseur |
| Réception & validation | Magasin / Service demandeur | suit non-conformités et incidents réception |
| Facturation & paiement | Comptabilité | détecte doublons facture et incohérences de paiement |
| Analyse post-achat | Data Analyst | produit KPI, rapports et recommandations |
| Amélioration continue | Direction + Data Analyst | boucle d'optimisation sur coûts, fournisseurs et processus |

### Modules Data Analyst visibles dans l'application

- `Dashboard intelligent` : total achats, alertes, recommandations prioritaires, historique des dépenses
- `Spend Analysis` : lecture des dépenses par période, produit, entité et fournisseur
- `Scoring fournisseurs` : meilleur rapport qualité/prix, fournisseur à négocier, fournisseur en retard
- `Détection d'anomalies` : dérive prix vs marché, doublons de facture, paiements incohérents
- `Workflow achats` : lecture synthétique du flux `besoin -> fournisseur -> négociation -> commande -> livraison -> paiement -> analyse`

---

## 🛠️ Configuration

### Variables d'environnement (`.env`)
```bash
# Copier le fichier exemple
cp .env.example .env
```

Éditer `.env`:
```
APP_ENV=production
SECRET_KEY=changez-moi-absolument-en-production
DATABASE_URL=postgresql://user:pass@host/db  # Optionnel (PostgreSQL)
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=votre@email.com
MAIL_PASSWORD=app-password
DASHBOARD_PURCHASE_BUDGET=1000000
DASHBOARD_BUDGET_WARNING_PCT=85
DASHBOARD_NEGOTIATION_ALERT_THRESHOLD=10
DASHBOARD_SUPPLIER_DEPENDENCY_THRESHOLD=35
```

---

## 📦 Structure du Projet

```
.
├── app.py                  # Autorisations et routes
├── models.py               # Modèles SQLAlchemy
├── config.py               # Configuration
├── wsgi.py                 # Entry point production
├── init_app.py             # Script d'initialisation
├── requirements.txt        # Dépendances Python
├── Dockerfile              # Containerisation
├── Procfile                # Heroku/Render config
├── templates/              # Templates Jinja2
│   ├── base.html           # Mise en page de base
│   ├── login.html          # Connexion
│   ├── dashboard.html      # Tableau de bord
│   ├── commandes.html      # Liste commandes
│   ├── commande_detail.html
│   ├── admin/              # Pages admin
│   │   ├── commande_form.html
│   │   ├── fournisseur_form.html
│   │   ├── utilisateur_form.html
│   │   ├── utilisateurs.html
│   │   ├── profil.html
│   │   └── logs.html
│   └── performances/
│       ├── acheteurs.html
│       ├── fournisseurs.html
│       └── produits.html
├── static/                 # CSS, JS
│   ├── css/style.css
│   └── js/scripts.js
├── instance/               # Base de données SQLite (développement)
└── uploads/                # Fichiers uploadés
```

---

## 🚢 Déploiement Production

### Render
1. Push du projet vers GitHub
2. Créer un Blueprint Render depuis le repo
3. Render lit automatiquement `render.yaml`
4. Renseigner `ADMIN_PASSWORD` au premier déploiement
5. Laisser Render créer la base Postgres et injecter `DATABASE_URL`

### Docker
```bash
docker build -t achat:latest .
docker run -p 5000:5000 \
  -e SECRET_KEY=your-key \
  -e APP_ENV=production \
  -e ADMIN_PASSWORD=change-me \
  -e DATABASE_URL=postgresql://user:pass@host:5432/dbname \
  achat:latest
```

### Manuellement
```bash
# Installer Python 3.12
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
alembic upgrade head
python init_app.py
gunicorn wsgi:app --workers 4
```

### Migrations de schéma
```bash
# Base neuve
alembic upgrade head

# Base existante déjà créée hors Alembic
alembic stamp head

# Nouveau changement de schéma
alembic revision --autogenerate -m "ajout champs ventes"
alembic upgrade head
```

### Tests
```bash
pip install -r requirements-dev.txt
pytest
python smoke_test.py
```

---

## 🔄 Mise à Jour

```bash
git pull
source venv/bin/activate
pip install -r requirements.txt --upgrade
python init_app.py
```

---

## ⚠️ Important pour la Production

1. **Définir `ADMIN_PASSWORD` avant le premier déploiement**
   - En production, aucun mot de passe admin par défaut ne doit être utilisé
   - Le premier compte admin est créé à partir de `ADMIN_USERNAME`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`

2. **Générer une SECRET_KEY sécurisée**
   ```python
   python -c "import secrets; print(secrets.token_hex(32))"
   ```

3. **Utiliser PostgreSQL** au lieu de SQLite
   - Render perd le filesystem local à chaque redéploiement
   - SQLite ne convient donc pas en production hébergée

4. **HTTPS obligatoire**

5. **Sauvegarder régulièrement la base de données**

6. **Superviser les endpoints**
   - `GET /healthz`
   - `GET /readyz`

---

## 📞 Dépannage

| Problème | Solution |
|----------|----------|
| "unable to open database file" | Relancer `python init_app.py` |
| ModuleNotFoundError | `pip install -r requirements.txt` |
| SECRET_KEY error | Créer `.env` avec SECRET_KEY |
| Port 5000 déjà utilisé | `python app.py --port 5001` |
| Import Excel échoue | Vérifier format .xlsx et colonnes |
| Alembic échoue sur une base existante | `alembic stamp head` |

---

## 📘 Exploitation

Checklist et runbook détaillés :

- [PRODUCTION_CHECKLIST.md](/home/ghost/Afrilux_Smart/Achat/docs/PRODUCTION_CHECKLIST.md)

---

## 📚 Documentation API

### REST Endpoints
- `GET /api/commandes` - Liste toutes les commandes
- `GET /api/commandes/statistiques` - Stats globales
- `GET /api/dashboard/kpi` - KPI du dashboard
- `GET /api/performances/global` - Performances globales

---

## 📝 Licence

Propriétaire AFRILUX SMART

---

**Dernière mise à jour:** 1 avril 2026
