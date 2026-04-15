from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, date
import math
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

class Utilisateur(UserMixin, db.Model):
    __tablename__ = 'utilisateurs'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='spectateur', index=True)
    actif = db.Column(db.Boolean, default=True, index=True)
    nom_complet = db.Column(db.String(100))  # ← Cette ligne doit exister
    telephone = db.Column(db.String(50))     # ← Cette ligne doit exister
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    created_by = db.Column(db.Integer, db.ForeignKey('utilisateurs.id'))
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def __repr__(self):
        return f'<Utilisateur {self.username}>'

class Fournisseur(db.Model):
    __tablename__ = 'fournisseurs'
    
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(200), nullable=False, index=True)
    statut_juridique = db.Column(db.String(100))
    pays = db.Column(db.String(100))
    ville = db.Column(db.String(100))
    dirigeant = db.Column(db.String(100))
    telephone1 = db.Column(db.String(50))
    telephone2 = db.Column(db.String(50))
    email1 = db.Column(db.String(100))
    email2 = db.Column(db.String(100))
    categorie = db.Column(db.String(100), index=True)
    statut = db.Column(db.String(50), default='Actif', index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    commandes = db.relationship('Commande', backref='fournisseur', lazy=True)
    
    def __repr__(self):
        return f'<Fournisseur {self.nom}>'

class Commande(db.Model):
    __tablename__ = 'commandes'
    STATUT_PAYE = 'PAYÉ'
    STATUT_A_PAYER = 'A PAYER'
    PHASE_EN_COURS = 'EN COURS'
    PHASE_ACHEVEE = 'ACHEVÉE'
    
    id = db.Column(db.Integer, primary_key=True)
    nr = db.Column(db.Integer)
    date_cde = db.Column(db.Date, index=True)
    entite = db.Column(db.String(50), index=True)
    demandeur = db.Column(db.String(100))
    service_demandeur = db.Column(db.String(100))
    acheteur = db.Column(db.String(50), index=True)
    fournisseur_id = db.Column(db.Integer, db.ForeignKey('fournisseurs.id'), index=True)
    affaire = db.Column(db.Text)
    bon_commande = db.Column(db.String(100), index=True)
    date_livraison = db.Column(db.Date, index=True)
    date_reception = db.Column(db.Date, index=True)
    bon_livraison = db.Column(db.String(100))
    facture = db.Column(db.String(100), index=True)
    montant = db.Column(db.Float, default=0)
    avance = db.Column(db.Float, default=0)
    solde = db.Column(db.Float, default=0)
    prix_reference_marche = db.Column(db.Float, default=0)
    commande_conforme = db.Column(db.Boolean, default=True, index=True)
    rupture_fournisseur = db.Column(db.Boolean, default=False, index=True)
    note_fournisseur = db.Column(db.Float)
    note_service = db.Column(db.Float)
    statut = db.Column(db.String(20), index=True)
    date_paiement = db.Column(db.Date, index=True)
    commentaire = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)

    def calculer_solde(self):
        """Calcule le solde et met à jour le statut de paiement."""
        montant = float(self.montant or 0)
        avance = float(self.avance or 0)
        self.solde = max(montant - avance, 0)
        paiement_complet = avance >= montant and self.date_paiement is not None
        self.statut = self.STATUT_PAYE if paiement_complet else self.STATUT_A_PAYER

    def est_payee(self):
        return self.statut == self.STATUT_PAYE

    def est_achevee(self):
        return self.est_payee() and self.date_reception is not None and bool((self.bon_livraison or '').strip())

    def get_statut_avancement(self):
        return self.PHASE_ACHEVEE if self.est_achevee() else self.PHASE_EN_COURS

    def get_niveau_processus(self):
        if self.est_achevee():
            return 'Commande achevée'
        if self.est_payee():
            return 'Payée, en attente de réception'
        return 'En attente de paiement'

    def get_ecart_livraison(self):
        """Retourne l'écart entre date prévue et date réelle."""
        if self.date_livraison and self.date_reception:
            return (self.date_reception - self.date_livraison).days
        return None

    def get_ecart_prix_marche_pct(self):
        """Retourne l'écart de prix par rapport au marché."""
        if self.prix_reference_marche and self.prix_reference_marche > 0:
            return ((self.montant or 0) - self.prix_reference_marche) / self.prix_reference_marche * 100
        return None
    
    def get_delai(self):
        """Retourne le nombre de jours de retard (0 si pas de retard)"""
        if self.date_livraison and self.date_livraison < date.today():
            return (date.today() - self.date_livraison).days
        return 0
    
    def est_en_retard(self):
        return self.get_delai() > 0
    
    def to_dict(self):
        return {
            'id': self.id,
            'nr': self.nr,
            'date_cde': self.date_cde.isoformat() if self.date_cde else None,
            'entite': self.entite,
            'demandeur': self.demandeur,
            'service_demandeur': self.service_demandeur,
            'acheteur': self.acheteur,
            'fournisseur': self.fournisseur.nom if self.fournisseur else None,
            'affaire': self.affaire,
            'bon_commande': self.bon_commande,
            'date_livraison': self.date_livraison.isoformat() if self.date_livraison else None,
            'date_reception': self.date_reception.isoformat() if self.date_reception else None,
            'bon_livraison': self.bon_livraison,
            'facture': self.facture,
            'montant': self.montant,
            'avance': self.avance,
            'solde': self.solde,
            'prix_reference_marche': self.prix_reference_marche,
            'commande_conforme': self.commande_conforme,
            'rupture_fournisseur': self.rupture_fournisseur,
            'note_fournisseur': self.note_fournisseur,
            'note_service': self.note_service,
            'statut': self.statut,
            'statut_avancement': self.get_statut_avancement(),
            'niveau_processus': self.get_niveau_processus(),
            'date_paiement': self.date_paiement.isoformat() if self.date_paiement else None,
            'commentaire': self.commentaire,
            'delai': self.get_delai(),
            'ecart_livraison': self.get_ecart_livraison(),
            'ecart_prix_marche_pct': self.get_ecart_prix_marche_pct(),
        }
    
    def __repr__(self):
        return f'<Commande {self.nr} - {self.entite}>'

