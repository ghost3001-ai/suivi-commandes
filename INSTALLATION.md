# Installation et Démarrage

## Prérequis
- Python 3.12+
- pip

## Installation rapide

### 1. Créer un environnement virtuel
```bash
python3.12 -m venv venv
source venv/bin/activate  # Sur Linux/Mac
# ou
venv\Scripts\activate  # Sur Windows
```

### 2. Installer les dépendances
```bash
pip install -r requirements.txt
```

### 3. Initialiser la base de données
```bash
python init_app.py
```

Cela va:
- ✓ Créer les dossiers `instance/`, `uploads/`, `logs/`
- ✓ Initialiser la base de données SQLite
- ✓ Créer l'utilisateur admin par défaut

### 4. Lancer l'application

**En développement:**
```bash
python app.py
```
L'app sera accessible à: `http://localhost:5000`

**En production avec Gunicorn:**
```bash
python init_app.py
gunicorn wsgi:app --bind 0.0.0.0:5000
```

## Identification par défaut
- **Utilisateur:** admin
- **Mot de passe:** admin123

⚠️ En production, définissez `ADMIN_PASSWORD` avant le premier lancement.

## Variables d'environnement

Créer un fichier `.env` à la racine du projet:
```bash
cp .env.example .env
```

Puis éditer `.env` pour:
```
FLASK_ENV=production
SECRET_KEY=your-secret-key-change-this
ADMIN_USERNAME=admin
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=change-this-admin-password
DATABASE_URL=postgresql://user:password@localhost/achat_db
```

## Déploiement Render

Le projet contient déjà un fichier `render.yaml`.

1. Push du repo sur GitHub
2. Dans Render, créer un Blueprint depuis le dépôt
3. Saisir `ADMIN_PASSWORD` quand Render le demande
4. Laisser Render provisionner la base Postgres liée au service web

## Dépannage

**Erreur: "unable to open database file"**
- Le dossier `instance/` existe-t-il? Relancer `python init_app.py`
- Vérifier les permissions: `ls -la instance/`

**Erreur: ModuleNotFoundError**
- Vérifier que venv est activé: `which python` doit pointer vers venv
- Relancer: `pip install -r requirements.txt`

**Erreur: "SECRET_KEY must be set in production"**
- Créer `.env` et ajouter une `SECRET_KEY`
- Ou définir: `export SECRET_KEY="your-key"` avant de lancer l'app
