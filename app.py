from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, session
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from flask_wtf.csrf import CSRFProtect
from werkzeug.utils import secure_filename
from datetime import datetime, date, timedelta
import pandas as pd
import os
from io import BytesIO, StringIO
import json

from config import Config
from models import db, Utilisateur, Fournisseur, Commande, LogAction
from sqlalchemy import func, case, and_, or_, inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError

# Initialisation de l'application
app = Flask(__name__)
app.config.from_object(Config)

# Initialisation des extensions
db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Veuillez vous connecter pour accéder à cette page'

# CSRF Protection
csrf = CSRFProtect(app)

# Création du dossier d'upload si nécessaire
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

@login_manager.user_loader
def load_user(user_id):
    return Utilisateur.query.get(int(user_id))

# ==================== CONTEXT PROCESSORS ====================

@app.context_processor
def utility_processor():
    def format_date(d):
        if d:
            return d.strftime('%d/%m/%Y')
        return ''
    
    def format_montant(m):
        return f"{m:,.0f}" if m else "0"
    
    return dict(format_date=format_date, format_montant=format_montant)

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

# ==================== ROUTES AUTHENTIFICATION ====================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = Utilisateur.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            if not user.actif:
                flash('Ce compte est désactivé', 'danger')
                return render_template('login.html')
            
            login_user(user)
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            # Redirection vers la page demandée
            next_page = request.args.get('next')
            flash(f'Bienvenue {user.username}!', 'success')
            return redirect(next_page or url_for('dashboard'))
        else:
            flash('Nom d\'utilisateur ou mot de passe incorrect', 'danger')
    
    return render_template('login.html')

