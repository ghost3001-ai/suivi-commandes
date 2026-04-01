#!/usr/bin/env python
"""
Script pour initialiser l'application
1. Crée les répertoires nécessaires
2. Initialise la base de données
3. Crée l'utilisateur admin par défaut
"""

import os
import sys

# Créer les répertoires nécessaires
directories = ['instance', 'uploads', 'logs']
for directory in directories:
    os.makedirs(directory, exist_ok=True)
    print(f"✓ Dossier '{directory}' créé/vérifié")

print()

# Importer l'app
try:
    from app import app, db, init_db
    print("✓ Application Flask chargée")
except ImportError as e:
    print(f"✗ Erreur d'import Flask: {e}")
    print("  → Installez les dépendances: pip install -r requirements.txt")
    sys.exit(1)
except Exception as e:
    print(f"✗ Erreur lors du chargement de Flask: {e}")
    sys.exit(1)

print()

# Initialiser la base de données
try:
    init_db()
    print("✓ Base de données initialisée")
except Exception as e:
    print(f"✗ Erreur lors de l'initialisation: {e}")
    sys.exit(1)

print()
print("=" * 50)
print("✓ Initialisation RÉUSSIE!")
print("=" * 50)
print()
print("Commandes utiles:")
print("  Démarrage:       python app.py")
print("  Production:      gunicorn wsgi:app")
if os.environ.get('FLASK_ENV') == 'production':
    print("  Admin initial:   ADMIN_USERNAME / ADMIN_PASSWORD")
else:
    print("  Credentials:     admin / admin123")
print()
print("Changez le mot de passe admin en production!")
print("=" * 50)
