from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, date
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

class Utilisateur(UserMixin, db.Model):
    __tablename__ = 'utilisateurs'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='spectateur')
    actif = db.Column(db.Boolean, default=True)
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
    nom = db.Column(db.String(200), nullable=False)
    statut_juridique = db.Column(db.String(100))
    pays = db.Column(db.String(100))
    ville = db.Column(db.String(100))
    dirigeant = db.Column(db.String(100))
    telephone1 = db.Column(db.String(50))
    telephone2 = db.Column(db.String(50))
    email1 = db.Column(db.String(100))
    email2 = db.Column(db.String(100))
    categorie = db.Column(db.String(100))
    statut = db.Column(db.String(50), default='Actif')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    commandes = db.relationship('Commande', backref='fournisseur', lazy=True)
    
    def __repr__(self):
        return f'<Fournisseur {self.nom}>'

class Commande(db.Model):
    __tablename__ = 'commandes'
    STATUT_PAYE = 'PAYÉ'
    STATUT_A_PAYER = 'A PAYER'
    
    id = db.Column(db.Integer, primary_key=True)
    nr = db.Column(db.Integer)
    date_cde = db.Column(db.Date)
    entite = db.Column(db.String(50))
    demandeur = db.Column(db.String(100))
    service_demandeur = db.Column(db.String(100))
    acheteur = db.Column(db.String(50))
    fournisseur_id = db.Column(db.Integer, db.ForeignKey('fournisseurs.id'))
    affaire = db.Column(db.Text)
    bon_commande = db.Column(db.String(100))
    date_livraison = db.Column(db.Date)
    bon_livraison = db.Column(db.String(100))
    facture = db.Column(db.String(100))
    montant = db.Column(db.Float, default=0)
    avance = db.Column(db.Float, default=0)
    solde = db.Column(db.Float, default=0)
    statut = db.Column(db.String(20))
    date_paiement = db.Column(db.Date)
    commentaire = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def calculer_solde(self):
        """Calcule le solde et met à jour le statut"""
        self.solde = self.montant - self.avance
        self.statut = self.STATUT_PAYE if self.solde <= 0 else self.STATUT_A_PAYER

    def est_payee(self):
        return self.statut == self.STATUT_PAYE
    
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
            'bon_livraison': self.bon_livraison,
            'facture': self.facture,
            'montant': self.montant,
            'avance': self.avance,
            'solde': self.solde,
            'statut': self.statut,
            'date_paiement': self.date_paiement.isoformat() if self.date_paiement else None,
            'commentaire': self.commentaire,
            'delai': self.get_delai()
        }
    
    def __repr__(self):
        return f'<Commande {self.nr} - {self.entite}>'

class LogAction(db.Model):
    __tablename__ = 'logs'
    
    id = db.Column(db.Integer, primary_key=True)
    utilisateur_id = db.Column(db.Integer, db.ForeignKey('utilisateurs.id'))
    action = db.Column(db.String(50))
    table = db.Column(db.String(50))
    record_id = db.Column(db.Integer)
    details = db.Column(db.Text)
    ip_address = db.Column(db.String(45))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    utilisateur = db.relationship('Utilisateur', backref='logs')
    
    def __repr__(self):
        return f'<Log {self.action} - {self.created_at}>'


class Produit(db.Model):
    """Modèle pour les produits/équipements"""
    __tablename__ = 'produits'
    
    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(200), nullable=False)
    code = db.Column(db.String(50), unique=True)
    description = db.Column(db.Text)
    categorie = db.Column(db.String(100))
    sous_categorie = db.Column(db.String(100))
    prix_unitaire = db.Column(db.Float, default=0)
    unite = db.Column(db.String(20))  # pièce, kg, mètre, etc.
    
    # Relations
    commandes_produits = db.relationship('CommandeProduit', backref='produit', lazy=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f'<Produit {self.nom}>'


class CommandeProduit(db.Model):
    """Table de liaison entre commandes et produits"""
    __tablename__ = 'commande_produits'
    
    id = db.Column(db.Integer, primary_key=True)
    commande_id = db.Column(db.Integer, db.ForeignKey('commandes.id'))
    produit_id = db.Column(db.Integer, db.ForeignKey('produits.id'))
    quantite = db.Column(db.Float, default=1)
    prix_unitaire = db.Column(db.Float, default=0)
    montant_total = db.Column(db.Float, default=0)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    commande = db.relationship('Commande', backref='produits_lies')
    
    def calculer_montant(self):
        self.montant_total = self.quantite * self.prix_unitaire
        return self.montant_total


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