@app.route('/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    flash('Vous avez été déconnecté', 'info')
    return redirect(url_for('login'))

# ==================== ROUTES PRINCIPALES ====================

@app.route('/')
@app.route('/dashboard')
@login_required
def dashboard():
    # KPI
    total_commandes = Commande.query.count()
    montant_total = db.session.query(db.func.sum(Commande.montant)).scalar() or 0
    total_a_payer = db.session.query(db.func.sum(Commande.solde)).filter(
        Commande.statut == Commande.STATUT_A_PAYER
    ).scalar() or 0
    total_payer = db.session.query(db.func.sum(Commande.solde)).filter(
        Commande.statut == Commande.STATUT_PAYE
    ).scalar() or 0
    
    # Commandes en retard (sans charger toutes les commandes en mémoire)
    nb_retard = db.session.query(db.func.count(Commande.id)).filter(
        Commande.date_livraison.isnot(None),
        Commande.date_livraison < date.today()
    ).scalar() or 0
    
    # Dernières commandes
    dernieres_commandes = Commande.query.order_by(Commande.date_cde.desc()).limit(10).all()
    
    # Données pour graphiques
    par_entite = db.session.query(Commande.entite, db.func.sum(Commande.montant)).group_by(Commande.entite).all()
    par_statut = db.session.query(Commande.statut, db.func.count(Commande.id)).group_by(Commande.statut).all()
    
    # Évolution par mois (12 derniers mois)
    evolution = []
    for i in range(11, -1, -1):
        mois = date.today().replace(day=1) - timedelta(days=30*i)
        debut_mois = mois.replace(day=1)
        if mois.month == 12:
            fin_mois = mois.replace(year=mois.year+1, month=1, day=1) - timedelta(days=1)
        else:
            fin_mois = mois.replace(month=mois.month+1, day=1) - timedelta(days=1)
        
        total = db.session.query(db.func.sum(Commande.montant)).filter(
            Commande.date_cde >= debut_mois,
            Commande.date_cde <= fin_mois
        ).scalar() or 0
        
        evolution.append({
            'mois': debut_mois.strftime('%b %Y'),
            'total': total
        })
    
    # Top fournisseurs
    top_fournisseurs = db.session.query(
        Fournisseur.nom, 
        db.func.sum(Commande.montant).label('total')
    ).join(Commande, Fournisseur.id == Commande.fournisseur_id)\
     .group_by(Fournisseur.nom)\
     .order_by(db.func.sum(Commande.montant).desc())\
     .limit(5).all()
    
    return render_template('dashboard.html',
                         total_commandes=total_commandes,
                         montant_total=montant_total,
                         total_a_payer=total_a_payer,
                         total_payer=total_payer,
                         nb_retard=nb_retard,
                         dernieres_commandes=dernieres_commandes,
                         par_entite=par_entite,
                         par_statut=par_statut,
                         evolution=evolution,
                         top_fournisseurs=top_fournisseurs)

# ==================== ROUTES COMMANDES ====================

@app.route('/commandes')
@login_required
def commandes():
    # Récupération des filtres
    entite = request.args.get('entite', '')
    statut = request.args.get('statut', '')
    acheteur = request.args.get('acheteur', '')
    fournisseur = request.args.get('fournisseur', '')
    recherche = request.args.get('recherche', '')
    
    query = Commande.query
    
    if entite:
        query = query.filter(Commande.entite == entite)
    if statut in {Commande.STATUT_PAYE, Commande.STATUT_A_PAYER}:
        query = query.filter(Commande.statut == statut)
    if acheteur:
        query = query.filter(Commande.acheteur == acheteur)
    if fournisseur and fournisseur.isdigit():
        query = query.filter(Commande.fournisseur_id == int(fournisseur))
    if recherche:
        query = query.filter(
            Commande.affaire.contains(recherche) | 
            Commande.demandeur.contains(recherche) |
            Commande.bon_commande.contains(recherche)
        )
    
    commandes = query.order_by(Commande.date_cde.desc()).all()
    
    # Récupération des options pour les filtres
    entites = db.session.query(Commande.entite).distinct().all()
    acheteurs = db.session.query(Commande.acheteur).distinct().all()
    fournisseurs = Fournisseur.query.order_by(Fournisseur.nom).all()
    
    return render_template('commandes.html',
                         commandes=commandes,
                         entites=[e[0] for e in entites if e[0]],
                         acheteurs=[a[0] for a in acheteurs if a[0]],
                         fournisseurs=fournisseurs,
                         filtres={
                             'entite': entite,
                             'statut': statut,
                             'acheteur': acheteur,
                             'fournisseur': fournisseur,
                             'recherche': recherche,
                         })

@app.route('/commande/ajouter', methods=['GET', 'POST'])
@login_required
def ajouter_commande():
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('dashboard'))
    
    fournisseurs = Fournisseur.query.order_by(Fournisseur.nom).all()
    
    if request.method == 'POST':
        try:
            # Validation des montants
            montant = valider_montant(request.form.get('montant', 0))
            avance = valider_montant(request.form.get('avance', 0))
            
            # Vérifier que avance <= montant
            if avance > montant:
                flash('L\'avance ne peut pas être supérieure au montant', 'danger')
                return render_template('admin/commande_form.html', 
                                     fournisseurs=fournisseurs, 
                                     commande=None,
                                     titre="Ajouter une commande")
            
            commande = Commande(
                nr=request.form.get('nr', 0),
                date_cde=datetime.strptime(request.form['date_cde'], '%Y-%m-%d').date() if request.form.get('date_cde') else None,
                entite=request.form.get('entite'),
                demandeur=request.form.get('demandeur'),
                service_demandeur=request.form.get('service_demandeur'),
                acheteur=request.form.get('acheteur'),
                fournisseur_id=int(request.form['fournisseur_id']) if request.form.get('fournisseur_id') else None,
                affaire=request.form.get('affaire'),
                bon_commande=request.form.get('bon_commande'),
                date_livraison=datetime.strptime(request.form['date_livraison'], '%Y-%m-%d').date() if request.form.get('date_livraison') else None,
                bon_livraison=request.form.get('bon_livraison'),
                facture=request.form.get('facture'),
                montant=montant,
                avance=avance,
                date_paiement=datetime.strptime(request.form['date_paiement'], '%Y-%m-%d').date() if request.form.get('date_paiement') else None,
                commentaire=request.form.get('commentaire')
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
                         titre="Ajouter une commande")

