#!/usr/bin/env python
"""Script d'import du fichier Excel vers l'application"""

import pandas as pd
from datetime import datetime
from app import app, db
from models import Commande, Fournisseur

def importer_commandes(fichier_excel):
    """Importe les commandes depuis un fichier Excel"""
    
    # Lire le fichier Excel
    df = pd.read_excel(fichier_excel, sheet_name='Commande')
    
    # Nettoyer les données
    df = df.fillna('')
    
    compteur = 0
    erreurs = 0
    
    with app.app_context():
        for idx, row in df.iterrows():
            # Ignorer les lignes vides ou d'en-tête
            if idx < 4:  # Les 4 premières lignes sont des en-têtes/formules
                continue
                
            # Récupérer les données
            nr = row.get('Nr.', '')
            if pd.isna(nr):
                nr = 0
            else:
                nr = int(nr) if str(nr).isdigit() else 0
            
            date_cde = row.get('Date CDE')
            if pd.notna(date_cde) and date_cde:
                try:
                    date_cde = pd.to_datetime(date_cde).date()
                except:
                    date_cde = None
            else:
                date_cde = None
            
            entite = row.get('Entité')
            if pd.isna(entite):
                entite = None
            else:
                entite = str(entite).strip()
            
            demandeur = row.get('Demandeur')
            if pd.isna(demandeur):
                demandeur = None
            else:
                demandeur = str(demandeur).strip()
            
            service_demandeur = row.get('Service Demandeur')
            if pd.isna(service_demandeur):
                service_demandeur = None
            else:
                service_demandeur = str(service_demandeur).strip()
            
            acheteur = row.get('Acheteur')
            if pd.isna(acheteur):
                acheteur = None
            else:
                acheteur = str(acheteur).strip()
            
            # Gestion du fournisseur
            nom_fournisseur = row.get('Fournisseur')
            fournisseur_id = None
            if pd.notna(nom_fournisseur) and nom_fournisseur:
                nom_fournisseur = str(nom_fournisseur).strip()
                fournisseur = Fournisseur.query.filter_by(nom=nom_fournisseur).first()
                if not fournisseur:
                    # Créer le fournisseur s'il n'existe pas
                    fournisseur = Fournisseur(
                        nom=nom_fournisseur,
                        pays='Cameroun' if 'CAM' in str(nom_fournisseur).upper() else 'Inconnu',
                        statut='Actif'
                    )
                    db.session.add(fournisseur)
                    db.session.commit()
                    print(f"➕ Fournisseur créé: {nom_fournisseur}")
                fournisseur_id = fournisseur.id
            
            affaire = row.get('Affaire/Commande')
            if pd.isna(affaire):
                affaire = None
            else:
                affaire = str(affaire).strip()
            
            bon_commande = row.get('N° Bon commande')
            if pd.isna(bon_commande):
                bon_commande = None
            else:
                bon_commande = str(bon_commande).strip()
            
            date_livraison = row.get('Date Livraison')
            if pd.notna(date_livraison) and date_livraison:
                try:
                    date_livraison = pd.to_datetime(date_livraison).date()
                except:
                    date_livraison = None
            else:
                date_livraison = None
            
            bon_livraison = row.get('N° Bon Livraison')
            if pd.isna(bon_livraison):
                bon_livraison = None
            else:
                bon_livraison = str(bon_livraison).strip()
            
            facture = row.get('Facture')
            if pd.isna(facture):
                facture = None
            else:
                facture = str(facture).strip()
            
            montant = row.get('Montant')
            if pd.isna(montant) or montant == '':
                montant = 0
            else:
                # Nettoyer les montants avec espaces et virgules
                if isinstance(montant, str):
                    montant = montant.replace(' ', '').replace(',', '')
                try:
                    montant = float(montant)
                except:
                    montant = 0
            
            avance = row.get('Avance')
            if pd.isna(avance) or avance == '':
                avance = 0
            else:
                if isinstance(avance, str):
                    avance = avance.replace(' ', '').replace(',', '')
                try:
                    avance = float(avance)
                except:
                    avance = 0
            
            commentaire = row.get('Commentaire')
            if pd.isna(commentaire):
                commentaire = None
            else:
                commentaire = str(commentaire).strip()
            
            # Vérifier si la commande existe déjà (par bon de commande ou facture)
            if bon_commande and bon_commande != '':
                existe = Commande.query.filter_by(bon_commande=bon_commande).first()
            elif facture and facture != '':
                existe = Commande.query.filter_by(facture=facture).first()
            else:
                existe = None
            
            if existe:
                print(f"⏭️ Commande {bon_commande} existe déjà, ignorée")
                continue
            
            # Créer la commande
            commande = Commande(
                nr=nr if nr != 0 else None,
                date_cde=date_cde,
                entite=entite,
                demandeur=demandeur,
                service_demandeur=service_demandeur,
                acheteur=acheteur,
                fournisseur_id=fournisseur_id,
                affaire=affaire,
                bon_commande=bon_commande,
                date_livraison=date_livraison,
                bon_livraison=bon_livraison,
                facture=facture,
                montant=montant,
                avance=avance,
                commentaire=commentaire
            )
            commande.calculer_solde()
            
            db.session.add(commande)
            compteur += 1
            
            if compteur % 50 == 0:
                db.session.commit()
                print(f"📦 {compteur} commandes importées...")
        
        # Commit final
        db.session.commit()
        
        print(f"\n✅ Import terminé !")
        print(f"   - {compteur} commandes importées")
        print(f"   - {erreurs} erreurs")
        
        return compteur

if __name__ == '__main__':
    import sys
    
    # Chemin vers votre fichier Excel
    if len(sys.argv) > 1:
        fichier = sys.argv[1]
    else:
        fichier = input("Chemin du fichier Excel à importer: ")
    
    print(f"📁 Import du fichier: {fichier}")
    importer_commandes(fichier)
