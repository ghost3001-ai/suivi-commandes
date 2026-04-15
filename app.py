from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, send_from_directory, session, has_request_context, g
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from flask_wtf.csrf import CSRFProtect
from logging.handlers import RotatingFileHandler
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from datetime import datetime, date, timedelta
import pandas as pd
import logging
import os
import re
import smtplib
import time
import fcntl
from io import BytesIO, StringIO
import json
import unicodedata
from email.message import EmailMessage
from urllib.parse import urlencode
from uuid import uuid4

from category_catalog import get_category_catalog
from config import Config
from models import (
    db,
    Utilisateur,
    Fournisseur,
    Commande,
    LogAction,
    Produit,
    Vente,
    LigneVente,
    MouvementStock,
    DashboardSubscription,
)
from sqlalchemy import func, case, and_, or_, inspect, text, extract
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import selectinload, joinedload
from collections import defaultdict, OrderedDict

# Initialisation de l'application
app = Flask(__name__)
app.config.from_object(Config)
dashboard_scheduler = None
database_bootstrap_uri = None
synchronized_supplier_reference_key = None
scheduler_lock_handle = None

ROLE_ADMIN = 'admin'
ROLE_SPECTATEUR = 'spectateur'
ROLE_ACHATS = 'achats'
ROLE_SERVICE_COMPTABLE = 'service_comptable'
ROLE_SERVICE_MARKETING = 'service_marketing'
ROLE_GESTIONNAIRE_STOCK = 'gestionnaire_stock'
ROLE_INGENIEUR = 'ingenieur'

ROLE_LABELS = OrderedDict([
    (ROLE_ADMIN, 'Administrateur'),
    (ROLE_SPECTATEUR, 'Spectateur'),
    (ROLE_ACHATS, 'Achats'),
    (ROLE_SERVICE_COMPTABLE, 'Service comptable'),
    (ROLE_SERVICE_MARKETING, 'Service marketing'),
    (ROLE_GESTIONNAIRE_STOCK, 'Gestionnaire de stock'),
    (ROLE_INGENIEUR, 'Ingénieur'),
])

ROLE_HOME_ENDPOINTS = {
    ROLE_ADMIN: 'dashboard',
    ROLE_SPECTATEUR: 'dashboard',
    ROLE_ACHATS: 'commandes',
    ROLE_SERVICE_COMPTABLE: 'commandes',
    ROLE_SERVICE_MARKETING: 'commandes',
    ROLE_GESTIONNAIRE_STOCK: 'stocks',
    ROLE_INGENIEUR: 'commandes',
}

ENTITE_OPTIONS = ['AFRILUX', 'SMART']
ACHETEUR_OPTIONS = ['GILLES', 'JISLAIN', 'ALAIN']
COMMANDE_LIST_VIEWS = OrderedDict([
    ('en_cours', 'Commandes en cours'),
    ('achevees', 'Commandes achevées'),
    ('non_payees', 'Commandes non payées'),
    ('payees', 'Commandes payées'),
    ('toutes', 'Toutes les commandes'),
])

SERVICE_DEMANDEUR_OPTIONS = [
    'Achat',
    'Marketing',
    'Direction',
    'Ingenierie',
    'Commerciale',
    'Froid & climatisation',
    'Courant fort',
    'Courant faible',
    'Logistique',
    'Magasin',
    'Finance & Comptabilité',
    'IT & SAV',
]

STOCK_TYPE_OPTIONS = OrderedDict([
    (Produit.TYPE_MATIERE_PREMIERE, 'Matières premières'),
    (Produit.TYPE_EN_COURS, 'En-cours de production'),
    (Produit.TYPE_PRODUIT_FINI, 'Produits finis'),
    (Produit.TYPE_MRO, 'Maintenance / MRO'),
])

STOCK_REPLENISHMENT_OPTIONS = OrderedDict([
    (Produit.REAPPRO_POINT_COMMANDE, 'Point de commande'),
    (Produit.REAPPRO_CALENDAIRE, 'Réapprovisionnement calendaire'),
    (Produit.REAPPRO_KANBAN, 'Kanban'),
])

STOCK_VALUATION_OPTIONS = OrderedDict([
    (Produit.VALORISATION_CUMP, 'CUMP'),
    (Produit.VALORISATION_FIFO, 'FIFO'),
    (Produit.VALORISATION_LIFO, 'LIFO'),
])

ROLE_PERMISSIONS = {
    ROLE_ADMIN: {
        'dashboard_view',
        'dashboard_manage',
        'commandes_view',
        'commandes_manage',
        'commandes_payment_manage',
        'commandes_reception_manage',
        'stocks_view',
        'stocks_manage',
        'ventes_view',
        'ventes_manage',
        'performances_view',
        'fournisseurs_manage',
        'users_manage',
        'logs_view',
    },
    ROLE_SPECTATEUR: {
        'dashboard_view',
        'commandes_view',
        'stocks_view',
        'ventes_view',
        'performances_view',
    },
    ROLE_ACHATS: {
        'commandes_view',
        'commandes_manage',
    },
    ROLE_SERVICE_COMPTABLE: {
        'commandes_view',
        'commandes_payment_manage',
    },
    ROLE_SERVICE_MARKETING: {
        'commandes_view',
    },
    ROLE_GESTIONNAIRE_STOCK: {
        'commandes_view',
        'commandes_reception_manage',
        'stocks_view',
        'stocks_manage',
    },
    ROLE_INGENIEUR: {
        'commandes_view',
        'stocks_view',
        'performances_view',
    },
}

# Initialisation des extensions
db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Veuillez vous connecter pour accéder à cette page'

if app.config.get('PROXY_FIX_ENABLED'):
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=app.config.get('PROXY_FIX_X_FOR', 1),
        x_proto=app.config.get('PROXY_FIX_X_PROTO', 1),
        x_host=app.config.get('PROXY_FIX_X_HOST', 1),
    )

# CSRF Protection
csrf = CSRFProtect(app)

# Création du dossier d'upload si nécessaire
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


def configure_app_logging():
    """Configure un logging applicatif exploitable en production."""
    log_level_name = app.config.get('LOG_LEVEL', 'INFO')
    log_level = getattr(logging, str(log_level_name).upper(), logging.INFO)
    log_file = app.config.get('LOG_FILE')
    formatter = logging.Formatter(
        '%(asctime)s %(levelname)s [%(name)s] %(message)s'
    )

    app.logger.setLevel(log_level)

    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        existing_file_handler = next(
            (
                handler for handler in app.logger.handlers
                if isinstance(handler, RotatingFileHandler)
                and getattr(handler, 'baseFilename', None) == os.path.abspath(log_file)
            ),
            None,
        )
        if existing_file_handler is None:
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=app.config.get('LOG_MAX_BYTES', 5 * 1024 * 1024),
                backupCount=app.config.get('LOG_BACKUP_COUNT', 5),
            )
            file_handler.setLevel(log_level)
            file_handler.setFormatter(formatter)
            app.logger.addHandler(file_handler)

    werkzeug_logger = logging.getLogger('werkzeug')
    werkzeug_logger.setLevel(log_level)


configure_app_logging()

@login_manager.user_loader
def load_user(user_id):
    if not user_id:
        return None

    try:
        database_bootstrapped = getattr(g, 'database_bootstrapped', False) if has_request_context() else False
        if not database_bootstrapped:
            database_bootstrapped = ensure_database_ready()
        if database_bootstrapped:
            if has_request_context():
                session.pop('_user_id', None)
                session.pop('_fresh', None)
            return None

        user = db.session.get(Utilisateur, int(user_id))
        if user is None and has_request_context():
            session.pop('_user_id', None)
            session.pop('_fresh', None)
        return user
    except (TypeError, ValueError):
        return None
    except OperationalError:
        db.session.rollback()
        if has_request_context():
            session.pop('_user_id', None)
            session.pop('_fresh', None)
        return None


def ensure_database_ready(force=False):
    """Initialise la base une seule fois par URL si elle n'est pas prête."""
    global database_bootstrap_uri

    database_uri = app.config.get('SQLALCHEMY_DATABASE_URI')
    if not force and database_bootstrap_uri == database_uri:
        return False

    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    bootstrapped = False
    if 'utilisateurs' not in existing_tables:
        init_db()
        bootstrapped = True

    database_bootstrap_uri = database_uri
    return bootstrapped


@app.before_request
def bootstrap_database_before_request():
    g.database_bootstrapped = ensure_database_ready()


@app.before_request
def prepare_request_context():
    session.permanent = True
    g.request_started_at = time.perf_counter()


@app.after_request
def apply_operational_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('X-Permitted-Cross-Domain-Policies', 'none')
    response.headers.setdefault('Permissions-Policy', 'geolocation=(), microphone=(), camera=()')

    if request.endpoint != 'dashboard_embed':
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')

    if app.config.get('IS_PRODUCTION') and request.is_secure:
        response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')

    if request.endpoint in {'login', 'logout'} or current_user.is_authenticated:
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'

    duration_ms = None
    if hasattr(g, 'request_started_at'):
        duration_ms = int((time.perf_counter() - g.request_started_at) * 1000)
        if duration_ms >= app.config.get('SLOW_REQUEST_THRESHOLD_MS', 1000):
            app.logger.warning(
                'Slow request %s %s completed in %sms with status %s',
                request.method,
                request.path,
                duration_ms,
                response.status_code,
            )

    response.headers['X-Response-Time-Ms'] = str(duration_ms or 0)
    return response


def get_requested_page(default=1):
    try:
        page = int(request.args.get('page', default))
    except (TypeError, ValueError):
        page = default
    return page if page > 0 else default


def normalize_user_role(role):
    normalized_role = (role or ROLE_SPECTATEUR).strip().lower()
    if normalized_role not in ROLE_LABELS:
        return ROLE_SPECTATEUR
    return normalized_role


def get_role_label(role):
    return ROLE_LABELS.get(normalize_user_role(role), ROLE_LABELS[ROLE_SPECTATEUR])


def user_has_permission(user, permission):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    role = normalize_user_role(getattr(user, 'role', None))
    return permission in ROLE_PERMISSIONS.get(role, set())


def user_has_any_permission(user, *permissions):
    return any(user_has_permission(user, permission) for permission in permissions)


def get_commande_edit_capabilities(user=None):
    user = user or current_user
    can_manage_core = user_has_permission(user, 'commandes_manage')
    can_manage_payment = user_has_any_permission(user, 'commandes_payment_manage', 'commandes_manage')
    can_manage_reception = user_has_any_permission(user, 'commandes_reception_manage', 'commandes_manage')
    return {
        'can_manage_core': can_manage_core,
        'can_manage_payment': can_manage_payment,
        'can_manage_reception': can_manage_reception,
        'can_edit_any': can_manage_core or can_manage_payment or can_manage_reception,
        'can_delete': can_manage_core,
    }


def commande_completed_expression():
    return and_(
        Commande.statut == Commande.STATUT_PAYE,
        Commande.date_reception.isnot(None),
        Commande.bon_livraison.isnot(None),
        func.trim(Commande.bon_livraison) != '',
    )


def commande_in_progress_expression():
    return or_(
        Commande.statut.is_(None),
        Commande.statut != Commande.STATUT_PAYE,
        Commande.date_reception.is_(None),
        Commande.bon_livraison.is_(None),
        func.trim(Commande.bon_livraison) == '',
    )


def normalize_commande_list_view(view):
    return view if view in COMMANDE_LIST_VIEWS else 'en_cours'


def get_home_endpoint_for_user(user=None):
    user = user or current_user
    if not user or not getattr(user, 'is_authenticated', False):
        return 'login'

    role = normalize_user_role(getattr(user, 'role', None))
    endpoint = ROLE_HOME_ENDPOINTS.get(role)
    if endpoint:
        return endpoint

    if user_has_permission(user, 'dashboard_view'):
        return 'dashboard'
    if user_has_permission(user, 'commandes_view'):
        return 'commandes'
    if user_has_permission(user, 'stocks_view'):
        return 'stocks'
    if user_has_permission(user, 'performances_view'):
        return 'performances'
    if user_has_permission(user, 'ventes_view'):
        return 'ventes'
    return 'admin_profil'


def redirect_access_denied(default_endpoint=None):
    flash('Accès non autorisé', 'danger')
    return redirect(url_for(default_endpoint or get_home_endpoint_for_user()))


def require_permission(permission, default_endpoint=None):
    if user_has_permission(current_user, permission):
        return None
    return redirect_access_denied(default_endpoint=default_endpoint)

# ==================== CONTEXT PROCESSORS ====================

@app.context_processor
def utility_processor():
    def format_date(d):
        if d:
            return d.strftime('%d/%m/%Y')
        return ''
    
    def format_montant(m):
        return f"{m:,.0f}" if m else "0"

    def page_url(page, endpoint=None, **overrides):
        endpoint = endpoint or request.endpoint
        args = request.args.to_dict(flat=True)
        args.update({key: value for key, value in overrides.items() if value is not None})
        args['page'] = page
        return url_for(endpoint, **args)

    def can(permission):
        return user_has_permission(current_user, permission)

    def can_edit_commande():
        return get_commande_edit_capabilities(current_user)['can_edit_any']
    
    return dict(
        format_date=format_date,
        format_montant=format_montant,
        page_url=page_url,
        can=can,
        can_edit_commande=can_edit_commande,
        role_label=lambda role: get_role_label(role),
    )

# ==================== VALIDATION FUNCTIONS ====================

def valider_montant(montant_str):
    """Valide et retourne un montant numérique"""
    try:
        montant = float(montant_str) if montant_str else 0
        if montant < 0:
            raise ValueError("Le montant ne peut pas être négatif")
        return montant
    except (ValueError, TypeError):
        raise ValueError("Montant invalide")

def valider_email(email):
    """Valide un format d'email"""
    if not email:
        return True  # Email optionnel
    import re
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email):
        raise ValueError("Format d'email invalide")
    return True

def valider_telephone(telephone):
    """Valide un numéro de téléphone"""
    if not telephone:
        return True  # Téléphone optionnel
    # Simple validation: au moins 9 caractères, et contient des chiffres
    if len(str(telephone).replace(' ', '').replace('-', '')) < 9:
        raise ValueError("Numéro de téléphone invalide")
    return True

def valider_mot_de_passe(password):
    """Valide un mot de passe minimal."""
    if not password or len(password) < 6:
        raise ValueError("Le mot de passe doit contenir au moins 6 caractères")
    return True

def parser_date_import(value):
    """Convertit une valeur importée en date Python valide."""
    if value is None or value == '' or pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    parsed = pd.to_datetime(value, errors='coerce')
    if pd.isna(parsed):
        return None
    return parsed.date()

def parser_montant_import(value):
    """Nettoie les montants issus d'Excel/CSV."""
    if value is None or value == '' or pd.isna(value):
        return 0
    if isinstance(value, (int, float)):
        return float(value)

    cleaned = str(value).strip().replace(' ', '')
    cleaned = cleaned.replace('\u202f', '').replace('\xa0', '')

    if ',' in cleaned and '.' in cleaned:
        cleaned = cleaned.replace('.', '').replace(',', '.')
    elif ',' in cleaned:
        cleaned = cleaned.replace(',', '.')

    cleaned = ''.join(ch for ch in cleaned if ch.isdigit() or ch in '.-')
    if not cleaned:
        return 0

    try:
        return float(cleaned)
    except ValueError:
        return 0

def valider_quantite(valeur, autoriser_negative=False):
    """Valide une quantité."""
    try:
        quantite = float(valeur)
    except (TypeError, ValueError):
        raise ValueError("Quantité invalide")

    if autoriser_negative:
        if quantite == 0:
            raise ValueError("La quantité ne peut pas être nulle")
    elif quantite <= 0:
        raise ValueError("La quantité doit être supérieure à zéro")

    return quantite


def valider_nombre_non_negatif(valeur, champ):
    """Valide un nombre positif ou nul."""
    if valeur in (None, ''):
        return 0.0
    try:
        nombre = float(valeur)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{champ} invalide") from exc
    if nombre < 0:
        raise ValueError(f"{champ} ne peut pas être négatif")
    return nombre


def valider_taux_pourcentage(valeur, champ):
    """Valide un taux en pourcentage entre 0 et 100."""
    taux = valider_nombre_non_negatif(valeur, champ)
    if taux > 100:
        raise ValueError(f"{champ} doit être compris entre 0 et 100")
    return taux

def valider_note_fournisseur(valeur, champ='Note'):
    """Valide une note fournisseur sur 5."""
    if valeur in (None, ''):
        return None
    try:
        note = float(valeur)
    except (TypeError, ValueError):
        raise ValueError(f"{champ} invalide")
    if note < 0 or note > 5:
        raise ValueError(f"{champ} doit être comprise entre 0 et 5")
    return note

def valider_service_demandeur(valeur):
    """Valide le service demandeur d'une commande."""
    service = (valeur or '').strip()
    if not service:
        return None
    if service not in SERVICE_DEMANDEUR_OPTIONS:
        raise ValueError('Service demandeur invalide')
    return service


def valider_choix_liste(valeur, options, champ, obligatoire=False):
    selected_value = (valeur or '').strip()
    if not selected_value:
        if obligatoire:
            raise ValueError(f'{champ} obligatoire')
        return None
    if selected_value not in options:
        raise ValueError(f'{champ} invalide')
    return selected_value


def valider_texte_requis(valeur, champ):
    text_value = (valeur or '').strip()
    if not text_value:
        raise ValueError(f'{champ} obligatoire')
    return text_value


def nettoyer_texte_optionnel(valeur):
    text_value = (valeur or '').strip()
    return text_value or None


def parse_commande_date_input(valeur, champ, obligatoire=False):
    text_value = (valeur or '').strip()
    if not text_value:
        if obligatoire:
            raise ValueError(f'{champ} obligatoire')
        return None
    try:
        return datetime.strptime(text_value, '%Y-%m-%d').date()
    except ValueError as exc:
        raise ValueError(f'{champ} invalide') from exc


def parse_commande_numero(valeur):
    text_value = (valeur or '').strip()
    if not text_value:
        return None
    try:
        return int(text_value)
    except (TypeError, ValueError) as exc:
        raise ValueError('Numéro de commande invalide') from exc


def parse_commande_fournisseur_id(valeur, obligatoire=False):
    text_value = (valeur or '').strip()
    if not text_value:
        if obligatoire:
            raise ValueError('Fournisseur obligatoire')
        return None
    if not text_value.isdigit():
        raise ValueError('Fournisseur invalide')
    return int(text_value)


def validate_commande_workflow_state(montant, avance, date_paiement=None, facture=None, date_reception=None, bon_livraison=None):
    if avance > montant:
        raise ValueError("L'avance ne peut pas être supérieure au montant")

    cleaned_facture = nettoyer_texte_optionnel(facture)
    cleaned_bon_livraison = nettoyer_texte_optionnel(bon_livraison)

    if bool(date_paiement) != bool(cleaned_facture):
        raise ValueError('Le paiement requiert à la fois une date de paiement et un numéro de facture')
    if date_paiement and avance < montant:
        raise ValueError('Le paiement final exige une avance cumulée égale au montant de la commande')
    if bool(date_reception) != bool(cleaned_bon_livraison):
        raise ValueError('La réception requiert à la fois une date réelle et un numéro de bon de livraison')

    return cleaned_facture, cleaned_bon_livraison


def get_commande_form_values(commande=None, form_data=None):
    def field_value(field_name, default=''):
        if form_data is not None and field_name in form_data:
            return form_data.get(field_name)
        value = getattr(commande, field_name, None) if commande else None
        return value if value is not None else default

    def date_value(field_name):
        raw_value = field_value(field_name)
        if not raw_value:
            return ''
        if isinstance(raw_value, date):
            return raw_value.strftime('%Y-%m-%d')
        return str(raw_value)

    def checkbox_value(field_name, default=False):
        if form_data is not None and field_name in form_data:
            return field_name in form_data
        if commande is not None and getattr(commande, field_name, None) is not None:
            return bool(getattr(commande, field_name))
        return default

    fournisseur_id = field_value('fournisseur_id', '')
    return {
        'nr': field_value('nr', ''),
        'date_cde': date_value('date_cde'),
        'entite': field_value('entite', ''),
        'acheteur': field_value('acheteur', ''),
        'service_demandeur': field_value('service_demandeur', ''),
        'demandeur': field_value('demandeur', ''),
        'fournisseur_id': str(fournisseur_id) if fournisseur_id not in (None, '') else '',
        'bon_commande': field_value('bon_commande', ''),
        'affaire': field_value('affaire', ''),
        'montant': field_value('montant', ''),
        'avance': field_value('avance', ''),
        'date_livraison': date_value('date_livraison'),
        'date_paiement': date_value('date_paiement'),
        'facture': field_value('facture', ''),
        'date_reception': date_value('date_reception'),
        'bon_livraison': field_value('bon_livraison', ''),
        'commentaire': field_value('commentaire', ''),
        'commande_conforme': checkbox_value('commande_conforme', default=True),
        'rupture_fournisseur': checkbox_value('rupture_fournisseur', default=False),
        'note_fournisseur': field_value('note_fournisseur', ''),
        'note_service': field_value('note_service', ''),
    }


def get_product_category_catalog():
    """Retourne le référentiel familles/catégories des produits."""
    return get_category_catalog(
        app.config.get('CATEGORY_CATALOG_FILE'),
        app.config.get('CATEGORY_FAMILY_OVERRIDE_FILE'),
    )


def get_product_form_values(produit=None, form_data=None):
    """Construit les valeurs de formulaire produit."""
    if form_data is not None:
        values = {
            'nom': (form_data.get('nom') or '').strip(),
            'code': (form_data.get('code') or '').strip(),
            'description': (form_data.get('description') or '').strip(),
            'famille': (form_data.get('famille') or '').strip(),
            'categorie': (form_data.get('categorie') or '').strip(),
            'type_stock': (form_data.get('type_stock') or Produit.TYPE_PRODUIT_FINI).strip(),
            'methode_reappro': (form_data.get('methode_reappro') or Produit.REAPPRO_POINT_COMMANDE).strip(),
            'methode_valorisation': (form_data.get('methode_valorisation') or Produit.VALORISATION_CUMP).strip(),
            'unite': (form_data.get('unite') or '').strip(),
            'prix_unitaire': form_data.get('prix_unitaire', ''),
            'stock_initial': form_data.get('stock_initial', 0),
            'stock_minimum': form_data.get('stock_minimum', 0),
            'stock_securite': form_data.get('stock_securite', 0),
            'delai_approvisionnement_jours': form_data.get('delai_approvisionnement_jours', 0),
            'periodicite_reappro_jours': form_data.get('periodicite_reappro_jours', 0),
            'consommation_moyenne_journaliere': form_data.get('consommation_moyenne_journaliere', 0),
            'cout_passation_commande': form_data.get('cout_passation_commande', 0),
            'taux_possession_annuel': form_data.get('taux_possession_annuel', 25),
            'actif': bool(form_data.get('actif', True)),
        }
        return values

    if produit:
        return {
            'nom': produit.nom or '',
            'code': produit.code or '',
            'description': produit.description or '',
            'famille': produit.famille or '',
            'categorie': produit.categorie or '',
            'type_stock': produit.type_stock or Produit.TYPE_PRODUIT_FINI,
            'methode_reappro': produit.methode_reappro or Produit.REAPPRO_POINT_COMMANDE,
            'methode_valorisation': produit.methode_valorisation or Produit.VALORISATION_CUMP,
            'unite': produit.unite or '',
            'prix_unitaire': produit.prix_unitaire if produit.prix_unitaire is not None else 0,
            'stock_initial': 0,
            'stock_minimum': produit.stock_minimum if produit.stock_minimum is not None else 0,
            'stock_securite': produit.stock_securite if produit.stock_securite is not None else 0,
            'delai_approvisionnement_jours': produit.delai_approvisionnement_jours if produit.delai_approvisionnement_jours is not None else 0,
            'periodicite_reappro_jours': produit.periodicite_reappro_jours if produit.periodicite_reappro_jours is not None else 0,
            'consommation_moyenne_journaliere': produit.consommation_moyenne_journaliere if produit.consommation_moyenne_journaliere is not None else 0,
            'cout_passation_commande': produit.cout_passation_commande if produit.cout_passation_commande is not None else 0,
            'taux_possession_annuel': produit.taux_possession_annuel if produit.taux_possession_annuel is not None else 25,
            'actif': bool(produit.actif),
        }

    return {
        'nom': '',
        'code': '',
        'description': '',
        'famille': '',
        'categorie': '',
        'type_stock': Produit.TYPE_PRODUIT_FINI,
        'methode_reappro': Produit.REAPPRO_POINT_COMMANDE,
        'methode_valorisation': Produit.VALORISATION_CUMP,
        'unite': '',
        'prix_unitaire': 0,
        'stock_initial': 0,
        'stock_minimum': 0,
        'stock_securite': 0,
        'delai_approvisionnement_jours': 0,
        'periodicite_reappro_jours': 0,
        'consommation_moyenne_journaliere': 0,
        'cout_passation_commande': 0,
        'taux_possession_annuel': 25,
        'actif': True,
    }


def get_product_catalog_context(form_values):
    """Prépare les listes dépendantes pour le formulaire produit."""
    catalog = get_product_category_catalog()
    family_label = form_values.get('famille') or ''
    category_label = form_values.get('categorie') or ''

    if family_label:
        available_categories = list(catalog['categories_by_family'].get(family_label, []))
    else:
        available_categories = []
        seen_categories = set()
        for family_name in catalog.get('families', []):
            for category_name in catalog['categories_by_family'].get(family_name, []):
                if category_name not in seen_categories:
                    seen_categories.add(category_name)
                    available_categories.append(category_name)

    if category_label and category_label not in available_categories:
        available_categories.append(category_label)

    return {
        'catalog_ui': {
            'families': catalog['families'],
            'categories_by_family': catalog['categories_by_family'],
        },
        'available_categories': available_categories,
    }


def normalize_product_taxonomy(famille, categorie, sous_categorie=None):
    """Valide la cohérence famille/catégorie."""
    catalog = get_product_category_catalog()
    famille = (famille or '').strip() or None
    categorie = (categorie or '').strip() or None

    if famille and catalog['family_lookup'] and famille not in catalog['family_lookup']:
        raise ValueError('Famille invalide')

    if categorie:
        mapped_family = catalog['category_to_family'].get(categorie)
        if catalog['category_lookup'] and categorie not in catalog['category_lookup']:
            raise ValueError('Catégorie invalide')
        if mapped_family:
            if famille and famille != mapped_family:
                raise ValueError('La catégorie sélectionnée ne correspond pas à la famille choisie')
            famille = mapped_family

    return famille, categorie, None


def sync_existing_product_taxonomy():
    """Complète la famille et retire les sous-catégories des produits existants."""
    catalog = get_product_category_catalog()

    produits = Produit.query.filter(
        or_(
            Produit.famille.is_(None),
            Produit.famille == '',
        )
    ).all()
    updated = 0
    for produit in produits:
        mapped_family = catalog['category_to_family'].get(produit.categorie)
        if mapped_family and produit.famille != mapped_family:
            produit.famille = mapped_family
            updated += 1

    inspector = inspect(db.engine)
    product_columns = {column['name'] for column in inspector.get_columns('produits')} if 'produits' in inspector.get_table_names() else set()
    cleared_subcategories = 0
    if 'sous_categorie' in product_columns:
        clear_result = db.session.execute(text("""
            UPDATE produits
            SET sous_categorie = NULL
            WHERE sous_categorie IS NOT NULL
              AND TRIM(CAST(sous_categorie AS TEXT)) != ''
        """))
        cleared_subcategories = clear_result.rowcount or 0

    if updated or cleared_subcategories:
        db.session.commit()
        print(f"Taxonomie produit synchronisée: {updated} famille(s) et {cleared_subcategories} sous-catégorie(s) nettoyées")


def build_stock_abc_map(produits):
    """Classe les produits actifs selon la méthode ABC sur la valeur de stock."""
    actifs = [produit for produit in produits if produit.actif]
    ranked = sorted(actifs, key=lambda produit: float(produit.valeur_stock or 0), reverse=True)
    total_value = sum(float(produit.valeur_stock or 0) for produit in ranked)
    abc_map = {}
    counts = {'A': 0, 'B': 0, 'C': 0}
    cumulative_value = 0.0

    for produit in ranked:
        cumulative_before = cumulative_value
        cumulative_value += float(produit.valeur_stock or 0)

        if total_value <= 0:
            classe = 'C'
        else:
            cumulative_ratio_before = (cumulative_before / total_value) * 100
            if cumulative_ratio_before < 80:
                classe = 'A'
            elif cumulative_ratio_before < 95:
                classe = 'B'
            else:
                classe = 'C'

        abc_map[produit.id] = classe
        counts[classe] += 1

    return abc_map, counts


def annotate_stock_products(produits, abc_map):
    """Ajoute des indicateurs calculés aux produits pour l'affichage."""
    for produit in produits:
        produit.abc_classe = abc_map.get(produit.id, 'C')
        produit.etat_stock = produit.get_etat_stock()
        produit.couverture_stock_jours_display = produit.couverture_stock_jours
        produit.qec_display = produit.quantite_economique_commande
        produit.point_commande_display = produit.point_commande
        produit.reappro_recommande_display = produit.get_quantite_reappro_recommandee()
    return produits


def build_stock_management_summary():
    """Construit les KPI métier de gestion des stocks."""
    produits_actifs = Produit.query.filter(Produit.actif.is_(True)).all()
    abc_map, abc_counts = build_stock_abc_map(produits_actifs)
    annotate_stock_products(produits_actifs, abc_map)

    total_value = sum(float(produit.valeur_stock or 0) for produit in produits_actifs)
    nb_ruptures = sum(1 for produit in produits_actifs if produit.est_en_rupture())
    nb_stock_faible = sum(1 for produit in produits_actifs if produit.est_stock_faible())
    nb_a_reappro = sum(1 for produit in produits_actifs if produit.doit_etre_reapprovisionne())
    couverture_values = [
        produit.couverture_stock_jours
        for produit in produits_actifs
        if produit.couverture_stock_jours is not None
    ]
    couverture_moyenne = average_non_null(*couverture_values)
    cout_possession_estime = sum(
        float(produit.valeur_stock or 0) * float(produit.taux_possession_annuel or 0) / 100
        for produit in produits_actifs
    )
    taux_service_stock = ((len(produits_actifs) - nb_ruptures) / len(produits_actifs) * 100) if produits_actifs else 0

    annual_period_start = date.today() - timedelta(days=365)
    cout_sorties_annuel = db.session.query(
        func.coalesce(func.sum(LigneVente.quantite * Produit.prix_unitaire), 0)
    ).select_from(
        LigneVente
    ).join(
        Vente,
        LigneVente.vente_id == Vente.id,
    ).join(
        Produit,
        LigneVente.produit_id == Produit.id,
    ).filter(
        Vente.date_vente >= annual_period_start
    ).scalar() or 0
    rotation_estimee = (cout_sorties_annuel / total_value) if total_value else None

    produits_a_reappro = sorted(
        [produit for produit in produits_actifs if produit.doit_etre_reapprovisionne()],
        key=lambda produit: (
            0 if produit.est_en_rupture() else 1,
            produit.couverture_stock_jours if produit.couverture_stock_jours is not None else float('inf'),
            -float(produit.valeur_stock or 0),
        ),
    )

    return {
        'abc_map': abc_map,
        'abc_counts': abc_counts,
        'valeur_stock': total_value,
        'nb_ruptures': nb_ruptures,
        'nb_stock_faible': nb_stock_faible,
        'nb_a_reappro': nb_a_reappro,
        'couverture_moyenne': round(couverture_moyenne, 1) if couverture_moyenne is not None else None,
        'cout_possession_estime': cout_possession_estime,
        'taux_service_stock': round(taux_service_stock, 1),
        'rotation_estimee': round(rotation_estimee, 2) if rotation_estimee is not None else None,
        'cout_sorties_annuel': cout_sorties_annuel,
        'produits_a_reappro_all': produits_a_reappro,
        'produits_a_reappro': produits_a_reappro[:8],
    }


def normalize_import_column_name(value):
    """Normalise un nom de colonne pour l'import."""
    text = unicodedata.normalize('NFKD', str(value or ''))
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', ' ', text).strip()
    return text


IGNORE_IMPORT_FIELD = '__ignore__'
SALES_IMPORT_FIELD_SPECS = OrderedDict([
    ('Reference', {'label': 'Référence', 'required': True, 'aliases': ['Reference', 'Référence', 'Ref']}),
    ('Date vente', {'label': 'Date vente', 'required': True, 'aliases': ['Date vente', 'Date']}),
    ('Client', {'label': 'Client', 'required': True, 'aliases': ['Client', 'Client nom', 'Nom client']}),
    ('Téléphone', {'label': 'Téléphone', 'required': False, 'aliases': ['Telephone', 'Téléphone', 'Client telephone']}),
    ('Code produit', {'label': 'Code produit', 'required': False, 'aliases': ['Code produit', 'Produit code', 'SKU']}),
    ('Produit', {'label': 'Produit', 'required': False, 'aliases': ['Produit', 'Nom produit', 'Article']}),
    ('Quantité', {'label': 'Quantité', 'required': True, 'aliases': ['Quantite', 'Quantité', 'Qté', 'Qte']}),
    ('Prix unitaire', {'label': 'Prix unitaire', 'required': True, 'aliases': ['Prix unitaire', 'PU', 'Prix']}),
    ('Montant payé', {'label': 'Montant payé', 'required': False, 'aliases': ['Montant paye', 'Montant payé', 'Encaisse', 'Encaissé']}),
    ('Canal', {'label': 'Canal', 'required': False, 'aliases': ['Canal', 'Canal vente']}),
    ('Type client', {'label': 'Type client', 'required': False, 'aliases': ['Type client', 'Segment client']}),
    ('Région', {'label': 'Région', 'required': False, 'aliases': ['Region', 'Région', 'Zone']}),
    ('Retour effectué', {'label': 'Retour effectué', 'required': False, 'aliases': ['Retour effectue', 'Retour effectué', 'Retour']}),
    ('Montant retour', {'label': 'Montant retour', 'required': False, 'aliases': ['Montant retour', 'Retour montant', 'Remboursement']}),
    ('Commentaire', {'label': 'Commentaire', 'required': False, 'aliases': ['Commentaire', 'Observation', 'Note']}),
])