@app.route('/commande/modifier/<int:id>', methods=['GET', 'POST'])
@login_required
def modifier_commande(id):
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('dashboard'))
    
    commande = Commande.query.get_or_404(id)
    fournisseurs = Fournisseur.query.order_by(Fournisseur.nom).all()
    
    if request.method == 'POST':
        try:
            # Validation des montants
            montant = valider_montant(request.form.get('montant', 0))
            avance = valider_montant(request.form.get('avance', 0))
            
            # Vérifier que avance <= montant
            if avance > montant:
                flash('L\'avance ne peut pas être supérieure au montant', 'danger')
                return render_template('admin/commande_form.html', 
                                     commande=commande, 
                                     fournisseurs=fournisseurs,
                                     titre="Modifier la commande")
            
            commande.nr = request.form.get('nr', 0)
            commande.date_cde = datetime.strptime(request.form['date_cde'], '%Y-%m-%d').date() if request.form.get('date_cde') else None
            commande.entite = request.form.get('entite')
            commande.demandeur = request.form.get('demandeur')
            commande.service_demandeur = request.form.get('service_demandeur')
            commande.acheteur = request.form.get('acheteur')
            commande.fournisseur_id = int(request.form['fournisseur_id']) if request.form.get('fournisseur_id') else None
            commande.affaire = request.form.get('affaire')
            commande.bon_commande = request.form.get('bon_commande')
            commande.date_livraison = datetime.strptime(request.form['date_livraison'], '%Y-%m-%d').date() if request.form.get('date_livraison') else None
            commande.bon_livraison = request.form.get('bon_livraison')
            commande.facture = request.form.get('facture')
            commande.montant = montant
            commande.avance = avance
            commande.date_paiement = datetime.strptime(request.form['date_paiement'], '%Y-%m-%d').date() if request.form.get('date_paiement') else None
            commande.commentaire = request.form.get('commentaire')
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
                         fournisseurs=fournisseurs,
                         titre="Modifier la commande")

@app.route('/commande/supprimer/<int:id>', methods=['POST'])
@login_required
def supprimer_commande(id):
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('dashboard'))
    
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
    commande = Commande.query.get_or_404(id)
    return render_template('commande_detail.html', commande=commande)

# ==================== ROUTES FOURNISSEURS ====================

@app.route('/fournisseurs')
@login_required
def fournisseurs():
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('dashboard'))
    
    fournisseurs = Fournisseur.query.order_by(Fournisseur.nom).all()
    return render_template('admin/fournisseurs.html', fournisseurs=fournisseurs)

@app.route('/fournisseur/ajouter', methods=['GET', 'POST'])
@login_required
def ajouter_fournisseur():
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('dashboard'))
    
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
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('dashboard'))
    
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
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('dashboard'))
    
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

# ==================== ROUTES IMPORT/EXPORT ====================

@app.route('/exporter/excel')
@login_required
def exporter_excel():
    commandes = Commande.query.all()
    
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
            'N° Bon Livraison': c.bon_livraison,
            'Facture': c.facture,
            'Montant': c.montant,
            'Avance': c.avance,
            'Solde': c.solde,
            'Statut': c.statut,
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