class LogAction(db.Model):
    __tablename__ = 'logs'
    
    id = db.Column(db.Integer, primary_key=True)
    utilisateur_id = db.Column(db.Integer, db.ForeignKey('utilisateurs.id'))
    action = db.Column(db.String(50), index=True)
    table = db.Column(db.String(50), index=True)
    record_id = db.Column(db.Integer, index=True)
    details = db.Column(db.Text)
    ip_address = db.Column(db.String(45))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    utilisateur = db.relationship('Utilisateur', backref='logs')
    
    def __repr__(self):
        return f'<Log {self.action} - {self.created_at}>'


class Produit(db.Model):
    """Modèle pour les produits/équipements"""
    __tablename__ = 'produits'

    TYPE_MATIERE_PREMIERE = 'MATIERE_PREMIERE'
    TYPE_EN_COURS = 'EN_COURS'
    TYPE_PRODUIT_FINI = 'PRODUIT_FINI'
    TYPE_MRO = 'MRO'

    REAPPRO_CALENDAIRE = 'CALENDAIRE'
    REAPPRO_POINT_COMMANDE = 'POINT_COMMANDE'
    REAPPRO_KANBAN = 'KANBAN'

    VALORISATION_FIFO = 'FIFO'
    VALORISATION_LIFO = 'LIFO'
    VALORISATION_CUMP = 'CUMP'
    
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(200), nullable=False, index=True)
    code = db.Column(db.String(50), unique=True)
    description = db.Column(db.Text)
    famille = db.Column(db.String(150), index=True)
    categorie = db.Column(db.String(100), index=True)
    type_stock = db.Column(db.String(30), default=TYPE_PRODUIT_FINI, index=True)
    methode_reappro = db.Column(db.String(30), default=REAPPRO_POINT_COMMANDE, index=True)
    methode_valorisation = db.Column(db.String(20), default=VALORISATION_CUMP)
    prix_unitaire = db.Column(db.Float, default=0)
    unite = db.Column(db.String(20))  # pièce, kg, mètre, etc.
    stock_actuel = db.Column(db.Float, default=0, index=True)
    stock_minimum = db.Column(db.Float, default=0)
    stock_securite = db.Column(db.Float, default=0)
    delai_approvisionnement_jours = db.Column(db.Float, default=0)
    periodicite_reappro_jours = db.Column(db.Float, default=0)
    consommation_moyenne_journaliere = db.Column(db.Float, default=0)
    cout_passation_commande = db.Column(db.Float, default=0)
    taux_possession_annuel = db.Column(db.Float, default=25)
    actif = db.Column(db.Boolean, default=True, index=True)
    
    # Relations
    commandes_produits = db.relationship('CommandeProduit', backref='produit', lazy=True)
    lignes_vente = db.relationship('LigneVente', backref='produit', lazy=True)
    mouvements_stock = db.relationship('MouvementStock', backref='produit', lazy=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)

    @property
    def valeur_stock(self):
        return (self.stock_actuel or 0) * (self.prix_unitaire or 0)

    @property
    def demande_annuelle_estimee(self):
        return max(float(self.consommation_moyenne_journaliere or 0), 0) * 365

    @property
    def point_commande(self):
        return (
            max(float(self.consommation_moyenne_journaliere or 0), 0)
            * max(float(self.delai_approvisionnement_jours or 0), 0)
        ) + max(float(self.stock_securite or 0), 0)

    @property
    def couverture_stock_jours(self):
        consommation = float(self.consommation_moyenne_journaliere or 0)
        if consommation <= 0:
            return None
        return (self.stock_actuel or 0) / consommation

    @property
    def quantite_economique_commande(self):
        demande_annuelle = self.demande_annuelle_estimee
        cout_passation = float(self.cout_passation_commande or 0)
        cout_unitaire = float(self.prix_unitaire or 0)
        taux_possession = float(self.taux_possession_annuel or 0) / 100

        if demande_annuelle <= 0 or cout_passation <= 0 or cout_unitaire <= 0 or taux_possession <= 0:
            return None

        return math.sqrt((2 * demande_annuelle * cout_passation) / (cout_unitaire * taux_possession))

    def est_stock_faible(self):
        return self.actif and (self.stock_actuel or 0) <= (self.stock_minimum or 0)

    def est_en_rupture(self):
        return self.actif and (self.stock_actuel or 0) <= 0

    def doit_etre_reapprovisionne(self):
        if not self.actif:
            return False
        if self.methode_reappro == self.REAPPRO_KANBAN:
            return (self.stock_actuel or 0) <= max(float(self.stock_securite or 0), float(self.stock_minimum or 0))
        if self.methode_reappro == self.REAPPRO_CALENDAIRE and (self.periodicite_reappro_jours or 0) > 0:
            couverture = self.couverture_stock_jours
            if couverture is None:
                return (self.stock_actuel or 0) <= max(float(self.stock_minimum or 0), float(self.stock_securite or 0))
            return couverture <= float(self.periodicite_reappro_jours or 0)
        seuil = self.point_commande
        if seuil > 0:
            return (self.stock_actuel or 0) <= seuil
        return self.est_stock_faible()

    def get_quantite_reappro_recommandee(self):
        if not self.doit_etre_reapprovisionne():
            return None

        qec = self.quantite_economique_commande
        if qec is not None:
            return qec

        cible = max(float(self.stock_minimum or 0), float(self.stock_securite or 0))
        if self.methode_reappro == self.REAPPRO_CALENDAIRE and (self.periodicite_reappro_jours or 0) > 0:
            cible += max(float(self.consommation_moyenne_journaliere or 0), 0) * float(self.periodicite_reappro_jours or 0)
        elif self.point_commande > 0:
            cible = max(cible, self.point_commande)

        recommandee = cible - float(self.stock_actuel or 0)
        return recommandee if recommandee > 0 else None

    def get_type_stock_label(self):
        labels = {
            self.TYPE_MATIERE_PREMIERE: 'Matière première',
            self.TYPE_EN_COURS: 'En-cours',
            self.TYPE_PRODUIT_FINI: 'Produit fini',
            self.TYPE_MRO: 'Maintenance / MRO',
        }
        return labels.get(self.type_stock, 'Produit fini')

    def get_methode_reappro_label(self):
        labels = {
            self.REAPPRO_CALENDAIRE: 'Calendaire',
            self.REAPPRO_POINT_COMMANDE: 'Point de commande',
            self.REAPPRO_KANBAN: 'Kanban',
        }
        return labels.get(self.methode_reappro, 'Point de commande')

    def get_methode_valorisation_label(self):
        labels = {
            self.VALORISATION_FIFO: 'FIFO',
            self.VALORISATION_LIFO: 'LIFO',
            self.VALORISATION_CUMP: 'CUMP',
        }
        return labels.get(self.methode_valorisation, 'CUMP')

    def get_etat_stock(self):
        if not self.actif:
            return 'INACTIF'
        if self.est_en_rupture():
            return 'RUPTURE'
        if self.doit_etre_reapprovisionne():
            return 'A_REAPPROVISIONNER'
        if self.est_stock_faible():
            return 'STOCK_FAIBLE'
        return 'DISPONIBLE'
    
    def __repr__(self):
        return f'<Produit {self.nom}>'