COMMAND_IMPORT_FIELD_SPECS = OrderedDict([
    ('Nr.', {'label': 'Nr.', 'required': True, 'aliases': ['Nr.', 'Nr', 'Numero', 'Numéro']}),
    ('Date CDE', {'label': 'Date CDE', 'required': True, 'aliases': ['Date CDE', 'Date commande', 'Date']}),
    ('Entité', {'label': 'Entité', 'required': False, 'aliases': ['Entité', 'Entite']}),
    ('Demandeur', {'label': 'Demandeur', 'required': False, 'aliases': ['Demandeur']}),
    ('Service Demandeur', {'label': 'Service Demandeur', 'required': False, 'aliases': ['Service Demandeur', 'Service demandeur']}),
    ('Acheteur', {'label': 'Acheteur', 'required': False, 'aliases': ['Acheteur']}),
    ('Fournisseur', {'label': 'Fournisseur', 'required': False, 'aliases': ['Fournisseur', 'Supplier']}),
    ('Affaire/Commande', {'label': 'Affaire/Commande', 'required': False, 'aliases': ['Affaire/Commande', 'Affaire', 'Commande', 'Objet']}),
    ('N° Bon commande', {'label': 'N° Bon commande', 'required': False, 'aliases': ['N° Bon commande', 'No Bon commande', 'Bon commande', 'BC']}),
    ('Date Livraison', {'label': 'Date Livraison', 'required': False, 'aliases': ['Date Livraison', 'Livraison']}),
    ('Date Réception', {'label': 'Date Réception', 'required': False, 'aliases': ['Date Réception', 'Date reception', 'Réception']}),
    ('N° Bon Livraison', {'label': 'N° Bon Livraison', 'required': False, 'aliases': ['N° Bon Livraison', 'Bon Livraison', 'BL']}),
    ('Facture', {'label': 'Facture', 'required': False, 'aliases': ['Facture']}),
    ('Montant', {'label': 'Montant', 'required': True, 'aliases': ['Montant', 'Montant TTC', 'Montant HT']}),
    ('Avance', {'label': 'Avance', 'required': False, 'aliases': ['Avance', 'Acompte']}),
    ('Prix Référence Marché', {'label': 'Prix Référence Marché', 'required': False, 'aliases': ['Prix Référence Marché', 'Prix Reference Marche', 'Prix marché', 'Prix marche']}),
    ('Commande Conforme', {'label': 'Commande Conforme', 'required': False, 'aliases': ['Commande Conforme', 'Conforme']}),
    ('Rupture Fournisseur', {'label': 'Rupture Fournisseur', 'required': False, 'aliases': ['Rupture Fournisseur', 'Rupture']}),
    ('Note Performance Fournisseur', {'label': 'Note Performance Fournisseur', 'required': False, 'aliases': ['Note Performance Fournisseur', 'Note performance', 'Note fournisseur']}),
    ('Note SAV Fournisseur', {'label': 'Note SAV Fournisseur', 'required': False, 'aliases': ['Note SAV Fournisseur', 'Note SAV']}),
    ('Date Paiement', {'label': 'Date Paiement', 'required': False, 'aliases': ['Date Paiement', 'Paiement']}),
    ('Commentaire', {'label': 'Commentaire', 'required': False, 'aliases': ['Commentaire', 'Observation', 'Note']}),
])

STOCK_IMPORT_FIELD_SPECS = OrderedDict([
    ('Code produit', {'label': 'Code produit', 'required': False, 'aliases': ['Code produit', 'Produit code', 'SKU', 'Code']}),
    ('Produit', {'label': 'Produit', 'required': False, 'aliases': ['Produit', 'Nom produit', 'Article', 'Libelle', 'Libellé']}),
    ('Famille', {'label': 'Famille', 'required': False, 'aliases': ['Famille']}),
    ('Catégorie', {'label': 'Catégorie', 'required': False, 'aliases': ['Categorie', 'Catégorie', 'Category']}),
    ('Description', {'label': 'Description', 'required': False, 'aliases': ['Description', 'Details', 'Détails']}),
    ('Unité', {'label': 'Unité', 'required': False, 'aliases': ['Unite', 'Unité', 'UM', 'Unit']}),
    ('Prix unitaire', {'label': 'Prix unitaire', 'required': False, 'aliases': ['Prix unitaire', 'PU', 'Prix']}),
    ('Stock actuel', {'label': 'Stock actuel', 'required': True, 'aliases': ['Stock actuel', 'Stock', 'Quantite stock', 'Quantité stock', 'Qte stock', 'Quantité']}),
    ('Stock minimum', {'label': 'Stock minimum', 'required': False, 'aliases': ['Stock minimum', 'Seuil stock', 'Seuil']}),
    ('Actif', {'label': 'Actif', 'required': False, 'aliases': ['Actif', 'Active', 'Disponible']}),
    ('Motif', {'label': 'Motif', 'required': False, 'aliases': ['Motif', 'Raison', 'Commentaire', 'Observation', 'Note']}),
])


def build_import_field_choices(field_specs):
    choices = [{'value': IGNORE_IMPORT_FIELD, 'label': 'Supprimer cette colonne'}]
    choices.extend(
        {'value': field_name, 'label': field_spec['label']}
        for field_name, field_spec in field_specs.items()
    )
    return choices


def get_sales_import_field_choices():
    return build_import_field_choices(SALES_IMPORT_FIELD_SPECS)


def get_command_import_field_choices():
    return build_import_field_choices(COMMAND_IMPORT_FIELD_SPECS)


def get_stock_import_field_choices():
    return build_import_field_choices(STOCK_IMPORT_FIELD_SPECS)


def is_empty_import_cell(value):
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ''
    return pd.isna(value)


def get_sales_import_preview_dir():
    preview_dir = os.path.join(app.config['UPLOAD_FOLDER'], 'sales_import_previews')
    os.makedirs(preview_dir, exist_ok=True)
    return preview_dir


def cleanup_expired_import_preview_files():
    """Supprime les aperçus d'import temporaires trop anciens."""
    preview_dir = get_sales_import_preview_dir()
    ttl_hours = max(int(app.config.get('IMPORT_PREVIEW_TTL_HOURS', 24) or 24), 1)
    cutoff_timestamp = time.time() - ttl_hours * 3600

    for file_name in os.listdir(preview_dir):
        metadata_path = os.path.join(preview_dir, file_name)
        if not file_name.endswith('.json') or not os.path.isfile(metadata_path):
            continue

        try:
            if os.path.getmtime(metadata_path) >= cutoff_timestamp:
                continue

            with open(metadata_path, 'r', encoding='utf-8') as handle:
                metadata = json.load(handle)
            source_path = metadata.get('file_path')
            if source_path and os.path.exists(source_path):
                os.remove(source_path)
            os.remove(metadata_path)
        except Exception as exc:
            app.logger.warning('Preview import cleanup skipped for %s: %s', metadata_path, exc)


def save_sales_import_preview_file(uploaded_file):
    extension = os.path.splitext(uploaded_file.filename or '')[1].lower()
    if extension not in {'.xlsx', '.xls', '.csv'}:
        raise ValueError('Format non supporté. Utilisez Excel (.xlsx, .xls) ou CSV')

    cleanup_expired_import_preview_files()
    preview_dir = get_sales_import_preview_dir()
    token = uuid4().hex
    saved_path = os.path.join(preview_dir, f'{token}{extension}')
    uploaded_file.save(saved_path)

    metadata = {
        'file_path': saved_path,
        'extension': extension,
        'original_filename': secure_filename(uploaded_file.filename or '') or os.path.basename(saved_path),
    }
    metadata_path = os.path.join(preview_dir, f'{token}.json')
    with open(metadata_path, 'w', encoding='utf-8') as handle:
        json.dump(metadata, handle)
    return token


def load_sales_import_preview_file(token):
    if not token or not re.fullmatch(r'[a-f0-9]{32}', token):
        raise ValueError('Token d’aperçu invalide')

    metadata_path = os.path.join(get_sales_import_preview_dir(), f'{token}.json')
    if not os.path.exists(metadata_path):
        raise ValueError('Aperçu introuvable. Réimportez le fichier source.')

    with open(metadata_path, 'r', encoding='utf-8') as handle:
        metadata = json.load(handle)

    file_path = metadata.get('file_path')
    if not file_path or not os.path.exists(file_path):
        raise ValueError('Fichier source introuvable. Réimportez le fichier.')

    return metadata


def delete_sales_import_preview_file(token):
    try:
        metadata = load_sales_import_preview_file(token)
    except ValueError:
        return

    for path in (metadata.get('file_path'), os.path.join(get_sales_import_preview_dir(), f'{token}.json')):
        if path and os.path.exists(path):
            os.remove(path)


def read_sales_import_raw_dataframe(file_path):
    extension = os.path.splitext(file_path)[1].lower()
    if extension == '.csv':
        dataframe = pd.read_csv(file_path, header=None, dtype=object, keep_default_na=False)
    else:
        dataframe = pd.read_excel(file_path, header=None, dtype=object)
    return dataframe.fillna('')


def format_import_preview_cell(value):
    if is_empty_import_cell(value):
        return ''
    if isinstance(value, (datetime, date)):
        return value.strftime('%Y-%m-%d')
    return str(value).strip()


def build_import_column_map(columns):
    return {normalize_import_column_name(column): column for column in columns}


def guess_import_header_row(raw_dataframe, field_specs, max_scan_rows=20):
    if raw_dataframe.empty:
        return 1

    normalized_aliases = {
        field_name: {
            normalize_import_column_name(alias)
            for alias in field_spec['aliases']
        }
        for field_name, field_spec in field_specs.items()
    }

    best_row = 1
    best_score = -1
    row_limit = min(len(raw_dataframe), max_scan_rows)
    for row_number in range(1, row_limit + 1):
        row_values = raw_dataframe.iloc[row_number - 1].tolist()
        score = 0
        for cell_value in row_values:
            normalized_cell = normalize_import_column_name(cell_value)
            if not normalized_cell:
                continue
            for aliases in normalized_aliases.values():
                if normalized_cell in aliases:
                    score += 3
                    break
                if any(normalized_cell in alias or alias in normalized_cell for alias in aliases):
                    score += 1
                    break
        if score > best_score:
            best_row = row_number
            best_score = score

    if best_score <= 0:
        for row_number in range(1, len(raw_dataframe) + 1):
            if any(not is_empty_import_cell(value) for value in raw_dataframe.iloc[row_number - 1].tolist()):
                return row_number
    return best_row


def guess_sales_header_row(raw_dataframe, max_scan_rows=20):
    return guess_import_header_row(raw_dataframe, SALES_IMPORT_FIELD_SPECS, max_scan_rows=max_scan_rows)


def suggest_import_field_mapping(header_value, field_specs):
    normalized_header = normalize_import_column_name(header_value)
    if not normalized_header or normalized_header.startswith('unnamed'):
        return IGNORE_IMPORT_FIELD

    best_field = IGNORE_IMPORT_FIELD
    best_score = 0
    for field_name, field_spec in field_specs.items():
        aliases = [normalize_import_column_name(alias) for alias in field_spec['aliases']]
        for alias in aliases:
            if not alias:
                continue
            if normalized_header == alias:
                return field_name
            if normalized_header in alias or alias in normalized_header:
                score = min(len(alias), len(normalized_header))
                if score > best_score:
                    best_field = field_name
                    best_score = score
    return best_field


def suggest_sales_field_mapping(header_value):
    return suggest_import_field_mapping(header_value, SALES_IMPORT_FIELD_SPECS)


def parse_import_row_numbers(value):
    if not value:
        return []
    row_numbers = []
    for token in re.split(r'[\s,;]+', str(value).strip()):
        if not token:
            continue
        if not token.isdigit():
            raise ValueError(f'Numéro de ligne invalide: {token}')
        row_number = int(token)
        if row_number <= 0:
            raise ValueError(f'Numéro de ligne invalide: {token}')
        row_numbers.append(row_number)
    return sorted(set(row_numbers))


def collect_import_grid_edits(form_data):
    edited_cells = {}
    for key, value in form_data.items():
        match = re.fullmatch(r'raw_cell_(\d+)_(\d+)', key)
        if not match:
            continue
        row_number = int(match.group(1))
        column_index = int(match.group(2))
        edited_cells[(row_number, column_index)] = value
    return edited_cells


def apply_import_grid_edits(raw_dataframe, edited_cells):
    if not edited_cells:
        return raw_dataframe

    edited_dataframe = raw_dataframe.copy()
    row_count = len(edited_dataframe.index)
    column_count = len(edited_dataframe.columns)

    for (row_number, column_index), value in edited_cells.items():
        if 1 <= row_number <= row_count and 0 <= column_index < column_count:
            edited_dataframe.iat[row_number - 1, column_index] = value

    return edited_dataframe


def get_import_field_label(field_specs, field_name):
    field_spec = field_specs.get(field_name)
    if field_spec:
        return field_spec['label']
    return field_name


def append_unique(items, value):
    if value and value not in items:
        items.append(value)


def get_import_text_value(value):
    if value is None or pd.isna(value):
        return ''
    return str(value).strip()


def parse_preview_numeric(value, label):
    text_value = get_import_text_value(value)
    if not text_value:
        return None

    normalized = text_value.replace(' ', '').replace('\u202f', '').replace('\xa0', '')
    if ',' in normalized and '.' in normalized:
        normalized = normalized.replace('.', '').replace(',', '.')
    elif ',' in normalized:
        normalized = normalized.replace(',', '.')

    if not re.fullmatch(r'-?\d+(?:\.\d+)?', normalized):
        raise ValueError(f'{label} doit être numérique')
    return float(normalized)


def make_preview_issue(field, message, suggestion=None):
    return {
        'field': field,
        'message': message,
        'suggestion': suggestion,
    }


def format_preview_issue(issue):
    if issue.get('suggestion'):
        return f"{issue['message']} Suggestion: {issue['suggestion']}"
    return issue['message']


def build_import_column_mapping(raw_dataframe, header_row, field_specs, submitted_mapping=None):
    if header_row <= 0 or header_row > len(raw_dataframe.index):
        raise ValueError('La ligne d’en-tête sélectionnée est invalide')

    header_values = raw_dataframe.iloc[header_row - 1].tolist()
    mapping = {}
    columns = []

    for column_index, header_value in enumerate(header_values):
        sample_values = []
        for row_number in range(header_row + 1, min(len(raw_dataframe.index), header_row + 5) + 1):
            sample = format_import_preview_cell(raw_dataframe.iloc[row_number - 1, column_index])
            if sample:
                sample_values.append(sample)

        raw_header = format_import_preview_cell(header_value)
        display_header = raw_header or f'Colonne {column_index + 1}'
        suggested_field = suggest_import_field_mapping(raw_header, field_specs)
        selected_field = (submitted_mapping or {}).get(str(column_index), suggested_field)
        if selected_field not in {choice['value'] for choice in build_import_field_choices(field_specs)}:
            selected_field = suggested_field

        mapping[str(column_index)] = selected_field
        columns.append({
            'index': column_index,
            'header': display_header,
            'raw_header': raw_header,
            'sample_values': sample_values[:3],
            'selected_field': selected_field,
            'suggested_field': suggested_field,
            'suggested_label': 'Supprimer cette colonne'
                if suggested_field == IGNORE_IMPORT_FIELD
                else get_import_field_label(field_specs, suggested_field),
            'recommended_delete': selected_field == IGNORE_IMPORT_FIELD,
        })

    return mapping, columns


def build_sales_import_column_mapping(raw_dataframe, header_row, submitted_mapping=None):
    return build_import_column_mapping(
        raw_dataframe,
        header_row,
        SALES_IMPORT_FIELD_SPECS,
        submitted_mapping=submitted_mapping,
    )


def build_sales_transformed_dataframe(raw_dataframe, header_row, rows_to_delete, column_mapping):
    if raw_dataframe.empty:
        return pd.DataFrame()
    if header_row <= 0 or header_row > len(raw_dataframe.index):
        raise ValueError('La ligne d’en-tête sélectionnée est invalide')

    indexed_df = raw_dataframe.copy()
    indexed_df.index = range(1, len(indexed_df.index) + 1)

    effective_rows_to_delete = {row for row in rows_to_delete if row in indexed_df.index and row != header_row}
    data_frame = indexed_df.drop(index=sorted(effective_rows_to_delete), errors='ignore')
    data_frame = data_frame[data_frame.index > header_row].copy()

    transformed = pd.DataFrame(index=data_frame.index)
    transformed['__source_row_number__'] = data_frame.index
    for column_index in data_frame.columns:
        target_field = column_mapping.get(str(column_index), IGNORE_IMPORT_FIELD)
        if target_field == IGNORE_IMPORT_FIELD:
            continue

        source_series = data_frame[column_index].apply(format_import_preview_cell)
        if target_field not in transformed.columns:
            transformed[target_field] = source_series
        else:
            existing_series = transformed[target_field].fillna('')
            mask = existing_series.astype(str).str.strip() == ''
            transformed.loc[mask, target_field] = source_series.loc[mask]

    data_columns = [column for column in transformed.columns if column != '__source_row_number__']
    if not data_columns:
        return transformed

    transformed = transformed.replace('', pd.NA)
    transformed = transformed.dropna(how='all', subset=data_columns).fillna('')
    return transformed.reset_index(drop=True)


def get_import_mapping_errors(transformed_dataframe, field_specs, required_one_of=None):
    errors = []
    for field_name, field_spec in field_specs.items():
        if not field_spec['required']:
            continue
        if field_name not in transformed_dataframe.columns:
            errors.append(f'Colonne obligatoire non mappée: {field_spec["label"]}')
            continue
        if transformed_dataframe[field_name].astype(str).str.strip().eq('').all():
            errors.append(f'Colonne vide après transformation: {field_spec["label"]}')

    if required_one_of and not any(field_name in transformed_dataframe.columns for field_name in required_one_of):
        errors.append('Mappez au moins ' + ' ou '.join(f'"{field_name}"' for field_name in required_one_of))

    return errors


def get_sales_import_mapping_errors(transformed_dataframe):
    return get_import_mapping_errors(
        transformed_dataframe,
        SALES_IMPORT_FIELD_SPECS,
        required_one_of=['Code produit', 'Produit'],
    )

def build_import_field_to_columns(column_mapping):
    field_to_columns = defaultdict(list)
    for column_index, field_name in column_mapping.items():
        if field_name == IGNORE_IMPORT_FIELD:
            continue
        field_to_columns[field_name].append(int(column_index))
    return dict(field_to_columns)


def build_product_preview_lookup():
    products = Produit.query.with_entities(
        Produit.id,
        Produit.code,
        Produit.nom,
        Produit.actif,
    ).all()
    lookup = {'by_code': {}, 'by_name': {}}
    for product_id, code, nom, actif in products:
        product_info = {
            'id': product_id,
            'code': code,
            'nom': nom,
            'actif': bool(actif),
        }
        if code:
            lookup['by_code'][str(code).strip().lower()] = product_info
        if nom:
            lookup['by_name'][str(nom).strip().lower()] = product_info
    return lookup


def build_command_preview_lookup():
    existing_bons = {
        str(value).strip().lower()
        for (value,) in db.session.query(Commande.bon_commande)
        .filter(Commande.bon_commande.isnot(None), Commande.bon_commande != '')
        .all()
    }
    existing_factures = {
        str(value).strip().lower()
        for (value,) in db.session.query(Commande.facture)
        .filter(Commande.facture.isnot(None), Commande.facture != '')
        .all()
    }
    return {
        'existing_bons': existing_bons,
        'existing_factures': existing_factures,
    }


def validate_sales_preview_row(row, validator_context=None):
    issues = []
    validator_context = validator_context or {}
    product_lookup = validator_context.get('products', {})
    by_code = product_lookup.get('by_code', {})
    by_name = product_lookup.get('by_name', {})

    reference = get_import_text_value(row.get('Reference'))
    if not reference:
        issues.append(make_preview_issue('Reference', 'Référence manquante', 'Renseigner la référence de vente'))

    sale_date = get_import_text_value(row.get('Date vente'))
    if not sale_date:
        issues.append(make_preview_issue('Date vente', 'Date de vente manquante', 'Renseigner la date de vente'))
    elif parser_date_import(sale_date) is None:
        issues.append(make_preview_issue('Date vente', 'Date de vente invalide', 'Utiliser un format de date reconnu'))

    client = get_import_text_value(row.get('Client'))
    if not client:
        issues.append(make_preview_issue('Client', 'Client manquant', 'Renseigner le nom du client'))

    product_code = get_import_text_value(row.get('Code produit'))
    product_name = get_import_text_value(row.get('Produit'))
    if not product_code and not product_name:
        issues.append(make_preview_issue('Produit', 'Produit non identifié', 'Renseigner un code produit ou un nom produit'))
    else:
        product_from_code = by_code.get(product_code.lower()) if product_code else None
        product_from_name = by_name.get(product_name.lower()) if product_name else None

        if product_code and not product_from_code and product_from_name:
            issues.append(make_preview_issue('Code produit', 'Code produit inconnu', 'Corriger le code ou laisser le nom produit comme référence'))
        elif product_code and not product_from_code and not product_name:
            issues.append(make_preview_issue('Code produit', 'Code produit inconnu', 'Corriger le code produit'))

        if product_name and not product_from_name and product_from_code:
            issues.append(make_preview_issue('Produit', 'Nom produit non reconnu', 'Corriger le libellé produit'))
        elif product_name and not product_from_name and not product_code:
            issues.append(make_preview_issue('Produit', 'Produit introuvable', 'Corriger le libellé produit'))

        if product_from_code and product_from_name and product_from_code['id'] != product_from_name['id']:
            issues.append(make_preview_issue('Produit', 'Code produit et nom produit incohérents', 'Aligner le code et le nom sur le même produit'))

        resolved_product = product_from_code or product_from_name
        if resolved_product and not resolved_product['actif']:
            issues.append(make_preview_issue('Produit', 'Produit inactif', 'Réactiver le produit ou choisir un autre produit'))

    quantity_value = get_import_text_value(row.get('Quantité'))
    if not quantity_value:
        issues.append(make_preview_issue('Quantité', 'Quantité manquante', 'Renseigner une quantité supérieure à zéro'))
    else:
        try:
            quantity = parse_preview_numeric(quantity_value, 'Quantité')
            if quantity is None or quantity <= 0:
                raise ValueError
        except ValueError:
            issues.append(make_preview_issue('Quantité', 'Quantité invalide', 'Utiliser un nombre strictement supérieur à zéro'))

    unit_price_value = get_import_text_value(row.get('Prix unitaire'))
    if not unit_price_value:
        issues.append(make_preview_issue('Prix unitaire', 'Prix unitaire manquant', 'Renseigner un prix unitaire'))
    else:
        try:
            unit_price = parse_preview_numeric(unit_price_value, 'Prix unitaire')
            if unit_price is None or unit_price < 0:
                raise ValueError
        except ValueError:
            issues.append(make_preview_issue('Prix unitaire', 'Prix unitaire invalide', 'Utiliser un nombre positif ou nul'))

    for field_name, label in [('Montant payé', 'Montant payé'), ('Montant retour', 'Montant retour')]:
        raw_value = get_import_text_value(row.get(field_name))
        if not raw_value:
            continue
        try:
            parsed_value = parse_preview_numeric(raw_value, label)
            if parsed_value is not None and parsed_value < 0:
                raise ValueError
        except ValueError:
            issues.append(make_preview_issue(field_name, f'{label} invalide', 'Utiliser un nombre positif ou nul'))

    return issues


def validate_command_preview_row(row, validator_context=None):
    issues = []
    validator_context = validator_context or {}
    existing_bons = validator_context.get('existing_bons', set())
    existing_factures = validator_context.get('existing_factures', set())

    nr_value = get_import_text_value(row.get('Nr.'))
    if not nr_value:
        issues.append(make_preview_issue('Nr.', 'Numéro de commande manquant', 'Renseigner le numéro de commande'))
    else:
        try:
            int(float(nr_value))
        except (TypeError, ValueError):
            issues.append(make_preview_issue('Nr.', 'Numéro de commande invalide', 'Utiliser un entier'))

    order_date = get_import_text_value(row.get('Date CDE'))
    if not order_date:
        issues.append(make_preview_issue('Date CDE', 'Date de commande manquante', 'Renseigner la date de commande'))
    elif parser_date_import(order_date) is None:
        issues.append(make_preview_issue('Date CDE', 'Date de commande invalide', 'Utiliser un format de date reconnu'))

    for optional_date_field, label in [('Date Livraison', 'Date livraison'), ('Date Réception', 'Date réception'), ('Date Paiement', 'Date paiement')]:
        optional_date = get_import_text_value(row.get(optional_date_field))
        if optional_date and parser_date_import(optional_date) is None:
            issues.append(make_preview_issue(optional_date_field, f'{label} invalide', 'Corriger le format de date'))

    montant_value = get_import_text_value(row.get('Montant'))
    montant = None
    if not montant_value:
        issues.append(make_preview_issue('Montant', 'Montant manquant', 'Renseigner le montant de la commande'))
    else:
        try:
            montant = parse_preview_numeric(montant_value, 'Montant')
            if montant is None or montant < 0:
                raise ValueError
        except ValueError:
            issues.append(make_preview_issue('Montant', 'Montant invalide', 'Utiliser un nombre positif ou nul'))

    avance_value = get_import_text_value(row.get('Avance'))
    if avance_value:
        try:
            avance = parse_preview_numeric(avance_value, 'Avance')
            if avance is not None and avance < 0:
                raise ValueError
            if montant is not None and avance is not None and avance > montant:
                issues.append(make_preview_issue('Avance', 'Avance supérieure au montant', 'Réduire l’avance ou corriger le montant'))
        except ValueError:
            issues.append(make_preview_issue('Avance', 'Avance invalide', 'Utiliser un nombre positif ou nul'))

    bon_commande = get_import_text_value(row.get('N° Bon commande'))
    if bon_commande and bon_commande.lower() in existing_bons:
        issues.append(make_preview_issue('N° Bon commande', 'Bon de commande déjà existant', 'Corriger le numéro ou supprimer la ligne'))

    facture = get_import_text_value(row.get('Facture'))
    if facture and facture.lower() in existing_factures:
        issues.append(make_preview_issue('Facture', 'Facture déjà existante', 'Corriger la facture ou supprimer la ligne'))

    for note_field, label in [('Note Performance Fournisseur', 'Note performance'), ('Note SAV Fournisseur', 'Note SAV')]:
        note_value = get_import_text_value(row.get(note_field))
        if not note_value:
            continue
        try:
            note = parse_preview_numeric(note_value, label)
            if note is None or note < 0 or note > 5:
                raise ValueError
        except ValueError:
            issues.append(make_preview_issue(note_field, f'{label} invalide', 'Utiliser une note entre 0 et 5'))

    return issues


def validate_stock_preview_row(row, validator_context=None):
    issues = []
    validator_context = validator_context or {}
    product_lookup = validator_context.get('products', {})
    by_code = product_lookup.get('by_code', {})
    by_name = product_lookup.get('by_name', {})

    product_code = get_import_text_value(row.get('Code produit'))
    product_name = get_import_text_value(row.get('Produit'))
    if not product_code and not product_name:
        issues.append(make_preview_issue('Produit', 'Produit non identifié', 'Renseigner un code produit ou un nom produit'))
    else:
        product_from_code = by_code.get(product_code.lower()) if product_code else None
        product_from_name = by_name.get(product_name.lower()) if product_name else None

        if product_code and product_name and product_from_code and product_from_name and product_from_code['id'] != product_from_name['id']:
            issues.append(make_preview_issue('Produit', 'Code produit et nom produit incohérents', 'Aligner le code et le nom sur le même produit'))

        if product_code and not product_from_code and not product_name:
            issues.append(make_preview_issue('Produit', 'Produit inconnu', 'Ajouter le nom du produit pour permettre sa création'))

    stock_value = get_import_text_value(row.get('Stock actuel'))
    if not stock_value:
        issues.append(make_preview_issue('Stock actuel', 'Stock actuel manquant', 'Renseigner le stock courant'))
    else:
        try:
            stock = parse_preview_numeric(stock_value, 'Stock actuel')
            if stock is None or stock < 0:
                raise ValueError
        except ValueError:
            issues.append(make_preview_issue('Stock actuel', 'Stock actuel invalide', 'Utiliser un nombre positif ou nul'))

    for field_name, label in [('Stock minimum', 'Stock minimum'), ('Prix unitaire', 'Prix unitaire')]:
        raw_value = get_import_text_value(row.get(field_name))
        if not raw_value:
            continue
        try:
            parsed_value = parse_preview_numeric(raw_value, label)
            if parsed_value is not None and parsed_value < 0:
                raise ValueError
        except ValueError:
            issues.append(make_preview_issue(field_name, f'{label} invalide', 'Utiliser un nombre positif ou nul'))

    famille = get_import_text_value(row.get('Famille')) or None
    categorie = get_import_text_value(row.get('Catégorie')) or None
    if famille or categorie:
        try:
            normalize_product_taxonomy(famille, categorie)
        except ValueError as exc:
            issues.append(make_preview_issue('Catégorie', str(exc), 'Corriger la famille ou la catégorie'))

    return issues


def analyze_import_preview_rows(transformed_dataframe, field_specs, column_mapping, required_one_of=None, row_validator=None, validator_context=None):
    field_to_columns = build_import_field_to_columns(column_mapping)
    raw_row_issue_map = defaultdict(list)
    raw_cell_issue_map = defaultdict(list)
    transformed_row_issue_map = defaultdict(list)
    transformed_cell_issue_map = defaultdict(list)
    analysis_entries = []

    for _, row in transformed_dataframe.iterrows():
        source_row_number = row.get('__source_row_number__')
        source_row_number = int(source_row_number) if source_row_number not in (None, '') else None
        row_issues = []

        for field_name, field_spec in field_specs.items():
            if not field_spec['required']:
                continue
            if not get_import_text_value(row.get(field_name)):
                row_issues.append(make_preview_issue(field_name, f'{field_spec["label"]} manquante', f'Renseigner {field_spec["label"].lower()}'))

        if required_one_of and not any(get_import_text_value(row.get(field_name)) for field_name in required_one_of):
            required_labels = [get_import_field_label(field_specs, field_name) for field_name in required_one_of]
            row_issues.append(make_preview_issue(
                required_one_of[0],
                'Aucune colonne d’identification renseignée',
                'Renseigner ' + ' ou '.join(required_labels),
            ))

        if row_validator:
            row_issues.extend(row_validator(row, validator_context))

        if not row_issues or source_row_number is None:
            continue

        for issue in row_issues:
            issue_text = format_preview_issue(issue)
            append_unique(transformed_row_issue_map[source_row_number], issue_text)

            field_name = issue.get('field')
            column_indices = field_to_columns.get(field_name, [])
            for column_index in column_indices:
                append_unique(raw_cell_issue_map[(source_row_number, column_index)], issue_text)
                append_unique(transformed_cell_issue_map[(source_row_number, field_name)], issue_text)

            append_unique(raw_row_issue_map[source_row_number], issue_text)
            analysis_entries.append({
                'row_number': source_row_number,
                'column_label': f'Col {column_indices[0] + 1}' if column_indices else '',
                'field_label': get_import_field_label(field_specs, field_name) if field_name else 'Ligne',
                'message': issue['message'],
                'suggestion': issue.get('suggestion') or '',
            })

    return {
        'raw_row_issue_map': dict(raw_row_issue_map),
        'raw_cell_issue_map': dict(raw_cell_issue_map),
        'transformed_row_issue_map': dict(transformed_row_issue_map),
        'transformed_cell_issue_map': dict(transformed_cell_issue_map),
        'analysis_entries': analysis_entries,
    }


def build_import_preview_context(token, field_specs, header_row=None, rows_to_delete=None, submitted_mapping=None, required_one_of=None, edited_cells=None, row_validator=None, validator_context=None):
    metadata = load_sales_import_preview_file(token)
    raw_dataframe = read_sales_import_raw_dataframe(metadata['file_path'])
    raw_dataframe = apply_import_grid_edits(raw_dataframe, edited_cells)
    if raw_dataframe.empty:
        raise ValueError('Le fichier est vide')

    recommended_header_row = guess_import_header_row(raw_dataframe, field_specs)
    if header_row is None:
        header_row = recommended_header_row

    empty_rows = [
        row_number
        for row_number in range(1, len(raw_dataframe.index) + 1)
        if all(is_empty_import_cell(value) for value in raw_dataframe.iloc[row_number - 1].tolist())
    ]
    recommended_rows_to_delete = sorted(set(empty_rows + list(range(1, recommended_header_row))))
    if rows_to_delete is None:
        rows_to_delete = recommended_rows_to_delete

    column_mapping, columns = build_import_column_mapping(
        raw_dataframe,
        header_row,
        field_specs,
        submitted_mapping=submitted_mapping,
    )
    transformed_dataframe = build_sales_transformed_dataframe(raw_dataframe, header_row, rows_to_delete, column_mapping)
    mapping_errors = get_import_mapping_errors(
        transformed_dataframe,
        field_specs,
        required_one_of=required_one_of,
    )
    analysis_context = analyze_import_preview_rows(
        transformed_dataframe,
        field_specs,
        column_mapping,
        required_one_of=required_one_of,
        row_validator=row_validator,
        validator_context=validator_context,
    )

    max_preview_rows = 25
    max_preview_columns = min(len(raw_dataframe.columns), 15)
    raw_preview_rows = []
    for row_number in range(1, min(len(raw_dataframe.index), max_preview_rows) + 1):
        row_cells = []
        row_issue_messages = analysis_context['raw_row_issue_map'].get(row_number, [])
        is_recommended_deleted = row_number in recommended_rows_to_delete
        raw_preview_rows.append({
            'row_number': row_number,
            'cells': row_cells,
            'is_header': row_number == header_row,
            'is_deleted': row_number in rows_to_delete,
            'is_recommended_deleted': is_recommended_deleted,
            'issues': row_issue_messages,
        })
        for column_index in range(max_preview_columns):
            header_hint = ''
            if row_number == header_row and column_index < len(columns):
                header_hint = columns[column_index]['suggested_label']
                if columns[column_index]['suggested_field'] == IGNORE_IMPORT_FIELD:
                    header_hint = 'Supprimer ou ignorer cette colonne'
                else:
                    header_hint = f'Mapper vers {header_hint}'
            row_cells.append({
                'value': format_import_preview_cell(raw_dataframe.iloc[row_number - 1, column_index]),
                'column_index': column_index,
                'input_name': f'raw_cell_{row_number}_{column_index}',
                'issues': analysis_context['raw_cell_issue_map'].get((row_number, column_index), []),
                'header_hint': header_hint,
            })

    transformed_headers = [
        column
        for column in transformed_dataframe.columns
        if column != '__source_row_number__'
    ]
    transformed_rows = []
    for _, transformed_row in transformed_dataframe.head(15).iterrows():
        source_row_number = int(transformed_row.get('__source_row_number__'))
        transformed_rows.append({
            'source_row_number': source_row_number,
            'issues': analysis_context['transformed_row_issue_map'].get(source_row_number, []),
            'cells': [
                {
                    'field': header,
                    'value': format_import_preview_cell(transformed_row.get(header)),
                    'issues': analysis_context['transformed_cell_issue_map'].get((source_row_number, header), []),
                }
                for header in transformed_headers
            ],
        })

    preview_context = {
        'token': token,
        'original_filename': metadata.get('original_filename') or os.path.basename(metadata['file_path']),
        'row_count': len(raw_dataframe.index),
        'column_count': len(raw_dataframe.columns),
        'header_row': header_row,
        'recommended_header_row': recommended_header_row,
        'rows_to_delete': sorted(set(rows_to_delete)),
        'rows_to_delete_value': ', '.join(str(row) for row in sorted(set(rows_to_delete))),
        'recommended_rows_to_delete': recommended_rows_to_delete,
        'raw_preview_column_labels': [f'Col {column_index + 1}' for column_index in range(max_preview_columns)],
        'raw_preview_rows': raw_preview_rows,
        'raw_preview_is_truncated': len(raw_dataframe.index) > max_preview_rows or len(raw_dataframe.columns) > max_preview_columns,
        'columns': columns,
        'field_choices': build_import_field_choices(field_specs),
        'transformed_headers': transformed_headers,
        'transformed_rows': transformed_rows,
        'transformed_count': len(transformed_dataframe.index),
        'mapping_errors': mapping_errors,
        'recommended_deleted_columns': [column for column in columns if column['recommended_delete']],
        'analysis_entries': analysis_context['analysis_entries'][:40],
        'analysis_issue_count': len(analysis_context['analysis_entries']),
    }
    return preview_context, transformed_dataframe