@app.route('/importer/excel', methods=['POST'])
@login_required
def importer_excel():
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('dashboard'))
    
    if 'fichier' not in request.files:
        flash('Aucun fichier sélectionné', 'danger')
        return redirect(url_for('commandes'))
    
    fichier = request.files['fichier']
    if fichier.filename == '':
        flash('Aucun fichier sélectionné', 'danger')
        return redirect(url_for('commandes'))
    
    # Vérifier l'extension du fichier
    if not fichier.filename.lower().endswith(('.xlsx', '.xls', '.csv')):
        flash('Format de fichier non supporté. Utilisez Excel (.xlsx, .xls) ou CSV', 'danger')
        return redirect(url_for('commandes'))
    
    try:
        extension = os.path.splitext(fichier.filename)[1].lower()

        # Charger le fichier avec gestion d'erreurs
        try:
            if extension == '.csv':
                df = pd.read_csv(fichier)
            else:
                df = pd.read_excel(fichier)
        except Exception as e:
            flash(f'Erreur lors de la lecture du fichier: {str(e)[:100]}', 'danger')
            return redirect(url_for('commandes'))
        
        if df.empty:
            flash('Le fichier est vide', 'danger')
            return redirect(url_for('commandes'))
        
        # Vérifier les colonnes requises
        colonnes_requises = ['Nr.', 'Date CDE', 'Montant']
        colonnes_manquantes = [col for col in colonnes_requises if col not in df.columns]
        if colonnes_manquantes:
            flash(f'Colonnes manquantes: {", ".join(colonnes_manquantes)}', 'danger')
            return redirect(url_for('commandes'))
        
        compteur = 0
        erreurs = []
        
        for idx, row in df.iterrows():
            try:
                # Trouver ou créer le fournisseur
                nom_fournisseur = row.get('Fournisseur', '')
                fournisseur = None
                if nom_fournisseur and pd.notna(nom_fournisseur):
                    nom_fournisseur = str(nom_fournisseur).strip()
                    fournisseur = Fournisseur.query.filter_by(nom=nom_fournisseur).first()
                    if not fournisseur:
                        fournisseur = Fournisseur(
                            nom=nom_fournisseur,
                            pays='Cameroun',
                            statut='Actif'
                        )
                        db.session.add(fournisseur)
                        db.session.flush()
                
                # Validation des montants
                montant = parser_montant_import(row.get('Montant', 0))
                avance = parser_montant_import(row.get('Avance', 0))
                
                if montant < 0 or avance < 0:
                    erreurs.append(f'Ligne {idx+2}: Montants négatifs')
                    continue
                
                if avance > montant:
                    erreurs.append(f'Ligne {idx+2}: Avance > Montant')
                    continue

                bon_commande = str(row.get('N° Bon commande')).strip() if pd.notna(row.get('N° Bon commande')) else None
                facture = str(row.get('Facture')).strip() if pd.notna(row.get('Facture')) else None
                existe = None
                if bon_commande:
                    existe = Commande.query.filter_by(bon_commande=bon_commande).first()
                if not existe and facture:
                    existe = Commande.query.filter_by(facture=facture).first()
                if existe:
                    erreurs.append(f'Ligne {idx+2}: commande déjà existante')
                    continue
                
                commande = Commande(
                    nr=int(row.get('Nr.', 0)) if pd.notna(row.get('Nr.')) else None,
                    date_cde=parser_date_import(row.get('Date CDE')),
                    entite=str(row.get('Entité')).strip() if pd.notna(row.get('Entité')) else None,
                    demandeur=str(row.get('Demandeur')).strip() if pd.notna(row.get('Demandeur')) else None,
                    service_demandeur=str(row.get('Service Demandeur')).strip() if pd.notna(row.get('Service Demandeur')) else None,
                    acheteur=str(row.get('Acheteur')).strip() if pd.notna(row.get('Acheteur')) else None,
                    fournisseur_id=fournisseur.id if fournisseur else None,
                    affaire=str(row.get('Affaire/Commande')).strip() if pd.notna(row.get('Affaire/Commande')) else None,
                    bon_commande=bon_commande,
                    date_livraison=parser_date_import(row.get('Date Livraison')),
                    bon_livraison=str(row.get('N° Bon Livraison')).strip() if pd.notna(row.get('N° Bon Livraison')) else None,
                    facture=facture,
                    montant=montant,
                    avance=avance,
                    date_paiement=parser_date_import(row.get('Date Paiement')),
                    commentaire=str(row.get('Commentaire')).strip() if pd.notna(row.get('Commentaire')) else None
                )
                commande.calculer_solde()
                db.session.add(commande)
                compteur += 1
            except Exception as row_error:
                erreurs.append(f'Ligne {idx+2}: {str(row_error)[:50]}')
                continue
        
        db.session.commit()
        
        # Message de succès avec détail des erreurs
        if erreurs:
            msg_erreurs = ' | '.join(erreurs[:5])  # Afficher max 5 erreurs
            if len(erreurs) > 5:
                msg_erreurs += f' (+{len(erreurs)-5} erreurs)'
            flash(f'Import partiel: {compteur} commandes importées. Erreurs: {msg_erreurs}', 'warning')
        else:
            flash(f'Import réussi: {compteur} commandes importées', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Erreur lors de l\'import: {str(e)}', 'danger')
    
    return redirect(url_for('commandes'))

