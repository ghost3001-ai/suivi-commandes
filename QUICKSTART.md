# 🚀 QUICKSTART - Lancer l'Application

## 3 Secondes pour Démarrer

### Linux / Mac
```bash
chmod +x setup.sh
./setup.sh
```

### Windows
```bash
setup.bat
```

**Voilà! L'app démarre automatiquement sur http://localhost:5000**

---

## Si ça ne marche pas

### ❌ "Command not found"
```bash
# Linux/Mac: Installer Python 3.12
brew install python@3.12            # Mac
sudo apt-get install python3.12     # Ubuntu/Debian
```

### ❌ "Port already in use"
```bash
# Utiliser un autre port
python app.py --port 5001
```

### ❌ "Database file not found"
```bash
# Réinitialiser complètement
rm -rf instance/*
python init_app.py
python app.py
```

### ❌ Autre erreur
```bash
# Log complet
python -c "import app; print('OK')"
```

---

## 👤 Credentials

**User:** `admin`  
**Password:** `admin123`

⚠️ **À CHANGER en production!**

En production hébergée, définis `ADMIN_PASSWORD` dans l'environnement avant le premier démarrage.

---

## 📚 Documentation

- [README.md](README.md) - Fonctionnalités complètes
- [INSTALLATION.md](INSTALLATION.md) - Installation détaillée
- [Déploiement Production](README.md#-déploiement-production)

---

## Démarrage Manuel (si besoin)

```bash
# 1. Créer venv
python3.12 -m venv venv

# 2. Activer venv
source venv/bin/activate          # Linux/Mac
venv\Scripts\activate.bat         # Windows

# 3. Installer dépendances
pip install -r requirements.txt

# 4. Initialiser BD
python init_app.py

# 5. Lancer
python app.py
```

**Accès:** http://localhost:5000
