#!/usr/bin/env python
"""Importe et enrichit les fournisseurs depuis le classeur de suivi des commandes."""

from __future__ import annotations

import argparse
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

from app import app, db
from models import Commande, Fournisseur


DEFAULT_WORKBOOK = Path(__file__).resolve().parent / 'Projet Suivi Commande ASS.xlsx'
COMMAND_SHEET = 'Commande'
SUPPLIER_REFERENCE_SHEET = 'BD FRN'

LEADING_GENERIC_TOKENS = {
    'ETS', 'STE', 'ET', 'FSSEUR', 'FOURNISSEUR', 'SUPPLIER', 'SOCIETE', 'SOCIÉTÉ',
    'GROUPE', 'GROUP', 'COMPAGNIE', 'COMPANY',
}
TRAILING_LEGAL_TOKENS = {
    'SARL', 'SA', 'LTD', 'LIMITED', 'LLC', 'CO', 'INC', 'F', 'SARLAU', 'GMBH',
}


def clean_text(value):
    """Normalise un texte issu d'Excel."""
    if pd.isna(value):
        return ''
    text = re.sub(r'\s+', ' ', str(value)).strip()
    return '' if text.lower() == 'nan' else text


def clean_phone(value):
    """Nettoie un numéro de téléphone."""
    text = clean_text(value)
    if not text:
        return None
    text = text.replace('.0', '') if re.fullmatch(r'\d+\.0', text) else text
    digits = re.sub(r'[^\d+]', '', text)
    return digits or None


def clean_email(value):
    """Nettoie un email."""
    text = clean_text(value).lower()
    return text if '@' in text else None


def normalize_name_tokens(name):
    """Tokenise un nom de fournisseur pour matching."""
    ascii_name = unicodedata.normalize('NFKD', name or '').encode('ascii', 'ignore').decode('ascii').upper()
    return re.findall(r'[A-Z0-9]+', ascii_name)


def build_supplier_match_keys(name):
    """Construit plusieurs clés de rapprochement pour un nom fournisseur."""
    tokens = normalize_name_tokens(name)
    if not tokens:
        return set()

    variants = {''.join(tokens)}

    trimmed = list(tokens)
    while trimmed and trimmed[0] in LEADING_GENERIC_TOKENS:
        trimmed.pop(0)
    if trimmed:
        variants.add(''.join(trimmed))

    trimmed_suffix = list(trimmed)
    while trimmed_suffix and trimmed_suffix[-1] in TRAILING_LEGAL_TOKENS:
        trimmed_suffix.pop()
    if trimmed_suffix:
        variants.add(''.join(trimmed_suffix))

    if len(trimmed_suffix) > 1:
        variants.add(''.join(token[0] for token in trimmed_suffix if token))

    return {variant for variant in variants if len(variant) >= 2}


def supplier_names_match(left, right):
    """Indique si deux noms semblent représenter le même fournisseur."""
    left_keys = build_supplier_match_keys(left)
    right_keys = build_supplier_match_keys(right)
    if not left_keys or not right_keys:
        return False

    if left_keys & right_keys:
        return True

    for left_key in left_keys:
        for right_key in right_keys:
            shorter, longer = sorted((left_key, right_key), key=len)
            if len(shorter) >= 4 and (longer.startswith(shorter) or shorter.startswith(longer)):
                return True
            if len(shorter) >= 6 and left_key[:4] == right_key[:4]:
                if SequenceMatcher(None, left_key, right_key).ratio() >= 0.92:
                    return True
    return False


def infer_supplier_country(name):
    """Déduit un pays quand il est explicitement identifiable dans le nom."""
    upper_name = clean_text(name).upper()
    if 'CAMEROUN' in upper_name:
        return 'Cameroun'
    if any(keyword in upper_name for keyword in ('CHINE', 'FOSHAN', 'WENZHOU', 'SHENZHEN', 'QINGDAO')):
        return 'Chine'
    if 'FRANCE' in upper_name:
        return 'France'
    if any(keyword in upper_name for keyword in ('DUBAI', 'UAE', 'EMIRATES')):
        return 'Émirats Arabes Unis'
    if upper_name.endswith(' TOGO'):
        return 'Togo'
    if 'SENEGAL' in upper_name or 'SÉNÉGAL' in upper_name:
        return 'Sénégal'
    return None