def build_sales_import_preview_context(token, header_row=None, rows_to_delete=None, submitted_mapping=None, edited_cells=None):
    return build_import_preview_context(
        token,
        SALES_IMPORT_FIELD_SPECS,
        header_row=header_row,
        rows_to_delete=rows_to_delete,
        submitted_mapping=submitted_mapping,
        required_one_of=['Code produit', 'Produit'],
        edited_cells=edited_cells,
        row_validator=validate_sales_preview_row,
        validator_context={'products': build_product_preview_lookup()},
    )


def build_command_import_preview_context(token, header_row=None, rows_to_delete=None, submitted_mapping=None, edited_cells=None):
    return build_import_preview_context(
        token,
        COMMAND_IMPORT_FIELD_SPECS,
        header_row=header_row,
        rows_to_delete=rows_to_delete,
        submitted_mapping=submitted_mapping,
        edited_cells=edited_cells,
        row_validator=validate_command_preview_row,
        validator_context=build_command_preview_lookup(),
    )


def build_stock_import_preview_context(token, header_row=None, rows_to_delete=None, submitted_mapping=None, edited_cells=None):
    return build_import_preview_context(
        token,
        STOCK_IMPORT_FIELD_SPECS,
        header_row=header_row,
        rows_to_delete=rows_to_delete,
        submitted_mapping=submitted_mapping,
        required_one_of=['Code produit', 'Produit'],
        edited_cells=edited_cells,
        row_validator=validate_stock_preview_row,
        validator_context={'products': build_product_preview_lookup()},
    )


def get_import_row_value(row, column_map, aliases):
    for alias in aliases:
        actual_column = column_map.get(normalize_import_column_name(alias))
        if not actual_column:
            continue
        value = row.get(actual_column)
        if value is None or value == '' or pd.isna(value):
            continue
        return value
    return None


def get_import_source_row_number(row, fallback):
    source_row_number = row.get('__source_row_number__')
    if source_row_number in (None, '') or pd.isna(source_row_number):
        return fallback
    try:
        return int(source_row_number)
    except (TypeError, ValueError):
        return fallback


def normalize_sales_channel(value):
    normalized = normalize_import_column_name(value)
    if normalized in {'online', 'en ligne', 'web', 'internet', 'e commerce'}:
        return Vente.CANAL_ONLINE
    return Vente.CANAL_OFFLINE


def normalize_sales_client_type(value):
    normalized = normalize_import_column_name(value)
    if normalized in {'entreprise', 'societe', 'societe cliente'}:
        return Vente.TYPE_CLIENT_ENTREPRISE
    if normalized in {'revendeur', 'grossiste', 'dealer'}:
        return Vente.TYPE_CLIENT_REVENDEUR
    return Vente.TYPE_CLIENT_PARTICULIER


def parse_import_boolean(value):
    if value is None or value == '' or pd.isna(value):
        return False
    if isinstance(value, bool):
        return value
    normalized = normalize_import_column_name(value)
    return normalized in {'1', 'true', 'vrai', 'oui', 'yes'}


def resolve_sales_import_product(product_code=None, product_name=None):
    """Retrouve un produit à partir du code ou du nom."""
    product_code = (str(product_code).strip() if product_code not in (None, '') and not pd.isna(product_code) else None)
    product_name = (str(product_name).strip() if product_name not in (None, '') and not pd.isna(product_name) else None)

    produit = None
    if product_code:
        produit = Produit.query.filter_by(code=product_code).first()
        if produit:
            return produit

    if product_name:
        produit = Produit.query.filter(func.lower(Produit.nom) == product_name.lower()).first()
        if produit:
            return produit

    if product_code:
        raise ValueError(f'Produit introuvable pour le code "{product_code}"')
    raise ValueError(f'Produit introuvable pour le libellé "{product_name or ""}"')


def resolve_stock_import_product(product_code=None, product_name=None):
    """Retrouve un produit existant à partir du code ou du nom."""
    product_code = (str(product_code).strip() if product_code not in (None, '') and not pd.isna(product_code) else None)
    product_name = (str(product_name).strip() if product_name not in (None, '') and not pd.isna(product_name) else None)

    product_by_code = None
    product_by_name = None

    if product_code:
        product_by_code = Produit.query.filter_by(code=product_code).first()

    if product_name:
        product_by_name = Produit.query.filter(func.lower(Produit.nom) == product_name.lower()).first()

    if product_by_code and product_by_name and product_by_code.id != product_by_name.id:
        raise ValueError('Le code produit et le nom désignent deux produits différents')

    return product_by_code or product_by_name


def merge_sales_import_field(group_data, field_name, value, row_number, label):
    """Empêche les incohérences d'en-tête sur une même référence de vente."""
    if value is None:
        return

    current_value = group_data.get(field_name)
    if current_value in (None, ''):
        group_data[field_name] = value
        return

    if current_value != value:
        raise ValueError(f'{label} incohérent sur la ligne {row_number}')


def import_sales_dataframe(dataframe):
    """Importe des ventes depuis un fichier tabulaire externe."""
    if dataframe.empty:
        raise ValueError('Le fichier est vide')

    column_map = build_import_column_map(dataframe.columns)
    required_aliases = {
        'reference': ['Reference', 'Référence', 'Ref'],
        'date_vente': ['Date vente', 'Date'],
        'client_nom': ['Client', 'Client nom', 'Nom client'],
        'quantite': ['Quantite', 'Quantité', 'Qté', 'Qte'],
        'prix_unitaire': ['Prix unitaire', 'PU', 'Prix'],
    }
    missing = []
    for field_name, aliases in required_aliases.items():
        if not any(normalize_import_column_name(alias) in column_map for alias in aliases):
            missing.append(aliases[0])
    if not any(
        normalize_import_column_name(alias) in column_map
        for alias in ['Code produit', 'Produit code', 'SKU', 'Produit', 'Nom produit', 'Article']
    ):
        missing.append('Code produit ou Produit')
    if missing:
        raise ValueError(f'Colonnes manquantes: {", ".join(missing)}')

    grouped_sales = OrderedDict()
    parse_errors = []

    for row_index, (_, row) in enumerate(dataframe.iterrows(), start=2):
        source_row_number = get_import_source_row_number(row, row_index)
        try:
            reference = str(get_import_row_value(row, column_map, ['Reference', 'Référence', 'Ref']) or '').strip()
            if not reference:
                raise ValueError('Référence manquante')

            client_nom = str(get_import_row_value(row, column_map, ['Client', 'Client nom', 'Nom client']) or '').strip()
            if not client_nom:
                raise ValueError('Client manquant')

            product_code = get_import_row_value(row, column_map, ['Code produit', 'Produit code', 'SKU'])
            product_name = get_import_row_value(row, column_map, ['Produit', 'Nom produit', 'Article'])
            produit = resolve_sales_import_product(product_code, product_name)
            if not produit.actif:
                raise ValueError(f'Produit inactif: {produit.nom}')

            quantite = valider_quantite(get_import_row_value(row, column_map, ['Quantite', 'Quantité', 'Qté', 'Qte']))
            prix_unitaire = valider_montant(get_import_row_value(row, column_map, ['Prix unitaire', 'PU', 'Prix']))
            date_vente = parser_date_import(get_import_row_value(row, column_map, ['Date vente', 'Date'])) or date.today()
            client_telephone = get_import_row_value(row, column_map, ['Telephone', 'Téléphone', 'Client telephone'])
            commentaire = get_import_row_value(row, column_map, ['Commentaire', 'Observation', 'Note'])
            montant_paye_raw = get_import_row_value(row, column_map, ['Montant paye', 'Montant payé', 'Encaisse', 'Encaissé'])
            montant_paye = valider_montant(montant_paye_raw) if montant_paye_raw not in (None, '') else None
            montant_retour_raw = get_import_row_value(row, column_map, ['Montant retour', 'Retour montant', 'Remboursement'])
            montant_retour = valider_montant(montant_retour_raw) if montant_retour_raw not in (None, '') else None
            retour_effectue_raw = get_import_row_value(row, column_map, ['Retour effectue', 'Retour effectué', 'Retour'])
            retour_effectue = parse_import_boolean(retour_effectue_raw) if retour_effectue_raw not in (None, '') else None
            if (montant_retour or 0) > 0:
                retour_effectue = True

            group_data = grouped_sales.setdefault(reference, {
                'reference': reference,
                'date_vente': None,
                'client_nom': None,
                'client_telephone': None,
                'canal_vente': None,
                'region': None,
                'type_client': None,
                'montant_paye': None,
                'retour_effectue': None,
                'montant_retour': None,
                'commentaire': None,
                'lines': [],
            })

            merge_sales_import_field(group_data, 'date_vente', date_vente, source_row_number, 'Date de vente')
            merge_sales_import_field(group_data, 'client_nom', client_nom, source_row_number, 'Client')
            merge_sales_import_field(group_data, 'client_telephone', str(client_telephone).strip() if client_telephone else None, source_row_number, 'Téléphone')
            raw_channel = get_import_row_value(row, column_map, ['Canal', 'Canal vente'])
            merge_sales_import_field(
                group_data,
                'canal_vente',
                normalize_sales_channel(raw_channel) if raw_channel not in (None, '') else None,
                source_row_number,
                'Canal',
            )
            merge_sales_import_field(group_data, 'region', str(get_import_row_value(row, column_map, ['Region', 'Région', 'Zone']) or '').strip() or None, source_row_number, 'Région')
            raw_type_client = get_import_row_value(row, column_map, ['Type client', 'Segment client'])
            merge_sales_import_field(
                group_data,
                'type_client',
                normalize_sales_client_type(raw_type_client) if raw_type_client not in (None, '') else None,
                source_row_number,
                'Type client',
            )
            merge_sales_import_field(group_data, 'montant_paye', montant_paye, source_row_number, 'Montant payé')
            merge_sales_import_field(group_data, 'retour_effectue', retour_effectue, source_row_number, 'Indicateur retour')
            merge_sales_import_field(group_data, 'montant_retour', montant_retour, source_row_number, 'Montant retour')
            merge_sales_import_field(group_data, 'commentaire', str(commentaire).strip() if commentaire else None, source_row_number, 'Commentaire')

            group_data['lines'].append({
                'produit_id': produit.id,
                'quantite': quantite,
                'prix_unitaire': prix_unitaire,
            })
        except Exception as exc:
            parse_errors.append(f'Ligne {source_row_number}: {str(exc)}')

    imported_count = 0
    import_errors = list(parse_errors)

    for reference, sale_data in grouped_sales.items():
        try:
            if Vente.query.filter_by(reference=reference).first():
                raise ValueError('Référence déjà existante')
            if not sale_data['lines']:
                raise ValueError('Aucune ligne de vente valide')

            cumulative_quantities = defaultdict(float)
            for line_data in sale_data['lines']:
                produit = Produit.query.get(line_data['produit_id'])
                if not produit:
                    raise ValueError('Un produit importé est introuvable')
                cumulative_quantities[produit.id] += line_data['quantite']
                if (produit.stock_actuel or 0) < cumulative_quantities[produit.id]:
                    raise ValueError(f'Stock insuffisant pour {produit.nom}')

            vente = Vente(
                reference=reference,
                client_nom=sale_data['client_nom'],
                client_telephone=sale_data['client_telephone'],
                date_vente=sale_data['date_vente'] or date.today(),
                canal_vente=sale_data['canal_vente'] or Vente.CANAL_OFFLINE,
                region=sale_data['region'],
                type_client=sale_data['type_client'] or Vente.TYPE_CLIENT_PARTICULIER,
                montant_paye=sale_data['montant_paye'] or 0,
                retour_effectue=bool(sale_data['retour_effectue']),
                montant_retour=sale_data['montant_retour'] or 0,
                commentaire=sale_data['commentaire'],
                created_by=current_user.id,
            )
            db.session.add(vente)
            db.session.flush()

            for line_data in sale_data['lines']:
                produit = Produit.query.get(line_data['produit_id'])
                if not produit:
                    raise ValueError('Un produit importé est introuvable')
                ligne = LigneVente(
                    vente=vente,
                    produit=produit,
                    quantite=line_data['quantite'],
                    prix_unitaire=line_data['prix_unitaire'],
                )
                ligne.calculer_montant()
                db.session.add(ligne)

            db.session.flush()
            vente.recalculer_totaux()

            if (vente.montant_paye or 0) > (vente.montant_total or 0):
                raise ValueError('Le montant payé dépasse le total de la vente')
            if (vente.montant_retour or 0) > (vente.montant_total or 0):
                raise ValueError('Le montant retour dépasse le total de la vente')

            for ligne in vente.lignes:
                appliquer_mouvement_stock(
                    ligne.produit,
                    -ligne.quantite,
                    MouvementStock.TYPE_SORTIE,
                    f'Import vente {vente.reference}',
                    vente=vente,
                )

            enregistrer_log('CREATE', 'vente', vente.id, f'Import vente {vente.reference}')
            db.session.commit()
            imported_count += 1
        except Exception as exc:
            db.session.rollback()
            import_errors.append(f'Vente {reference}: {str(exc)}')

    return imported_count, import_errors


def import_stock_dataframe(dataframe):
    """Importe un état de stock produit depuis un fichier tabulaire transformé."""
    if dataframe.empty:
        raise ValueError('Le fichier est vide')

    if 'Stock actuel' not in dataframe.columns:
        raise ValueError('Colonne manquante: Stock actuel')
    if not any(column in dataframe.columns for column in ['Code produit', 'Produit']):
        raise ValueError('Mappez au moins "Code produit" ou "Produit"')

    imported_count = 0
    import_errors = []

    for row_index, (_, row) in enumerate(dataframe.iterrows(), start=2):
        source_row_number = get_import_source_row_number(row, row_index)
        try:
            product_code = row.get('Code produit')
            product_name = row.get('Produit')
            product_code = str(product_code).strip() if pd.notna(product_code) and str(product_code).strip() else None
            product_name = str(product_name).strip() if pd.notna(product_name) and str(product_name).strip() else None

            if not product_code and not product_name:
                raise ValueError('Produit non identifié')

            produit = resolve_stock_import_product(product_code, product_name)
            is_new_product = produit is None

            if is_new_product and not product_name:
                raise ValueError('Le nom du produit est obligatoire pour créer un nouveau produit')

            famille = str(row.get('Famille')).strip() if pd.notna(row.get('Famille')) and str(row.get('Famille')).strip() else None
            categorie = str(row.get('Catégorie')).strip() if pd.notna(row.get('Catégorie')) and str(row.get('Catégorie')).strip() else None
            famille, categorie, _ = normalize_product_taxonomy(famille, categorie)

            stock_actuel = parser_montant_import(row.get('Stock actuel'))
            if stock_actuel < 0:
                raise ValueError('Le stock actuel ne peut pas être négatif')

            stock_minimum_value = row.get('Stock minimum')
            stock_minimum = None
            if stock_minimum_value is not None and not pd.isna(stock_minimum_value) and str(stock_minimum_value).strip() != '':
                stock_minimum = parser_montant_import(stock_minimum_value)
                if stock_minimum < 0:
                    raise ValueError('Le stock minimum ne peut pas être négatif')

            prix_unitaire_value = row.get('Prix unitaire')
            prix_unitaire = None
            if prix_unitaire_value is not None and not pd.isna(prix_unitaire_value) and str(prix_unitaire_value).strip() != '':
                prix_unitaire = parser_montant_import(prix_unitaire_value)
                if prix_unitaire < 0:
                    raise ValueError('Le prix unitaire ne peut pas être négatif')

            unite = str(row.get('Unité')).strip() if pd.notna(row.get('Unité')) and str(row.get('Unité')).strip() else None
            description = str(row.get('Description')).strip() if pd.notna(row.get('Description')) and str(row.get('Description')).strip() else None
            motif = str(row.get('Motif')).strip() if pd.notna(row.get('Motif')) and str(row.get('Motif')).strip() else 'Import stock'
            actif_value = row.get('Actif')
            actif = None
            if actif_value not in (None, '') and not pd.isna(actif_value):
                actif = parse_import_boolean(actif_value)

            if produit:
                if product_code and produit.code and produit.code != product_code:
                    raise ValueError('Le code importé ne correspond pas au produit existant')
                if product_code and not produit.code:
                    produit.code = product_code
                if product_name and not produit.nom:
                    produit.nom = product_name
            else:
                produit = Produit(
                    nom=product_name,
                    code=product_code,
                    stock_actuel=0,
                )
                db.session.add(produit)
                db.session.flush()

            if description is not None:
                produit.description = description
            if famille is not None:
                produit.famille = famille
            if categorie is not None:
                produit.categorie = categorie
            if unite is not None:
                produit.unite = unite
            if prix_unitaire is not None:
                produit.prix_unitaire = prix_unitaire
            if stock_minimum is not None:
                produit.stock_minimum = stock_minimum
            if actif is not None:
                produit.actif = actif
            elif is_new_product:
                produit.actif = True

            variation = stock_actuel - float(produit.stock_actuel or 0)
            mouvement = None
            if variation != 0:
                mouvement_type = MouvementStock.TYPE_ENTREE if is_new_product and variation > 0 else MouvementStock.TYPE_AJUSTEMENT
                mouvement = appliquer_mouvement_stock(
                    produit,
                    variation,
                    mouvement_type,
                    motif,
                )
                db.session.flush()

            log_action = 'CREATE' if is_new_product else 'UPDATE'
            log_details = f'Import stock {"création" if is_new_product else "mise à jour"} produit {produit.nom}'
            enregistrer_log(log_action, 'produit', produit.id, log_details)
            if mouvement:
                enregistrer_log(
                    'CREATE',
                    'mouvement_stock',
                    mouvement.id,
                    f'Import stock {produit.nom} ({variation:+,.2f})'
                )
            db.session.commit()
            imported_count += 1
        except Exception as exc:
            db.session.rollback()
            import_errors.append(f'Ligne {source_row_number}: {str(exc)}')

    return imported_count, import_errors


def import_commandes_dataframe(dataframe):
    """Importe des commandes depuis un fichier tabulaire transformé."""
    if dataframe.empty:
        raise ValueError('Le fichier est vide')

    required_columns = ['Nr.', 'Date CDE', 'Montant']
    missing_columns = [column for column in required_columns if column not in dataframe.columns]
    if missing_columns:
        raise ValueError(f'Colonnes manquantes: {", ".join(missing_columns)}')

    compteur = 0
    erreurs = []

    for row_index, (_, row) in enumerate(dataframe.iterrows(), start=2):
        source_row_number = get_import_source_row_number(row, row_index)
        try:
            nom_fournisseur = row.get('Fournisseur', '')
            fournisseur = None
            if nom_fournisseur and pd.notna(nom_fournisseur):
                nom_fournisseur = str(nom_fournisseur).strip()
                if nom_fournisseur:
                    fournisseur = Fournisseur.query.filter_by(nom=nom_fournisseur).first()
                    if not fournisseur:
                        fournisseur = Fournisseur(
                            nom=nom_fournisseur,
                            pays='Cameroun',
                            statut='Actif'
                        )
                        db.session.add(fournisseur)
                        db.session.flush()

            montant = parser_montant_import(row.get('Montant', 0))
            avance = parser_montant_import(row.get('Avance', 0))

            if montant < 0 or avance < 0:
                erreurs.append(f'Ligne {source_row_number}: montants négatifs')
                continue
            if avance > montant:
                erreurs.append(f'Ligne {source_row_number}: avance supérieure au montant')
                continue

            bon_commande = str(row.get('N° Bon commande')).strip() if pd.notna(row.get('N° Bon commande')) and str(row.get('N° Bon commande')).strip() else None
            facture = str(row.get('Facture')).strip() if pd.notna(row.get('Facture')) and str(row.get('Facture')).strip() else None

            existing = None
            if bon_commande:
                existing = Commande.query.filter_by(bon_commande=bon_commande).first()
            if not existing and facture:
                existing = Commande.query.filter_by(facture=facture).first()
            if existing:
                erreurs.append(f'Ligne {source_row_number}: commande déjà existante')
                continue

            note_performance = row.get('Note Performance Fournisseur')
            note_sav = row.get('Note SAV Fournisseur')
            commande = Commande(
                nr=int(float(row.get('Nr.', 0))) if pd.notna(row.get('Nr.')) and str(row.get('Nr.')).strip() != '' else None,
                date_cde=parser_date_import(row.get('Date CDE')),
                entite=str(row.get('Entité')).strip() if pd.notna(row.get('Entité')) else None,
                demandeur=str(row.get('Demandeur')).strip() if pd.notna(row.get('Demandeur')) else None,
                service_demandeur=str(row.get('Service Demandeur')).strip() if pd.notna(row.get('Service Demandeur')) else None,
                acheteur=str(row.get('Acheteur')).strip() if pd.notna(row.get('Acheteur')) else None,
                fournisseur_id=fournisseur.id if fournisseur else None,
                affaire=str(row.get('Affaire/Commande')).strip() if pd.notna(row.get('Affaire/Commande')) else None,
                bon_commande=bon_commande,
                date_livraison=parser_date_import(row.get('Date Livraison')),
                date_reception=parser_date_import(row.get('Date Réception')),
                bon_livraison=str(row.get('N° Bon Livraison')).strip() if pd.notna(row.get('N° Bon Livraison')) else None,
                facture=facture,
                montant=montant,
                avance=avance,
                prix_reference_marche=parser_montant_import(row.get('Prix Référence Marché', 0)),
                commande_conforme=False if str(row.get('Commande Conforme')).strip().lower() in {'false', '0', 'non', 'no'} else True,
                rupture_fournisseur=str(row.get('Rupture Fournisseur')).strip().lower() in {'true', '1', 'oui', 'yes'},
                note_fournisseur=valider_note_fournisseur(note_performance, 'Note performance')
                    if pd.notna(note_performance) and str(note_performance).strip() != '' else None,
                note_service=valider_note_fournisseur(note_sav, 'Note SAV')
                    if pd.notna(note_sav) and str(note_sav).strip() != '' else None,
                date_paiement=parser_date_import(row.get('Date Paiement')),
                commentaire=str(row.get('Commentaire')).strip() if pd.notna(row.get('Commentaire')) else None
            )
            commande.calculer_solde()
            db.session.add(commande)
            compteur += 1
        except Exception as row_error:
            erreurs.append(f'Ligne {source_row_number}: {str(row_error)[:120]}')

    db.session.commit()
    return compteur, erreurs


def generer_reference_vente():
    """Génère une référence de vente unique."""
    return f"VTE-{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"


def get_request_user_id():
    if has_request_context() and getattr(current_user, 'is_authenticated', False):
        return current_user.id
    return None


def get_request_ip():
    if has_request_context():
        return request.remote_addr
    return None


def enregistrer_log(action, table, record_id, details):
    log = LogAction(
        utilisateur_id=get_request_user_id(),
        action=action,
        table=table,
        record_id=record_id,
        details=details,
        ip_address=get_request_ip()
    )
    db.session.add(log)

def appliquer_mouvement_stock(produit, variation, type_mouvement, motif, vente=None):
    """Applique un mouvement de stock et retourne l'historique créé."""
    stock_avant = produit.stock_actuel or 0
    stock_apres = stock_avant + variation

    if stock_apres < 0:
        raise ValueError(f"Stock insuffisant pour {produit.nom}")

    produit.stock_actuel = stock_apres

    mouvement = MouvementStock(
        produit=produit,
        utilisateur_id=get_request_user_id(),
        vente=vente,
        type_mouvement=type_mouvement,
        variation=variation,
        stock_avant=stock_avant,
        stock_apres=stock_apres,
        motif=motif,
    )
    db.session.add(mouvement)
    return mouvement

def get_stock_movement_form_lines(form=None):
    """Construit les lignes du formulaire de mouvement multi-produits."""
    if form is None:
        return [{'produit_id': '', 'quantite': ''}]

    produit_ids = form.getlist('produit_id[]')
    quantites = form.getlist('quantite[]')

    if not produit_ids and form.get('produit_id'):
        produit_ids = [form.get('produit_id')]
    if not quantites and form.get('quantite'):
        quantites = [form.get('quantite')]

    line_count = max(len(produit_ids), len(quantites), 1)
    lines = []
    for index in range(line_count):
        lines.append({
            'produit_id': (produit_ids[index] if index < len(produit_ids) else '') or '',
            'quantite': (quantites[index] if index < len(quantites) else '') or '',
        })
    return lines

def get_month_series(months=12):
    """Retourne une liste de premiers jours de mois, du plus ancien au plus récent."""
    current = date.today().replace(day=1)
    series = []
    for i in range(months - 1, -1, -1):
        year = current.year
        month = current.month - i
        while month <= 0:
            month += 12
            year -= 1
        while month > 12:
            month -= 12
            year += 1
        series.append(date(year, month, 1))
    return series

def get_month_series_between(start_date=None, end_date=None, fallback_months=12):
    """Retourne une série mensuelle alignée sur une plage de dates."""
    if not start_date or not end_date:
        return get_month_series(months=fallback_months)

    current = start_date.replace(day=1)
    end_month = end_date.replace(day=1)
    series = []

    while current <= end_month:
        series.append(current)
        next_month = current.month + 1
        next_year = current.year
        if next_month > 12:
            next_month = 1
            next_year += 1
        current = date(next_year, next_month, 1)

    return series or get_month_series(months=fallback_months)

def get_monthly_amount_map(base_query, date_column, amount_column):
    """Agrège un montant par mois de manière compatible SQLite/Postgres."""
    year_part = extract('year', date_column)
    month_part = extract('month', date_column)
    rows = base_query.with_entities(
        year_part.label('year'),
        month_part.label('month'),
        func.coalesce(func.sum(amount_column), 0).label('total'),
    ).filter(date_column.isnot(None))\
     .group_by(year_part, month_part)\
     .all()

    return {
        (int(row.year), int(row.month)): float(row.total or 0)
        for row in rows
        if row.year is not None and row.month is not None
    }

def build_monthly_evolution(base_query, date_column, amount_column, months=12, month_series=None):
    month_series = month_series or get_month_series(months=months)
    monthly_map = get_monthly_amount_map(base_query, date_column, amount_column)
    return [
        {
            'mois': month_start.strftime('%b %Y'),
            'total': monthly_map.get((month_start.year, month_start.month), 0),
        }
        for month_start in month_series
    ]

def build_retard_stats(rows):
    """Calcule les retards moyens à partir d'une liste (clé, date_livraison)."""
    today = date.today()
    stats = defaultdict(lambda: {'sum': 0, 'count': 0})

    for key, livraison in rows:
        if not key or not livraison:
            continue
        stats[key]['count'] += 1
        if livraison < today:
            stats[key]['sum'] += (today - livraison).days

    return stats

def average_non_null(*values):
    """Calcule une moyenne en ignorant les valeurs nulles."""
    valid_values = [float(value) for value in values if value is not None]
    if not valid_values:
        return None
    return sum(valid_values) / len(valid_values)

def clamp(value, minimum=0, maximum=100):
    return max(minimum, min(maximum, value))

def parse_date_filter_value(value):
    """Parse une date HTML ou retourne None."""
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None

def get_period_bounds(period, start_raw=None, end_raw=None):
    """Retourne les bornes de période pour les filtres analytiques."""
    today = date.today()
    period = (period or 'month').strip().lower()

    if period == 'today':
        return today, today, 'today'
    if period == 'week':
        return today - timedelta(days=today.weekday()), today, 'week'
    if period == 'month':
        return today.replace(day=1), today, 'month'
    if period == 'year':
        return date(today.year, 1, 1), today, 'year'
    if period == 'custom':
        start_date = parse_date_filter_value(start_raw)
        end_date = parse_date_filter_value(end_raw)
        if start_date and end_date and start_date > end_date:
            start_date, end_date = end_date, start_date
        return start_date, end_date, 'custom'
    return None, None, 'all'

def shift_year_safe(value, years=-1):
    """Décale une date d'un an en gérant le 29 février."""
    if value is None:
        return None
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(month=2, day=28, year=value.year + years)

def get_comparison_bounds(start_date, end_date, comparison):
    """Construit une plage de comparaison MoM ou YoY."""
    comparison = (comparison or 'none').strip().lower()
    if not start_date or not end_date or comparison == 'none':
        return None, None

    if comparison == 'mom':
        duration_days = (end_date - start_date).days + 1
        previous_end = start_date - timedelta(days=1)
        previous_start = previous_end - timedelta(days=duration_days - 1)
        return previous_start, previous_end

    if comparison == 'yoy':
        return shift_year_safe(start_date, -1), shift_year_safe(end_date, -1)

    return None, None

def apply_date_window(query, column, start_date=None, end_date=None):
    """Applique une plage de dates à une query."""
    if start_date:
        query = query.filter(column >= start_date)
    if end_date:
        query = query.filter(column <= end_date)
    return query

def build_sales_filters(args):
    """Normalise les filtres analytiques de ventes/dashboard."""
    start_date, end_date, period = get_period_bounds(
        args.get('period'),
        args.get('start_date'),
        args.get('end_date'),
    )

    return {
        'period': period,
        'start_date': start_date,
        'end_date': end_date,
        'comparison': (args.get('comparison') or 'none').strip().lower(),
        'categorie': (args.get('categorie') or '').strip(),
        'produit_id': (args.get('produit_id') or '').strip(),
        'canal': (args.get('canal') or '').strip().upper(),
        'region': (args.get('region') or '').strip(),
        'type_client': (args.get('type_client') or '').strip().upper(),
    }

def get_default_sales_filters():
    """Filtres par défaut pour les exports, emails et embed hors requête utilisateur."""
    start_date, end_date, period = get_period_bounds('month')
    return {
        'period': period,
        'start_date': start_date,
        'end_date': end_date,
        'comparison': 'none',
        'categorie': '',
        'produit_id': '',
        'canal': '',
        'region': '',
        'type_client': '',
    }

def apply_sales_filters_to_vente_query(query, filters):
    """Applique les filtres analytiques à une query Vente."""
    query = apply_date_window(query, Vente.date_vente, filters['start_date'], filters['end_date'])

    if filters['canal'] in {Vente.CANAL_OFFLINE, Vente.CANAL_ONLINE}:
        query = query.filter(Vente.canal_vente == filters['canal'])
    if filters['region']:
        query = query.filter(Vente.region == filters['region'])
    if filters['type_client'] in {
        Vente.TYPE_CLIENT_PARTICULIER,
        Vente.TYPE_CLIENT_ENTREPRISE,
        Vente.TYPE_CLIENT_REVENDEUR,
    }:
        query = query.filter(Vente.type_client == filters['type_client'])

    if filters['categorie'] or filters['produit_id'].isdigit():
        vente_ids_query = db.session.query(LigneVente.vente_id).join(
            Produit, Produit.id == LigneVente.produit_id
        )
        if filters['categorie']:
            vente_ids_query = vente_ids_query.filter(Produit.categorie == filters['categorie'])
        if filters['produit_id'].isdigit():
            vente_ids_query = vente_ids_query.filter(LigneVente.produit_id == int(filters['produit_id']))
        query = query.filter(Vente.id.in_(vente_ids_query.distinct()))

    return query

def apply_sales_filters_to_line_query(query, filters):
    """Applique les filtres analytiques à une query LigneVente jointe à Vente/Produit."""
    query = apply_date_window(query, Vente.date_vente, filters['start_date'], filters['end_date'])

    if filters['canal'] in {Vente.CANAL_OFFLINE, Vente.CANAL_ONLINE}:
        query = query.filter(Vente.canal_vente == filters['canal'])
    if filters['region']:
        query = query.filter(Vente.region == filters['region'])
    if filters['type_client'] in {
        Vente.TYPE_CLIENT_PARTICULIER,
        Vente.TYPE_CLIENT_ENTREPRISE,
        Vente.TYPE_CLIENT_REVENDEUR,
    }:
        query = query.filter(Vente.type_client == filters['type_client'])
    if filters['categorie']:
        query = query.filter(Produit.categorie == filters['categorie'])
    if filters['produit_id'].isdigit():
        query = query.filter(LigneVente.produit_id == int(filters['produit_id']))

    return query

