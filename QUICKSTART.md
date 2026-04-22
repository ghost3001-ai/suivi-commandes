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

Une fois connecté, ouvre `/dashboard` pour voir le cockpit `Data Analyst achats` :
- historique des dépenses
- alertes de dépassement budget
- scoring et recommandation fournisseur
- signaux de négociation, livraison, réception et facturation

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

### Variables utiles pour les alertes achats

```bash
# Budget achats de la période analysée
export DASHBOARD_PURCHASE_BUDGET=1000000

# Déclenche l’alerte "budget déjà utilisé à 85%"
export DASHBOARD_BUDGET_WARNING_PCT=85

# Seuil de dérive prix vs marché
export DASHBOARD_NEGOTIATION_ALERT_THRESHOLD=10

# Seuil de dépendance fournisseur
export DASHBOARD_SUPPLIER_DEPENDENCY_THRESHOLD=35
```

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