# ==================== ROUTES API ====================

@app.route('/api/commandes')
@login_required
def api_commandes():
    commandes = Commande.query.all()
    return jsonify([c.to_dict() for c in commandes])

@app.route('/api/commandes/statistiques')
@login_required
def api_statistiques():
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
    commandes = Commande.query.all()
    nb_retard = sum(1 for c in commandes if c.est_en_retard())
    
    return jsonify({
        'total_commandes': len(commandes),
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

def migrate_legacy_sqlite_schema():
    """Ajoute les colonnes manquantes sur une base SQLite existante."""
    database_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
    if not database_uri.startswith('sqlite'):
        return

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
            'created_at': 'DATETIME',
        },
        'commandes': {
            'date_paiement': 'DATE',
            'updated_at': 'DATETIME',
        },
    }

    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())

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

        # Normalisation des données héritées pour éviter les crashs ORM.
        connection.execute(text("""
            UPDATE commandes
            SET montant = COALESCE(montant, 0),
                avance = COALESCE(avance, 0)
        """))
        connection.execute(text("""
            UPDATE commandes
            SET solde = COALESCE(montant, 0) - COALESCE(avance, 0)
        """))
        connection.execute(
            text("""
                UPDATE commandes
                SET statut = CASE
                    WHEN COALESCE(solde, 0) <= 0 THEN :statut_paye
                    ELSE :statut_a_payer
                END
            """),
            {
                'statut_paye': Commande.STATUT_PAYE,
                'statut_a_payer': Commande.STATUT_A_PAYER,
            }
        )
        for column_name in ('date_cde', 'date_livraison', 'date_paiement'):
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
        migrate_legacy_sqlite_schema()

        is_production = os.environ.get('FLASK_ENV') == 'production'
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
        
        # Créer quelques fournisseurs de test si la base est vide
        fournisseurs_test = [
            {'nom': 'ETS GREEN', 'pays': 'Cameroun', 'categorie': 'Transport'},
            {'nom': 'BAT ELEC', 'pays': 'Cameroun', 'categorie': 'Électrique'},
            {'nom': 'STE ICE', 'pays': 'Cameroun', 'categorie': 'Mobilier'},
            {'nom': 'DJIMY TECHNOLOGIE SERVICES', 'pays': 'Cameroun', 'categorie': 'Informatique'},
            {'nom': 'FOSHAN YOU YOU FURNITURE CO', 'pays': 'Chine', 'categorie': 'Mobilier'},
        ]
        created_count = 0
        for fournisseur_data in fournisseurs_test:
            exists = Fournisseur.query.filter_by(nom=fournisseur_data['nom']).first()
            if exists:
                continue
            db.session.add(Fournisseur(**fournisseur_data))
            created_count += 1

        if created_count:
            db.session.commit()
            print("Fournisseurs de test créés")

