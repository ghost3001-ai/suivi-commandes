import os
from datetime import timedelta
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

SOURCE_WORKBOOK_PATH = os.path.join(BASE_DIR, 'Projet Suivi Commande ASS.xlsx')


def get_database_url():
    """Construit une URL de base de données compatible en dev et prod."""
    database_url = os.environ.get('DATABASE_URL')
    if database_url:
        if database_url.startswith('postgres://'):
            return database_url.replace('postgres://', 'postgresql://', 1)
        return database_url
    return f'sqlite:///{SQLITE_DB_PATH}'


def get_runtime_env():
    return (os.environ.get('APP_ENV') or os.environ.get('FLASK_ENV') or 'development').strip().lower()

class Config:
    ENVIRONMENT = get_runtime_env()
    IS_PRODUCTION = ENVIRONMENT == 'production'

    # SECRET_KEY MUST be set in production
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        import warnings
        if IS_PRODUCTION:
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
    SQLALCHEMY_ENGINE_OPTIONS = {}
    if not SQLALCHEMY_DATABASE_URI.startswith('sqlite'):
        SQLALCHEMY_ENGINE_OPTIONS = {
            'pool_pre_ping': True,
            'pool_recycle': 300,
        }
    
    # Configuration pour l'upload de fichiers
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    CATEGORY_CATALOG_FILE = (
        os.environ.get('CATEGORY_CATALOG_FILE')
        or (SOURCE_WORKBOOK_PATH if os.path.exists(SOURCE_WORKBOOK_PATH) else os.path.join(BASE_DIR, 'data', 'categorie_ass1.xlsx'))
    )
    CATEGORY_FAMILY_OVERRIDE_FILE = os.environ.get('CATEGORY_FAMILY_OVERRIDE_FILE') or os.path.join(BASE_DIR, 'data', 'familles_ass1.xlsx')
    IMPORT_PREVIEW_TTL_HOURS = int(os.environ.get('IMPORT_PREVIEW_TTL_HOURS') or 24)

    # Pagination et ergonomie d'exploitation
    DEFAULT_PAGE_SIZE = int(os.environ.get('DEFAULT_PAGE_SIZE') or 25)
    LOG_PAGE_SIZE = int(os.environ.get('LOG_PAGE_SIZE') or 50)

    # Session et cookies
    SESSION_COOKIE_NAME = os.environ.get('SESSION_COOKIE_NAME') or 'afrilux_session'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.environ.get('SESSION_COOKIE_SAMESITE') or 'Lax'
    SESSION_COOKIE_SECURE = IS_PRODUCTION
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = SESSION_COOKIE_SAMESITE
    REMEMBER_COOKIE_SECURE = IS_PRODUCTION
    PERMANENT_SESSION_LIFETIME = timedelta(hours=int(os.environ.get('SESSION_LIFETIME_HOURS') or 12))
    PREFERRED_URL_SCHEME = 'https' if IS_PRODUCTION else 'http'

    # Proxy / reverse proxy
    PROXY_FIX_ENABLED = os.environ.get('PROXY_FIX_ENABLED', '1' if IS_PRODUCTION else '0').lower() in {'1', 'true', 'yes'}
    PROXY_FIX_X_FOR = int(os.environ.get('PROXY_FIX_X_FOR') or 1)
    PROXY_FIX_X_PROTO = int(os.environ.get('PROXY_FIX_X_PROTO') or 1)
    PROXY_FIX_X_HOST = int(os.environ.get('PROXY_FIX_X_HOST') or 1)

    # Logging
    LOG_LEVEL = (os.environ.get('LOG_LEVEL') or ('INFO' if IS_PRODUCTION else 'DEBUG')).upper()
    LOG_FILE = os.path.join(BASE_DIR, 'logs', 'app.log')
    LOG_MAX_BYTES = int(os.environ.get('LOG_MAX_BYTES') or 5 * 1024 * 1024)
    LOG_BACKUP_COUNT = int(os.environ.get('LOG_BACKUP_COUNT') or 5)
    SLOW_REQUEST_THRESHOLD_MS = int(os.environ.get('SLOW_REQUEST_THRESHOLD_MS') or 1000)
    SCHEDULER_LOCK_FILE = os.path.join(BASE_DIR, 'logs', 'dashboard_scheduler.lock')
    
    # Configuration email (pour futures notifications)
    MAIL_SERVER = os.environ.get('MAIL_SERVER')
    MAIL_PORT = int(os.environ.get('MAIL_PORT') or 587)
    MAIL_USE_TLS = True
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')

    # Dashboard analytics and automation
    DASHBOARD_CA_ALERT_THRESHOLD = float(os.environ.get('DASHBOARD_CA_ALERT_THRESHOLD') or 100000)
    DASHBOARD_PRODUCT_ALERT_THRESHOLD = float(os.environ.get('DASHBOARD_PRODUCT_ALERT_THRESHOLD') or 50000)
    DASHBOARD_ALERT_POLL_SECONDS = int(os.environ.get('DASHBOARD_ALERT_POLL_SECONDS') or 30)
    DASHBOARD_EMBED_TOKEN = os.environ.get('DASHBOARD_EMBED_TOKEN') or 'embed-demo-token'
    DASHBOARD_SCHEDULER_ENABLED = os.environ.get('DASHBOARD_SCHEDULER_ENABLED', '').lower() in {'1', 'true', 'yes'}
    DASHBOARD_REPORT_SENDER = os.environ.get('DASHBOARD_REPORT_SENDER') or MAIL_USERNAME or 'noreply@example.com'
