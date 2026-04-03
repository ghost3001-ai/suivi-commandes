# Production Checklist

Guide d’exploitation pour rendre le projet répétable en environnement entreprise.

## 1. Préparer l’environnement

Variables minimales à définir en production :

```bash
APP_ENV=production
SECRET_KEY=<secret-long-et-aleatoire>
DATABASE_URL=postgresql://user:password@host:5432/dbname
ADMIN_USERNAME=admin
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=<mot-de-passe-fort>
```

Variables complémentaires utiles :

```bash
MAIL_SERVER=smtp.example.com
MAIL_PORT=587
MAIL_USERNAME=alerts@example.com
MAIL_PASSWORD=<mail-password>
DASHBOARD_SCHEDULER_ENABLED=true
LOG_LEVEL=INFO
DEFAULT_PAGE_SIZE=25
LOG_PAGE_SIZE=50
```

## 2. Installer les dépendances

Runtime :

```bash
pip install -r requirements.txt
```

Outillage qualité et migrations :

```bash
pip install -r requirements-dev.txt
```

## 3. Base de données et migrations

### Nouvelle base vide

```bash
alembic upgrade head
python init_app.py
```

### Base existante déjà créée par `init_db()` / `db.create_all()`

La migration initiale Alembic est une baseline du schéma courant. Sur une base déjà existante, il faut d’abord la marquer comme alignée :

```bash
alembic stamp head
```

Ensuite, pour les prochains changements de schéma :

```bash
alembic revision --autogenerate -m "description du changement"
alembic upgrade head
```

## 4. Vérifications avant déploiement

```bash
pytest
python smoke_test.py
python -m compileall app.py config.py models.py init_app.py
```

Contrôles attendus :

- `pytest` doit passer
- `smoke_test.py` doit passer
- `/healthz` doit répondre `200`
- `/readyz` doit répondre `200`
- le compte admin initial doit être créé une seule fois

## 5. Déploiement

### Render

1. Pousser le dépôt sur `main`
2. Lancer le Blueprint Render
3. Vérifier que `DATABASE_URL` est injecté
4. Renseigner `ADMIN_PASSWORD`
5. Vérifier que le health check cible `/healthz`

### Docker / autre hébergeur

Commande de démarrage recommandée :

```bash
python init_app.py && gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 4 --worker-class sync --access-logfile - --error-logfile -
```

## 6. Contrôles post-déploiement

Vérifier :

- connexion `/login`
- accès `/dashboard`
- état `/healthz`
- état `/readyz`
- pagination `/commandes?page=2`, `/stocks?page=2`, `/ventes?page=2`
- export dashboard Excel
- imports avec prévisualisation

## 7. Exploitation courante

Logs :

- fichier applicatif : `logs/app.log`
- logs HTTP : stdout / Gunicorn

Sauvegardes :

- PostgreSQL : planifier un dump régulier côté hébergeur ou `pg_dump`
- stocker les sauvegardes hors du serveur applicatif

Sécurité :

- ne jamais laisser `SECRET_KEY` ou `ADMIN_PASSWORD` par défaut
- utiliser PostgreSQL en production, pas SQLite
- invalider les anciennes sessions si la base est recréée

## 8. Procédure incident

### L’application ne démarre pas

Contrôler :

```bash
python init_app.py
python -c "from app import app; print(app.config['SQLALCHEMY_DATABASE_URI'])"
```

### La base existe mais Alembic échoue

Cas fréquent : base initialisée avant Alembic.

```bash
alembic stamp head
```

Puis rejouer uniquement les futures migrations.

### `readyz` renvoie 503

- vérifier `DATABASE_URL`
- vérifier l’accessibilité réseau vers PostgreSQL
- vérifier les credentials DB

## 9. Discipline de changement

Avant toute mise en production :

1. créer une migration Alembic si le schéma change
2. exécuter `pytest`
3. exécuter `smoke_test.py`
4. déployer
5. valider `/readyz` et le login
