#!/usr/bin/env python
"""Import adapté à la structure du fichier Excel"""

import sqlite3
import pandas as pd
import re
import os
from datetime import datetime

DB_PATH = 'instance/commandes.db'

def nettoyer_montant(valeur):
    """Nettoie une valeur de montant"""
    if pd.isna(valeur) or valeur == '':
        return 0
    if isinstance(valeur, (int, float)):
        return float(valeur)
    valeur_str = str(valeur).strip()
    # Enlever les espaces et remplacer la virgule par point
    valeur_str = valeur_str.replace(' ', '').replace(',', '.')
    # Garder seulement les chiffres et le point
    valeur_str = re.sub(r'[^\d.-]', '', valeur_str)
    try:
        return float(valeur_str)
    except:
        return 0

def importer_commandes(fichier_excel):
    print(f"📁 Lecture: {fichier_excel}")
    
    # Lire le fichier en commençant à la ligne 4 (index 3 pour les en-têtes)
    # header=3 signifie que la ligne 4 (index 3) contient les noms des colonnes
    df = pd.read_excel(fichier_excel, sheet_name='Commande', header=3)
    
    # Supprimer les colonnes vides
    df = df.dropna(axis=1, how='all')
    
    print(f"📊 {len(df)} lignes de données trouvées")
    print(f"📋 Colonnes: {list(df.columns)}")
    
    # Nettoyer les noms de colonnes
    df.columns = [str(col).strip() for col in df.columns]
    
    # Filtrer les lignes vides (sans numéro)
    df = df[df['# Nr.'].notna()]
    
    print(f"📊 {len(df)} lignes après filtrage")
    
    # Créer la base
    os.makedirs('instance', exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Créer les tables si nécessaire
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fournisseurs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom VARCHAR(200) UNIQUE,
            pays VARCHAR(100),
            statut VARCHAR(50) DEFAULT 'Actif'
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS commandes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nr INTEGER,
            date_cde DATE,
            entite VARCHAR(50),
            demandeur VARCHAR(100),
            service_demandeur VARCHAR(100),
            acheteur VARCHAR(50),
            fournisseur_id INTEGER,
            affaire TEXT,
            bon_commande VARCHAR(100),
            date_livraison DATE,
            bon_livraison VARCHAR(100),
            facture VARCHAR(100),
            montant REAL DEFAULT 0,
            avance REAL DEFAULT 0,
            solde REAL DEFAULT 0,
            statut VARCHAR(20),
            commentaire TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (fournisseur_id) REFERENCES fournisseurs(id)
        )
    ''')
    
    conn.commit()
    
    compteur = 0
    erreurs = 0
    
    for idx, row in df.iterrows():
        try:
            # Récupérer les données
            nr = row.get('# Nr.')
            if pd.notna(nr):
                nr = int(nr)
            else:
                continue
            
            # Date commande
            date_cde = row.get('Date CDE')
            if pd.notna(date_cde):
                if isinstance(date_cde, datetime):
                    date_cde = date_cde.strftime('%Y-%m-%d')
                else:
                    try:
                        date_cde = pd.to_datetime(date_cde).strftime('%Y-%m-%d')
                    except:
                        date_cde = None
            else:
                date_cde = None
            
            entite = row.get('Entité')
            entite = str(entite).strip() if pd.notna(entite) else None
            
            demandeur = row.get('Demandeur')
            demandeur = str(demandeur).strip() if pd.notna(demandeur) else None
            
            service_demandeur = row.get('Service Demandeur')
            service_demandeur = str(service_demandeur).strip() if pd.notna(service_demandeur) else None
            
            acheteur = row.get('Acheteur')
            acheteur = str(acheteur).strip() if pd.notna(acheteur) else None
            
            # Gestion du fournisseur
            nom_fournisseur = row.get('Fournisseur')
            fournisseur_id = None
            if pd.notna(nom_fournisseur):
                nom_fournisseur = str(nom_fournisseur).strip()
                if nom_fournisseur:
                    cursor.execute("SELECT id FROM fournisseurs WHERE nom = ?", (nom_fournisseur,))
                    result = cursor.fetchone()
                    if result:
                        fournisseur_id = result[0]
                    else:
                        # Déterminer le pays approximatif
                        pays = 'Cameroun'
                        if any(x in nom_fournisseur.upper() for x in ['CHINE', 'FOSHAN', 'WENZHOU', 'SHENZHEN', 'QINGDAO']):
                            pays = 'Chine'
                        elif 'FRANCE' in nom_fournisseur.upper() or 'PARIS' in nom_fournisseur.upper():
                            pays = 'France'
                        elif 'DUBAI' in nom_fournisseur.upper() or 'UAE' in nom_fournisseur.upper():
                            pays = 'Dubai'
                        
                        cursor.execute("INSERT INTO fournisseurs (nom, pays) VALUES (?, ?)", (nom_fournisseur, pays))
                        fournisseur_id = cursor.lastrowid
                        print(f"➕ Fournisseur créé: {nom_fournisseur} ({pays})")
            
            affaire = row.get('Affaire/Commande')
            affaire = str(affaire).strip() if pd.notna(affaire) else None
            
            bon_commande = row.get('N° Bon commande')
            bon_commande = str(bon_commande).strip() if pd.notna(bon_commande) else None
            
            # Date livraison
            date_livraison = row.get('Date Livraison')
            if pd.notna(date_livraison):
                if isinstance(date_livraison, datetime):
                    date_livraison = date_livraison.strftime('%Y-%m-%d')
                else:
                    try:
                        date_livraison = pd.to_datetime(date_livraison).strftime('%Y-%m-%d')
                    except:
                        date_livraison = None
            else:
                date_livraison = None
            
            bon_livraison = row.get('N° Bon Livraison')
            bon_livraison = str(bon_livraison).strip() if pd.notna(bon_livraison) else None
            
            facture = row.get('Facture')
            facture = str(facture).strip() if pd.notna(facture) else None
            
            montant = nettoyer_montant(row.get('Montant'))
            avance = nettoyer_montant(row.get('Avance'))
            solde = nettoyer_montant(row.get('Solde'))
            
            # Si solde est vide, le calculer
            if solde == 0 and montant > 0:
                solde = montant - avance
            
            statut = "PAYER" if solde <= 0 else "A PAYER"
            
            commentaire = row.get('Commentaire')
            commentaire = str(commentaire).strip() if pd.notna(commentaire) else None
            
            # Vérifier si la commande existe déjà
            if bon_commande:
                cursor.execute("SELECT id FROM commandes WHERE bon_commande = ?", (bon_commande,))
                if cursor.fetchone():
                    continue
            
            # Insérer
            cursor.execute('''
                INSERT INTO commandes (
                    nr, date_cde, entite, demandeur, service_demandeur,
                    acheteur, fournisseur_id, affaire, bon_commande,
                    date_livraison, bon_livraison, facture, montant,
                    avance, solde, statut, commentaire
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                nr, date_cde, entite, demandeur, service_demandeur,
                acheteur, fournisseur_id, affaire, bon_commande,
                date_livraison, bon_livraison, facture, montant,
                avance, solde, statut, commentaire
            ))
            
            compteur += 1
            if compteur % 50 == 0:
                conn.commit()
                print(f"📦 {compteur} commandes importées...")
                
        except Exception as e:
            erreurs += 1
            print(f"❌ Erreur ligne {idx}: {e}")
            continue
    
    conn.commit()
    conn.close()
    
    print(f"\n✅ IMPORT TERMINÉ !")
    print(f"   📦 {compteur} commandes importées")
    print(f"   ⚠️ {erreurs} erreurs")
    
    # Vérification
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM commandes")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM fournisseurs")
    nb_fourn = cursor.fetchone()[0]
    conn.close()
    
    print(f"\n📊 Bilan base de données:")
    print(f"   - {total} commandes")
    print(f"   - {nb_fourn} fournisseurs")

if __name__ == '__main__':
    import sys
    
    # Chercher le fichier
    fichier = "Projet Suivi Commande ASS.xlsx"
    if not os.path.exists(fichier):
        fichier = "../Projet Suivi Commande ASS.xlsx"
    
    if os.path.exists(fichier):
        print(f"✅ Fichier trouvé: {fichier}")
        importer_commandes(fichier)
    else:
        print(f"❌ Fichier non trouvé: Projet Suivi Commande ASS.xlsx")
        print("Veuillez placer le fichier dans le dossier actuel")