class CommandeProduit(db.Model):
    """Table de liaison entre commandes et produits"""
    __tablename__ = 'commande_produits'
    
    id = db.Column(db.Integer, primary_key=True)
    commande_id = db.Column(db.Integer, db.ForeignKey('commandes.id'), index=True)
    produit_id = db.Column(db.Integer, db.ForeignKey('produits.id'), index=True)
    quantite = db.Column(db.Float, default=1)
    prix_unitaire = db.Column(db.Float, default=0)
    montant_total = db.Column(db.Float, default=0)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    commande = db.relationship('Commande', backref='produits_lies')
    
    def calculer_montant(self):
        self.montant_total = self.quantite * self.prix_unitaire
        return self.montant_total


class Vente(db.Model):
    """En-tête de vente client."""
    __tablename__ = 'ventes'

    STATUT_PAYEE = 'PAYÉE'
    STATUT_PARTIELLE = 'PARTIELLE'
    STATUT_EN_ATTENTE = 'EN ATTENTE'
    CANAL_OFFLINE = 'OFFLINE'
    CANAL_ONLINE = 'ONLINE'
    TYPE_CLIENT_PARTICULIER = 'PARTICULIER'
    TYPE_CLIENT_ENTREPRISE = 'ENTREPRISE'
    TYPE_CLIENT_REVENDEUR = 'REVENDEUR'

    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(50), unique=True, nullable=False)
    client_nom = db.Column(db.String(200), nullable=False, index=True)
    client_telephone = db.Column(db.String(50))
    date_vente = db.Column(db.Date, default=date.today, nullable=False, index=True)
    canal_vente = db.Column(db.String(20), default=CANAL_OFFLINE, index=True)
    region = db.Column(db.String(100), index=True)
    type_client = db.Column(db.String(30), default=TYPE_CLIENT_PARTICULIER, index=True)
    montant_total = db.Column(db.Float, default=0)
    montant_paye = db.Column(db.Float, default=0)
    solde = db.Column(db.Float, default=0)
    retour_effectue = db.Column(db.Boolean, default=False, index=True)
    montant_retour = db.Column(db.Float, default=0)
    statut_paiement = db.Column(db.String(20), default=STATUT_EN_ATTENTE, index=True)
    commentaire = db.Column(db.Text)
    created_by = db.Column(db.Integer, db.ForeignKey('utilisateurs.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)

    lignes = db.relationship('LigneVente', backref='vente', lazy=True, cascade='all, delete-orphan')
    utilisateur = db.relationship('Utilisateur', backref='ventes')
    mouvements_stock = db.relationship('MouvementStock', backref='vente', lazy=True)

    @property
    def montant_net(self):
        return (self.montant_total or 0) - (self.montant_retour or 0)

    def recalculer_totaux(self):
        self.montant_total = sum((ligne.montant_total or 0) for ligne in self.lignes)
        self.solde = (self.montant_total or 0) - (self.montant_paye or 0)

        if self.solde <= 0 and self.montant_total > 0:
            self.statut_paiement = self.STATUT_PAYEE
        elif self.montant_paye > 0:
            self.statut_paiement = self.STATUT_PARTIELLE
        else:
            self.statut_paiement = self.STATUT_EN_ATTENTE

    def __repr__(self):
        return f'<Vente {self.reference}>'


class LigneVente(db.Model):
    """Lignes des ventes."""
    __tablename__ = 'lignes_ventes'

    id = db.Column(db.Integer, primary_key=True)
    vente_id = db.Column(db.Integer, db.ForeignKey('ventes.id'), nullable=False, index=True)
    produit_id = db.Column(db.Integer, db.ForeignKey('produits.id'), nullable=False, index=True)
    quantite = db.Column(db.Float, default=1, nullable=False)
    prix_unitaire = db.Column(db.Float, default=0, nullable=False)
    montant_total = db.Column(db.Float, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def calculer_montant(self):
        self.montant_total = (self.quantite or 0) * (self.prix_unitaire or 0)
        return self.montant_total


class MouvementStock(db.Model):
    """Historique des mouvements de stock."""
    __tablename__ = 'mouvements_stock'

    TYPE_ENTREE = 'ENTREE'
    TYPE_SORTIE = 'SORTIE'
    TYPE_AJUSTEMENT = 'AJUSTEMENT'

    id = db.Column(db.Integer, primary_key=True)
    produit_id = db.Column(db.Integer, db.ForeignKey('produits.id'), nullable=False, index=True)
    utilisateur_id = db.Column(db.Integer, db.ForeignKey('utilisateurs.id'), index=True)
    vente_id = db.Column(db.Integer, db.ForeignKey('ventes.id'), index=True)
    type_mouvement = db.Column(db.String(20), nullable=False, index=True)
    variation = db.Column(db.Float, default=0, nullable=False)
    stock_avant = db.Column(db.Float, default=0, nullable=False)
    stock_apres = db.Column(db.Float, default=0, nullable=False)
    motif = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    utilisateur = db.relationship('Utilisateur', backref='mouvements_stock')

    def __repr__(self):
        return f'<MouvementStock {self.type_mouvement} {self.variation}>'


class PerformanceAcheteur(db.Model):
    """Table d'agrégation des performances des acheteurs"""
    __tablename__ = 'performances_acheteurs'
    
    id = db.Column(db.Integer, primary_key=True)
    acheteur = db.Column(db.String(50), nullable=False)
    periode = db.Column(db.String(10))  # mois, trimestre, annee
    total_commandes = db.Column(db.Integer, default=0)
    total_montant = db.Column(db.Float, default=0)
    nombre_fournisseurs = db.Column(db.Integer, default=0)
    delai_moyen_livraison = db.Column(db.Float, default=0)
    taux_retard = db.Column(db.Float, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PerformanceFournisseur(db.Model):
    """Table d'agrégation des performances des fournisseurs"""
    __tablename__ = 'performances_fournisseurs'
    
    id = db.Column(db.Integer, primary_key=True)
    fournisseur_id = db.Column(db.Integer, db.ForeignKey('fournisseurs.id'))
    periode = db.Column(db.String(10))
    total_commandes = db.Column(db.Integer, default=0)
    total_montant = db.Column(db.Float, default=0)
    delai_moyen_livraison = db.Column(db.Float, default=0)
    taux_retard = db.Column(db.Float, default=0)
    montant_a_payer = db.Column(db.Float, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    fournisseur = db.relationship('Fournisseur', backref='performances')


class DashboardSubscription(db.Model):
    """Abonnements aux rapports email du dashboard."""
    __tablename__ = 'dashboard_subscriptions'

    FREQUENCY_DAILY = 'DAILY'
    FREQUENCY_WEEKLY = 'WEEKLY'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False, index=True)
    frequency = db.Column(db.String(20), default=FREQUENCY_DAILY, index=True)
    include_pdf = db.Column(db.Boolean, default=True)
    include_excel = db.Column(db.Boolean, default=True)
    actif = db.Column(db.Boolean, default=True, index=True)
    last_sent_at = db.Column(db.DateTime, index=True)
    next_send_at = db.Column(db.DateTime, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)

    def __repr__(self):
        return f'<DashboardSubscription {self.email} {self.frequency}>'
