import os
from dotenv import load_dotenv

load_dotenv()

# Get the base directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Créer le dossier instance dès le démarrage
INSTANCE_PATH = os.path.join(BASE_DIR, 'instance')
os.makedirs(INSTANCE_PATH, exist_ok=True)
SQLITE_DB_PATH = os.path.join(INSTANCE_PATH, 'commandes.db').replace('\\', '/')

# Créer aussi les dossiers uploads et logs
os.makedirs(os.path.join(BASE_DIR, 'uploads'), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, 'logs'), exist_ok=True)


def get_database_url():
    """Construit une URL de base de données compatible en dev et prod."""
    database_url = os.environ.get('DATABASE_URL')
    if database_url:
        if database_url.startswith('postgres://'):
            return database_url.replace('postgres://', 'postgresql://', 1)
        return database_url
    return f'sqlite:///{SQLITE_DB_PATH}'

class Config:
    # SECRET_KEY MUST be set in production
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        import warnings
        if os.environ.get('FLASK_ENV') == 'production':
            raise ValueError('SECRET_KEY environment variable MUST be set in production')
        SECRET_KEY = 'dev-secret-key-change-in-production'
        warnings.warn('Using default SECRET_KEY - NEVER use in production!')
    
    # CSRF Protection
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None
    
    # Database configuration - use relative path for SQLite
    DATABASE_URL = os.environ.get('DATABASE_URL')
    SQLALCHEMY_DATABASE_URI = get_database_url()
    
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Configuration pour l'upload de fichiers
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    
    # Configuration email (pour futures notifications)
    MAIL_SERVER = os.environ.get('MAIL_SERVER')
    MAIL_PORT = int(os.environ.get('MAIL_PORT') or 587)
    MAIL_USE_TLS = True
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