def infer_supplier_city(name):
    """Déduit une ville quand elle est explicite dans le nom."""
    upper_name = clean_text(name).upper()
    if 'FOSHAN' in upper_name:
        return 'Foshan'
    if 'WENZHOU' in upper_name:
        return 'Wenzhou'
    if 'SHENZHEN' in upper_name:
        return 'Shenzhen'
    if 'QINGDAO' in upper_name:
        return 'Qingdao'
    if 'DUBAI' in upper_name:
        return 'Dubai'
    return None


def infer_legal_status(name):
    """Déduit le statut juridique à partir du nom si explicite."""
    upper_name = clean_text(name).upper()
    if 'CO., LIMITED' in upper_name or 'CO.,LIMITED' in upper_name:
        return 'CO., LIMITED'
    if 'CO., LTD' in upper_name or 'CO.LTD' in upper_name:
        return 'CO., LTD'
    if re.search(r'(^|\s)LIMITED($|\s)', upper_name):
        return 'LIMITED'
    if re.search(r'(^|\s)LTD($|\s)', upper_name):
        return 'LTD'
    if re.search(r'(^|\s)LLC($|\s)', upper_name):
        return 'LLC'
    if re.search(r'(^|\s)SARL($|\s)', upper_name):
        return 'SARL'
    if re.search(r'(^|\s)SA($|\s)', upper_name):
        return 'SA'
    if re.search(r'(^|\s)ETS($|\s)', upper_name):
        return 'ETS'
    return None


def build_reference_category(row):
    """Construit la catégorie fournisseur à partir du référentiel BD FRN."""
    category = clean_text(row.get('Catégorie/Famille'))
    if category:
        return category[:100]

    products = []
    for column in ('Produit 1', 'Produit 2', 'Produit 3'):
        value = clean_text(row.get(column))
        if value and value not in products:
            products.append(value)
    return ' / '.join(products)[:100] if products else None


def parse_command_sheet_suppliers(workbook_path):
    """Extrait les fournisseurs depuis l'onglet Commande."""
    dataframe = pd.read_excel(workbook_path, sheet_name=COMMAND_SHEET, header=3)
    suppliers = []

    for raw_name in dataframe.get('Fournisseur', []).tolist():
        name = clean_text(raw_name)
        if not name:
            continue
        suppliers.append({
            'nom': name,
            'statut_juridique': infer_legal_status(name),
            'pays': infer_supplier_country(name),
            'ville': infer_supplier_city(name),
            'dirigeant': None,
            'telephone1': None,
            'telephone2': None,
            'email1': None,
            'email2': None,
            'categorie': None,
            'statut': 'Actif',
            '_source': 'commande',
        })

    return suppliers


def parse_reference_sheet_suppliers(workbook_path):
    """Extrait les fournisseurs détaillés depuis l'onglet BD FRN."""
    dataframe = pd.read_excel(workbook_path, sheet_name=SUPPLIER_REFERENCE_SHEET, header=2)
    suppliers = []

    for _, row in dataframe.iterrows():
        name = clean_text(row.get('Nom ou Raison sociale'))
        if not name:
            continue
        suppliers.append({
            'nom': name,
            'statut_juridique': clean_text(row.get('Statut juridique')) or infer_legal_status(name),
            'pays': clean_text(row.get('Pays')) or infer_supplier_country(name),
            'ville': clean_text(row.get('Ville (siège)')) or infer_supplier_city(name),
            'dirigeant': clean_text(row.get('Dirigeant/Commercial')) or None,
            'telephone1': clean_phone(row.get('Téléphone 1')),
            'telephone2': clean_phone(row.get('Téléphone 2')),
            'email1': clean_email(row.get('E-mail 1')),
            'email2': clean_email(row.get('E-mail 2')),
            'categorie': build_reference_category(row),
            'statut': clean_text(row.get('Statut FRN')) or 'Actif',
            '_source': 'reference',
        })

    return suppliers


def merge_supplier_payload(target, incoming):
    """Fusionne deux fiches fournisseurs, en privilégiant la source référentiel."""
    target_source = target.get('_source') == 'reference'
    incoming_source = incoming.get('_source') == 'reference'

    if incoming_source and not target_source:
        target['nom'] = incoming['nom']
        target['_source'] = 'reference'

    for field in (
        'statut_juridique', 'pays', 'ville', 'dirigeant',
        'telephone1', 'telephone2', 'email1', 'email2', 'categorie', 'statut',
    ):
        incoming_value = incoming.get(field)
        current_value = target.get(field)
        if incoming_value and (not current_value or (incoming_source and incoming_value != current_value)):
            target[field] = incoming_value