# ==================== ROUTES PERFORMANCES ====================

@app.route('/performances')
@login_required
def performances():
    """Page principale des performances"""
    return render_template('performances/index.html')


@app.route('/performances/acheteurs')
@login_required
def performances_acheteurs():
    """Performances par acheteur"""
    from sqlalchemy import func, case
    
    # Utilisation de CASE WHEN au lieu de IF (compatible SQLite)
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
    
    acheteurs_data = []
    
    for stat in stats_acheteurs:
        if stat.acheteur:
            # Calcul du délai moyen directement en Python (plus simple avec une requête distincte si nécessaire)
            commandes_acheteur = Commande.query.filter_by(acheteur=stat.acheteur).all()
            
            # Calcul du délai moyen
            delais = [c.get_delai() for c in commandes_acheteur if c.date_livraison]
            delai_moyen = sum(delais) / len(delais) if delais else 0
            
            # Calcul du taux de retard
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
    from sqlalchemy import func
    
    # Commandes de l'acheteur
    commandes = Commande.query.filter_by(acheteur=nom).order_by(Commande.date_cde.desc()).all()
    
    # Statistiques par mois
    stats_mensuelles = db.session.query(
        func.strftime('%Y-%m', Commande.date_cde).label('mois'),
        func.count(Commande.id).label('total_commandes'),
        func.sum(Commande.montant).label('total_montant')
    ).filter(Commande.acheteur == nom)\
     .group_by('mois')\
     .order_by('mois').all()
    
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
    
    # Évolution mensuelle pour graphique
    evolution = [{'mois': m[0], 'total': m[2]} for m in stats_mensuelles]
    
    return render_template('performances/acheteur_detail.html',
                         acheteur=nom,
                         commandes=commandes,
                         evolution=evolution,
                         top_fournisseurs=top_fournisseurs,
                         total_commandes=len(commandes),
                         total_montant=sum(c.montant for c in commandes),
                         montant_a_payer=sum(c.solde for c in commandes if c.statut == Commande.STATUT_A_PAYER))


@app.route('/performances/fournisseurs')
@login_required
def performances_fournisseurs():
    """Performances par fournisseur"""
    from sqlalchemy import func, case
    
    # Utilisation de CASE WHEN au lieu de IF (compatible SQLite)
    stats_fournisseurs = db.session.query(
        Fournisseur.id,
        Fournisseur.nom,
        Fournisseur.pays,
        func.count(Commande.id).label('total_commandes'),
        func.sum(Commande.montant).label('total_montant'),
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
    ).join(Commande, Fournisseur.id == Commande.fournisseur_id, isouter=True)\
     .group_by(Fournisseur.id).all()
    
    fournisseurs_data = []
    
    for f in stats_fournisseurs:
        commandes_fournisseur = Commande.query.filter_by(fournisseur_id=f.id).all() if f.total_commandes else []
        
        # Calcul du délai moyen
        delais = [c.get_delai() for c in commandes_fournisseur if c.date_livraison]
        delai_moyen = sum(delais) / len(delais) if delais else 0
        
        # Calcul du taux de retard
        taux_retard = (f.nb_retard / f.total_commandes * 100) if f.total_commandes > 0 else 0
        
        fournisseurs_data.append({
            'id': f.id,
            'nom': f.nom,
            'pays': f.pays,
            'total_commandes': f.total_commandes,
            'total_montant': f.total_montant or 0,
            'montant_a_payer': f.montant_a_payer or 0,
            'delai_moyen': round(delai_moyen, 1),
            'taux_retard': round(taux_retard, 1)
        })
    
    return render_template('performances/fournisseurs.html', 
                         stats=fournisseurs_data)

