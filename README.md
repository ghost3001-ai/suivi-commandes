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

# 3. Initialiser
python init_app.py

# 4. Lancer
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

### 📋 Audit
- ✅ Logs complets de toutes les actions
- ✅ IP source enregistrée
- ✅ Consultation des historiques

---

## 🛠️ Configuration

### Variables d'environnement (`.env`)
```bash
# Copier le fichier exemple
cp .env.example .env
```

Éditer `.env`:
```
FLASK_ENV=production
SECRET_KEY=changez-moi-absolument-en-production
DATABASE_URL=postgresql://user:pass@host/db  # Optionnel (PostgreSQL)
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USERNAME=votre@email.com
MAIL_PASSWORD=app-password
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
  -e FLASK_ENV=production \
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
python init_app.py
gunicorn wsgi:app --workers 4
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

---

## 📞 Dépannage

| Problème | Solution |
|----------|----------|
| "unable to open database file" | Relancer `python init_app.py` |
| ModuleNotFoundError | `pip install -r requirements.txt` |
| SECRET_KEY error | Créer `.env` avec SECRET_KEY |
| Port 5000 déjà utilisé | `python app.py --port 5001` |
| Import Excel échoue | Vérifier format .xlsx et colonnes |

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