def build_canonical_suppliers(workbook_path):
    """Construit la liste canonique de fournisseurs du classeur."""
    canonical_suppliers = []

    for payload in parse_reference_sheet_suppliers(workbook_path) + parse_command_sheet_suppliers(workbook_path):
        match = next(
            (supplier for supplier in canonical_suppliers if supplier_names_match(supplier['nom'], payload['nom'])),
            None,
        )
        if match is None:
            canonical_suppliers.append(payload)
        else:
            merge_supplier_payload(match, payload)

    canonical_suppliers.sort(key=lambda item: item['nom'])
    return canonical_suppliers


def import_suppliers(workbook_path, replace_existing=False):
    """Importe les fournisseurs du classeur en base."""
    suppliers = build_canonical_suppliers(workbook_path)

    with app.app_context():
        if replace_existing:
            if db.session.query(Commande.id).limit(1).first() is not None:
                raise SystemExit('Refus de remplacer les fournisseurs: des commandes existent déjà en base.')
            db.session.query(Fournisseur).delete()
            db.session.commit()

        existing_suppliers = Fournisseur.query.all()
        created = 0
        updated = 0

        for payload in suppliers:
            fournisseur = next(
                (item for item in existing_suppliers if supplier_names_match(item.nom, payload['nom'])),
                None,
            )

            if fournisseur is None:
                fournisseur = Fournisseur(nom=payload['nom'])
                db.session.add(fournisseur)
                existing_suppliers.append(fournisseur)
                created += 1

            changed = False
            if payload['nom'] and fournisseur.nom != payload['nom'] and supplier_names_match(fournisseur.nom, payload['nom']):
                if payload.get('_source') == 'reference':
                    fournisseur.nom = payload['nom']
                    changed = True

            for field in (
                'statut_juridique', 'pays', 'ville', 'dirigeant',
                'telephone1', 'telephone2', 'email1', 'email2', 'categorie', 'statut',
            ):
                incoming_value = payload.get(field)
                current_value = getattr(fournisseur, field)
                if incoming_value and incoming_value != current_value:
                    setattr(fournisseur, field, incoming_value)
                    changed = True

            if changed:
                updated += 1

        db.session.commit()

    return suppliers, created, updated


def main():
    parser = argparse.ArgumentParser(description='Importe les fournisseurs du classeur Projet Suivi Commande ASS.xlsx')
    parser.add_argument('workbook', nargs='?', default=str(DEFAULT_WORKBOOK), help='Chemin du classeur source')
    parser.add_argument('--replace-existing', action='store_true', help='Remplace entièrement la table fournisseurs si aucune commande n’existe')
    args = parser.parse_args()

    workbook_path = Path(args.workbook).resolve()
    if not workbook_path.exists():
        raise SystemExit(f'Fichier introuvable: {workbook_path}')

    suppliers, created, updated = import_suppliers(workbook_path, replace_existing=args.replace_existing)
    with_country = sum(1 for supplier in suppliers if supplier.get('pays'))
    with_city = sum(1 for supplier in suppliers if supplier.get('ville'))
    with_legal_status = sum(1 for supplier in suppliers if supplier.get('statut_juridique'))
    with_phone = sum(1 for supplier in suppliers if supplier.get('telephone1') or supplier.get('telephone2'))
    with_email = sum(1 for supplier in suppliers if supplier.get('email1') or supplier.get('email2'))
    with_category = sum(1 for supplier in suppliers if supplier.get('categorie'))

    print(f'Fichier source: {workbook_path}')
    print(f'Fournisseurs canoniques détectés: {len(suppliers)}')
    print(f'Fournisseurs créés: {created}')
    print(f'Fournisseurs mis à jour: {updated}')
    print(f'Avec pays: {with_country}')
    print(f'Avec ville: {with_city}')
    print(f'Avec statut juridique: {with_legal_status}')
    print(f'Avec téléphone: {with_phone}')
    print(f'Avec email: {with_email}')
    print(f'Avec catégorie: {with_category}')


if __name__ == '__main__':
    main()
