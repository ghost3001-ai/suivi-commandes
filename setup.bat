@echo off
REM Setup et démarrage pour Windows

echo ==========================================
echo Setup Application Suivi Commandes
echo ==========================================
echo.

REM Vérifier Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python n'est pas installé ou non accessible
    exit /b 1
)
echo ✓ Python trouvé
echo.

REM Créer venv
if not exist "venv" (
    echo 🔧 Création de l'environnement virtuel...
    python -m venv venv
)

REM Activer venv
call venv\Scripts\activate.bat

REM Installer dépendances
echo 📦 Installation des dépendances...
pip install --upgrade pip setuptools wheel >nul 2>&1
pip install -r requirements.txt >nul 2>&1
echo ✓ Dépendances installées
echo.

REM Initialiser BD
if not exist "instance\commandes.db" (
    echo 🗄️  Initialisation de la base de données...
    python init_app.py
) else (
    echo ✓ Base de données existe déjà
)
echo.

REM Démarrer
echo ==========================================
echo ✓ Setup TERMINÉ!
echo ==========================================
echo.
echo 🌐 URL: http://localhost:5000
echo 👤 Admin: admin / admin123
echo 🛑 Arrêt: Ctrl+C
echo.
echo Démarrage de l'app...
echo.

python app.py

pause