def compute_sales_analytics(filters):
    """Calcule les KPI et graphiques ventes pour le dashboard."""
    sales_query = apply_sales_filters_to_vente_query(Vente.query, filters)
    line_query = apply_sales_filters_to_line_query(
        db.session.query(LigneVente, Vente, Produit)
        .join(Vente, Vente.id == LigneVente.vente_id)
        .join(Produit, Produit.id == LigneVente.produit_id),
        filters
    )

    total_ventes, chiffre_affaires_brut, total_encaisse, total_solde, ticket_moyen, total_retours = sales_query.with_entities(
        func.count(Vente.id),
        func.coalesce(func.sum(Vente.montant_total), 0),
        func.coalesce(func.sum(Vente.montant_paye), 0),
        func.coalesce(func.sum(Vente.solde), 0),
        func.coalesce(func.avg(Vente.montant_total), 0),
        func.coalesce(func.sum(Vente.montant_retour), 0),
    ).one()

    chiffre_affaires_net = (chiffre_affaires_brut or 0) - (total_retours or 0)
    ventes_payees = sales_query.filter(Vente.statut_paiement == Vente.STATUT_PAYEE).count()
    ventes_en_attente = sales_query.filter(Vente.statut_paiement != Vente.STATUT_PAYEE).count()
    taux_encaissement = (total_encaisse / chiffre_affaires_brut * 100) if chiffre_affaires_brut else 0

    clients_total = sales_query.with_entities(
        func.count(func.distinct(Vente.client_nom))
    ).filter(Vente.client_nom.isnot(None), Vente.client_nom != '').scalar() or 0
    clients_retour = sales_query.with_entities(
        func.count(func.distinct(Vente.client_nom))
    ).filter(
        Vente.client_nom.isnot(None),
        Vente.client_nom != '',
        or_(Vente.retour_effectue.is_(True), func.coalesce(Vente.montant_retour, 0) > 0)
    ).scalar() or 0
    taux_retour_client = (clients_retour / clients_total * 100) if clients_total else 0
    clv_moyen = (chiffre_affaires_net / clients_total) if clients_total else 0

    evolution = build_monthly_evolution(sales_query, Vente.date_vente, Vente.montant_total, months=12)

    ventes_par_canal = [
        {
            'canal': row[0] or 'NON RENSEIGNÉ',
            'total_ventes': row[1] or 0,
            'chiffre_affaires': row[2] or 0,
            'encaisse': row[3] or 0,
        }
        for row in sales_query.with_entities(
            Vente.canal_vente,
            func.count(Vente.id),
            func.coalesce(func.sum(Vente.montant_total), 0),
            func.coalesce(func.sum(Vente.montant_paye), 0),
        ).group_by(Vente.canal_vente).all()
    ]

    ventes_par_region = [
        {
            'region': row[0] or 'NON RENSEIGNÉE',
            'total_ventes': row[1] or 0,
            'chiffre_affaires': row[2] or 0,
        }
        for row in sales_query.with_entities(
            Vente.region,
            func.count(Vente.id),
            func.coalesce(func.sum(Vente.montant_total), 0),
        ).group_by(Vente.region).all()
        if row[0] or row[2]
    ]

    ventes_par_type_client = [
        {
            'type_client': row[0] or 'NON RENSEIGNÉ',
            'total_ventes': row[1] or 0,
            'chiffre_affaires': row[2] or 0,
        }
        for row in sales_query.with_entities(
            Vente.type_client,
            func.count(Vente.id),
            func.coalesce(func.sum(Vente.montant_total), 0),
        ).group_by(Vente.type_client).all()
    ]

    ventes_par_categorie = [
        {
            'categorie': row[0] or 'Non classée',
            'quantite': row[1] or 0,
            'chiffre_affaires': row[2] or 0,
            'nb_ventes': row[3] or 0,
        }
        for row in line_query.with_entities(
            func.coalesce(Produit.categorie, 'Non classée'),
            func.coalesce(func.sum(LigneVente.quantite), 0),
            func.coalesce(func.sum(LigneVente.montant_total), 0),
            func.count(func.distinct(LigneVente.vente_id)),
        ).group_by(Produit.categorie).order_by(func.sum(LigneVente.montant_total).desc()).all()
    ]

    top_clients = [
        {
            'client_nom': row[0],
            'nb_ventes': row[1] or 0,
            'chiffre_affaires': row[2] or 0,
            'encaisse': row[3] or 0,
        }
        for row in sales_query.with_entities(
            Vente.client_nom,
            func.count(Vente.id),
            func.coalesce(func.sum(Vente.montant_total), 0),
            func.coalesce(func.sum(Vente.montant_paye), 0),
        ).filter(Vente.client_nom.isnot(None), Vente.client_nom != '')\
         .group_by(Vente.client_nom)\
         .order_by(func.sum(Vente.montant_total).desc())\
         .limit(10).all()
    ]

    top_produits_rows = line_query.with_entities(
        Produit.id,
        Produit.nom,
        func.coalesce(Produit.categorie, 'Non classée'),
        func.coalesce(func.sum(LigneVente.quantite), 0),
        func.coalesce(func.sum(LigneVente.montant_total), 0),
        func.count(func.distinct(LigneVente.vente_id)),
    ).group_by(Produit.id, Produit.nom, Produit.categorie)\
     .order_by(func.sum(LigneVente.montant_total).desc())\
     .all()

    total_ca_produits = sum(float(row[4] or 0) for row in top_produits_rows)
    top_produits = []
    pareto_produits = []
    cumul_ca = 0
    for row in top_produits_rows:
        produit_data = {
            'id': row[0],
            'nom': row[1],
            'categorie': row[2] or 'Non classée',
            'quantite': float(row[3] or 0),
            'chiffre_affaires': float(row[4] or 0),
            'nb_ventes': row[5] or 0,
            'part_ca': (float(row[4] or 0) / total_ca_produits * 100) if total_ca_produits else 0,
        }
        cumul_ca += produit_data['chiffre_affaires']
        produit_data['cumul_part_ca'] = (cumul_ca / total_ca_produits * 100) if total_ca_produits else 0
        top_produits.append(produit_data)

    for produit in top_produits:
        if total_ca_produits <= 0:
            break
        pareto_produits.append(produit)
        if produit['cumul_part_ca'] >= 80:
            break

    pareto_nombre = len(pareto_produits)
    total_produits_vendus = len(top_produits)
    pareto_ratio_produits = (pareto_nombre / total_produits_vendus * 100) if total_produits_vendus else 0
    pareto_part_ca = (sum(item['chiffre_affaires'] for item in pareto_produits) / total_ca_produits * 100) if total_ca_produits else 0

    return {
        'total_ventes': total_ventes,
        'chiffre_affaires_brut': chiffre_affaires_brut or 0,
        'chiffre_affaires_net': chiffre_affaires_net or 0,
        'total_encaisse': total_encaisse or 0,
        'total_solde': total_solde or 0,
        'ticket_moyen': ticket_moyen or 0,
        'total_retours': total_retours or 0,
        'ventes_payees': ventes_payees,
        'ventes_en_attente': ventes_en_attente,
        'taux_encaissement': round(taux_encaissement, 1),
        'taux_retour_client': round(taux_retour_client, 1),
        'clients_total': clients_total,
        'clients_retour': clients_retour,
        'clv_moyen': clv_moyen or 0,
        'evolution': evolution,
        'ventes_par_canal': ventes_par_canal,
        'ventes_par_region': ventes_par_region,
        'ventes_par_type_client': ventes_par_type_client,
        'ventes_par_categorie': ventes_par_categorie,
        'top_clients': top_clients,
        'top_produits': top_produits[:10],
        'pareto_produits': pareto_produits,
        'pareto_nombre': pareto_nombre,
        'pareto_ratio_produits': round(pareto_ratio_produits, 1),
        'pareto_part_ca': round(pareto_part_ca, 1),
    }

def build_supplier_performance_data(start_date=None, end_date=None, fournisseur_id=None, include_inactive=False, evolution_months=8):
    """Construit les KPI détaillés des fournisseurs."""
    join_is_outer = include_inactive or fournisseur_id is not None
    stats_query = db.session.query(
        Fournisseur.id,
        Fournisseur.nom,
        Fournisseur.pays,
        func.count(Commande.id).label('total_commandes'),
        func.coalesce(func.sum(Commande.montant), 0).label('total_montant'),
        func.coalesce(func.avg(Commande.montant), 0).label('montant_moyen'),
        func.coalesce(
            func.sum(
                case(
                    (Commande.statut == Commande.STATUT_A_PAYER, Commande.solde),
                    else_=0
                )
            ),
            0
        ).label('montant_a_payer'),
        func.count(
            case(
                (
                    and_(
                        Commande.date_livraison.isnot(None),
                        Commande.date_livraison < date.today(),
                        Commande.date_reception.is_(None),
                    ),
                    1,
                ),
                else_=None,
            )
        ).label('nb_retard_ouverts'),
        func.coalesce(func.avg(Commande.note_fournisseur), 0).label('note_moyenne'),
        func.coalesce(func.avg(Commande.note_service), 0).label('note_service_moyenne'),
        func.count(Commande.note_fournisseur).label('nb_notes_fournisseur'),
        func.count(Commande.note_service).label('nb_notes_service'),
        func.count(
            case(
                (Commande.commande_conforme.is_(True), 1),
                else_=None,
            )
        ).label('nb_conformes'),
        func.count(
            case(
                (Commande.rupture_fournisseur.is_(True), 1),
                else_=None,
            )
        ).label('nb_ruptures'),
        func.coalesce(
            func.sum(
                case(
                    (
                        and_(
                            Commande.prix_reference_marche.isnot(None),
                            Commande.prix_reference_marche > 0,
                        ),
                        Commande.prix_reference_marche,
                    ),
                    else_=0,
                )
            ),
            0,
        ).label('prix_reference_total'),
        func.coalesce(
            func.sum(
                case(
                    (
                        and_(
                            Commande.prix_reference_marche.isnot(None),
                            Commande.prix_reference_marche > 0,
                        ),
                        Commande.montant,
                    ),
                    else_=0,
                )
            ),
            0,
        ).label('prix_compare_total'),
    ).join(Commande, Fournisseur.id == Commande.fournisseur_id, isouter=join_is_outer)

    if fournisseur_id:
        stats_query = stats_query.filter(Fournisseur.id == fournisseur_id)
    if start_date or end_date:
        stats_query = apply_date_window(stats_query, Commande.date_cde, start_date, end_date)

    stats_rows = stats_query.group_by(Fournisseur.id, Fournisseur.nom, Fournisseur.pays).all()

    evaluation_query = db.session.query(
        Commande.fournisseur_id,
        Commande.date_cde,
        Commande.date_livraison,
        Commande.date_reception,
        Commande.note_fournisseur,
        Commande.note_service,
        Commande.commande_conforme,
        Commande.rupture_fournisseur,
        Commande.prix_reference_marche,
        Commande.montant,
    ).filter(Commande.fournisseur_id.isnot(None))

    if fournisseur_id:
        evaluation_query = evaluation_query.filter(Commande.fournisseur_id == fournisseur_id)
    if start_date or end_date:
        evaluation_query = apply_date_window(evaluation_query, Commande.date_cde, start_date, end_date)

    evaluation_rows = evaluation_query.all()

    evaluation_by_supplier = defaultdict(list)
    monthly_scores = defaultdict(lambda: defaultdict(list))
    for row in evaluation_rows:
        evaluation_by_supplier[row.fournisseur_id].append(row)
        order_score = average_non_null(row.note_fournisseur, row.note_service)
        if row.date_cde and order_score is not None:
            monthly_scores[row.fournisseur_id][(row.date_cde.year, row.date_cde.month)].append(order_score)

    items = []
    total_general = sum(float(row.total_montant or 0) for row in stats_rows)

    for row in stats_rows:
        supplier_evaluations = evaluation_by_supplier.get(row.id, [])
        delivery_gaps = []
        deliveries_with_actual = 0
        deliveries_within_target = 0

        for evaluation in supplier_evaluations:
            if evaluation.date_livraison and evaluation.date_reception:
                gap = (evaluation.date_reception - evaluation.date_livraison).days
                delivery_gaps.append(gap)
                deliveries_with_actual += 1
                if gap < 10:
                    deliveries_within_target += 1

        delai_moyen = average_non_null(*delivery_gaps)
        score_performance = average_non_null(
            row.note_moyenne if row.nb_notes_fournisseur else None,
            row.note_service_moyenne if row.nb_notes_service else None,
        )
        taux_conformite = ((row.nb_conformes or 0) / row.total_commandes * 100) if row.total_commandes else 0
        taux_rupture = ((row.nb_ruptures or 0) / row.total_commandes * 100) if row.total_commandes else 0
        taux_retard = ((row.nb_retard_ouverts or 0) / row.total_commandes * 100) if row.total_commandes else 0
        taux_paiement = (((row.total_montant or 0) - (row.montant_a_payer or 0)) / (row.total_montant or 1) * 100) if row.total_montant else 0
        part_achats = ((row.total_montant or 0) / total_general * 100) if total_general else 0
        respect_delai = (deliveries_within_target / deliveries_with_actual * 100) if deliveries_with_actual else 0
        price_competitiveness_pct = None
        if (row.prix_reference_total or 0) > 0:
            price_competitiveness_pct = ((row.prix_compare_total or 0) - row.prix_reference_total) / row.prix_reference_total * 100

        quality_score = clamp(taux_conformite / 20, 0, 5)
        delay_score = clamp(5 - (max(delai_moyen or 0, 0) / 10), 0, 5) if deliveries_with_actual else 0
        service_score = score_performance or 0
        price_score = 5 if price_competitiveness_pct is None else clamp(5 - max(price_competitiveness_pct, 0) / 5, 0, 5)
        rupture_score = clamp(5 - (taux_rupture / 5), 0, 5) if row.total_commandes else 0

        score_fiabilite = average_non_null(
            (score_performance / 5 * 100) if score_performance is not None else None,
            quality_score / 5 * 100 if row.total_commandes else None,
            delay_score / 5 * 100 if deliveries_with_actual else None,
            price_score / 5 * 100 if price_competitiveness_pct is not None else None,
            rupture_score / 5 * 100 if row.total_commandes else None,
        ) or 0

        if score_performance is None:
            quadrant = 'NON_NOTÉ'
        elif delai_moyen is not None and delai_moyen < 10 and score_performance >= 4:
            quadrant = 'EXCELLENT'
        elif delai_moyen is not None and delai_moyen >= 10 and score_performance >= 4:
            quadrant = 'A_SURVEILLER'
        elif delai_moyen is not None and delai_moyen < 10 and score_performance < 4:
            quadrant = 'A_FIDELISER'
        else:
            quadrant = 'A_REMPLACER'

        items.append({
            'id': row.id,
            'nom': row.nom,
            'pays': row.pays,
            'total_commandes': int(row.total_commandes or 0),
            'total_montant': float(row.total_montant or 0),
            'montant_moyen': float(row.montant_moyen or 0),
            'montant_a_payer': float(row.montant_a_payer or 0),
            'nb_retard_ouverts': int(row.nb_retard_ouverts or 0),
            'delai_moyen': round(delai_moyen, 1) if delai_moyen is not None else None,
            'respect_delai': round(respect_delai, 1),
            'taux_retard': round(taux_retard, 1),
            'taux_conformite': round(taux_conformite, 1),
            'taux_rupture': round(taux_rupture, 1),
            'taux_paiement': round(taux_paiement, 1),
            'part_achats': round(part_achats, 1),
            'score_performance': round(score_performance, 2) if score_performance is not None else None,
            'score_service': round(service_score, 2) if service_score else None,
            'score_qualite': round(quality_score, 2),
            'score_delai': round(delay_score, 2),
            'score_prix': round(price_score, 2) if price_competitiveness_pct is not None else None,
            'score_rupture': round(rupture_score, 2),
            'score_fiabilite': round(score_fiabilite, 1),
            'price_competitiveness_pct': round(price_competitiveness_pct, 1) if price_competitiveness_pct is not None else None,
            'deliveries_with_actual': deliveries_with_actual,
            'quadrant': quadrant,
        })

    items.sort(key=lambda supplier: (supplier['total_montant'], supplier['score_performance'] or 0), reverse=True)

    month_series = get_month_series(months=evolution_months)
    top_suppliers_for_evolution = [
        supplier for supplier in items
        if supplier['score_performance'] is not None
    ][:4]

    evolution_datasets = []
    for supplier in top_suppliers_for_evolution:
        evolution_datasets.append({
            'label': supplier['nom'],
            'scores': [
                round(
                    average_non_null(*monthly_scores[supplier['id']].get((month_start.year, month_start.month), [])),
                    2
                ) if monthly_scores[supplier['id']].get((month_start.year, month_start.month)) else None
                for month_start in month_series
            ]
        })

    matrix_points = [
        {
            'x': supplier['delai_moyen'] if supplier['delai_moyen'] is not None else 0,
            'y': supplier['score_performance'] if supplier['score_performance'] is not None else 0,
            'label': supplier['nom'],
            'quadrant': supplier['quadrant'],
        }
        for supplier in items
        if supplier['score_performance'] is not None and supplier['delai_moyen'] is not None
    ]

    summary = {
        'total_fournisseurs': len(items),
        'fournisseurs_excellents': sum(1 for supplier in items if supplier['quadrant'] == 'EXCELLENT'),
        'fournisseurs_surveillance': sum(1 for supplier in items if supplier['quadrant'] == 'A_SURVEILLER'),
        'fournisseurs_remplacement': sum(1 for supplier in items if supplier['quadrant'] == 'A_REMPLACER'),
        'score_moyen': round(
            average_non_null(*(supplier['score_performance'] for supplier in items if supplier['score_performance'] is not None)) or 0,
            2
        ),
        'conformite_moyenne': round(
            average_non_null(*(supplier['taux_conformite'] for supplier in items if supplier['total_commandes'])) or 0,
            1
        ),
        'rupture_moyenne': round(
            average_non_null(*(supplier['taux_rupture'] for supplier in items if supplier['total_commandes'])) or 0,
            1
        ),
        'delai_moyen_general': round(
            average_non_null(*(supplier['delai_moyen'] for supplier in items if supplier['delai_moyen'] is not None)) or 0,
            1
        ),
    }

    return {
        'items': items,
        'top_items': items[:5],
        'critical_items': sorted(
            [
                supplier for supplier in items
                if supplier['quadrant'] in {'A_REMPLACER', 'A_SURVEILLER'} or supplier['montant_a_payer'] > 0
            ],
            key=lambda supplier: (
                supplier['quadrant'] == 'A_REMPLACER',
                supplier['taux_rupture'],
                supplier['delai_moyen'] or 0,
                supplier['montant_a_payer'],
            ),
            reverse=True,
        )[:5],
        'summary': summary,
        'matrix_points': matrix_points,
        'matrix_delay_threshold': 10,
        'matrix_score_threshold': 4,
        'evolution_labels': [month_start.strftime('%b %Y') for month_start in month_series],
        'evolution_datasets': evolution_datasets,
    }

def build_supplier_filters(args, default_period='all'):
    """Normalise les filtres analytiques fournisseurs."""
    start_date, end_date, period = get_period_bounds(
        args.get('period') or default_period,
        args.get('start_date'),
        args.get('end_date'),
    )
    include_inactive_raw = args.get('include_inactive')
    include_inactive = True if include_inactive_raw is None else include_inactive_raw in {'1', 'true', 'on', 'yes'}
    return {
        'period': period,
        'start_date': start_date,
        'end_date': end_date,
        'include_inactive': include_inactive,
    }

def build_supplier_filter_querystring(filters):
    """Construit une query string pour les filtres fournisseurs."""
    params = {}
    if filters.get('period'):
        params['period'] = filters['period']
    if filters.get('start_date'):
        params['start_date'] = filters['start_date'].isoformat()
    if filters.get('end_date'):
        params['end_date'] = filters['end_date'].isoformat()
    if filters.get('include_inactive'):
        params['include_inactive'] = '1'
    return urlencode(params)

def get_period_label(filters):
    """Retourne un libellé humain de période analytique."""
    period = filters.get('period')
    start_date = filters.get('start_date')
    end_date = filters.get('end_date')

    if period == 'today':
        return "Aujourd'hui"
    if period == 'week':
        return 'Cette semaine'
    if period == 'month':
        return 'Ce mois'
    if period == 'year':
        return 'Cette année'
    if period == 'all':
        return 'Historique complet'
    if period == 'custom':
        if start_date and end_date:
            return f"Du {start_date.strftime('%d/%m/%Y')} au {end_date.strftime('%d/%m/%Y')}"
        if start_date:
            return f"Depuis le {start_date.strftime('%d/%m/%Y')}"
        if end_date:
            return f"Jusqu'au {end_date.strftime('%d/%m/%Y')}"
        return 'Période personnalisée'
    return 'Période analytique'

def get_supplier_detail_payload(fournisseur, filters, evolution_months=12):
    """Construit le contexte détaillé d'un fournisseur pour la vue et les exports."""
    analytics = build_supplier_performance_data(
        start_date=filters.get('start_date'),
        end_date=filters.get('end_date'),
        fournisseur_id=fournisseur.id,
        include_inactive=True,
        evolution_months=evolution_months,
    )
    supplier_stats = analytics['items'][0] if analytics['items'] else {
        'total_commandes': 0,
        'total_montant': 0,
        'montant_a_payer': 0,
        'delai_moyen': None,
        'taux_retard': 0,
        'taux_paiement': 0,
        'montant_moyen': 0,
        'score_performance': None,
        'taux_conformite': 0,
        'taux_rupture': 0,
        'respect_delai': 0,
        'price_competitiveness_pct': None,
        'score_qualite': 0,
        'score_delai': 0,
        'score_service': None,
        'score_prix': None,
        'score_fiabilite': 0,
        'quadrant': 'NON_NOTÉ',
    }
    commandes_query = apply_date_window(
        Commande.query.options(selectinload(Commande.fournisseur)).filter(Commande.fournisseur_id == fournisseur.id),
        Commande.date_cde,
        filters.get('start_date'),
        filters.get('end_date'),
    )
    commandes = commandes_query.order_by(Commande.date_cde.desc(), Commande.created_at.desc()).all()
    month_series = get_month_series_between(filters.get('start_date'), filters.get('end_date'), fallback_months=evolution_months)
    evolution = build_monthly_evolution(
        apply_date_window(
            Commande.query.filter(Commande.fournisseur_id == fournisseur.id),
            Commande.date_cde,
            filters.get('start_date'),
            filters.get('end_date'),
        ),
        Commande.date_cde,
        Commande.montant,
        month_series=month_series,
    )
    score_evolution = analytics['evolution_datasets'][0]['scores'] if analytics['evolution_datasets'] else []
    radar_metrics = [
        {'label': 'Qualité produit', 'value': supplier_stats['score_qualite']},
        {'label': 'Prix compétitif', 'value': supplier_stats['score_prix'] if supplier_stats['score_prix'] is not None else 0},
        {'label': 'Délai livraison', 'value': supplier_stats['score_delai']},
        {'label': 'Service après-vente', 'value': supplier_stats['score_service'] if supplier_stats['score_service'] is not None else 0},
        {'label': 'Satisfaction', 'value': supplier_stats['score_performance'] if supplier_stats['score_performance'] is not None else 0},
    ]
    statut_repartition = [
        {'statut': row[0], 'count': row[1]}
        for row in apply_date_window(
            db.session.query(Commande.statut, func.count(Commande.id)).filter(Commande.fournisseur_id == fournisseur.id),
            Commande.date_cde,
            filters.get('start_date'),
            filters.get('end_date'),
        ).group_by(Commande.statut).all()
        if row[0]
    ]
    return {
        'analytics': analytics,
        'supplier_stats': supplier_stats,
        'commandes': commandes,
        'evolution': evolution,
        'score_evolution_labels': analytics['evolution_labels'],
        'score_evolution': score_evolution,
        'radar_metrics': radar_metrics,
        'statut_repartition': statut_repartition,
    }

def build_dashboard_context(filters):
    """Construit tout le contexte analytique du dashboard."""
    comparison_start, comparison_end = get_comparison_bounds(
        filters['start_date'],
        filters['end_date'],
        filters['comparison'],
    )

    commandes_query = apply_date_window(Commande.query, Commande.date_cde, filters['start_date'], filters['end_date'])
    total_commandes, montant_total, total_a_payer, montant_moyen_commande = commandes_query.with_entities(
        func.count(Commande.id),
        func.coalesce(func.sum(Commande.montant), 0),
        func.coalesce(
            func.sum(
                case(
                    (Commande.statut == Commande.STATUT_A_PAYER, Commande.solde),
                    else_=0
                )
            ),
            0
        ),
        func.coalesce(func.avg(Commande.montant), 0),
    ).one()

    nb_retard = commandes_query.filter(
        Commande.date_livraison.isnot(None),
        Commande.date_livraison < date.today()
    ).count()

    dernieres_commandes = commandes_query.options(
        selectinload(Commande.fournisseur)
    ).order_by(Commande.date_cde.desc(), Commande.created_at.desc()).limit(10).all()
    par_entite_rows = commandes_query.with_entities(
        Commande.entite,
        func.coalesce(func.sum(Commande.montant), 0)
    ).group_by(Commande.entite).all()
    par_entite = [
        {'entite': row[0] or 'NON RENSEIGNÉE', 'montant': float(row[1] or 0)}
        for row in par_entite_rows
    ]
    evolution_achats = build_monthly_evolution(commandes_query, Commande.date_cde, Commande.montant, months=12)

    supplier_analytics = build_supplier_performance_data(
        start_date=filters['start_date'],
        end_date=filters['end_date'],
        include_inactive=False,
        evolution_months=8,
    )
    fournisseurs_data = supplier_analytics['items']
    top_fournisseurs = supplier_analytics['top_items']
    fournisseurs_critiques = supplier_analytics['critical_items']

    sales_analytics = compute_sales_analytics(filters)
    sales_query = apply_sales_filters_to_vente_query(Vente.query, filters)
    dernieres_ventes = sales_query.order_by(
        Vente.date_vente.desc(),
        Vente.created_at.desc()
    ).limit(8).all()

    stock_query = Produit.query.filter(Produit.actif.is_(True))
    if filters['categorie']:
        stock_query = stock_query.filter(Produit.categorie == filters['categorie'])
    if filters['produit_id'].isdigit():
        stock_query = stock_query.filter(Produit.id == int(filters['produit_id']))

    total_produits_actifs, valeur_stock, nb_stock_faible, nb_ruptures_stock = stock_query.with_entities(
        func.count(Produit.id),
        func.coalesce(func.sum(Produit.stock_actuel * Produit.prix_unitaire), 0),
        func.coalesce(
            func.sum(
                case(
                    (Produit.stock_actuel <= Produit.stock_minimum, 1),
                    else_=0
                )
            ),
            0
        ),
        func.coalesce(
            func.sum(
                case(
                    (Produit.stock_actuel <= 0, 1),
                    else_=0
                )
            ),
            0
        ),
    ).one()
    alertes_stock = stock_query.filter(
        Produit.stock_actuel <= Produit.stock_minimum
    ).order_by(Produit.stock_actuel.asc(), Produit.nom.asc()).limit(8).all()

    comparison_label = None
    sales_comparison_pct = None
    purchases_comparison_pct = None
    if comparison_start and comparison_end:
        previous_filters = dict(filters)
        previous_filters['start_date'] = comparison_start
        previous_filters['end_date'] = comparison_end
        previous_sales = compute_sales_analytics(previous_filters)
        previous_purchase_total = apply_date_window(
            Commande.query,
            Commande.date_cde,
            comparison_start,
            comparison_end,
        ).with_entities(func.coalesce(func.sum(Commande.montant), 0)).scalar() or 0

        if previous_sales['chiffre_affaires_brut']:
            sales_comparison_pct = (
                (sales_analytics['chiffre_affaires_brut'] - previous_sales['chiffre_affaires_brut'])
                / previous_sales['chiffre_affaires_brut']
            ) * 100
        if previous_purchase_total:
            purchases_comparison_pct = (
                (montant_total - previous_purchase_total) / previous_purchase_total
            ) * 100
        comparison_label = 'vs période précédente' if filters['comparison'] == 'mom' else 'vs N-1'

    categories = db.session.query(Produit.categorie)\
        .filter(Produit.categorie.isnot(None), Produit.categorie != '')\
        .distinct()\
        .order_by(Produit.categorie.asc())\
        .all()
    produits = Produit.query.filter(Produit.actif.is_(True)).order_by(Produit.nom.asc()).all()
    regions = db.session.query(Vente.region)\
        .filter(Vente.region.isnot(None), Vente.region != '')\
        .distinct()\
        .order_by(Vente.region.asc())\
        .all()

    context = {
        'filtres': filters,
        'filter_querystring': build_filter_querystring(filters),
        'comparison_label': comparison_label,
        'sales_comparison_pct': round(sales_comparison_pct, 1) if sales_comparison_pct is not None else None,
        'purchases_comparison_pct': round(purchases_comparison_pct, 1) if purchases_comparison_pct is not None else None,
        'total_commandes': total_commandes,
        'montant_total': montant_total,
        'total_a_payer': total_a_payer,
        'montant_moyen_commande': montant_moyen_commande,
        'nb_retard': nb_retard,
        'total_fournisseurs_analytiques': supplier_analytics['summary']['total_fournisseurs'],
        'fournisseurs_excellents': supplier_analytics['summary']['fournisseurs_excellents'],
        'fournisseurs_surveillance': supplier_analytics['summary']['fournisseurs_surveillance'],
        'fournisseurs_remplacement': supplier_analytics['summary']['fournisseurs_remplacement'],
        'score_moyen_fournisseurs': supplier_analytics['summary']['score_moyen'],
        'conformite_moyenne_fournisseurs': supplier_analytics['summary']['conformite_moyenne'],
        'rupture_moyenne_fournisseurs': supplier_analytics['summary']['rupture_moyenne'],
        'delai_moyen_fournisseurs': supplier_analytics['summary']['delai_moyen_general'],
        'total_produits_actifs': total_produits_actifs,
        'valeur_stock': valeur_stock,
        'nb_stock_faible': nb_stock_faible,
        'nb_ruptures_stock': nb_ruptures_stock,
        'dernieres_commandes': dernieres_commandes,
        'dernieres_ventes': dernieres_ventes,
        'alertes_stock': alertes_stock,
        'par_entite': par_entite,
        'evolution_achats': evolution_achats,
        'top_fournisseurs': top_fournisseurs,
        'fournisseurs_critiques': fournisseurs_critiques,
        'fournisseurs_matrix_points': supplier_analytics['matrix_points'],
        'fournisseurs_evolution_labels': supplier_analytics['evolution_labels'],
        'fournisseurs_evolution_datasets': supplier_analytics['evolution_datasets'],
        'categories': [row[0] for row in categories],
        'produits': produits,
        'regions': [row[0] for row in regions],
        'canaux': [Vente.CANAL_OFFLINE, Vente.CANAL_ONLINE],
        'types_client': [
            Vente.TYPE_CLIENT_PARTICULIER,
            Vente.TYPE_CLIENT_ENTREPRISE,
            Vente.TYPE_CLIENT_REVENDEUR,
        ],
        'email_reporting_ready': bool(app.config.get('MAIL_SERVER')),
        'dashboard_poll_seconds': app.config.get('DASHBOARD_ALERT_POLL_SECONDS', 30),
        'dashboard_scheduler_enabled': app.config.get('DASHBOARD_SCHEDULER_ENABLED', False),
        'dashboard_ca_threshold': app.config.get('DASHBOARD_CA_ALERT_THRESHOLD'),
        'dashboard_product_threshold': app.config.get('DASHBOARD_PRODUCT_ALERT_THRESHOLD'),
        'subscriptions': DashboardSubscription.query.filter_by(actif=True)
            .order_by(DashboardSubscription.email.asc(), DashboardSubscription.frequency.asc())
            .all(),
    }
    context.update(sales_analytics)
    alerts_payload = get_dashboard_alerts(filters, context)
    context.update({
        'dashboard_alerts': alerts_payload['alerts'],
        'dashboard_alert_counts': alerts_payload['counts'],
        'low_performing_products': get_low_performing_products(filters, limit=5),
    })

    if has_request_context():
        embed_query = context['filter_querystring']
        embed_prefix = urlencode({'token': app.config.get('DASHBOARD_EMBED_TOKEN')})
        full_query = '&'.join(part for part in (embed_prefix, embed_query) if part)
        embed_url = f"{url_for('dashboard_embed', _external=True)}?{full_query}"
        context.update({
            'dashboard_embed_url': embed_url,
            'dashboard_embed_code': (
                f'<iframe src="{embed_url}" width="100%" height="720" '
                'frameborder="0" loading="lazy"></iframe>'
            ),
            'dashboard_alerts_api_url': (
                f"{url_for('api_dashboard_alerts')}?{context['filter_querystring']}"
                if context['filter_querystring'] else url_for('api_dashboard_alerts')
            ),
        })
    else:
        context.update({
            'dashboard_embed_url': None,
            'dashboard_embed_code': '',
            'dashboard_alerts_api_url': None,
        })
    return context

def build_filter_querystring(filters):
    """Construit une query string à partir des filtres dashboard."""
    params = {}
    if filters.get('period'):
        params['period'] = filters['period']
    if filters.get('comparison') and filters['comparison'] != 'none':
        params['comparison'] = filters['comparison']
    if filters.get('start_date'):
        params['start_date'] = filters['start_date'].isoformat()
    if filters.get('end_date'):
        params['end_date'] = filters['end_date'].isoformat()
    for key in ('categorie', 'produit_id', 'canal', 'region', 'type_client'):
        value = filters.get(key)
        if value:
            params[key] = value
    return urlencode(params)

def get_filtered_commandes_query(filters):
    return apply_date_window(
        Commande.query.options(selectinload(Commande.fournisseur)),
        Commande.date_cde,
        filters['start_date'],
        filters['end_date'],
    )

def get_filtered_ventes_query(filters):
    return apply_sales_filters_to_vente_query(
        Vente.query.options(selectinload(Vente.lignes).selectinload(LigneVente.produit)),
        filters
    )

def get_filtered_lignes_query(filters):
    return apply_sales_filters_to_line_query(
        db.session.query(LigneVente, Vente, Produit)
        .join(Vente, Vente.id == LigneVente.vente_id)
        .join(Produit, Produit.id == LigneVente.produit_id),
        filters
    )

def get_filtered_stock_query(filters):
    query = Produit.query.filter(Produit.actif.is_(True))
    if filters.get('categorie'):
        query = query.filter(Produit.categorie == filters['categorie'])
    if (filters.get('produit_id') or '').isdigit():
        query = query.filter(Produit.id == int(filters['produit_id']))
    return query

def calculate_next_send_at(frequency, reference=None):
    """Calcule la prochaine échéance d'envoi email."""
    reference = reference or datetime.utcnow()
    next_run = reference.replace(hour=8, minute=0, second=0, microsecond=0)
    if next_run <= reference:
        next_run += timedelta(days=1)

    frequency = (frequency or DashboardSubscription.FREQUENCY_DAILY).upper()
    if frequency == DashboardSubscription.FREQUENCY_WEEKLY:
        while next_run.weekday() != 0:
            next_run += timedelta(days=1)
    return next_run

