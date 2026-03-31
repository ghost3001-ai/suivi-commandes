#!/usr/bin/env python
"""Script simplifié d'import du fichier Excel"""

import sys
import os
import pandas as pd
import re

# Ajouter le dossier parent au path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app, db
from models import Commande, Fournisseur

def nettoyer_montant(valeur):
    """Nettoie une valeur de montant"""
    if pd.isna(valeur) or valeur == '':
        return 0
    if isinstance(valeur, (int, float)):
        return float(valeur)
    valeur_str = str(valeur).strip()
    valeur_str = re.sub(r'[^\d.,-]', '', valeur_str)
    valeur_str = valeur_str.replace(' ', '').replace(',', '')
    try:
        return float(valeur_str)
    except:
        return 0

def importer_commandes(fichier_excel):
    """Importe les commandes depuis un fichier Excel"""
    
    print(f"📁 Lecture du fichier: {fichier_excel}")
    
    if not os.path.exists(fichier_excel):
        print(f"❌ Fichier non trouvé: {fichier_excel}")
        return
    
    try:
        # Lire le fichier Excel
        df = pd.read_excel(fichier_excel, sheet_name='Commande')
        print(f"📊 {len(df)} lignes trouvées")
        print(f"📋 Colonnes: {list(df.columns)}")
    except Exception as e:
        print(f"❌ Erreur de lecture: {e}")
        return
    
    compteur = 0
    erreurs = 0
    
    with app.app_context():
        for idx, row in df.iterrows():
            # Ignorer les lignes vides
            fournisseur_val = row.get('Fournisseur')
            if pd.isna(fournisseur_val) or str(fournisseur_val).strip() == '':
                continue
            
            # Ignorer les lignes d'en-tête (lignes 0-4 contiennent des formules)
            if idx < 4:
                continue
            
            try:
                # Récupérer les données
                nr = row.get('Nr.', 0)
                if pd.notna(nr) and str(nr).isdigit():
                    nr = int(nr)
                else:
                    nr = None
                
                # Date commande
                date_cde = row.get('Date CDE')
                if pd.notna(date_cde):
                    try:
                        date_cde = pd.to_datetime(date_cde).date()
                    except:
                        date_cde = None
                else:
                    date_cde = None
                
                entite = str(row.get('Entité')).strip() if pd.notna(row.get('Entité')) else None
                demandeur = str(row.get('Demandeur')).strip() if pd.notna(row.get('Demandeur')) else None
                service_demandeur = str(row.get('Service Demandeur')).strip() if pd.notna(row.get('Service Demandeur')) else None
                acheteur = str(row.get('Acheteur')).strip() if pd.notna(row.get('Acheteur')) else None
                
                # Gestion du fournisseur
                nom_fournisseur = str(row.get('Fournisseur')).strip()
                fournisseur = Fournisseur.query.filter_by(nom=nom_fournisseur).first()
                if not fournisseur:
                    pays = 'Cameroun'
                    if 'CHINE' in nom_fournisseur.upper() or 'FOSHAN' in nom_fournisseur.upper() or 'WENZHOU' in nom_fournisseur.upper():
                        pays = 'Chine'
                    elif 'FRANCE' in nom_fournisseur.upper():
                        pays = 'France'
                    
                    fournisseur = Fournisseur(
                        nom=nom_fournisseur,
                        pays=pays,
                        statut='Actif'
                    )
                    db.session.add(fournisseur)
                    db.session.commit()
                    print(f"➕ Fournisseur créé: {nom_fournisseur}")
                
                affaire = str(row.get('Affaire/Commande')).strip() if pd.notna(row.get('Affaire/Commande')) else None
                bon_commande = str(row.get('N° Bon commande')).strip() if pd.notna(row.get('N° Bon commande')) else None
                
                date_livraison = row.get('Date Livraison')
                if pd.notna(date_livraison):
                    try:
                        date_livraison = pd.to_datetime(date_livraison).date()
                    except:
                        date_livraison = None
                else:
                    date_livraison = None
                
                bon_livraison = str(row.get('N° Bon Livraison')).strip() if pd.notna(row.get('N° Bon Livraison')) else None
                facture = str(row.get('Facture')).strip() if pd.notna(row.get('Facture')) else None
                montant = nettoyer_montant(row.get('Montant'))
                avance = nettoyer_montant(row.get('Avance'))
                commentaire = str(row.get('Commentaire')).strip() if pd.notna(row.get('Commentaire')) else None
                
                # Vérifier si la commande existe déjà
                existe = None
                if bon_commande and bon_commande != '':
                    existe = Commande.query.filter_by(bon_commande=bon_commande).first()
                if not existe and facture and facture != '':
                    existe = Commande.query.filter_by(facture=facture).first()
                
                if existe:
                    continue
                
                # Créer la commande
                commande = Commande(
                    nr=nr,
                    date_cde=date_cde,
                    entite=entite,
                    demandeur=demandeur,
                    service_demandeur=service_demandeur,
                    acheteur=acheteur,
                    fournisseur_id=fournisseur.id,
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
                    
            except Exception as e:
                erreurs += 1
                print(f"❌ Erreur ligne {idx}: {e}")
                continue
        
        # Commit final
        db.session.commit()
        
        print(f"\n✅ Import terminé !")
        print(f"   - {compteur} commandes importées")
        print(f"   - {erreurs} erreurs")
        
        return compteur

if __name__ == '__main__':
    # Trouver le fichier Excel
    fichier = "Projet Suivi Commande ASS.xlsx"
    
    if not os.path.exists(fichier):
        print(f"📂 Recherche du fichier...")
        # Chercher dans le dossier parent
        parent_fichier = f"../{fichier}"
        if os.path.exists(parent_fichier):
            fichier = parent_fichier
            print(f"✅ Fichier trouvé: {fichier}")
        else:
            print(f"❌ Fichier non trouvé: {fichier}")
            print("Veuillez placer le fichier dans le dossier actuel")
            sys.exit(1)
    
    importer_commandes(fichier)
