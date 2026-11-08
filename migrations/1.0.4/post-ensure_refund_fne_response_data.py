# -*- coding: utf-8 -*-
"""Sécurise la présence de la colonne ``refund_fne_response_data``.

La colonne est créée automatiquement par l'ORM à partir de la déclaration du
champ dans ``models/account_move.py``. Ce script reste une filet de sécurité
pour les bases installées avant l'introduction du champ (version 1.0.1).
"""


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'account_move'
          AND column_name = 'refund_fne_response_data'
    """)

    if not cr.fetchone():
        cr.execute("""
            ALTER TABLE account_move
            ADD COLUMN IF NOT EXISTS refund_fne_response_data TEXT
        """)
        print("Champ refund_fne_response_data ajouté à la table account_move")
    else:
        print("Champ refund_fne_response_data déjà présent sur account_move")