def autosize_excel_worksheet(worksheet):
    for column in worksheet.columns:
        max_length = 0
        column_letter = column[0].column_letter
        for cell in column:
            try:
                max_length = max(max_length, len(str(cell.value or '')))
            except Exception:
                pass
        worksheet.column_dimensions[column_letter].width = min(max_length + 2, 45)

def build_dashboard_excel_bytes(filters, context=None):
    """Construit un export Excel brut du dashboard filtré."""
    context = context or build_dashboard_context(filters)
    commandes = get_filtered_commandes_query(filters).all()
    ventes = get_filtered_ventes_query(filters).all()
    lignes = get_filtered_lignes_query(filters).all()
    produits = get_filtered_stock_query(filters).all()

    kpi_rows = [
        {'Indicateur': 'Commandes', 'Valeur': context['total_commandes']},
        {'Indicateur': 'Montant achats', 'Valeur': context['montant_total']},
        {'Indicateur': 'À payer fournisseurs', 'Valeur': context['total_a_payer']},
        {'Indicateur': 'Commandes en retard', 'Valeur': context['nb_retard']},
        {'Indicateur': 'Ventes', 'Valeur': context['total_ventes']},
        {'Indicateur': 'CA brut', 'Valeur': context['chiffre_affaires_brut']},
        {'Indicateur': 'CA net', 'Valeur': context['chiffre_affaires_net']},
        {'Indicateur': 'Encaissements', 'Valeur': context['total_encaisse']},
        {'Indicateur': 'CLV moyen', 'Valeur': context['clv_moyen']},
        {'Indicateur': 'Valeur du stock', 'Valeur': context['valeur_stock']},
    ]

    commandes_rows = [{
        'Nr.': commande.nr,
        'Date CDE': commande.date_cde,
        'Entité': commande.entite,
        'Acheteur': commande.acheteur,
        'Fournisseur': commande.fournisseur.nom if commande.fournisseur else None,
        'Affaire': commande.affaire,
        'Bon commande': commande.bon_commande,
        'Date livraison': commande.date_livraison,
        'Date réception': commande.date_reception,
        'Montant': commande.montant,
        'Avance': commande.avance,
        'Solde': commande.solde,
        'Conforme': commande.commande_conforme,
        'Rupture fournisseur': commande.rupture_fournisseur,
        'Note performance': commande.note_fournisseur,
        'Note SAV': commande.note_service,
        'Statut': commande.statut,
        'Avancement': commande.get_statut_avancement(),
        'Niveau processus': commande.get_niveau_processus(),
        'Date paiement': commande.date_paiement,
    } for commande in commandes]

    ventes_rows = [{
        'Référence': vente.reference,
        'Date vente': vente.date_vente,
        'Client': vente.client_nom,
        'Téléphone': vente.client_telephone,
        'Canal': vente.canal_vente,
        'Région': vente.region,
        'Type client': vente.type_client,
        'Montant total': vente.montant_total,
        'Montant payé': vente.montant_paye,
        'Montant retour': vente.montant_retour,
        'Montant net': vente.montant_net,
        'Solde': vente.solde,
        'Statut': vente.statut_paiement,
    } for vente in ventes]

    lignes_rows = [{
        'Référence vente': vente.reference,
        'Date vente': vente.date_vente,
        'Produit': produit.nom,
        'Famille': produit.famille,
        'Catégorie': produit.categorie,
        'Quantité': ligne.quantite,
        'Prix unitaire': ligne.prix_unitaire,
        'Montant total': ligne.montant_total,
        'Canal': vente.canal_vente,
        'Région': vente.region,
        'Type client': vente.type_client,
    } for ligne, vente, produit in lignes]

    stock_rows = [{
        'Produit': produit.nom,
        'Code': produit.code,
        'Famille': produit.famille,
        'Catégorie': produit.categorie,
        'Prix unitaire': produit.prix_unitaire,
        'Stock actuel': produit.stock_actuel,
        'Stock minimum': produit.stock_minimum,
        'Valeur stock': produit.valeur_stock,
        'État': 'RUPTURE' if (produit.stock_actuel or 0) <= 0 else 'FAIBLE' if produit.est_stock_faible() else 'OK',
    } for produit in produits]

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pd.DataFrame(kpi_rows).to_excel(writer, sheet_name='KPI', index=False)
        pd.DataFrame(commandes_rows).to_excel(writer, sheet_name='Commandes', index=False)
        pd.DataFrame(ventes_rows).to_excel(writer, sheet_name='Ventes', index=False)
        pd.DataFrame(lignes_rows).to_excel(writer, sheet_name='Lignes ventes', index=False)
        pd.DataFrame(stock_rows).to_excel(writer, sheet_name='Stock', index=False)

        for worksheet in writer.sheets.values():
            autosize_excel_worksheet(worksheet)

    output.seek(0)
    return output

def build_dashboard_pdf_bytes(filters, context=None):
    """Construit un rapport PDF synthétique du dashboard."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    context = context or build_dashboard_context(filters)
    alerts_payload = get_dashboard_alerts(filters, context)

    def money(value):
        return f"{float(value or 0):,.0f} FCFA"

    period_label = {
        'today': "Aujourd'hui",
        'week': 'Cette semaine',
        'month': 'Ce mois',
        'year': 'Cette année',
        'custom': 'Période personnalisée',
        'all': 'Historique complet',
    }.get(filters.get('period'), 'Période analytique')

    output = BytesIO()
    styles = getSampleStyleSheet()
    story = [
        Paragraph('Rapport analytique complet', styles['Title']),
        Paragraph(f'Période: {period_label}', styles['Normal']),
        Paragraph(f"Généré le {datetime.now().strftime('%d/%m/%Y %H:%M')}", styles['Normal']),
        Spacer(1, 16),
    ]

    kpi_data = [
        ['Indicateur', 'Valeur'],
        ['Commandes', context['total_commandes']],
        ['Montant achats', money(context['montant_total'])],
        ['A payer fournisseurs', money(context['total_a_payer'])],
        ['Ventes', context['total_ventes']],
        ['CA brut', money(context['chiffre_affaires_brut'])],
        ['CA net', money(context['chiffre_affaires_net'])],
        ['Encaissements', money(context['total_encaisse'])],
        ['CLV moyen', money(context['clv_moyen'])],
        ['Valeur stock', money(context['valeur_stock'])],
    ]
    kpi_table = Table(kpi_data, repeatRows=1)
    kpi_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e6d')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#d9dee8')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f7f9fc')]),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    story.extend([
        Paragraph('Synthèse KPI', styles['Heading2']),
        kpi_table,
        Spacer(1, 18),
    ])

    alert_rows = [['Niveau', 'Catégorie', 'Message']]
    for alert in alerts_payload['alerts'][:8]:
        alert_rows.append([alert['level'].upper(), alert['category'], alert['message']])
    if len(alert_rows) == 1:
        alert_rows.append(['INFO', 'SYSTEME', 'Aucune alerte active'])
    alert_table = Table(alert_rows, repeatRows=1)
    alert_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#198754')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#d9dee8')),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    story.extend([
        Paragraph('Alertes actives', styles['Heading2']),
        alert_table,
        Spacer(1, 18),
    ])

    top_products_data = [['Produit', 'Catégorie', 'CA', 'Part CA']]
    for produit in context['top_produits'][:8]:
        top_products_data.append([
            produit['nom'],
            produit['categorie'],
            money(produit['chiffre_affaires']),
            f"{float(produit['part_ca'] or 0):.1f}%",
        ])
    if len(top_products_data) == 1:
        top_products_data.append(['-', '-', '0 FCFA', '0%'])

    top_suppliers_data = [['Fournisseur', 'Montant achats', 'Score', 'Conformité', 'Fiabilité']]
    for fournisseur in context['top_fournisseurs'][:8]:
        top_suppliers_data.append([
            fournisseur['nom'],
            money(fournisseur['total_montant']),
            f"{float(fournisseur['score_performance'] or 0):.2f}/5" if fournisseur.get('score_performance') is not None else '-',
            f"{float(fournisseur['taux_conformite'] or 0):.1f}%",
            f"{float(fournisseur['score_fiabilite'] or 0):.1f}/100",
        ])
    if len(top_suppliers_data) == 1:
        top_suppliers_data.append(['-', '0 FCFA', '-', '0%', '0/100'])

    top_products_table = Table(top_products_data, repeatRows=1)
    top_products_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#dc3545')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#d9dee8')),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    top_suppliers_table = Table(top_suppliers_data, repeatRows=1)
    top_suppliers_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0d6efd')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#d9dee8')),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))

    story.extend([
        Paragraph('Top produits ventes', styles['Heading2']),
        top_products_table,
        Spacer(1, 18),
        Paragraph('Performance fournisseurs', styles['Heading2']),
        top_suppliers_table,
    ])

    doc = SimpleDocTemplate(output, pagesize=A4, leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)
    doc.build(story)
    output.seek(0)
    return output

def build_supplier_performance_excel_bytes(filters, analytics=None):
    """Construit un export Excel dédié à la performance fournisseurs."""
    analytics = analytics or build_supplier_performance_data(
        start_date=filters.get('start_date'),
        end_date=filters.get('end_date'),
        include_inactive=filters.get('include_inactive', True),
        evolution_months=8,
    )
    output = BytesIO()

    summary_rows = [{
        'Période': get_period_label(filters),
        'Fournisseurs suivis': analytics['summary']['total_fournisseurs'],
        'Excellents': analytics['summary']['fournisseurs_excellents'],
        'À surveiller': analytics['summary']['fournisseurs_surveillance'],
        'À remplacer': analytics['summary']['fournisseurs_remplacement'],
        'Score moyen': analytics['summary']['score_moyen'],
        'Conformité moyenne (%)': analytics['summary']['conformite_moyenne'],
        'Rupture moyenne (%)': analytics['summary']['rupture_moyenne'],
        'Délai moyen global (j)': analytics['summary']['delai_moyen_general'],
        'Inclure inactifs': 'Oui' if filters.get('include_inactive') else 'Non',
    }]
    suppliers_rows = [{
        'Fournisseur': supplier['nom'],
        'Pays': supplier['pays'] or '',
        'Quadrant': supplier['quadrant'],
        'Commandes': supplier['total_commandes'],
        'Montant achats': supplier['total_montant'],
        'Montant moyen': supplier['montant_moyen'],
        'Montant à payer': supplier['montant_a_payer'],
        'Score performance': supplier['score_performance'],
        'Fiabilité (/100)': supplier['score_fiabilite'],
        'Délai moyen (j)': supplier['delai_moyen'],
        'Respect délai (%)': supplier['respect_delai'],
        'Conformité (%)': supplier['taux_conformite'],
        'Rupture (%)': supplier['taux_rupture'],
        'Retard (%)': supplier['taux_retard'],
        'Paiement (%)': supplier['taux_paiement'],
        'Part achats (%)': supplier['part_achats'],
        'Prix vs marché (%)': supplier['price_competitiveness_pct'],
    } for supplier in analytics['items']]
    evolution_rows = []
    for dataset in analytics['evolution_datasets']:
        for month_label, score in zip(analytics['evolution_labels'], dataset['scores']):
            evolution_rows.append({
                'Fournisseur': dataset['label'],
                'Mois': month_label,
                'Score moyen': score,
            })
    critical_rows = [{
        'Fournisseur': supplier['nom'],
        'Quadrant': supplier['quadrant'],
        'Montant à payer': supplier['montant_a_payer'],
        'Taux retard (%)': supplier['taux_retard'],
        'Taux rupture (%)': supplier['taux_rupture'],
        'Délai moyen (j)': supplier['delai_moyen'],
        'Fiabilité (/100)': supplier['score_fiabilite'],
    } for supplier in analytics['critical_items']]

    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name='Synthese', index=False)
        pd.DataFrame(suppliers_rows).to_excel(writer, sheet_name='Fournisseurs', index=False)
        pd.DataFrame(evolution_rows or [{'Fournisseur': '', 'Mois': '', 'Score moyen': ''}]).to_excel(
            writer,
            sheet_name='Evolution',
            index=False,
        )
        pd.DataFrame(critical_rows or [{
            'Fournisseur': '',
            'Quadrant': '',
            'Montant à payer': '',
            'Taux retard (%)': '',
            'Taux rupture (%)': '',
            'Délai moyen (j)': '',
            'Fiabilité (/100)': '',
        }]).to_excel(writer, sheet_name='Critiques', index=False)

        for worksheet in writer.sheets.values():
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        max_length = max(max_length, len(str(cell.value or '')))
                    except Exception:
                        continue
                worksheet.column_dimensions[column_letter].width = min(max_length + 2, 40)

    output.seek(0)
    return output

def build_supplier_performance_pdf_bytes(filters, analytics=None):
    """Construit un rapport PDF synthétique dédié aux fournisseurs."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    analytics = analytics or build_supplier_performance_data(
        start_date=filters.get('start_date'),
        end_date=filters.get('end_date'),
        include_inactive=filters.get('include_inactive', True),
        evolution_months=8,
    )

    def fmt_money(value):
        return f"{float(value or 0):,.0f} FCFA"

    output = BytesIO()
    styles = getSampleStyleSheet()
    story = [
        Paragraph('Rapport performance fournisseurs', styles['Title']),
        Paragraph(f"Période: {get_period_label(filters)}", styles['Normal']),
        Paragraph(f"Généré le {datetime.now().strftime('%d/%m/%Y %H:%M')}", styles['Normal']),
        Spacer(1, 16),
    ]

    summary_table = Table([
        ['Indicateur', 'Valeur'],
        ['Fournisseurs suivis', analytics['summary']['total_fournisseurs']],
        ['Excellents', analytics['summary']['fournisseurs_excellents']],
        ['À surveiller', analytics['summary']['fournisseurs_surveillance']],
        ['À remplacer', analytics['summary']['fournisseurs_remplacement']],
        ['Score moyen', f"{float(analytics['summary']['score_moyen'] or 0):.2f}/5"],
        ['Conformité moyenne', f"{float(analytics['summary']['conformite_moyenne'] or 0):.1f}%"],
        ['Rupture moyenne', f"{float(analytics['summary']['rupture_moyenne'] or 0):.1f}%"],
        ['Délai moyen global', f"{float(analytics['summary']['delai_moyen_general'] or 0):.1f} j"],
    ], repeatRows=1)
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e6d')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#d9dee8')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f7f9fc')]),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    story.extend([
        Paragraph('Synthèse KPI', styles['Heading2']),
        summary_table,
        Spacer(1, 16),
    ])

    supplier_rows = [['Fournisseur', 'Montant', 'Score', 'Délai', 'Conformité', 'Rupture', 'Prix vs marché', 'Fiabilité']]
    for supplier in analytics['items'][:12]:
        supplier_rows.append([
            supplier['nom'],
            fmt_money(supplier['total_montant']),
            f"{float(supplier['score_performance'] or 0):.2f}/5" if supplier['score_performance'] is not None else '-',
            f"{float(supplier['delai_moyen'] or 0):.1f} j" if supplier['delai_moyen'] is not None else '-',
            f"{float(supplier['taux_conformite'] or 0):.1f}%",
            f"{float(supplier['taux_rupture'] or 0):.1f}%",
            f"{float(supplier['price_competitiveness_pct'] or 0):+.1f}%" if supplier['price_competitiveness_pct'] is not None else '-',
            f"{float(supplier['score_fiabilite'] or 0):.1f}/100",
        ])
    if len(supplier_rows) == 1:
        supplier_rows.append(['-', '0 FCFA', '-', '-', '0%', '0%', '-', '0/100'])

    supplier_table = Table(supplier_rows, repeatRows=1)
    supplier_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0d6efd')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#d9dee8')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f7f9fc')]),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    story.extend([
        Paragraph('Classement fournisseurs', styles['Heading2']),
        supplier_table,
    ])

    if analytics['critical_items']:
        critical_rows = [['Fournisseur', 'Quadrant', 'À payer', 'Retard', 'Rupture']]
        for supplier in analytics['critical_items'][:8]:
            critical_rows.append([
                supplier['nom'],
                supplier['quadrant'],
                fmt_money(supplier['montant_a_payer']),
                f"{float(supplier['taux_retard'] or 0):.1f}%",
                f"{float(supplier['taux_rupture'] or 0):.1f}%",
            ])
        critical_table = Table(critical_rows, repeatRows=1)
        critical_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#dc3545')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#d9dee8')),
            ('PADDING', (0, 0), (-1, -1), 6),
        ]))
        story.extend([
            Spacer(1, 16),
            Paragraph('Fournisseurs critiques', styles['Heading2']),
            critical_table,
        ])

    doc = SimpleDocTemplate(output, pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)
    doc.build(story)
    output.seek(0)
    return output

def build_supplier_detail_excel_bytes(fournisseur, filters, payload=None):
    """Construit un export Excel détaillé pour un fournisseur."""
    payload = payload or get_supplier_detail_payload(fournisseur, filters, evolution_months=12)
    supplier_stats = payload['supplier_stats']
    output = BytesIO()

    summary_rows = [{
        'Fournisseur': fournisseur.nom,
        'Période': get_period_label(filters),
        'Commandes': supplier_stats['total_commandes'],
        'Montant total': supplier_stats['total_montant'],
        'Montant à payer': supplier_stats['montant_a_payer'],
        'Montant moyen': supplier_stats['montant_moyen'],
        'Score performance': supplier_stats['score_performance'],
        'Conformité (%)': supplier_stats['taux_conformite'],
        'Rupture (%)': supplier_stats['taux_rupture'],
        'Délai moyen (j)': supplier_stats['delai_moyen'],
        'Taux paiement (%)': supplier_stats['taux_paiement'],
        'Prix vs marché (%)': supplier_stats['price_competitiveness_pct'],
        'Fiabilité (/100)': supplier_stats['score_fiabilite'],
        'Quadrant': supplier_stats['quadrant'],
    }]
    commandes_rows = [{
        'Nr': commande.nr,
        'Date': commande.date_cde,
        'Acheteur': commande.acheteur,
        'Description': commande.affaire,
        'Montant': commande.montant,
        'Statut': commande.statut,
        'Date livraison': commande.date_livraison,
        'Date réception': commande.date_reception,
        'Délai réel (j)': commande.get_ecart_livraison(),
        'Conforme': commande.commande_conforme,
        'Rupture': commande.rupture_fournisseur,
        'Note fournisseur': commande.note_fournisseur,
        'Note service': commande.note_service,
        'Prix vs marché (%)': commande.get_ecart_prix_marche_pct(),
    } for commande in payload['commandes']]
    evolution_rows = [{
        'Mois': row['mois'],
        'Montant achats': row['total'],
        'Score moyen': payload['score_evolution'][index] if index < len(payload['score_evolution']) else None,
    } for index, row in enumerate(payload['evolution'])]

    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pd.DataFrame(summary_rows).to_excel(writer, sheet_name='Synthese', index=False)
        pd.DataFrame(commandes_rows or [{
            'Nr': '',
            'Date': '',
            'Acheteur': '',
            'Description': '',
            'Montant': '',
            'Statut': '',
            'Date livraison': '',
            'Date réception': '',
            'Délai réel (j)': '',
            'Conforme': '',
            'Rupture': '',
            'Note fournisseur': '',
            'Note service': '',
            'Prix vs marché (%)': '',
        }]).to_excel(writer, sheet_name='Commandes', index=False)
        pd.DataFrame(evolution_rows or [{'Mois': '', 'Montant achats': '', 'Score moyen': ''}]).to_excel(
            writer,
            sheet_name='Evolution',
            index=False,
        )
        for worksheet in writer.sheets.values():
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        max_length = max(max_length, len(str(cell.value or '')))
                    except Exception:
                        continue
                worksheet.column_dimensions[column_letter].width = min(max_length + 2, 45)

    output.seek(0)
    return output