@app.route('/performances/fournisseur/<int:id>')
@login_required
def performance_fournisseur_detail(id):
    """Détail des performances d'un fournisseur"""
    fournisseur = Fournisseur.query.get_or_404(id)
    
    # Commandes du fournisseur
    commandes = Commande.query.filter_by(fournisseur_id=id).order_by(Commande.date_cde.desc()).all()
    
    # Statistiques par mois
    stats_mensuelles = db.session.query(
        func.strftime('%Y-%m', Commande.date_cde).label('mois'),
        func.count(Commande.id).label('total_commandes'),
        func.sum(Commande.montant).label('total_montant')
    ).filter(Commande.fournisseur_id == id)\
     .group_by('mois')\
     .order_by('mois').all()
    
    # Achats par catégorie
    # (à adapter selon votre structure de données)
    
    evolution = [{'mois': m[0], 'total': m[2]} for m in stats_mensuelles]
    
    return render_template('performances/fournisseur_detail.html',
                         fournisseur=fournisseur,
                         commandes=commandes,
                         evolution=evolution,
                         total_commandes=len(commandes),
                         total_montant=sum(c.montant for c in commandes),
                         montant_a_payer=sum(c.solde for c in commandes if c.statut == Commande.STATUT_A_PAYER))


@app.route('/performances/produits')
@login_required
def performances_produits():
    """Performances par produit"""
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
    
    return render_template('performances/produits.html', 
                         stats=produits_data,
                         total_montant=total_general)

@app.route('/api/performances/global')
@login_required
def api_performances_global():
    """API pour les performances globales"""
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
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('dashboard'))
    
    utilisateurs = Utilisateur.query.order_by(Utilisateur.created_at.desc()).all()
    return render_template('admin/utilisateurs.html', utilisateurs=utilisateurs)


@app.route('/admin/utilisateur/ajouter', methods=['GET', 'POST'])
@login_required
def admin_utilisateur_ajouter():
    """Ajouter un utilisateur (admin uniquement)"""
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        try:
            username = request.form.get('username')
            email = request.form.get('email')
            password = request.form.get('password')
            role = request.form.get('role')
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
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('dashboard'))
    
    utilisateur = Utilisateur.query.get_or_404(id)
    
    if request.method == 'POST':
        try:
            username = request.form.get('username')
            email = request.form.get('email')
            role = request.form.get('role')
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

            if utilisateur.id == current_user.id and current_user.role == 'admin':
                admins = Utilisateur.query.filter_by(role='admin').count()
                if admins == 1 and role != 'admin':
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
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('dashboard'))
    
    utilisateur = Utilisateur.query.get_or_404(id)
    
    # Empêcher la suppression de son propre compte
    if utilisateur.id == current_user.id:
        flash('Vous ne pouvez pas supprimer votre propre compte', 'danger')
        return redirect(url_for('admin_utilisateurs'))
    
    # Empêcher la suppression du dernier admin
    if utilisateur.role == 'admin':
        admins = Utilisateur.query.filter_by(role='admin').count()
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
    if current_user.role != 'admin':
        flash('Accès non autorisé', 'danger')
        return redirect(url_for('dashboard'))
    
    # Récupération des filtres
    action = request.args.get('action', '')
    table = request.args.get('table', '')
    utilisateur_id = request.args.get('utilisateur', '')
    
    query = LogAction.query
    
    if action:
        query = query.filter(LogAction.action == action)
    if table:
        query = query.filter(LogAction.table == table)
    if utilisateur_id and utilisateur_id.isdigit():
        query = query.filter(LogAction.utilisateur_id == int(utilisateur_id))
    
    logs = query.order_by(LogAction.created_at.desc()).limit(500).all()
    
    # Liste des actions et tables pour les filtres
    actions = db.session.query(LogAction.action).distinct().all()
    tables = db.session.query(LogAction.table).distinct().all()
    utilisateurs = Utilisateur.query.all()
    
    return render_template('admin/logs.html',
                         logs=logs,
                         actions=[a[0] for a in actions if a[0]],
                         tables=[t[0] for t in tables if t[0]],
                         utilisateurs=utilisateurs)


if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