def build_supplier_detail_pdf_bytes(fournisseur, filters, payload=None):
    """Construit un rapport PDF détaillé pour un fournisseur."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    payload = payload or get_supplier_detail_payload(fournisseur, filters, evolution_months=12)
    supplier_stats = payload['supplier_stats']

    def fmt_money(value):
        return f"{float(value or 0):,.0f} FCFA"

    output = BytesIO()
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f'Rapport fournisseur: {fournisseur.nom}', styles['Title']),
        Paragraph(f"Période: {get_period_label(filters)}", styles['Normal']),
        Paragraph(f"Généré le {datetime.now().strftime('%d/%m/%Y %H:%M')}", styles['Normal']),
        Spacer(1, 16),
    ]

    summary_table = Table([
        ['Indicateur', 'Valeur'],
        ['Commandes', supplier_stats['total_commandes']],
        ['Montant total', fmt_money(supplier_stats['total_montant'])],
        ['À payer', fmt_money(supplier_stats['montant_a_payer'])],
        ['Montant moyen', fmt_money(supplier_stats['montant_moyen'])],
        ['Score performance', f"{float(supplier_stats['score_performance'] or 0):.2f}/5" if supplier_stats['score_performance'] is not None else '-'],
        ['Conformité', f"{float(supplier_stats['taux_conformite'] or 0):.1f}%"],
        ['Rupture', f"{float(supplier_stats['taux_rupture'] or 0):.1f}%"],
        ['Délai moyen', f"{float(supplier_stats['delai_moyen'] or 0):.1f} j" if supplier_stats['delai_moyen'] is not None else '-'],
        ['Taux paiement', f"{float(supplier_stats['taux_paiement'] or 0):.1f}%"],
        ['Prix vs marché', f"{float(supplier_stats['price_competitiveness_pct'] or 0):+.1f}%" if supplier_stats['price_competitiveness_pct'] is not None else '-'],
        ['Fiabilité', f"{float(supplier_stats['score_fiabilite'] or 0):.1f}/100"],
    ], repeatRows=1)
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e6d')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#d9dee8')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f7f9fc')]),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    story.extend([
        Paragraph('Synthèse KPI', styles['Heading2']),
        summary_table,
        Spacer(1, 16),
    ])

    commandes_rows = [['Nr', 'Date', 'Acheteur', 'Montant', 'Délai', 'Conforme', 'Rupture', 'Score', 'Prix vs marché']]
    for commande in payload['commandes'][:15]:
        score_commande = average_non_null(commande.note_fournisseur, commande.note_service)
        commandes_rows.append([
            commande.nr or '-',
            commande.date_cde.strftime('%d/%m/%Y') if commande.date_cde else '-',
            commande.acheteur or '-',
            fmt_money(commande.montant),
            f"{commande.get_ecart_livraison()} j" if commande.get_ecart_livraison() is not None else '-',
            'Oui' if commande.commande_conforme else 'Non',
            'Oui' if commande.rupture_fournisseur else 'Non',
            f"{float(score_commande or 0):.1f}/5" if score_commande is not None else '-',
            f"{float(commande.get_ecart_prix_marche_pct() or 0):+.1f}%" if commande.get_ecart_prix_marche_pct() is not None else '-',
        ])
    if len(commandes_rows) == 1:
        commandes_rows.append(['-', '-', '-', '0 FCFA', '-', '-', '-', '-', '-'])

    commandes_table = Table(commandes_rows, repeatRows=1)
    commandes_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0d6efd')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#d9dee8')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f7f9fc')]),
        ('PADDING', (0, 0), (-1, -1), 5),
    ]))
    story.extend([
        Paragraph('Historique commandes', styles['Heading2']),
        commandes_table,
    ])

    doc = SimpleDocTemplate(output, pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)
    doc.build(story)
    output.seek(0)
    return output

def get_low_performing_products(filters, limit=5):
    """Identifie les produits actifs avec stock mais faible traction commerciale."""
    line_subquery = get_filtered_lignes_query(filters).with_entities(
        Produit.id.label('produit_id'),
        func.coalesce(func.sum(LigneVente.quantite), 0).label('quantite_vendue'),
        func.coalesce(func.sum(LigneVente.montant_total), 0).label('chiffre_affaires'),
        func.count(func.distinct(LigneVente.vente_id)).label('nb_ventes'),
    ).group_by(Produit.id).subquery()

    query = db.session.query(
        Produit.id,
        Produit.nom,
        Produit.categorie,
        Produit.stock_actuel,
        Produit.stock_minimum,
        func.coalesce(line_subquery.c.quantite_vendue, 0).label('quantite_vendue'),
        func.coalesce(line_subquery.c.chiffre_affaires, 0).label('chiffre_affaires'),
        func.coalesce(line_subquery.c.nb_ventes, 0).label('nb_ventes'),
    ).outerjoin(line_subquery, line_subquery.c.produit_id == Produit.id)\
     .filter(Produit.actif.is_(True))

    if filters.get('categorie'):
        query = query.filter(Produit.categorie == filters['categorie'])
    if (filters.get('produit_id') or '').isdigit():
        query = query.filter(Produit.id == int(filters['produit_id']))

    rows = query.order_by(
        func.coalesce(line_subquery.c.chiffre_affaires, 0).asc(),
        Produit.stock_actuel.desc(),
        Produit.nom.asc(),
    ).limit(max(limit * 3, 10)).all()

    threshold = app.config['DASHBOARD_PRODUCT_ALERT_THRESHOLD']
    results = []
    for row in rows:
        if (row.stock_actuel or 0) <= 0:
            continue
        if (row.chiffre_affaires or 0) > threshold and (row.nb_ventes or 0) > 0:
            continue
        results.append({
            'id': row.id,
            'nom': row.nom,
            'categorie': row.categorie or 'Non classée',
            'stock_actuel': float(row.stock_actuel or 0),
            'stock_minimum': float(row.stock_minimum or 0),
            'quantite_vendue': float(row.quantite_vendue or 0),
            'chiffre_affaires': float(row.chiffre_affaires or 0),
            'nb_ventes': int(row.nb_ventes or 0),
        })
        if len(results) >= limit:
            break

    return results

def get_dashboard_alerts(filters=None, context=None):
    """Construit les alertes métier du dashboard."""
    filters = filters or get_default_sales_filters()
    context = context or build_dashboard_context(filters)
    alerts = []

    if (context.get('chiffre_affaires_net') or 0) < app.config['DASHBOARD_CA_ALERT_THRESHOLD']:
        alerts.append({
            'id': 'ca-threshold',
            'level': 'warning',
            'category': 'CA',
            'title': 'Seuil de chiffre d’affaires non atteint',
            'message': (
                f"Le CA net est à {float(context.get('chiffre_affaires_net') or 0):,.0f} FCFA, "
                f"sous le seuil de {app.config['DASHBOARD_CA_ALERT_THRESHOLD']:,.0f} FCFA."
            ),
            'url': url_for('performances_ventes') if has_request_context() else None,
        })

    if (context.get('nb_ruptures_stock') or 0) > 0:
        alerts.append({
            'id': 'rupture-stock',
            'level': 'danger',
            'category': 'STOCK',
            'title': 'Rupture de stock',
            'message': f"{context['nb_ruptures_stock']} produit(s) sont en rupture de stock.",
            'url': url_for('stocks', etat='rupture') if has_request_context() else None,
        })

    if (context.get('nb_stock_faible') or 0) > 0:
        alerts.append({
            'id': 'stock-faible',
            'level': 'warning',
            'category': 'STOCK',
            'title': 'Stock faible',
            'message': f"{context['nb_stock_faible']} produit(s) sont au seuil minimum ou en dessous.",
            'url': url_for('stocks', etat='faible') if has_request_context() else None,
        })

    low_products = get_low_performing_products(filters, limit=3)
    for produit in low_products:
        alerts.append({
            'id': f'produit-{produit["id"]}',
            'level': 'info' if produit['nb_ventes'] > 0 else 'warning',
            'category': 'PRODUIT',
            'title': 'Performance produit faible',
            'message': (
                f"{produit['nom']} ne génère que {produit['chiffre_affaires']:,.0f} FCFA "
                f"pour {produit['nb_ventes']} vente(s) avec un stock de {produit['stock_actuel']:,.0f}."
            ),
            'url': url_for('stocks') if has_request_context() else None,
        })

    if context.get('fournisseurs_critiques'):
        top_critical = context['fournisseurs_critiques'][0]
        alerts.append({
            'id': f'fournisseur-{top_critical["id"]}',
            'level': 'warning',
            'category': 'FOURNISSEUR',
            'title': 'Fournisseur critique',
            'message': (
                f"{top_critical['nom']} cumule {top_critical['taux_retard']:.1f}% de retard "
                f"et {top_critical['montant_a_payer']:,.0f} FCFA à payer."
            ),
            'url': url_for('performances_fournisseurs') if has_request_context() else None,
        })

    if (context.get('total_solde') or 0) > 0:
        alerts.append({
            'id': 'solde-encaissement',
            'level': 'info',
            'category': 'ENCAISSEMENT',
            'title': 'Encaissements à relancer',
            'message': f"{context['total_solde']:,.0f} FCFA restent à encaisser sur les ventes.",
            'url': url_for('ventes') if has_request_context() else None,
        })

    severity_order = {'danger': 0, 'warning': 1, 'info': 2, 'success': 3}
    alerts.sort(key=lambda item: (severity_order.get(item['level'], 9), item['category'], item['title']))
    counts = {
        'total': len(alerts),
        'danger': sum(1 for item in alerts if item['level'] == 'danger'),
        'warning': sum(1 for item in alerts if item['level'] == 'warning'),
        'info': sum(1 for item in alerts if item['level'] == 'info'),
    }
    return {
        'alerts': alerts,
        'counts': counts,
        'generated_at': datetime.utcnow().isoformat(),
    }

def build_dashboard_email_body(context, alerts_payload):
    """Construit un email texte simple pour les abonnements dashboard."""
    lines = [
        'Rapport automatique du tableau de bord',
        '',
        f"Commandes: {context['total_commandes']}",
        f"Montant achats: {float(context['montant_total'] or 0):,.0f} FCFA",
        f"Ventes: {context['total_ventes']}",
        f"CA net: {float(context['chiffre_affaires_net'] or 0):,.0f} FCFA",
        f"Encaissements: {float(context['total_encaisse'] or 0):,.0f} FCFA",
        f"Valeur du stock: {float(context['valeur_stock'] or 0):,.0f} FCFA",
        '',
        'Alertes:',
    ]
    if alerts_payload['alerts']:
        for alert in alerts_payload['alerts'][:10]:
            lines.append(f"- [{alert['level'].upper()}] {alert['title']}: {alert['message']}")
    else:
        lines.append('- Aucune alerte active')
    return '\n'.join(lines)

def send_dashboard_report_email(subscription, filters=None):
    """Envoie le rapport dashboard par email."""
    if not app.config.get('MAIL_SERVER'):
        raise ValueError('MAIL_SERVER n\'est pas configuré')

    filters = filters or get_default_sales_filters()
    with app.app_context():
        with app.test_request_context('/dashboard'):
            context = build_dashboard_context(filters)
            alerts_payload = get_dashboard_alerts(filters, context)
            message = EmailMessage()
            message['Subject'] = f"Rapport dashboard - {datetime.now().strftime('%d/%m/%Y')}"
            message['From'] = app.config['DASHBOARD_REPORT_SENDER']
            message['To'] = subscription.email
            message.set_content(build_dashboard_email_body(context, alerts_payload))

            if subscription.include_pdf:
                try:
                    message.add_attachment(
                        build_dashboard_pdf_bytes(filters, context).getvalue(),
                        maintype='application',
                        subtype='pdf',
                        filename=f"rapport_dashboard_{datetime.now().strftime('%Y%m%d')}.pdf",
                    )
                except ModuleNotFoundError as exc:
                    message.set_content(
                        build_dashboard_email_body(context, alerts_payload) +
                        f"\n\nPièce jointe PDF ignorée: dépendance manquante ({exc.name})."
                    )

            if subscription.include_excel:
                message.add_attachment(
                    build_dashboard_excel_bytes(filters, context).getvalue(),
                    maintype='application',
                    subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    filename=f"dashboard_brut_{datetime.now().strftime('%Y%m%d')}.xlsx",
                )

            with smtplib.SMTP(app.config['MAIL_SERVER'], app.config['MAIL_PORT'], timeout=20) as smtp:
                if app.config.get('MAIL_USE_TLS'):
                    smtp.starttls()
                if app.config.get('MAIL_USERNAME'):
                    smtp.login(app.config['MAIL_USERNAME'], app.config.get('MAIL_PASSWORD') or '')
                smtp.send_message(message)

def process_dashboard_subscriptions():
    """Traite les abonnements à échéance."""
    with app.app_context():
        now = datetime.utcnow()
        due_subscriptions = DashboardSubscription.query.filter(
            DashboardSubscription.actif.is_(True),
            or_(
                DashboardSubscription.next_send_at.is_(None),
                DashboardSubscription.next_send_at <= now
            )
        ).all()

        for subscription in due_subscriptions:
            try:
                send_dashboard_report_email(subscription)
                subscription.last_sent_at = now
                subscription.next_send_at = calculate_next_send_at(subscription.frequency, now)
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                print(f"Erreur abonnement dashboard {subscription.email}: {exc}")

def start_dashboard_scheduler():
    """Démarre le scheduler d'envoi si activé par configuration."""
    global dashboard_scheduler, scheduler_lock_handle

    if dashboard_scheduler is not None or not app.config.get('DASHBOARD_SCHEDULER_ENABLED'):
        return dashboard_scheduler

    lock_file_path = app.config.get('SCHEDULER_LOCK_FILE')
    if lock_file_path:
        os.makedirs(os.path.dirname(lock_file_path), exist_ok=True)
        scheduler_lock_handle = open(lock_file_path, 'a+')
        try:
            fcntl.flock(scheduler_lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            app.logger.info('Dashboard scheduler not started in this process: lock already held')
            return dashboard_scheduler

    from apscheduler.schedulers.background import BackgroundScheduler

    dashboard_scheduler = BackgroundScheduler(timezone='UTC')
    dashboard_scheduler.add_job(
        func=process_dashboard_subscriptions,
        trigger='interval',
        minutes=10,
        id='dashboard_subscription_job',
        replace_existing=True,
        max_instances=1,
    )
    dashboard_scheduler.start()
    app.logger.info('Dashboard scheduler started')
    return dashboard_scheduler

def is_dashboard_embed_authorized():
    token = (request.args.get('token') or request.headers.get('X-Embed-Token') or '').strip()
    return bool(token) and token == app.config.get('DASHBOARD_EMBED_TOKEN')

# ==================== HEALTH CHECKS ====================

@app.route('/healthz')
def healthz():
    return jsonify({
        'status': 'ok',
        'service': 'suivi-commandes',
        'environment': app.config.get('ENVIRONMENT'),
        'timestamp': datetime.utcnow().isoformat(),
    })


@app.route('/readyz')
def readyz():
    try:
        ensure_database_ready()
        db.session.execute(text('SELECT 1'))
        return jsonify({
            'status': 'ready',
            'database': 'ok',
            'timestamp': datetime.utcnow().isoformat(),
        })
    except Exception as exc:
        db.session.rollback()
        return jsonify({
            'status': 'error',
            'database': 'unavailable',
            'error': str(exc),
            'timestamp': datetime.utcnow().isoformat(),
        }), 503

# ==================== ROUTES AUTHENTIFICATION ====================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for(get_home_endpoint_for_user()))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = Utilisateur.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            if not user.actif:
                app.logger.warning('Login denied for inactive user "%s" from %s', username, request.remote_addr)
                flash('Ce compte est désactivé', 'danger')
                return render_template('login.html')
            
            login_user(user)
            user.last_login = datetime.utcnow()
            enregistrer_log('LOGIN', 'utilisateur', user.id, f'Connexion utilisateur {user.username}')
            db.session.commit()
            app.logger.info('User "%s" logged in from %s', user.username, request.remote_addr)
            
            # Redirection vers la page demandée
            next_page = request.args.get('next')
            flash(f'Bienvenue {user.username}!', 'success')
            return redirect(next_page or url_for(get_home_endpoint_for_user(user)))
        else:
            app.logger.warning('Failed login for "%s" from %s', username, request.remote_addr)
            flash('Nom d\'utilisateur ou mot de passe incorrect', 'danger')
    
    return render_template('login.html')

@app.route('/logout', methods=['POST'])
@login_required
def logout():
    app.logger.info('User "%s" logged out from %s', current_user.username, request.remote_addr)
    enregistrer_log('LOGOUT', 'utilisateur', current_user.id, f'Déconnexion utilisateur {current_user.username}')
    logout_user()
    db.session.commit()
    flash('Vous avez été déconnecté', 'info')
    return redirect(url_for('login'))

# ==================== ROUTES PRINCIPALES ====================

@app.route('/')
@app.route('/dashboard')
@login_required
def dashboard():
    denied_response = require_permission('dashboard_view')
    if denied_response:
        return denied_response

    filters = build_sales_filters(request.args)
    context = build_dashboard_context(filters)
    return render_template('dashboard.html', **context)

@app.route('/dashboard/export/excel')
@login_required
def dashboard_export_excel():
    denied_response = require_permission('dashboard_view')
    if denied_response:
        return denied_response

    filters = build_sales_filters(request.args)
    context = build_dashboard_context(filters)
    output = build_dashboard_excel_bytes(filters, context)
    return send_file(
        output,
        download_name=f'dashboard_brut_{datetime.now().strftime("%Y%m%d_%H%M")}.xlsx',
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@app.route('/dashboard/export/pdf')
@login_required
def dashboard_export_pdf():
    denied_response = require_permission('dashboard_view')
    if denied_response:
        return denied_response

    filters = build_sales_filters(request.args)
    context = build_dashboard_context(filters)
    try:
        output = build_dashboard_pdf_bytes(filters, context)
    except ModuleNotFoundError as exc:
        flash(
            f'Export PDF indisponible: dépendance manquante ({exc.name}). Exécutez pip install -r requirements.txt.',
            'danger'
        )
        return redirect(url_for('dashboard', **request.args))
    return send_file(
        output,
        download_name=f'rapport_dashboard_{datetime.now().strftime("%Y%m%d_%H%M")}.pdf',
        as_attachment=True,
        mimetype='application/pdf'
    )

@app.route('/dashboard/subscriptions', methods=['POST'])
@login_required
def dashboard_subscriptions_create():
    denied_response = require_permission('dashboard_manage', 'dashboard')
    if denied_response:
        return denied_response

    try:
        email = (request.form.get('email') or '').strip().lower()
        frequency = (request.form.get('frequency') or DashboardSubscription.FREQUENCY_DAILY).strip().upper()
        include_pdf = 'include_pdf' in request.form
        include_excel = 'include_excel' in request.form

        if frequency not in {
            DashboardSubscription.FREQUENCY_DAILY,
            DashboardSubscription.FREQUENCY_WEEKLY,
        }:
            raise ValueError('Fréquence invalide')

        valider_email(email)
        if not email:
            raise ValueError('Email obligatoire')

        subscription = DashboardSubscription.query.filter_by(email=email, frequency=frequency).first()
        if subscription is None:
            subscription = DashboardSubscription(email=email, frequency=frequency)
            db.session.add(subscription)

        subscription.include_pdf = include_pdf
        subscription.include_excel = include_excel
        subscription.actif = True
        if subscription.next_send_at is None:
            subscription.next_send_at = calculate_next_send_at(subscription.frequency)

        db.session.flush()
        enregistrer_log('CREATE', 'dashboard_subscription', subscription.id, f'Abonnement dashboard {email}')
        db.session.commit()
        flash('Abonnement dashboard enregistré', 'success')
    except (ValueError, IntegrityError) as e:
        db.session.rollback()
        flash(f'Erreur abonnement dashboard: {str(e)}', 'danger')

    return redirect(url_for('dashboard', **request.args))

@app.route('/dashboard/subscriptions/<int:id>/delete', methods=['POST'])
@login_required
def dashboard_subscription_delete(id):
    denied_response = require_permission('dashboard_manage', 'dashboard')
    if denied_response:
        return denied_response

    subscription = DashboardSubscription.query.get_or_404(id)
    try:
        enregistrer_log('DELETE', 'dashboard_subscription', subscription.id, f'Suppression abonnement {subscription.email}')
        db.session.delete(subscription)
        db.session.commit()
        flash('Abonnement supprimé', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur lors de la suppression: {str(e)}', 'danger')

    return redirect(url_for('dashboard', **request.args))

@app.route('/api/dashboard/alerts')
@login_required
def api_dashboard_alerts():
    denied_response = require_permission('dashboard_view')
    if denied_response:
        return denied_response

    filters = build_sales_filters(request.args)
    context = build_dashboard_context(filters)
    return jsonify(get_dashboard_alerts(filters, context))

@app.route('/embed/dashboard')
def dashboard_embed():
    if not is_dashboard_embed_authorized():
        return render_template('errors/404.html'), 404

    filters = build_sales_filters(request.args)
    context = build_dashboard_context(filters)
    return render_template('dashboard_embed.html', **context)

@app.route('/sw.js')
def service_worker():
    return send_from_directory(app.static_folder, 'sw.js', mimetype='application/javascript')

@app.before_request
def boot_dashboard_automation():
    if app.config.get('DASHBOARD_SCHEDULER_ENABLED') and dashboard_scheduler is None:
        try:
            start_dashboard_scheduler()
        except Exception as exc:
            print(f"Scheduler dashboard indisponible: {exc}")

# ==================== ROUTES COMMANDES ====================

@app.route('/commandes')
@login_required
def commandes():
    denied_response = require_permission('commandes_view')
    if denied_response:
        return denied_response

    # Récupération des filtres
    vue = normalize_commande_list_view(request.args.get('vue', 'en_cours'))
    entite = request.args.get('entite', '')
    statut = request.args.get('statut', '')
    acheteur = request.args.get('acheteur', '')
    fournisseur = request.args.get('fournisseur', '')
    recherche = request.args.get('recherche', '')
    page = get_requested_page()
    
    base_query = Commande.query.options(selectinload(Commande.fournisseur))
    
    if entite:
        base_query = base_query.filter(Commande.entite == entite)
    if statut in {Commande.STATUT_PAYE, Commande.STATUT_A_PAYER}:
        base_query = base_query.filter(Commande.statut == statut)
    if acheteur:
        base_query = base_query.filter(Commande.acheteur == acheteur)
    if fournisseur and fournisseur.isdigit():
        base_query = base_query.filter(Commande.fournisseur_id == int(fournisseur))
    if recherche:
        base_query = base_query.filter(
            or_(
                Commande.affaire.contains(recherche),
                Commande.demandeur.contains(recherche),
                Commande.service_demandeur.contains(recherche),
                Commande.bon_commande.contains(recherche),
                Commande.facture.contains(recherche),
            )
        )

    view_counts = {
        'toutes': base_query.count(),
        'non_payees': base_query.filter(Commande.statut == Commande.STATUT_A_PAYER).count(),
        'payees': base_query.filter(Commande.statut == Commande.STATUT_PAYE).count(),
        'achevees': base_query.filter(commande_completed_expression()).count(),
        'en_cours': base_query.filter(commande_in_progress_expression()).count(),
    }

    query = base_query
    if vue == 'non_payees':
        query = query.filter(Commande.statut == Commande.STATUT_A_PAYER)
    elif vue == 'payees':
        query = query.filter(Commande.statut == Commande.STATUT_PAYE)
    elif vue == 'achevees':
        query = query.filter(commande_completed_expression())
    elif vue == 'en_cours':
        query = query.filter(commande_in_progress_expression())
    
    commandes_pagination = query.order_by(Commande.date_cde.desc()).paginate(
        page=page,
        per_page=app.config.get('DEFAULT_PAGE_SIZE', 25),
        error_out=False,
    )
    commandes = commandes_pagination.items
    
    # Récupération des options pour les filtres
    entites = db.session.query(Commande.entite).distinct().all()
    acheteurs = db.session.query(Commande.acheteur).distinct().all()
    ensure_supplier_reference_data()
    fournisseurs = Fournisseur.query.order_by(Fournisseur.nom).all()
    
    return render_template('commandes.html',
                         commandes=commandes,
                         entites=[e[0] for e in entites if e[0]],
                         acheteurs=[a[0] for a in acheteurs if a[0]],
                         fournisseurs=fournisseurs,
                         filtres={
                             'vue': vue,
                             'entite': entite,
                             'statut': statut,
                             'acheteur': acheteur,
                             'fournisseur': fournisseur,
                             'recherche': recherche,
                         },
                         pagination=commandes_pagination,
                         selected_view=vue,
                         selected_view_label=COMMANDE_LIST_VIEWS[vue],
                         view_labels=COMMANDE_LIST_VIEWS,
                         view_counts=view_counts,
                         commande_capabilities=get_commande_edit_capabilities())

@app.route('/commande/ajouter', methods=['GET', 'POST'])
@login_required
def ajouter_commande():
    denied_response = require_permission('commandes_manage', 'commandes')
    if denied_response:
        return denied_response

    ensure_supplier_reference_data()
    fournisseurs = Fournisseur.query.order_by(Fournisseur.nom).all()
    commande_capabilities = get_commande_edit_capabilities()
    form_values = get_commande_form_values(form_data=request.form if request.method == 'POST' else None)
    
    if request.method == 'POST':
        try:
            montant_input = (request.form.get('montant') or '').strip()
            avance_input = (request.form.get('avance') or '').strip()
            if not montant_input:
                raise ValueError('Montant obligatoire')
            if not avance_input:
                raise ValueError('Avance obligatoire')

            montant = valider_montant(montant_input)
            avance = valider_montant(avance_input)
            note_fournisseur = valider_note_fournisseur(request.form.get('note_fournisseur'), 'Note performance')
            note_service = valider_note_fournisseur(request.form.get('note_service'), 'Note SAV')
            service_demandeur = valider_service_demandeur(request.form.get('service_demandeur'))
            if not service_demandeur:
                raise ValueError('Service demandeur obligatoire')

            date_paiement = parse_commande_date_input(
                request.form.get('date_paiement'),
                'Date paiement',
            ) if commande_capabilities['can_manage_payment'] else None
            date_reception = parse_commande_date_input(
                request.form.get('date_reception'),
                'Date réception réelle',
            ) if commande_capabilities['can_manage_reception'] else None
            facture, bon_livraison = validate_commande_workflow_state(
                montant,
                avance,
                date_paiement=date_paiement,
                facture=request.form.get('facture'),
                date_reception=date_reception,
                bon_livraison=request.form.get('bon_livraison'),
            )
            
            commande = Commande(
                nr=parse_commande_numero(request.form.get('nr')),
                date_cde=parse_commande_date_input(request.form.get('date_cde'), 'Date commande', obligatoire=True),
                entite=valider_choix_liste(request.form.get('entite'), ENTITE_OPTIONS, 'Entité', obligatoire=True),
                demandeur=valider_texte_requis(request.form.get('demandeur'), 'Demandeur'),
                service_demandeur=service_demandeur,
                acheteur=valider_choix_liste(request.form.get('acheteur'), ACHETEUR_OPTIONS, 'Acheteur', obligatoire=True),
                fournisseur_id=parse_commande_fournisseur_id(request.form.get('fournisseur_id'), obligatoire=True),
                affaire=valider_texte_requis(request.form.get('affaire'), 'Affaire / Description'),
                bon_commande=valider_texte_requis(request.form.get('bon_commande'), 'N° Bon commande'),
                date_livraison=parse_commande_date_input(request.form.get('date_livraison'), 'Date livraison', obligatoire=True),
                date_reception=date_reception,
                bon_livraison=bon_livraison,
                facture=facture,
                montant=montant,
                avance=avance,
                commande_conforme='commande_conforme' in request.form,
                rupture_fournisseur='rupture_fournisseur' in request.form,
                note_fournisseur=note_fournisseur,
                note_service=note_service,
                date_paiement=date_paiement,
                commentaire=nettoyer_texte_optionnel(request.form.get('commentaire')),
            )
            commande.calculer_solde()
            db.session.add(commande)
            db.session.commit()
            
            # Log
            log = LogAction(
                utilisateur_id=current_user.id,
                action='CREATE',
                table='commande',
                record_id=commande.id,
                details=f'Ajout commande {commande.nr}',
                ip_address=request.remote_addr
            )
            db.session.add(log)
            db.session.commit()
            
            flash('Commande ajoutée avec succès', 'success')
            return redirect(url_for('commandes'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Erreur lors de l\'ajout: {str(e)}', 'danger')
    
    return render_template('admin/commande_form.html', 
                         fournisseurs=fournisseurs, 
                         commande=None,
                         form_values=form_values,
                         commande_capabilities=commande_capabilities,
                         entite_options=ENTITE_OPTIONS,
                         acheteur_options=ACHETEUR_OPTIONS,
                         services_demandeur=SERVICE_DEMANDEUR_OPTIONS,
                         titre="Ajouter une commande")

@app.route('/commande/modifier/<int:id>', methods=['GET', 'POST'])
@login_required
def modifier_commande(id):
    commande_capabilities = get_commande_edit_capabilities()
    if not commande_capabilities['can_edit_any']:
        return redirect_access_denied('commandes')

    ensure_supplier_reference_data()
    commande = Commande.query.get_or_404(id)
    fournisseurs = Fournisseur.query.order_by(Fournisseur.nom).all()
    form_values = get_commande_form_values(commande, request.form if request.method == 'POST' else None)
    
    if request.method == 'POST':
        try:
            updated_values = {}

            if commande_capabilities['can_manage_core']:
                service_demandeur = valider_service_demandeur(request.form.get('service_demandeur'))
                if not service_demandeur:
                    raise ValueError('Service demandeur obligatoire')

                montant_input = (request.form.get('montant') or '').strip()
                if not montant_input:
                    raise ValueError('Montant obligatoire')

                updated_values.update({
                    'nr': parse_commande_numero(request.form.get('nr')),
                    'date_cde': parse_commande_date_input(request.form.get('date_cde'), 'Date commande', obligatoire=True),
                    'entite': valider_choix_liste(request.form.get('entite'), ENTITE_OPTIONS, 'Entité', obligatoire=True),
                    'demandeur': valider_texte_requis(request.form.get('demandeur'), 'Demandeur'),
                    'service_demandeur': service_demandeur,
                    'acheteur': valider_choix_liste(request.form.get('acheteur'), ACHETEUR_OPTIONS, 'Acheteur', obligatoire=True),
                    'fournisseur_id': parse_commande_fournisseur_id(request.form.get('fournisseur_id'), obligatoire=True),
                    'affaire': valider_texte_requis(request.form.get('affaire'), 'Affaire / Description'),
                    'bon_commande': valider_texte_requis(request.form.get('bon_commande'), 'N° Bon commande'),
                    'date_livraison': parse_commande_date_input(request.form.get('date_livraison'), 'Date livraison', obligatoire=True),
                    'montant': valider_montant(montant_input),
                    'commande_conforme': 'commande_conforme' in request.form,
                    'rupture_fournisseur': 'rupture_fournisseur' in request.form,
                    'note_fournisseur': valider_note_fournisseur(request.form.get('note_fournisseur'), 'Note performance'),
                    'note_service': valider_note_fournisseur(request.form.get('note_service'), 'Note SAV'),
                    'commentaire': nettoyer_texte_optionnel(request.form.get('commentaire')),
                })

            can_manage_advance = commande_capabilities['can_manage_core'] or commande_capabilities['can_manage_payment']
            if can_manage_advance and 'avance' in request.form:
                avance_input = (request.form.get('avance') or '').strip()
                if not avance_input:
                    raise ValueError('Avance obligatoire')
                updated_values['avance'] = valider_montant(avance_input)

            if commande_capabilities['can_manage_payment']:
                updated_values['date_paiement'] = parse_commande_date_input(
                    request.form.get('date_paiement'),
                    'Date paiement',
                )
                updated_values['facture'] = request.form.get('facture')

            if commande_capabilities['can_manage_reception']:
                updated_values['date_reception'] = parse_commande_date_input(
                    request.form.get('date_reception'),
                    'Date réception réelle',
                )
                updated_values['bon_livraison'] = request.form.get('bon_livraison')

            target_montant = updated_values.get('montant', float(commande.montant or 0))
            target_avance = updated_values.get('avance', float(commande.avance or 0))
            target_date_paiement = updated_values.get('date_paiement', commande.date_paiement)
            target_facture = updated_values.get('facture', commande.facture)
            target_date_reception = updated_values.get('date_reception', commande.date_reception)
            target_bon_livraison = updated_values.get('bon_livraison', commande.bon_livraison)
            cleaned_facture, cleaned_bon_livraison = validate_commande_workflow_state(
                target_montant,
                target_avance,
                date_paiement=target_date_paiement,
                facture=target_facture,
                date_reception=target_date_reception,
                bon_livraison=target_bon_livraison,
            )

            if 'facture' in updated_values:
                updated_values['facture'] = cleaned_facture
            if 'bon_livraison' in updated_values:
                updated_values['bon_livraison'] = cleaned_bon_livraison

            for field_name, field_value in updated_values.items():
                setattr(commande, field_name, field_value)
            commande.calculer_solde()
            
            db.session.commit()
            
            # Log
            log = LogAction(
                utilisateur_id=current_user.id,
                action='UPDATE',
                table='commande',
                record_id=commande.id,
                details=f'Modification commande {commande.nr}',
                ip_address=request.remote_addr
            )
            db.session.add(log)
            db.session.commit()
            
            flash('Commande modifiée avec succès', 'success')
            return redirect(url_for('commandes'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Erreur lors de la modification: {str(e)}', 'danger')
    
    return render_template('admin/commande_form.html', 
                         commande=commande, 
                         form_values=form_values,
                         fournisseurs=fournisseurs,
                         commande_capabilities=commande_capabilities,
                         entite_options=ENTITE_OPTIONS,
                         acheteur_options=ACHETEUR_OPTIONS,
                         services_demandeur=SERVICE_DEMANDEUR_OPTIONS,
                         titre="Modifier la commande")

@app.route('/commande/supprimer/<int:id>', methods=['POST'])
@login_required
def supprimer_commande(id):
    denied_response = require_permission('commandes_manage', 'commandes')
    if denied_response:
        return denied_response
    
    commande = Commande.query.get_or_404(id)
    
    try:
        # Log avant suppression
        log = LogAction(
            utilisateur_id=current_user.id,
            action='DELETE',
            table='commande',
            record_id=commande.id,
            details=f'Suppression commande {commande.nr}',
            ip_address=request.remote_addr
        )
        db.session.add(log)
        db.session.commit()
        
        db.session.delete(commande)
        db.session.commit()
        
        flash('Commande supprimée avec succès', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur lors de la suppression: {str(e)}', 'danger')
    
    return redirect(url_for('commandes'))

@app.route('/commande/<int:id>')
@login_required
def voir_commande(id):
    denied_response = require_permission('commandes_view')
    if denied_response:
        return denied_response

    commande = Commande.query.get_or_404(id)
    return render_template(
        'commande_detail.html',
        commande=commande,
        commande_capabilities=get_commande_edit_capabilities(),
    )

# ==================== ROUTES FOURNISSEURS ====================

@app.route('/fournisseurs')
@login_required
def fournisseurs():
    denied_response = require_permission('fournisseurs_manage')
    if denied_response:
        return denied_response

    ensure_supplier_reference_data()

    fournisseurs = Fournisseur.query.order_by(Fournisseur.nom).all()
    return render_template('admin/fournisseurs.html', fournisseurs=fournisseurs)

@app.route('/fournisseur/ajouter', methods=['GET', 'POST'])
@login_required
def ajouter_fournisseur():
    denied_response = require_permission('fournisseurs_manage', 'fournisseurs')
    if denied_response:
        return denied_response
    
    if request.method == 'POST':
        try:
            valider_email(request.form.get('email1'))
            valider_email(request.form.get('email2'))
            valider_telephone(request.form.get('telephone1'))
            valider_telephone(request.form.get('telephone2'))

            fournisseur = Fournisseur(
                nom=request.form.get('nom'),
                statut_juridique=request.form.get('statut_juridique'),
                pays=request.form.get('pays'),
                ville=request.form.get('ville'),
                dirigeant=request.form.get('dirigeant'),
                telephone1=request.form.get('telephone1'),
                telephone2=request.form.get('telephone2'),
                email1=request.form.get('email1'),
                email2=request.form.get('email2'),
                categorie=request.form.get('categorie'),
                statut=request.form.get('statut', 'Actif')
            )
            db.session.add(fournisseur)
            db.session.commit()

            log = LogAction(
                utilisateur_id=current_user.id,
                action='CREATE',
                table='fournisseur',
                record_id=fournisseur.id,
                details=f'Ajout fournisseur {fournisseur.nom}',
                ip_address=request.remote_addr
            )
            db.session.add(log)
            db.session.commit()
            
            flash('Fournisseur ajouté avec succès', 'success')
            return redirect(url_for('fournisseurs'))
            
        except (ValueError, IntegrityError) as e:
            db.session.rollback()
            flash(f'Erreur lors de l\'ajout: {str(e)}', 'danger')
    
    return render_template('admin/fournisseur_form.html', fournisseur=None, titre="Ajouter un fournisseur")

@app.route('/fournisseur/modifier/<int:id>', methods=['GET', 'POST'])
@login_required
def modifier_fournisseur(id):
    denied_response = require_permission('fournisseurs_manage', 'fournisseurs')
    if denied_response:
        return denied_response
    
    fournisseur = Fournisseur.query.get_or_404(id)
    
    if request.method == 'POST':
        try:
            valider_email(request.form.get('email1'))
            valider_email(request.form.get('email2'))
            valider_telephone(request.form.get('telephone1'))
            valider_telephone(request.form.get('telephone2'))

            fournisseur.nom = request.form.get('nom')
            fournisseur.statut_juridique = request.form.get('statut_juridique')
            fournisseur.pays = request.form.get('pays')
            fournisseur.ville = request.form.get('ville')
            fournisseur.dirigeant = request.form.get('dirigeant')
            fournisseur.telephone1 = request.form.get('telephone1')
            fournisseur.telephone2 = request.form.get('telephone2')
            fournisseur.email1 = request.form.get('email1')
            fournisseur.email2 = request.form.get('email2')
            fournisseur.categorie = request.form.get('categorie')
            fournisseur.statut = request.form.get('statut')
            
            db.session.commit()

            log = LogAction(
                utilisateur_id=current_user.id,
                action='UPDATE',
                table='fournisseur',
                record_id=fournisseur.id,
                details=f'Modification fournisseur {fournisseur.nom}',
                ip_address=request.remote_addr
            )
            db.session.add(log)
            db.session.commit()
            
            flash('Fournisseur modifié avec succès', 'success')
            return redirect(url_for('fournisseurs'))
            
        except (ValueError, IntegrityError) as e:
            db.session.rollback()
            flash(f'Erreur lors de la modification: {str(e)}', 'danger')
    
    return render_template('admin/fournisseur_form.html', fournisseur=fournisseur, titre="Modifier le fournisseur")

@app.route('/fournisseur/supprimer/<int:id>', methods=['POST'])
@login_required
def supprimer_fournisseur(id):
    denied_response = require_permission('fournisseurs_manage', 'fournisseurs')
    if denied_response:
        return denied_response
    
    fournisseur = Fournisseur.query.get_or_404(id)
    
    # Vérifier si le fournisseur a des commandes
    if fournisseur.commandes:
        flash('Impossible de supprimer ce fournisseur car il est lié à des commandes', 'danger')
        return redirect(url_for('fournisseurs'))
    
    try:
        log = LogAction(
            utilisateur_id=current_user.id,
            action='DELETE',
            table='fournisseur',
            record_id=fournisseur.id,
            details=f'Suppression fournisseur {fournisseur.nom}',
            ip_address=request.remote_addr
        )
        db.session.add(log)
        db.session.commit()

        db.session.delete(fournisseur)
        db.session.commit()
        flash('Fournisseur supprimé avec succès', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur lors de la suppression: {str(e)}', 'danger')
    
    return redirect(url_for('fournisseurs'))

# ==================== ROUTES STOCKS & VENTES ====================

@app.route('/stocks')
@login_required
def stocks():
    denied_response = require_permission('stocks_view')
    if denied_response:
        return denied_response

    recherche = (request.args.get('recherche') or '').strip()
    famille = (request.args.get('famille') or '').strip()
    categorie = (request.args.get('categorie') or '').strip()
    type_stock = (request.args.get('type_stock') or '').strip()
    classe_abc = (request.args.get('classe_abc') or '').strip().upper()
    etat = (request.args.get('etat') or '').strip()
    page = get_requested_page()
    stock_summary = build_stock_management_summary()
    abc_map = stock_summary['abc_map']

    query = Produit.query

    if recherche:
        like_pattern = f'%{recherche}%'
        query = query.filter(
            or_(
                Produit.nom.ilike(like_pattern),
                Produit.code.ilike(like_pattern),
                Produit.description.ilike(like_pattern)
            )
        )

    if famille:
        query = query.filter(Produit.famille == famille)

    if categorie:
        query = query.filter(Produit.categorie == categorie)

    if type_stock in STOCK_TYPE_OPTIONS:
        query = query.filter(Produit.type_stock == type_stock)

    if classe_abc in {'A', 'B', 'C'}:
        matching_ids = [produit_id for produit_id, abc_class in abc_map.items() if abc_class == classe_abc]
        if matching_ids:
            query = query.filter(Produit.id.in_(matching_ids))
        else:
            query = query.filter(Produit.id == -1)

    if etat == 'actif':
        query = query.filter(Produit.actif.is_(True))
    elif etat == 'faible':
        query = query.filter(
            Produit.actif.is_(True),
            Produit.stock_actuel <= Produit.stock_minimum
        )
    elif etat == 'rupture':
        query = query.filter(Produit.actif.is_(True), Produit.stock_actuel <= 0)
    elif etat == 'a_reappro':
        reappro_ids = [produit.id for produit in stock_summary['produits_a_reappro_all']]
        if reappro_ids:
            query = query.filter(Produit.id.in_(reappro_ids))
        else:
            query = query.filter(Produit.id == -1)
    elif etat == 'inactif':
        query = query.filter(Produit.actif.is_(False))

    produits_pagination = query.order_by(Produit.nom.asc()).paginate(
        page=page,
        per_page=app.config.get('DEFAULT_PAGE_SIZE', 25),
        error_out=False,
    )
    produits = annotate_stock_products(produits_pagination.items, abc_map)
    familles = db.session.query(Produit.famille)\
        .filter(Produit.famille.isnot(None), Produit.famille != '')\
        .distinct()\
        .order_by(Produit.famille.asc())\
        .all()
    categories_query = db.session.query(Produit.categorie)\
        .filter(Produit.categorie.isnot(None), Produit.categorie != '')
    if famille:
        categories_query = categories_query.filter(Produit.famille == famille)
    categories = categories_query.distinct().order_by(Produit.categorie.asc()).all()
    mouvements_recents = MouvementStock.query.options(
        joinedload(MouvementStock.produit)
    ).order_by(MouvementStock.created_at.desc()).limit(10).all()
    total_produits = db.session.query(func.count(Produit.id)).filter(Produit.actif.is_(True)).scalar() or 0

    return render_template(
        'stocks/index.html',
        produits=produits,
        familles=[f[0] for f in familles],
        categories=[c[0] for c in categories],
        filtres={
            'recherche': recherche,
            'famille': famille,
            'categorie': categorie,
            'type_stock': type_stock,
            'classe_abc': classe_abc,
            'etat': etat,
        },
        total_produits=total_produits,
        valeur_stock=stock_summary['valeur_stock'],
        nb_stock_faible=stock_summary['nb_stock_faible'],
        nb_ruptures=stock_summary['nb_ruptures'],
        nb_a_reappro=stock_summary['nb_a_reappro'],
        couverture_moyenne=stock_summary['couverture_moyenne'],
        taux_service_stock=stock_summary['taux_service_stock'],
        rotation_estimee=stock_summary['rotation_estimee'],
        cout_possession_estime=stock_summary['cout_possession_estime'],
        abc_counts=stock_summary['abc_counts'],
        produits_a_reappro=stock_summary['produits_a_reappro'],
        stock_type_options=STOCK_TYPE_OPTIONS,
        mouvements_recents=mouvements_recents,
        pagination=produits_pagination,
    )


@app.route('/stocks/import/excel', methods=['GET', 'POST'])
@login_required
def importer_stock_excel():
    denied_response = require_permission('stocks_manage', 'stocks')
    if denied_response:
        return denied_response

    preview_context = None

    if request.method == 'POST':
        action = request.form.get('action') or 'upload_preview'

        try:
            if action == 'upload_preview':
                if 'fichier' not in request.files:
                    raise ValueError('Aucun fichier sélectionné')

                fichier = request.files['fichier']
                if not fichier or fichier.filename == '':
                    raise ValueError('Aucun fichier sélectionné')

                token = save_sales_import_preview_file(fichier)
                preview_context, _ = build_stock_import_preview_context(token)
                flash('Fichier analysé. Vérifiez les lignes et colonnes recommandées avant import.', 'info')

            elif action in {'refresh_preview', 'import_cleaned'}:
                token = request.form.get('preview_token')
                header_row_raw = request.form.get('header_row')
                if not header_row_raw or not header_row_raw.isdigit():
                    raise ValueError('La ligne d’en-tête doit être numérique')

                rows_to_delete = parse_import_row_numbers(request.form.get('rows_to_delete'))
                edited_cells = collect_import_grid_edits(request.form)
                submitted_mapping = {
                    key.replace('column_mapping_', ''): value
                    for key, value in request.form.items()
                    if key.startswith('column_mapping_')
                }
                preview_context, transformed_dataframe = build_stock_import_preview_context(
                    token,
                    header_row=int(header_row_raw),
                    rows_to_delete=rows_to_delete,
                    submitted_mapping=submitted_mapping,
                    edited_cells=edited_cells,
                )

                if action == 'import_cleaned':
                    if preview_context['mapping_errors']:
                        raise ValueError('Corrigez le mapping avant de lancer l’import')
                    if transformed_dataframe.empty:
                        raise ValueError('Aucune ligne exploitable après transformation')

                    imported_count, import_errors = import_stock_dataframe(transformed_dataframe)
                    delete_sales_import_preview_file(token)

                    if import_errors:
                        preview_errors = ' | '.join(import_errors[:5])
                        if len(import_errors) > 5:
                            preview_errors += f' (+{len(import_errors) - 5} erreurs)'
                        if imported_count > 0:
                            flash(f'Import partiel: {imported_count} produit(s) traité(s). Erreurs: {preview_errors}', 'warning')
                        else:
                            flash(f'Aucun produit traité. Erreurs: {preview_errors}', 'danger')
                    else:
                        flash(f'Import réussi: {imported_count} produit(s) traité(s)', 'success')
                    return redirect(url_for('stocks'))

                flash('Aperçu recalculé avec vos ajustements.', 'info')
            else:
                raise ValueError('Action d’import inconnue')

        except Exception as exc:
            db.session.rollback()
            flash(f'Erreur lors de l\'analyse/import: {str(exc)}', 'danger')

    return render_template('stocks/import.html', preview=preview_context)


@app.route('/stock/produit/ajouter', methods=['GET', 'POST'])
@login_required
def ajouter_produit():
    denied_response = require_permission('stocks_manage', 'stocks')
    if denied_response:
        return denied_response

    if request.method == 'POST':
        form_values = get_product_form_values(form_data={
            'nom': request.form.get('nom'),
            'code': request.form.get('code'),
            'description': request.form.get('description'),
            'famille': request.form.get('famille'),
            'categorie': request.form.get('categorie'),
            'type_stock': request.form.get('type_stock'),
            'methode_reappro': request.form.get('methode_reappro'),
            'methode_valorisation': request.form.get('methode_valorisation'),
            'unite': request.form.get('unite'),
            'prix_unitaire': request.form.get('prix_unitaire', 0),
            'stock_initial': request.form.get('stock_initial', 0),
            'stock_minimum': request.form.get('stock_minimum', 0),
            'stock_securite': request.form.get('stock_securite', 0),
            'delai_approvisionnement_jours': request.form.get('delai_approvisionnement_jours', 0),
            'periodicite_reappro_jours': request.form.get('periodicite_reappro_jours', 0),
            'consommation_moyenne_journaliere': request.form.get('consommation_moyenne_journaliere', 0),
            'cout_passation_commande': request.form.get('cout_passation_commande', 0),
            'taux_possession_annuel': request.form.get('taux_possession_annuel', 25),
            'actif': 'actif' in request.form,
        })
        try:
            nom = form_values['nom']
            code = form_values['code'] or None
            description = form_values['description'] or None
            famille, categorie, _ = normalize_product_taxonomy(
                form_values['famille'],
                form_values['categorie'],
            )
            type_stock = valider_choix_liste(
                form_values['type_stock'],
                list(STOCK_TYPE_OPTIONS.keys()),
                'Type de stock',
                obligatoire=True,
            )
            methode_reappro = valider_choix_liste(
                form_values['methode_reappro'],
                list(STOCK_REPLENISHMENT_OPTIONS.keys()),
                'Méthode de réapprovisionnement',
                obligatoire=True,
            )
            methode_valorisation = valider_choix_liste(
                form_values['methode_valorisation'],
                list(STOCK_VALUATION_OPTIONS.keys()),
                'Méthode de valorisation',
                obligatoire=True,
            )
            unite = form_values['unite'] or None
            prix_unitaire = valider_montant(request.form.get('prix_unitaire', 0))
            stock_initial = valider_nombre_non_negatif(request.form.get('stock_initial'), 'Stock initial')
            stock_minimum = valider_nombre_non_negatif(request.form.get('stock_minimum'), 'Stock minimum')
            stock_securite = valider_nombre_non_negatif(request.form.get('stock_securite'), 'Stock de sécurité')
            delai_approvisionnement_jours = valider_nombre_non_negatif(request.form.get('delai_approvisionnement_jours'), 'Délai d’approvisionnement')
            periodicite_reappro_jours = valider_nombre_non_negatif(request.form.get('periodicite_reappro_jours'), 'Périodicité de réapprovisionnement')
            consommation_moyenne_journaliere = valider_nombre_non_negatif(request.form.get('consommation_moyenne_journaliere'), 'Consommation moyenne journalière')
            cout_passation_commande = valider_nombre_non_negatif(request.form.get('cout_passation_commande'), 'Coût de passation')
            taux_possession_annuel = valider_taux_pourcentage(request.form.get('taux_possession_annuel'), 'Taux de possession annuel')
            actif = form_values['actif']

            if not nom:
                raise ValueError('Le nom du produit est obligatoire')
            if code and Produit.query.filter_by(code=code).first():
                raise ValueError('Ce code produit existe déjà')
            if stock_securite < stock_minimum:
                stock_securite = stock_minimum
            if methode_reappro != Produit.REAPPRO_CALENDAIRE:
                periodicite_reappro_jours = 0

            produit = Produit(
                nom=nom,
                code=code,
                description=description,
                famille=famille,
                categorie=categorie,
                type_stock=type_stock,
                methode_reappro=methode_reappro,
                methode_valorisation=methode_valorisation,
                prix_unitaire=prix_unitaire,
                unite=unite,
                stock_actuel=0,
                stock_minimum=stock_minimum,
                stock_securite=stock_securite,
                delai_approvisionnement_jours=delai_approvisionnement_jours,
                periodicite_reappro_jours=periodicite_reappro_jours,
                consommation_moyenne_journaliere=consommation_moyenne_journaliere,
                cout_passation_commande=cout_passation_commande,
                taux_possession_annuel=taux_possession_annuel,
                actif=actif,
            )
            db.session.add(produit)
            db.session.flush()

            if stock_initial > 0:
                appliquer_mouvement_stock(
                    produit,
                    stock_initial,
                    MouvementStock.TYPE_ENTREE,
                    'Stock initial'
                )

            enregistrer_log('CREATE', 'produit', produit.id, f'Ajout produit {produit.nom}')
            db.session.commit()
            flash('Produit ajouté avec succès', 'success')
            return redirect(url_for('stocks'))
        except (ValueError, IntegrityError) as e:
            db.session.rollback()
            flash(f'Erreur lors de l\'ajout: {str(e)}', 'danger')
            catalog_context = get_product_catalog_context(form_values)
            return render_template(
                'stocks/produit_form.html',
                produit=None,
                titre='Ajouter un produit',
                form_values=form_values,
                stock_type_options=STOCK_TYPE_OPTIONS,
                stock_replenishment_options=STOCK_REPLENISHMENT_OPTIONS,
                stock_valuation_options=STOCK_VALUATION_OPTIONS,
                **catalog_context,
            )

    form_values = get_product_form_values()
    catalog_context = get_product_catalog_context(form_values)
    return render_template(
        'stocks/produit_form.html',
        produit=None,
        titre='Ajouter un produit',
        form_values=form_values,
        stock_type_options=STOCK_TYPE_OPTIONS,
        stock_replenishment_options=STOCK_REPLENISHMENT_OPTIONS,
        stock_valuation_options=STOCK_VALUATION_OPTIONS,
        **catalog_context,
    )


@app.route('/stock/produit/modifier/<int:id>', methods=['GET', 'POST'])
@login_required
def modifier_produit(id):
    denied_response = require_permission('stocks_manage', 'stocks')
    if denied_response:
        return denied_response

    produit = Produit.query.get_or_404(id)

    if request.method == 'POST':
        form_values = get_product_form_values(form_data={
            'nom': request.form.get('nom'),
            'code': request.form.get('code'),
            'description': request.form.get('description'),
            'famille': request.form.get('famille'),
            'categorie': request.form.get('categorie'),
            'type_stock': request.form.get('type_stock'),
            'methode_reappro': request.form.get('methode_reappro'),
            'methode_valorisation': request.form.get('methode_valorisation'),
            'unite': request.form.get('unite'),
            'prix_unitaire': request.form.get('prix_unitaire', 0),
            'stock_minimum': request.form.get('stock_minimum', 0),
            'stock_securite': request.form.get('stock_securite', 0),
            'delai_approvisionnement_jours': request.form.get('delai_approvisionnement_jours', 0),
            'periodicite_reappro_jours': request.form.get('periodicite_reappro_jours', 0),
            'consommation_moyenne_journaliere': request.form.get('consommation_moyenne_journaliere', 0),
            'cout_passation_commande': request.form.get('cout_passation_commande', 0),
            'taux_possession_annuel': request.form.get('taux_possession_annuel', 25),
            'actif': 'actif' in request.form,
        })
        try:
            nom = form_values['nom']
            code = form_values['code'] or None
            prix_unitaire = valider_montant(request.form.get('prix_unitaire', 0))
            stock_minimum = valider_nombre_non_negatif(request.form.get('stock_minimum'), 'Stock minimum')
            stock_securite = valider_nombre_non_negatif(request.form.get('stock_securite'), 'Stock de sécurité')
            delai_approvisionnement_jours = valider_nombre_non_negatif(request.form.get('delai_approvisionnement_jours'), 'Délai d’approvisionnement')
            periodicite_reappro_jours = valider_nombre_non_negatif(request.form.get('periodicite_reappro_jours'), 'Périodicité de réapprovisionnement')
            consommation_moyenne_journaliere = valider_nombre_non_negatif(request.form.get('consommation_moyenne_journaliere'), 'Consommation moyenne journalière')
            cout_passation_commande = valider_nombre_non_negatif(request.form.get('cout_passation_commande'), 'Coût de passation')
            taux_possession_annuel = valider_taux_pourcentage(request.form.get('taux_possession_annuel'), 'Taux de possession annuel')
            type_stock = valider_choix_liste(
                form_values['type_stock'],
                list(STOCK_TYPE_OPTIONS.keys()),
                'Type de stock',
                obligatoire=True,
            )
            methode_reappro = valider_choix_liste(
                form_values['methode_reappro'],
                list(STOCK_REPLENISHMENT_OPTIONS.keys()),
                'Méthode de réapprovisionnement',
                obligatoire=True,
            )
            methode_valorisation = valider_choix_liste(
                form_values['methode_valorisation'],
                list(STOCK_VALUATION_OPTIONS.keys()),
                'Méthode de valorisation',
                obligatoire=True,
            )

            if not nom:
                raise ValueError('Le nom du produit est obligatoire')

            duplicate = None
            if code:
                duplicate = Produit.query.filter(Produit.code == code, Produit.id != produit.id).first()
            if duplicate:
                raise ValueError('Ce code produit existe déjà')
            if stock_securite < stock_minimum:
                stock_securite = stock_minimum
            if methode_reappro != Produit.REAPPRO_CALENDAIRE:
                periodicite_reappro_jours = 0

            produit.nom = nom
            produit.code = code
            produit.description = form_values['description'] or None
            produit.famille, produit.categorie, _ = normalize_product_taxonomy(
                form_values['famille'],
                form_values['categorie'],
            )
            produit.type_stock = type_stock
            produit.methode_reappro = methode_reappro
            produit.methode_valorisation = methode_valorisation
            produit.prix_unitaire = prix_unitaire
            produit.unite = form_values['unite'] or None
            produit.stock_minimum = stock_minimum
            produit.stock_securite = stock_securite
            produit.delai_approvisionnement_jours = delai_approvisionnement_jours
            produit.periodicite_reappro_jours = periodicite_reappro_jours
            produit.consommation_moyenne_journaliere = consommation_moyenne_journaliere
            produit.cout_passation_commande = cout_passation_commande
            produit.taux_possession_annuel = taux_possession_annuel
            produit.actif = form_values['actif']

            enregistrer_log('UPDATE', 'produit', produit.id, f'Modification produit {produit.nom}')
            db.session.commit()
            flash('Produit modifié avec succès', 'success')
            return redirect(url_for('stocks'))
        except (ValueError, IntegrityError) as e:
            db.session.rollback()
            flash(f'Erreur lors de la modification: {str(e)}', 'danger')
            catalog_context = get_product_catalog_context(form_values)
            return render_template(
                'stocks/produit_form.html',
                produit=produit,
                titre='Modifier le produit',
                form_values=form_values,
                stock_type_options=STOCK_TYPE_OPTIONS,
                stock_replenishment_options=STOCK_REPLENISHMENT_OPTIONS,
                stock_valuation_options=STOCK_VALUATION_OPTIONS,
                **catalog_context,
            )

    form_values = get_product_form_values(produit=produit)
    catalog_context = get_product_catalog_context(form_values)
    return render_template(
        'stocks/produit_form.html',
        produit=produit,
        titre='Modifier le produit',
        form_values=form_values,
        stock_type_options=STOCK_TYPE_OPTIONS,
        stock_replenishment_options=STOCK_REPLENISHMENT_OPTIONS,
        stock_valuation_options=STOCK_VALUATION_OPTIONS,
        **catalog_context,
    )


@app.route('/stock/produit/supprimer/<int:id>', methods=['POST'])
@login_required
def supprimer_produit(id):
    denied_response = require_permission('stocks_manage', 'stocks')
    if denied_response:
        return denied_response

    produit = Produit.query.get_or_404(id)

    if produit.lignes_vente or produit.commandes_produits or produit.mouvements_stock:
        flash('Impossible de supprimer ce produit car il est déjà utilisé', 'danger')
        return redirect(url_for('stocks'))

    try:
        enregistrer_log('DELETE', 'produit', produit.id, f'Suppression produit {produit.nom}')
        db.session.delete(produit)
        db.session.commit()
        flash('Produit supprimé avec succès', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur lors de la suppression: {str(e)}', 'danger')

    return redirect(url_for('stocks'))


@app.route('/stock/mouvements')
@login_required
def mouvements_stock():
    denied_response = require_permission('stocks_view', 'stocks')
    if denied_response:
        return denied_response

    produit_id = request.args.get('produit_id', '')
    type_mouvement = request.args.get('type_mouvement', '')

    query = MouvementStock.query

    if produit_id.isdigit():
        query = query.filter(MouvementStock.produit_id == int(produit_id))
    if type_mouvement in {
        MouvementStock.TYPE_ENTREE,
        MouvementStock.TYPE_SORTIE,
        MouvementStock.TYPE_AJUSTEMENT
    }:
        query = query.filter(MouvementStock.type_mouvement == type_mouvement)

    mouvements = query.options(
        joinedload(MouvementStock.produit),
        joinedload(MouvementStock.utilisateur),
    ).order_by(MouvementStock.created_at.desc()).all()
    produits = Produit.query.order_by(Produit.nom.asc()).all()

    return render_template(
        'stocks/mouvements.html',
        mouvements=mouvements,
        produits=produits,
        filtres={'produit_id': produit_id, 'type_mouvement': type_mouvement},
    )


@app.route('/stock/mouvement/ajouter', methods=['GET', 'POST'])
@login_required
def ajouter_mouvement_stock():
    denied_response = require_permission('stocks_manage', 'mouvements_stock')
    if denied_response:
        return denied_response

    produits = Produit.query.filter(Produit.actif.is_(True)).order_by(Produit.nom.asc()).all()
    form_lines = get_stock_movement_form_lines()
    form_values = {
        'type_mouvement': MouvementStock.TYPE_ENTREE,
        'motif': '',
    }

    if request.method == 'POST':
        form_lines = get_stock_movement_form_lines(request.form)
        form_values = {
            'type_mouvement': request.form.get('type_mouvement') or MouvementStock.TYPE_ENTREE,
            'motif': (request.form.get('motif') or '').strip(),
        }
        try:
            type_mouvement = form_values['type_mouvement']
            motif = form_values['motif']

            if type_mouvement not in {
                MouvementStock.TYPE_ENTREE,
                MouvementStock.TYPE_SORTIE,
                MouvementStock.TYPE_AJUSTEMENT
            }:
                raise ValueError('Type de mouvement invalide')

            movements_to_apply = []
            seen_products = set()
            for line_number, line in enumerate(form_lines, start=1):
                produit_id = (line.get('produit_id') or '').strip()
                quantite_value = (line.get('quantite') or '').strip()

                if not produit_id and not quantite_value:
                    continue
                if not produit_id or not quantite_value:
                    raise ValueError(f'Ligne {line_number}: produit et quantité sont obligatoires')
                if not produit_id.isdigit():
                    raise ValueError(f'Ligne {line_number}: produit invalide')
                if produit_id in seen_products:
                    raise ValueError(f'Ligne {line_number}: le produit est dupliqué')

                produit = Produit.query.get(int(produit_id))
                if not produit:
                    raise ValueError(f'Ligne {line_number}: produit introuvable')

                if type_mouvement == MouvementStock.TYPE_AJUSTEMENT:
                    variation = valider_quantite(quantite_value, autoriser_negative=True)
                elif type_mouvement == MouvementStock.TYPE_ENTREE:
                    variation = valider_quantite(quantite_value)
                else:
                    variation = -valider_quantite(quantite_value)

                movements_to_apply.append((produit, variation))
                seen_products.add(produit_id)

            if not movements_to_apply:
                raise ValueError('Ajoutez au moins un produit avec une quantité')

            for produit, variation in movements_to_apply:
                mouvement = appliquer_mouvement_stock(
                    produit,
                    variation,
                    type_mouvement,
                    motif or 'Mouvement manuel'
                )
                db.session.flush()
                enregistrer_log(
                    'CREATE',
                    'mouvement_stock',
                    mouvement.id,
                    f'{type_mouvement} stock {produit.nom} ({variation:+,.2f})'
                )
            db.session.commit()
            flash(f'{len(movements_to_apply)} mouvement(s) de stock enregistré(s)', 'success')
            return redirect(url_for('mouvements_stock'))
        except (ValueError, IntegrityError) as e:
            db.session.rollback()
            flash(f'Erreur lors de l\'enregistrement: {str(e)}', 'danger')

    return render_template(
        'stocks/mouvement_form.html',
        produits=produits,
        form_lines=form_lines,
        form_values=form_values,
    )


@app.route('/ventes')
@login_required
def ventes():
    denied_response = require_permission('ventes_view')
    if denied_response:
        return denied_response

    recherche = (request.args.get('recherche') or '').strip()
    statut = (request.args.get('statut') or '').strip()
    canal = (request.args.get('canal') or '').strip().upper()
    region = (request.args.get('region') or '').strip()
    type_client = (request.args.get('type_client') or '').strip().upper()
    page = get_requested_page()

    query = Vente.query
    if recherche:
        like_pattern = f'%{recherche}%'
        query = query.filter(
            or_(
                Vente.reference.ilike(like_pattern),
                Vente.client_nom.ilike(like_pattern),
                Vente.client_telephone.ilike(like_pattern)
            )
        )
    if statut in {
        Vente.STATUT_PAYEE,
        Vente.STATUT_PARTIELLE,
        Vente.STATUT_EN_ATTENTE
    }:
        query = query.filter(Vente.statut_paiement == statut)
    if canal in {Vente.CANAL_OFFLINE, Vente.CANAL_ONLINE}:
        query = query.filter(Vente.canal_vente == canal)
    if region:
        query = query.filter(Vente.region == region)
    if type_client in {
        Vente.TYPE_CLIENT_PARTICULIER,
        Vente.TYPE_CLIENT_ENTREPRISE,
        Vente.TYPE_CLIENT_REVENDEUR,
    }:
        query = query.filter(Vente.type_client == type_client)

    total_ca, total_encaisse, total_solde = query.with_entities(
        func.coalesce(func.sum(Vente.montant_total), 0),
        func.coalesce(func.sum(Vente.montant_paye), 0),
        func.coalesce(func.sum(Vente.solde), 0),
    ).one()
    ventes_pagination = query.order_by(Vente.date_vente.desc(), Vente.created_at.desc()).paginate(
        page=page,
        per_page=app.config.get('DEFAULT_PAGE_SIZE', 25),
        error_out=False,
    )
    ventes_liste = ventes_pagination.items
    regions = db.session.query(Vente.region)\
        .filter(Vente.region.isnot(None), Vente.region != '')\
        .distinct()\
        .order_by(Vente.region.asc())\
        .all()

    return render_template(
        'ventes.html',
        ventes=ventes_liste,
        filtres={
            'recherche': recherche,
            'statut': statut,
            'canal': canal,
            'region': region,
            'type_client': type_client,
        },
        regions=[row[0] for row in regions],
        total_ca=total_ca or 0,
        total_encaisse=total_encaisse or 0,
        total_solde=total_solde or 0,
        pagination=ventes_pagination,
    )


@app.route('/ventes/import/excel', methods=['GET', 'POST'])
@login_required
def importer_ventes_excel():
    denied_response = require_permission('ventes_manage', 'ventes')
    if denied_response:
        return denied_response

    preview_context = None

    if request.method == 'POST':
        action = request.form.get('action') or 'upload_preview'

        try:
            if action == 'upload_preview':
                if 'fichier' not in request.files:
                    raise ValueError('Aucun fichier sélectionné')

                fichier = request.files['fichier']
                if not fichier or fichier.filename == '':
                    raise ValueError('Aucun fichier sélectionné')

                token = save_sales_import_preview_file(fichier)
                preview_context, _ = build_sales_import_preview_context(token)
                flash('Fichier analysé. Vérifiez les lignes et colonnes recommandées avant import.', 'info')

            elif action in {'refresh_preview', 'import_cleaned'}:
                token = request.form.get('preview_token')
                header_row_raw = request.form.get('header_row')
                if not header_row_raw or not header_row_raw.isdigit():
                    raise ValueError('La ligne d’en-tête doit être numérique')

                rows_to_delete = parse_import_row_numbers(request.form.get('rows_to_delete'))
                edited_cells = collect_import_grid_edits(request.form)
                submitted_mapping = {
                    key.replace('column_mapping_', ''): value
                    for key, value in request.form.items()
                    if key.startswith('column_mapping_')
                }
                preview_context, transformed_dataframe = build_sales_import_preview_context(
                    token,
                    header_row=int(header_row_raw),
                    rows_to_delete=rows_to_delete,
                    submitted_mapping=submitted_mapping,
                    edited_cells=edited_cells,
                )

                if action == 'import_cleaned':
                    if preview_context['mapping_errors']:
                        raise ValueError('Corrigez le mapping avant de lancer l’import')
                    if transformed_dataframe.empty:
                        raise ValueError('Aucune ligne exploitable après transformation')

                    imported_count, import_errors = import_sales_dataframe(transformed_dataframe)
                    delete_sales_import_preview_file(token)

                    if import_errors:
                        preview_errors = ' | '.join(import_errors[:5])
                        if len(import_errors) > 5:
                            preview_errors += f' (+{len(import_errors) - 5} erreurs)'
                        if imported_count > 0:
                            flash(f'Import partiel: {imported_count} vente(s) importée(s). Erreurs: {preview_errors}', 'warning')
                        else:
                            flash(f'Aucune vente importée. Erreurs: {preview_errors}', 'danger')
                    else:
                        flash(f'Import réussi: {imported_count} vente(s) importée(s)', 'success')
                    return redirect(url_for('ventes'))

                flash('Aperçu recalculé avec vos ajustements.', 'info')
            else:
                raise ValueError('Action d’import inconnue')

        except Exception as exc:
            db.session.rollback()
            flash(f'Erreur lors de l\'analyse/import: {str(exc)}', 'danger')

    return render_template('ventes_import.html', preview=preview_context)


@app.route('/vente/ajouter', methods=['GET', 'POST'])
@login_required
def ajouter_vente():
    flash('La création manuelle des ventes est désactivée. Utilisez l’import Excel.', 'info')
    return redirect(url_for('importer_ventes_excel'))


@app.route('/vente/<int:id>')
@login_required
def voir_vente(id):
    denied_response = require_permission('ventes_view', 'ventes')
    if denied_response:
        return denied_response

    vente = Vente.query.options(
        selectinload(Vente.lignes).selectinload(LigneVente.produit)
    ).filter_by(id=id).first_or_404()
    return render_template('vente_detail.html', vente=vente)


@app.route('/vente/encaisser/<int:id>', methods=['POST'])
@login_required
def encaisser_vente(id):
    denied_response = require_permission('ventes_manage', 'ventes')
    if denied_response:
        return denied_response

    vente = Vente.query.get_or_404(id)

    try:
        montant = valider_montant(request.form.get('montant', 0))
        if montant <= 0:
            raise ValueError('Le montant doit être supérieur à zéro')
        if montant > (vente.solde or 0):
            raise ValueError('Le montant dépasse le solde restant')

        vente.montant_paye = (vente.montant_paye or 0) + montant
        vente.recalculer_totaux()
        enregistrer_log('UPDATE', 'vente', vente.id, f'Encaissement vente {vente.reference}: {montant:,.0f} FCFA')
        db.session.commit()
        flash('Encaissement enregistré', 'success')
    except (ValueError, IntegrityError) as e:
        db.session.rollback()
        flash(f'Erreur: {str(e)}', 'danger')

    return redirect(url_for('voir_vente', id=vente.id))


@app.route('/vente/supprimer/<int:id>', methods=['POST'])
@login_required
def supprimer_vente(id):
    denied_response = require_permission('ventes_manage', 'ventes')
    if denied_response:
        return denied_response

    vente = Vente.query.options(
        selectinload(Vente.lignes).selectinload(LigneVente.produit),
        selectinload(Vente.mouvements_stock),
    ).filter_by(id=id).first_or_404()

    try:
        for mouvement in list(vente.mouvements_stock):
            mouvement.vente = None
            if mouvement.motif:
                mouvement.motif = f'{mouvement.motif} [vente supprimée]'

        for ligne in list(vente.lignes):
            appliquer_mouvement_stock(
                ligne.produit,
                ligne.quantite,
                MouvementStock.TYPE_ENTREE,
                f'Annulation vente {vente.reference}'
            )

        enregistrer_log('DELETE', 'vente', vente.id, f'Suppression vente {vente.reference}')
        db.session.delete(vente)
        db.session.commit()
        flash('Vente supprimée et stock rétabli', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur lors de la suppression: {str(e)}', 'danger')

    return redirect(url_for('ventes'))

# ==================== ROUTES IMPORT/EXPORT ====================

@app.route('/exporter/excel')
@login_required
def exporter_excel():
    denied_response = require_permission('commandes_view', 'commandes')
    if denied_response:
        return denied_response

    commandes = Commande.query.options(selectinload(Commande.fournisseur)).all()
    
    data = []
    for c in commandes:
        data.append({
            'Nr.': c.nr,
            'Date CDE': c.date_cde,
            'Entité': c.entite,
            'Demandeur': c.demandeur,
            'Service Demandeur': c.service_demandeur,
            'Acheteur': c.acheteur,
            'Fournisseur': c.fournisseur.nom if c.fournisseur else '',
            'Affaire/Commande': c.affaire,
            'N° Bon commande': c.bon_commande,
            'Date Livraison': c.date_livraison,
            'Date Réception': c.date_reception,
            'N° Bon Livraison': c.bon_livraison,
            'Facture': c.facture,
            'Montant': c.montant,
            'Avance': c.avance,
            'Solde': c.solde,
            'Commande Conforme': c.commande_conforme,
            'Rupture Fournisseur': c.rupture_fournisseur,
            'Note Performance Fournisseur': c.note_fournisseur,
            'Note SAV Fournisseur': c.note_service,
            'Statut paiement': c.statut,
            'Avancement': c.get_statut_avancement(),
            'Niveau processus': c.get_niveau_processus(),
            'Date Paiement': c.date_paiement,
            'Commentaire': c.commentaire
        })
    
    df = pd.DataFrame(data)
    output = BytesIO()
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Commandes', index=False)
        
        # Ajuster les largeurs de colonnes
        worksheet = writer.sheets['Commandes']
        for column in worksheet.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except:
                    pass
            adjusted_width = min(max_length + 2, 50)
            worksheet.column_dimensions[column_letter].width = adjusted_width
    
    output.seek(0)
    
    return send_file(
        output,
        download_name=f'commandes_{datetime.now().strftime("%Y%m%d")}.xlsx',
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@app.route('/commandes/import/excel', methods=['GET', 'POST'])
@app.route('/importer/excel', methods=['GET', 'POST'])
@login_required
def importer_excel():
    denied_response = require_permission('commandes_manage', 'commandes')
    if denied_response:
        return denied_response

    preview_context = None

    if request.method == 'POST':
        action = request.form.get('action') or 'upload_preview'

        try:
            if action == 'upload_preview':
                if 'fichier' not in request.files:
                    raise ValueError('Aucun fichier sélectionné')

                fichier = request.files['fichier']
                if not fichier or fichier.filename == '':
                    raise ValueError('Aucun fichier sélectionné')

                token = save_sales_import_preview_file(fichier)
                preview_context, _ = build_command_import_preview_context(token)
                flash('Fichier analysé. Vérifiez les lignes et colonnes recommandées avant import.', 'info')

            elif action in {'refresh_preview', 'import_cleaned'}:
                token = request.form.get('preview_token')
                header_row_raw = request.form.get('header_row')
                if not header_row_raw or not header_row_raw.isdigit():
                    raise ValueError('La ligne d’en-tête doit être numérique')

                rows_to_delete = parse_import_row_numbers(request.form.get('rows_to_delete'))
                edited_cells = collect_import_grid_edits(request.form)
                submitted_mapping = {
                    key.replace('column_mapping_', ''): value
                    for key, value in request.form.items()
                    if key.startswith('column_mapping_')
                }
                preview_context, transformed_dataframe = build_command_import_preview_context(
                    token,
                    header_row=int(header_row_raw),
                    rows_to_delete=rows_to_delete,
                    submitted_mapping=submitted_mapping,
                    edited_cells=edited_cells,
                )

                if action == 'import_cleaned':
                    if preview_context['mapping_errors']:
                        raise ValueError('Corrigez le mapping avant de lancer l’import')
                    if transformed_dataframe.empty:
                        raise ValueError('Aucune ligne exploitable après transformation')

                    imported_count, import_errors = import_commandes_dataframe(transformed_dataframe)
                    delete_sales_import_preview_file(token)

                    if import_errors:
                        preview_errors = ' | '.join(import_errors[:5])
                        if len(import_errors) > 5:
                            preview_errors += f' (+{len(import_errors) - 5} erreurs)'
                        if imported_count > 0:
                            flash(f'Import partiel: {imported_count} commande(s) importée(s). Erreurs: {preview_errors}', 'warning')
                        else:
                            flash(f'Aucune commande importée. Erreurs: {preview_errors}', 'danger')
                    else:
                        flash(f'Import réussi: {imported_count} commande(s) importée(s)', 'success')
                    return redirect(url_for('commandes'))

                flash('Aperçu recalculé avec vos ajustements.', 'info')
            else:
                raise ValueError('Action d’import inconnue')

        except Exception as exc:
            db.session.rollback()
            flash(f'Erreur lors de l\'analyse/import: {str(exc)}', 'danger')

    return render_template('commandes_import.html', preview=preview_context)

# ==================== ROUTES API ====================

@app.route('/api/commandes')
@login_required
def api_commandes():
    denied_response = require_permission('commandes_view')
    if denied_response:
        return denied_response

    commandes = Commande.query.options(selectinload(Commande.fournisseur)).all()
    return jsonify([c.to_dict() for c in commandes])

@app.route('/api/commandes/statistiques')
@login_required
def api_statistiques():
    denied_response = require_permission('commandes_view')
    if denied_response:
        return denied_response

    total_commande = Commande.query.count()
    montant_total = db.session.query(db.func.sum(Commande.montant)).scalar() or 0
    montant_a_payer = db.session.query(db.func.sum(Commande.solde)).filter(
        Commande.statut == Commande.STATUT_A_PAYER
    ).scalar() or 0
    
    par_entite = []
    for e in db.session.query(Commande.entite, db.func.sum(Commande.montant)).group_by(Commande.entite).all():
        par_entite.append({'entite': e[0], 'montant': e[1]})
    
    return jsonify({
        'total_commandes': total_commande,
        'montant_total': montant_total,
        'montant_a_payer': montant_a_payer,
        'repartition_entite': par_entite
    })

@app.route('/api/dashboard/kpi')
@login_required
def api_kpi():
    denied_response = require_permission('dashboard_view')
    if denied_response:
        return denied_response

    total_commandes = Commande.query.count()
    nb_retard = db.session.query(func.count(Commande.id)).filter(
        Commande.date_livraison.isnot(None),
        Commande.date_livraison < date.today()
    ).scalar() or 0
    
    return jsonify({
        'total_commandes': total_commandes,
        'nb_retard': nb_retard,
        'montant_a_payer': db.session.query(db.func.sum(Commande.solde)).filter(
            Commande.statut == Commande.STATUT_A_PAYER
        ).scalar() or 0,
        'date_actualisation': datetime.now().isoformat()
    })

# ==================== GESTION DES ERREURS ====================

@app.errorhandler(404)
def page_not_found(e):
    return render_template('errors/404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('errors/500.html'), 500

# ==================== INITIALISATION ====================

def migrate_existing_schema():
    """Ajoute les colonnes manquantes sur une base existante."""
    dialect_name = db.engine.dialect.name

    column_migrations = {
        'fournisseurs': {
            'statut_juridique': 'VARCHAR(100)',
            'ville': 'VARCHAR(100)',
            'dirigeant': 'VARCHAR(100)',
            'telephone1': 'VARCHAR(50)',
            'telephone2': 'VARCHAR(50)',
            'email1': 'VARCHAR(100)',
            'email2': 'VARCHAR(100)',
            'categorie': 'VARCHAR(100)',
            'created_at': 'TIMESTAMP',
        },
        'commandes': {
            'date_paiement': 'DATE',
            'date_reception': 'DATE',
            'prix_reference_marche': 'FLOAT',
            'commande_conforme': 'BOOLEAN',
            'rupture_fournisseur': 'BOOLEAN',
            'note_fournisseur': 'FLOAT',
            'note_service': 'FLOAT',
            'updated_at': 'TIMESTAMP',
        },
        'produits': {
            'code': 'VARCHAR(50)',
            'description': 'TEXT',
            'famille': 'VARCHAR(150)',
            'categorie': 'VARCHAR(100)',
            'type_stock': 'VARCHAR(30)',
            'methode_reappro': 'VARCHAR(30)',
            'methode_valorisation': 'VARCHAR(20)',
            'prix_unitaire': 'FLOAT',
            'unite': 'VARCHAR(20)',
            'stock_actuel': 'FLOAT',
            'stock_minimum': 'FLOAT',
            'stock_securite': 'FLOAT',
            'delai_approvisionnement_jours': 'FLOAT',
            'periodicite_reappro_jours': 'FLOAT',
            'consommation_moyenne_journaliere': 'FLOAT',
            'cout_passation_commande': 'FLOAT',
            'taux_possession_annuel': 'FLOAT',
            'actif': 'BOOLEAN',
            'created_at': 'TIMESTAMP',
            'updated_at': 'TIMESTAMP',
        },
        'ventes': {
            'canal_vente': 'VARCHAR(20)',
            'region': 'VARCHAR(100)',
            'type_client': 'VARCHAR(30)',
            'retour_effectue': 'BOOLEAN',
            'montant_retour': 'FLOAT',
        },
    }

    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    index_statements = [
        "CREATE INDEX IF NOT EXISTS idx_commandes_date_cde ON commandes (date_cde)",
        "CREATE INDEX IF NOT EXISTS idx_commandes_acheteur ON commandes (acheteur)",
        "CREATE INDEX IF NOT EXISTS idx_commandes_entite ON commandes (entite)",
        "CREATE INDEX IF NOT EXISTS idx_commandes_fournisseur_id ON commandes (fournisseur_id)",
        "CREATE INDEX IF NOT EXISTS idx_commandes_statut ON commandes (statut)",
        "CREATE INDEX IF NOT EXISTS idx_commandes_date_livraison ON commandes (date_livraison)",
        "CREATE INDEX IF NOT EXISTS idx_commandes_date_reception ON commandes (date_reception)",
        "CREATE INDEX IF NOT EXISTS idx_commandes_bon_commande ON commandes (bon_commande)",
        "CREATE INDEX IF NOT EXISTS idx_commandes_facture ON commandes (facture)",
        "CREATE INDEX IF NOT EXISTS idx_commandes_conforme ON commandes (commande_conforme)",
        "CREATE INDEX IF NOT EXISTS idx_commandes_rupture ON commandes (rupture_fournisseur)",
        "CREATE INDEX IF NOT EXISTS idx_fournisseurs_nom ON fournisseurs (nom)",
        "CREATE INDEX IF NOT EXISTS idx_fournisseurs_categorie ON fournisseurs (categorie)",
        "CREATE INDEX IF NOT EXISTS idx_produits_nom ON produits (nom)",
        "CREATE INDEX IF NOT EXISTS idx_produits_famille ON produits (famille)",
        "CREATE INDEX IF NOT EXISTS idx_produits_categorie ON produits (categorie)",
        "CREATE INDEX IF NOT EXISTS idx_produits_type_stock ON produits (type_stock)",
        "CREATE INDEX IF NOT EXISTS idx_produits_methode_reappro ON produits (methode_reappro)",
        "CREATE INDEX IF NOT EXISTS idx_produits_actif ON produits (actif)",
        "CREATE INDEX IF NOT EXISTS idx_produits_stock_actuel ON produits (stock_actuel)",
        "CREATE INDEX IF NOT EXISTS idx_ventes_client_nom ON ventes (client_nom)",
        "CREATE INDEX IF NOT EXISTS idx_ventes_date_vente ON ventes (date_vente)",
        "CREATE INDEX IF NOT EXISTS idx_ventes_statut_paiement ON ventes (statut_paiement)",
        "CREATE INDEX IF NOT EXISTS idx_ventes_canal_vente ON ventes (canal_vente)",
        "CREATE INDEX IF NOT EXISTS idx_ventes_region ON ventes (region)",
        "CREATE INDEX IF NOT EXISTS idx_ventes_type_client ON ventes (type_client)",
        "CREATE INDEX IF NOT EXISTS idx_ventes_retour_effectue ON ventes (retour_effectue)",
        "CREATE INDEX IF NOT EXISTS idx_lignes_ventes_vente_id ON lignes_ventes (vente_id)",
        "CREATE INDEX IF NOT EXISTS idx_lignes_ventes_produit_id ON lignes_ventes (produit_id)",
        "CREATE INDEX IF NOT EXISTS idx_mouvements_stock_produit_id ON mouvements_stock (produit_id)",
        "CREATE INDEX IF NOT EXISTS idx_mouvements_stock_type ON mouvements_stock (type_mouvement)",
        "CREATE INDEX IF NOT EXISTS idx_mouvements_stock_created_at ON mouvements_stock (created_at)",
        "CREATE INDEX IF NOT EXISTS idx_logs_action ON logs (action)",
        'CREATE INDEX IF NOT EXISTS idx_logs_table ON logs ("table")',
        "CREATE INDEX IF NOT EXISTS idx_logs_created_at ON logs (created_at)",
    ]

    with db.engine.begin() as connection:
        for table_name, columns in column_migrations.items():
            if table_name not in existing_tables:
                continue

            existing_columns = {column['name'] for column in inspector.get_columns(table_name)}
            for column_name, column_type in columns.items():
                if column_name in existing_columns:
                    continue
                try:
                    connection.execute(
                        text(f'ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}')
                    )
                    print(f"Colonne ajoutée: {table_name}.{column_name}")
                except OperationalError as exc:
                    if 'duplicate column name' not in str(exc).lower():
                        raise

        for statement in index_statements:
            try:
                connection.execute(text(statement))
            except OperationalError:
                pass

        if 'produits' in existing_tables:
            connection.execute(text("""
                UPDATE produits
                SET prix_unitaire = COALESCE(prix_unitaire, 0),
                    stock_actuel = COALESCE(stock_actuel, 0),
                    stock_minimum = COALESCE(stock_minimum, 0),
                    stock_securite = COALESCE(stock_securite, 0),
                    delai_approvisionnement_jours = COALESCE(delai_approvisionnement_jours, 0),
                    periodicite_reappro_jours = COALESCE(periodicite_reappro_jours, 0),
                    consommation_moyenne_journaliere = COALESCE(consommation_moyenne_journaliere, 0),
                    cout_passation_commande = COALESCE(cout_passation_commande, 0),
                    taux_possession_annuel = COALESCE(taux_possession_annuel, 25),
                    type_stock = COALESCE(type_stock, :type_stock_default),
                    methode_reappro = COALESCE(methode_reappro, :reappro_default),
                    methode_valorisation = COALESCE(methode_valorisation, :valorisation_default)
            """), {
                'type_stock_default': Produit.TYPE_PRODUIT_FINI,
                'reappro_default': Produit.REAPPRO_POINT_COMMANDE,
                'valorisation_default': Produit.VALORISATION_CUMP,
            })
        if dialect_name == 'sqlite':
            # Normalisation des données héritées pour éviter les crashs ORM.
            connection.execute(text("""
                UPDATE commandes
                SET montant = COALESCE(montant, 0),
                    avance = COALESCE(avance, 0),
                    prix_reference_marche = COALESCE(prix_reference_marche, 0),
                    commande_conforme = COALESCE(commande_conforme, 1),
                    rupture_fournisseur = COALESCE(rupture_fournisseur, 0)
            """))
            connection.execute(text("""
                UPDATE commandes
                SET solde = COALESCE(montant, 0) - COALESCE(avance, 0)
            """))
            connection.execute(
                text("""
                    UPDATE commandes
                    SET statut = CASE
                        WHEN COALESCE(solde, 0) <= 0
                             AND date_paiement IS NOT NULL THEN :statut_paye
                        ELSE :statut_a_payer
                    END
                """),
                {
                    'statut_paye': Commande.STATUT_PAYE,
                    'statut_a_payer': Commande.STATUT_A_PAYER,
                }
            )
            for column_name in ('date_cde', 'date_livraison', 'date_reception', 'date_paiement'):
                connection.execute(text(f"""
                    UPDATE commandes
                    SET {column_name} = NULL
                    WHERE {column_name} IS NOT NULL
                      AND CAST({column_name} AS TEXT) NOT GLOB
                        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
                """))

def init_db():
    """Initialise la base de données avec un utilisateur admin par défaut"""
    with app.app_context():
        db.create_all()
        migrate_existing_schema()
        sync_existing_product_taxonomy()

        is_production = bool(app.config.get('IS_PRODUCTION'))
        admin_username = (os.environ.get('ADMIN_USERNAME') or 'admin').strip() or 'admin'
        admin_email = (os.environ.get('ADMIN_EMAIL') or 'admin@example.com').strip() or 'admin@example.com'
        admin_password = os.environ.get('ADMIN_PASSWORD')

        if is_production and not admin_password and Utilisateur.query.filter_by(role='admin').count() == 0:
            raise ValueError(
                'ADMIN_PASSWORD environment variable MUST be set in production before first deploy'
            )

        if not admin_password:
            admin_password = 'admin123'

        # Créer un utilisateur admin s'il n'en existe aucun
        if Utilisateur.query.filter_by(role='admin').count() == 0:
            admin = Utilisateur(
                username=admin_username,
                email=admin_email,
                role='admin',
                actif=True
            )
            admin.set_password(admin_password)
            db.session.add(admin)
            db.session.commit()
            if is_production:
                print(f"Utilisateur admin créé: {admin_username}")
            else:
                print(f"Utilisateur admin créé: {admin_username} / {admin_password}")

        ensure_supplier_reference_data()


def ensure_supplier_reference_data(force=False):
    """Synchronise les fournisseurs depuis le classeur source une fois par base."""
    global synchronized_supplier_reference_key

    if app.config.get('TESTING') and not force:
        return 0

    workbook_path = app.config.get('SUPPLIER_SOURCE_WORKBOOK_FILE')
    if not workbook_path or not os.path.exists(workbook_path):
        return 0

    sync_key = f"{app.config.get('SQLALCHEMY_DATABASE_URI')}::{os.path.abspath(workbook_path)}"
    if not force and synchronized_supplier_reference_key == sync_key:
        return 0

    try:
        from import_fournisseurs_workbook import import_suppliers

        before_count = Fournisseur.query.count()
        _, created, updated = import_suppliers(workbook_path, replace_existing=False)
        synchronized_supplier_reference_key = sync_key
        after_count = Fournisseur.query.count()
        if created or updated:
            print(
                f"Fournisseurs synchronisés depuis le classeur source: "
                f"{created} créés, {updated} mis à jour "
                f"({before_count} -> {after_count})"
            )
        return created + updated
    except Exception as exc:
        if app.config.get('IS_PRODUCTION'):
            print(f"Import fournisseurs ignoré: {exc}")
            return 0
        raise

# ==================== ROUTES PERFORMANCES ====================

@app.route('/performances')
@login_required
def performances():
    """Page principale des performances"""
    denied_response = require_permission('performances_view')
    if denied_response:
        return denied_response

    return render_template('performances/index.html')


@app.route('/performances/acheteurs')
@login_required
def performances_acheteurs():
    """Performances par acheteur"""
    denied_response = require_permission('performances_view', 'performances')
    if denied_response:
        return denied_response

    stats_acheteurs = db.session.query(
        Commande.acheteur,
        func.count(Commande.id).label('total_commandes'),
        func.sum(Commande.montant).label('total_montant'),
        func.avg(Commande.montant).label('montant_moyen'),
        func.count(func.distinct(Commande.fournisseur_id)).label('nb_fournisseurs'),
        func.sum(
            case(
                (Commande.statut == Commande.STATUT_A_PAYER, Commande.solde),
                else_=0
            )
        ).label('montant_a_payer'),
        func.count(
            case(
                (and_(Commande.date_livraison.isnot(None), Commande.date_livraison < date.today()), 1),
                else_=None
            )
        ).label('nb_retard')
    ).group_by(Commande.acheteur).all()

    retard_rows = db.session.query(Commande.acheteur, Commande.date_livraison)\
        .filter(Commande.acheteur.isnot(None), Commande.date_livraison.isnot(None))\
        .all()
    retard_stats = build_retard_stats(retard_rows)
    acheteurs_data = []

    for stat in stats_acheteurs:
        if stat.acheteur:
            delay_info = retard_stats.get(stat.acheteur, {'sum': 0, 'count': 0})
            delai_moyen = (delay_info['sum'] / delay_info['count']) if delay_info['count'] else 0
            taux_retard = (stat.nb_retard / stat.total_commandes * 100) if stat.total_commandes > 0 else 0

            acheteurs_data.append({
                'acheteur': stat.acheteur,
                'total_commandes': stat.total_commandes,
                'total_montant': stat.total_montant or 0,
                'montant_moyen': stat.montant_moyen or 0,
                'nb_fournisseurs': stat.nb_fournisseurs or 0,
                'montant_a_payer': stat.montant_a_payer or 0,
                'delai_moyen': round(delai_moyen, 1),
                'taux_retard': round(taux_retard, 1)
            })
    
    return render_template('performances/acheteurs.html', 
                         stats=acheteurs_data)


@app.route('/performances/acheteur/<nom>')
@login_required
def performance_acheteur_detail(nom):
    """Détail des performances d'un acheteur"""
    denied_response = require_permission('performances_view', 'performances')
    if denied_response:
        return denied_response

    commandes_query = Commande.query.options(
        selectinload(Commande.fournisseur)
    ).filter(Commande.acheteur == nom)
    commandes = commandes_query.order_by(Commande.date_cde.desc()).all()
    evolution = build_monthly_evolution(
        Commande.query.filter(Commande.acheteur == nom),
        Commande.date_cde,
        Commande.montant
    )

    # Top fournisseurs de l'acheteur
    top_fournisseurs = db.session.query(
        Fournisseur.nom,
        func.count(Commande.id).label('nb_commandes'),
        func.sum(Commande.montant).label('total_montant')
    ).join(Commande, Fournisseur.id == Commande.fournisseur_id)\
     .filter(Commande.acheteur == nom)\
     .group_by(Fournisseur.nom)\
     .order_by(func.sum(Commande.montant).desc())\
     .limit(5).all()
    total_commandes, total_montant, montant_a_payer = db.session.query(
        func.count(Commande.id),
        func.coalesce(func.sum(Commande.montant), 0),
        func.coalesce(
            func.sum(
                case(
                    (Commande.statut == Commande.STATUT_A_PAYER, Commande.solde),
                    else_=0
                )
            ),
            0
        )
    ).filter(Commande.acheteur == nom).one()
    
    return render_template('performances/acheteur_detail.html',
                         acheteur=nom,
                         commandes=commandes,
                         evolution=evolution,
                         top_fournisseurs=top_fournisseurs,
                         total_commandes=total_commandes,
                         total_montant=total_montant,
                         montant_a_payer=montant_a_payer)


@app.route('/performances/fournisseurs')
@login_required
def performances_fournisseurs():
    """Performances par fournisseur"""
    denied_response = require_permission('performances_view', 'performances')
    if denied_response:
        return denied_response

    filters = build_supplier_filters(request.args)
    analytics = build_supplier_performance_data(
        start_date=filters['start_date'],
        end_date=filters['end_date'],
        include_inactive=filters['include_inactive'],
        evolution_months=8,
    )
    return render_template(
        'performances/fournisseurs.html',
        stats=analytics['items'],
        summary=analytics['summary'],
        matrix_points=analytics['matrix_points'],
        evolution_labels=analytics['evolution_labels'],
        evolution_datasets=analytics['evolution_datasets'],
        matrix_delay_threshold=analytics['matrix_delay_threshold'],
        matrix_score_threshold=analytics['matrix_score_threshold'],
        filtres=filters,
        period_label=get_period_label(filters),
        filter_querystring=build_supplier_filter_querystring(filters),
    )

@app.route('/performances/fournisseurs/export/excel')
@login_required
def performances_fournisseurs_export_excel():
    """Export Excel des KPI fournisseurs filtrés."""
    denied_response = require_permission('performances_view', 'performances')
    if denied_response:
        return denied_response

    filters = build_supplier_filters(request.args)
    analytics = build_supplier_performance_data(
        start_date=filters['start_date'],
        end_date=filters['end_date'],
        include_inactive=filters['include_inactive'],
        evolution_months=8,
    )
    output = build_supplier_performance_excel_bytes(filters, analytics)
    return send_file(
        output,
        download_name=f'fournisseurs_performance_{datetime.now().strftime("%Y%m%d_%H%M")}.xlsx',
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )

@app.route('/performances/fournisseurs/export/pdf')
@login_required
def performances_fournisseurs_export_pdf():
    """Export PDF des KPI fournisseurs filtrés."""
    denied_response = require_permission('performances_view', 'performances')
    if denied_response:
        return denied_response

    filters = build_supplier_filters(request.args)
    analytics = build_supplier_performance_data(
        start_date=filters['start_date'],
        end_date=filters['end_date'],
        include_inactive=filters['include_inactive'],
        evolution_months=8,
    )
    output = build_supplier_performance_pdf_bytes(filters, analytics)
    return send_file(
        output,
        download_name=f'fournisseurs_performance_{datetime.now().strftime("%Y%m%d_%H%M")}.pdf',
        as_attachment=True,
        mimetype='application/pdf',
    )

@app.route('/performances/fournisseur/<int:id>')
@login_required
def performance_fournisseur_detail(id):
    """Détail des performances d'un fournisseur"""
    denied_response = require_permission('performances_view', 'performances')
    if denied_response:
        return denied_response

    fournisseur = Fournisseur.query.get_or_404(id)
    filters = build_supplier_filters(request.args)
    payload = get_supplier_detail_payload(fournisseur, filters, evolution_months=12)
    supplier_stats = payload['supplier_stats']

    return render_template(
        'performances/fournisseur_detail.html',
        fournisseur=fournisseur,
        commandes=payload['commandes'],
        evolution=payload['evolution'],
        score_evolution_labels=payload['score_evolution_labels'],
        score_evolution=payload['score_evolution'],
        radar_metrics=payload['radar_metrics'],
        statut_repartition=payload['statut_repartition'],
        total_commandes=supplier_stats['total_commandes'],
        total_montant=supplier_stats['total_montant'],
        montant_a_payer=supplier_stats['montant_a_payer'],
        delai_moyen=round(supplier_stats['delai_moyen'], 1) if supplier_stats['delai_moyen'] is not None else None,
        taux_retard=supplier_stats['taux_retard'],
        taux_paiement=supplier_stats['taux_paiement'],
        montant_moyen=supplier_stats['montant_moyen'],
        score_performance=supplier_stats['score_performance'],
        taux_conformite=supplier_stats['taux_conformite'],
        taux_rupture=supplier_stats['taux_rupture'],
        respect_delai=supplier_stats['respect_delai'],
        price_competitiveness_pct=supplier_stats['price_competitiveness_pct'],
        score_fiabilite=supplier_stats['score_fiabilite'],
        filtres=filters,
        period_label=get_period_label(filters),
        filter_querystring=build_supplier_filter_querystring(filters),
    )

@app.route('/performances/fournisseur/<int:id>/export/excel')
@login_required
def performance_fournisseur_export_excel(id):
    """Export Excel détaillé d'un fournisseur."""
    denied_response = require_permission('performances_view', 'performances')
    if denied_response:
        return denied_response

    fournisseur = Fournisseur.query.get_or_404(id)
    filters = build_supplier_filters(request.args)
    payload = get_supplier_detail_payload(fournisseur, filters, evolution_months=12)
    output = build_supplier_detail_excel_bytes(fournisseur, filters, payload)
    filename_slug = secure_filename(fournisseur.nom or f'fournisseur-{id}') or f'fournisseur-{id}'
    return send_file(
        output,
        download_name=f'{filename_slug}_performance_{datetime.now().strftime("%Y%m%d_%H%M")}.xlsx',
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )

@app.route('/performances/fournisseur/<int:id>/export/pdf')
@login_required
def performance_fournisseur_export_pdf(id):
    """Export PDF détaillé d'un fournisseur."""
    denied_response = require_permission('performances_view', 'performances')
    if denied_response:
        return denied_response

    fournisseur = Fournisseur.query.get_or_404(id)
    filters = build_supplier_filters(request.args)
    payload = get_supplier_detail_payload(fournisseur, filters, evolution_months=12)
    output = build_supplier_detail_pdf_bytes(fournisseur, filters, payload)
    filename_slug = secure_filename(fournisseur.nom or f'fournisseur-{id}') or f'fournisseur-{id}'
    return send_file(
        output,
        download_name=f'{filename_slug}_performance_{datetime.now().strftime("%Y%m%d_%H%M")}.pdf',
        as_attachment=True,
        mimetype='application/pdf',
    )


@app.route('/performances/produits')
@login_required
def performances_produits():
    """Performances par produit"""
    denied_response = require_permission('performances_view', 'performances')
    if denied_response:
        return denied_response

    from sqlalchemy import func
    
    # Agrégation par affaire/description
    stats_produits = db.session.query(
        Commande.affaire,
        func.count(Commande.id).label('total_commandes'),
        func.sum(Commande.montant).label('total_montant'),
        func.avg(Commande.montant).label('montant_moyen')
    ).filter(Commande.affaire != None)\
     .group_by(Commande.affaire)\
     .order_by(func.sum(Commande.montant).desc())\
     .limit(50).all()
    
    # Calcul du total général pour les pourcentages
    total_general = db.session.query(func.sum(Commande.montant)).scalar() or 0
    
    produits_data = []
    for p in stats_produits:
        if p.affaire:
            produits_data.append({
                'nom': p.affaire[:100],
                'total_commandes': p.total_commandes,
                'total_montant': p.total_montant or 0,
                'montant_moyen': p.montant_moyen or 0,
                'pourcentage': (p.total_montant / total_general * 100) if total_general > 0 else 0
            })

    catalog = get_product_category_catalog()
    categories_reference = []
    total_catalog_categories = 0
    for family_label in catalog.get('families', []):
        category_labels = list(catalog.get('categories_by_family', {}).get(family_label, []))
        total_catalog_categories += len(category_labels)
        categories_reference.append({
            'famille': family_label,
            'categories': category_labels,
            'count': len(category_labels),
        })

    return render_template(
        'performances/produits.html',
        stats=produits_data,
        total_montant=total_general,
        categories_reference=categories_reference,
        total_catalog_categories=total_catalog_categories,
        total_catalog_families=len(categories_reference),
        catalog_source=os.path.basename(catalog.get('source_path') or ''),
    )


@app.route('/performances/ventes')
@login_required
def performances_ventes():
    """Performances globales des ventes."""
    denied_response = require_permission('performances_view', 'performances')
    if denied_response:
        return denied_response

    total_ventes, chiffre_affaires, total_encaisse, total_solde, ticket_moyen = db.session.query(
        func.count(Vente.id),
        func.coalesce(func.sum(Vente.montant_total), 0),
        func.coalesce(func.sum(Vente.montant_paye), 0),
        func.coalesce(func.sum(Vente.solde), 0),
        func.coalesce(func.avg(Vente.montant_total), 0),
    ).one()

    ventes_payees = db.session.query(func.count(Vente.id)).filter(
        Vente.statut_paiement == Vente.STATUT_PAYEE
    ).scalar() or 0
    ventes_en_attente = db.session.query(func.count(Vente.id)).filter(
        Vente.statut_paiement == Vente.STATUT_EN_ATTENTE
    ).scalar() or 0
    taux_encaissement = (total_encaisse / chiffre_affaires * 100) if chiffre_affaires else 0

    evolution = build_monthly_evolution(Vente.query, Vente.date_vente, Vente.montant_total, months=12)

    statuts = [
        {'statut': row[0], 'count': row[1]}
        for row in db.session.query(
            Vente.statut_paiement,
            func.count(Vente.id)
        ).group_by(Vente.statut_paiement).all()
        if row[0]
    ]

    top_clients = [
        {
            'client_nom': row[0],
            'nb_ventes': row[1],
            'chiffre_affaires': row[2] or 0,
            'encaisse': row[3] or 0,
        }
        for row in db.session.query(
            Vente.client_nom,
            func.count(Vente.id),
            func.coalesce(func.sum(Vente.montant_total), 0),
            func.coalesce(func.sum(Vente.montant_paye), 0),
        ).filter(Vente.client_nom.isnot(None))\
         .group_by(Vente.client_nom)\
         .order_by(func.sum(Vente.montant_total).desc())\
         .limit(10).all()
        if row[0]
    ]

    top_produits = [
        {
            'nom': row[0],
            'quantite': row[1] or 0,
            'chiffre_affaires': row[2] or 0,
        }
        for row in db.session.query(
            Produit.nom,
            func.coalesce(func.sum(LigneVente.quantite), 0),
            func.coalesce(func.sum(LigneVente.montant_total), 0),
        ).join(LigneVente, Produit.id == LigneVente.produit_id)\
         .group_by(Produit.id, Produit.nom)\
         .order_by(func.sum(LigneVente.montant_total).desc())\
         .limit(10).all()
        if row[0]
    ]

    ventes_recentes = Vente.query.order_by(
        Vente.date_vente.desc(),
        Vente.created_at.desc()
    ).limit(10).all()

    return render_template(
        'performances/ventes.html',
        total_ventes=total_ventes,
        chiffre_affaires=chiffre_affaires,
        total_encaisse=total_encaisse,
        total_solde=total_solde,
        ticket_moyen=ticket_moyen,
        taux_encaissement=round(taux_encaissement, 1),
        ventes_payees=ventes_payees,
        ventes_en_attente=ventes_en_attente,
        evolution=evolution,
        statuts=statuts,
        top_clients=top_clients,
        top_produits=top_produits,
        ventes_recentes=ventes_recentes,
    )

@app.route('/api/performances/global')
@login_required
def api_performances_global():
    """API pour les performances globales"""
    denied_response = require_permission('performances_view', 'performances')
    if denied_response:
        return denied_response

    from sqlalchemy import func
    
    # Performances globales
    total_commandes = Commande.query.count()
    total_montant = db.session.query(func.sum(Commande.montant)).scalar() or 0
    
    # Par entité
    par_entite = []
    for e in db.session.query(Commande.entite, func.sum(Commande.montant)).group_by(Commande.entite).all():
        if e[0]:
            par_entite.append({'entite': e[0], 'montant': e[1]})
    
    # Par statut
    par_statut = []
    for s in db.session.query(Commande.statut, func.count(Commande.id)).group_by(Commande.statut).all():
        if s[0]:
            par_statut.append({'statut': s[0], 'count': s[1]})
    
    # Top acheteurs
    top_acheteurs = []
    for a in db.session.query(Commande.acheteur, func.sum(Commande.montant)).group_by(Commande.acheteur)\
                       .order_by(func.sum(Commande.montant).desc()).limit(5).all():
        if a[0]:
            top_acheteurs.append({'acheteur': a[0], 'montant': a[1]})
    
    # Top fournisseurs
    top_fournisseurs = []
    for f in db.session.query(Fournisseur.nom, func.sum(Commande.montant))\
                       .join(Commande, Fournisseur.id == Commande.fournisseur_id)\
                       .group_by(Fournisseur.nom)\
                       .order_by(func.sum(Commande.montant).desc()).limit(5).all():
        top_fournisseurs.append({'fournisseur': f[0], 'montant': f[1]})
    
    return jsonify({
        'total_commandes': total_commandes,
        'total_montant': total_montant,
        'par_entite': par_entite,
        'par_statut': par_statut,
        'top_acheteurs': top_acheteurs,
        'top_fournisseurs': top_fournisseurs
    })

# ==================== ROUTES GESTION UTILISATEURS ====================

@app.route('/admin/utilisateurs')
@login_required
def admin_utilisateurs():
    """Liste des utilisateurs (admin uniquement)"""
    denied_response = require_permission('users_manage')
    if denied_response:
        return denied_response
    
    utilisateurs = Utilisateur.query.order_by(Utilisateur.created_at.desc()).all()
    return render_template('admin/utilisateurs.html', utilisateurs=utilisateurs)


@app.route('/admin/utilisateur/ajouter', methods=['GET', 'POST'])
@login_required
def admin_utilisateur_ajouter():
    """Ajouter un utilisateur (admin uniquement)"""
    denied_response = require_permission('users_manage', 'admin_utilisateurs')
    if denied_response:
        return denied_response
    
    if request.method == 'POST':
        try:
            username = request.form.get('username')
            email = request.form.get('email')
            password = request.form.get('password')
            submitted_role = request.form.get('role')
            role = normalize_user_role(submitted_role)
            nom_complet = request.form.get('nom_complet')
            telephone = request.form.get('telephone')
            
            # Validations
            valider_email(email)
            valider_telephone(telephone)
            valider_mot_de_passe(password)
            
            # Vérifier si l'utilisateur existe déjà
            if Utilisateur.query.filter_by(username=username).first():
                flash('Ce nom d\'utilisateur existe déjà', 'danger')
                return redirect(url_for('admin_utilisateur_ajouter'))
            
            if Utilisateur.query.filter_by(email=email).first():
                flash('Cet email existe déjà', 'danger')
                return redirect(url_for('admin_utilisateur_ajouter'))

            if submitted_role not in ROLE_LABELS:
                raise ValueError('Rôle utilisateur invalide')
            
            # Créer l'utilisateur
            utilisateur = Utilisateur(
                username=username,
                email=email,
                role=role,
                nom_complet=nom_complet,
                telephone=telephone,
                created_by=current_user.id,
                actif=True
            )
            utilisateur.set_password(password)
            
            db.session.add(utilisateur)
            db.session.commit()
            
            # Log
            log = LogAction(
                utilisateur_id=current_user.id,
                action='CREATE',
                table='utilisateur',
                record_id=utilisateur.id,
                details=f'Ajout utilisateur {username}',
                ip_address=request.remote_addr
            )
            db.session.add(log)
            db.session.commit()
            
            flash(f'Utilisateur {username} créé avec succès', 'success')
            return redirect(url_for('admin_utilisateurs'))
        
        except (ValueError, IntegrityError) as e:
            db.session.rollback()
            flash(f'Erreur: {str(e)}', 'danger')
            return redirect(url_for('admin_utilisateur_ajouter'))
    
    return render_template('admin/utilisateur_form.html', 
                         utilisateur=None, 
                         titre="Ajouter un utilisateur")


@app.route('/admin/utilisateur/modifier/<int:id>', methods=['GET', 'POST'])
@login_required
def admin_utilisateur_modifier(id):
    """Modifier un utilisateur (admin uniquement)"""
    denied_response = require_permission('users_manage', 'admin_utilisateurs')
    if denied_response:
        return denied_response
    
    utilisateur = Utilisateur.query.get_or_404(id)
    
    if request.method == 'POST':
        try:
            username = request.form.get('username')
            email = request.form.get('email')
            submitted_role = request.form.get('role')
            role = normalize_user_role(submitted_role)
            nom_complet = request.form.get('nom_complet')
            telephone = request.form.get('telephone')
            actif = 'actif' in request.form
            nouveau_password = request.form.get('new_password')
            confirmer_password = request.form.get('confirm_password')

            valider_email(email)
            valider_telephone(telephone)

            username_exists = Utilisateur.query.filter(
                Utilisateur.username == username,
                Utilisateur.id != utilisateur.id
            ).first()
            if username_exists:
                raise ValueError("Ce nom d'utilisateur existe déjà")

            email_exists = Utilisateur.query.filter(
                Utilisateur.email == email,
                Utilisateur.id != utilisateur.id
            ).first()
            if email_exists:
                raise ValueError("Cet email existe déjà")
            if submitted_role not in ROLE_LABELS:
                raise ValueError("Rôle utilisateur invalide")

            if utilisateur.id == current_user.id and normalize_user_role(current_user.role) == ROLE_ADMIN:
                admins = Utilisateur.query.filter_by(role=ROLE_ADMIN).count()
                if admins == 1 and role != ROLE_ADMIN:
                    raise ValueError("Vous ne pouvez pas retirer votre propre rôle admin car vous êtes le seul administrateur")
                if admins == 1 and not actif:
                    raise ValueError("Vous ne pouvez pas désactiver le seul administrateur")

            utilisateur.username = username
            utilisateur.email = email
            utilisateur.role = role
            utilisateur.nom_complet = nom_complet
            utilisateur.telephone = telephone
            utilisateur.actif = actif

            if nouveau_password:
                valider_mot_de_passe(nouveau_password)
                if nouveau_password != confirmer_password:
                    raise ValueError("Les mots de passe ne correspondent pas")
                utilisateur.set_password(nouveau_password)

            db.session.commit()

            log = LogAction(
                utilisateur_id=current_user.id,
                action='UPDATE',
                table='utilisateur',
                record_id=utilisateur.id,
                details=f'Modification utilisateur {utilisateur.username}',
                ip_address=request.remote_addr
            )
            db.session.add(log)
            db.session.commit()

            flash(f'Utilisateur {utilisateur.username} modifié avec succès', 'success')
            return redirect(url_for('admin_utilisateurs'))
        except (ValueError, IntegrityError) as e:
            db.session.rollback()
            flash(f'Erreur: {str(e)}', 'danger')
    
    return render_template('admin/utilisateur_form.html', 
                         utilisateur=utilisateur, 
                         titre="Modifier l'utilisateur")


@app.route('/admin/utilisateur/supprimer/<int:id>', methods=['POST'])
@login_required
def admin_utilisateur_supprimer(id):
    """Supprimer un utilisateur (admin uniquement)"""
    denied_response = require_permission('users_manage', 'admin_utilisateurs')
    if denied_response:
        return denied_response
    
    utilisateur = Utilisateur.query.get_or_404(id)
    
    # Empêcher la suppression de son propre compte
    if utilisateur.id == current_user.id:
        flash('Vous ne pouvez pas supprimer votre propre compte', 'danger')
        return redirect(url_for('admin_utilisateurs'))
    
    # Empêcher la suppression du dernier admin
    if normalize_user_role(utilisateur.role) == ROLE_ADMIN:
        admins = Utilisateur.query.filter_by(role=ROLE_ADMIN).count()
        if admins <= 1:
            flash('Vous ne pouvez pas supprimer le dernier administrateur', 'danger')
            return redirect(url_for('admin_utilisateurs'))
    
    # Log avant suppression
    log = LogAction(
        utilisateur_id=current_user.id,
        action='DELETE',
        table='utilisateur',
        record_id=utilisateur.id,
        details=f'Suppression utilisateur {utilisateur.username}',
        ip_address=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()
    
    db.session.delete(utilisateur)
    db.session.commit()
    
    flash(f'Utilisateur {utilisateur.username} supprimé avec succès', 'success')
    return redirect(url_for('admin_utilisateurs'))


@app.route('/admin/profil', methods=['GET', 'POST'])
@login_required
def admin_profil():
    """Modifier son propre profil"""
    utilisateur = current_user
    
    if request.method == 'POST':
        action = request.form.get('action')

        try:
            if action == 'update_profile':
                email = request.form.get('email')
                nom_complet = request.form.get('nom_complet')
                telephone = request.form.get('telephone')

                valider_email(email)
                valider_telephone(telephone)

                email_exists = Utilisateur.query.filter(
                    Utilisateur.email == email,
                    Utilisateur.id != utilisateur.id
                ).first()
                if email_exists:
                    raise ValueError("Cet email existe déjà")

                utilisateur.email = email
                utilisateur.nom_complet = nom_complet
                utilisateur.telephone = telephone
                db.session.commit()

                log = LogAction(
                    utilisateur_id=current_user.id,
                    action='UPDATE',
                    table='profil',
                    record_id=utilisateur.id,
                    details='Mise à jour du profil',
                    ip_address=request.remote_addr
                )
                db.session.add(log)
                db.session.commit()

                flash('Profil mis à jour avec succès', 'success')

            elif action == 'change_password':
                ancien_password = request.form.get('ancien_password')
                nouveau_password = request.form.get('nouveau_password')
                confirmer_password = request.form.get('confirmer_password')

                if not ancien_password or not nouveau_password or not confirmer_password:
                    raise ValueError('Tous les champs mot de passe sont obligatoires')
                if not utilisateur.check_password(ancien_password):
                    raise ValueError('Ancien mot de passe incorrect')
                if nouveau_password != confirmer_password:
                    raise ValueError('Les nouveaux mots de passe ne correspondent pas')

                valider_mot_de_passe(nouveau_password)
                utilisateur.set_password(nouveau_password)
                db.session.commit()

                log = LogAction(
                    utilisateur_id=current_user.id,
                    action='UPDATE',
                    table='profil',
                    record_id=utilisateur.id,
                    details='Changement de mot de passe',
                    ip_address=request.remote_addr
                )
                db.session.add(log)
                db.session.commit()

                flash('Mot de passe modifié avec succès', 'success')
            else:
                raise ValueError("Action de formulaire inconnue")

            return redirect(url_for('admin_profil'))
        except (ValueError, IntegrityError) as e:
            db.session.rollback()
            flash(f'Erreur: {str(e)}', 'danger')
            return redirect(url_for('admin_profil'))
    
    return render_template('admin/profil.html', utilisateur=utilisateur)


@app.route('/admin/logs')
@login_required
def admin_logs():
    """Consulter les logs d'activité (admin uniquement)"""
    denied_response = require_permission('logs_view')
    if denied_response:
        return denied_response
    
    # Récupération des filtres
    action = request.args.get('action', '')
    table = request.args.get('table', '')
    utilisateur_id = request.args.get('utilisateur', '')
    page = get_requested_page()
    
    query = LogAction.query
    
    if action:
        query = query.filter(LogAction.action == action)
    if table:
        query = query.filter(LogAction.table == table)
    if utilisateur_id and utilisateur_id.isdigit():
        query = query.filter(LogAction.utilisateur_id == int(utilisateur_id))
    
    logs_pagination = query.options(joinedload(LogAction.utilisateur)).order_by(LogAction.created_at.desc()).paginate(
        page=page,
        per_page=app.config.get('LOG_PAGE_SIZE', 50),
        error_out=False,
    )
    logs = logs_pagination.items
    
    # Liste des actions et tables pour les filtres
    actions = db.session.query(LogAction.action).distinct().all()
    tables = db.session.query(LogAction.table).distinct().all()
    utilisateurs = Utilisateur.query.order_by(Utilisateur.username.asc()).all()
    
    return render_template('admin/logs.html',
                         logs=logs,
                         actions=[a[0] for a in actions if a[0]],
                         tables=[t[0] for t in tables if t[0]],
                         utilisateurs=utilisateurs,
                         pagination=logs_pagination)


if __name__ == '__main__':
    init_db()
    app.run(
        debug=not app.config.get('IS_PRODUCTION'),
        host='0.0.0.0',
        port=int(os.environ.get('PORT') or 5000),
    )
