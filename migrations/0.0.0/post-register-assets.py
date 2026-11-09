# -*- coding: utf-8 -*-
"""Déclare les assets du module (feuille de style backend + icône).

Ce script est exécuté à la mise à jour du module, lorsque ``post_init_hook``
n'est pas joué (Odoo ne le lance que sur une installation).

La feuille de style est déclarée en base (``ir.asset``) et non dans le
manifeste : le manifeste est interprété avec ``ast.literal_eval`` et ne peut
donc contenir aucune expression, alors qu'ici le chemin doit suivre le nom
réel du dossier de l'addon (qui peut différer du nom canonique de la
variante sur le serveur de destination).
"""

import os

# ``__name__`` vaut ``odoo.upgrade.<addon>.<version>.<script>`` : le nom
# technique réel du module est donc le troisième élément.
_MODULE_NAME = __name__.split('.')[2]

_CSS_PATH = '/%s/static/src/css/*.css' % _MODULE_NAME
_ICON_PATH = '/%s/static/description/icon.png' % _MODULE_NAME


def _module_name_from_path():
    """Repli : déduit le nom du module depuis l'emplacement du script."""
    return os.path.basename(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def migrate(cr, version):
    if not version:
        return

    module_name = _MODULE_NAME or _module_name_from_path()
    css_path = '/%s/static/src/css/*.css' % module_name
    icon_path = '/%s/static/description/icon.png' % module_name

    # Suppression des déclarations obsolètes (ancien nom de dossier).
    cr.execute("""
        DELETE FROM ir_asset
        WHERE bundle = 'web.assets_backend'
          AND path LIKE '/module_invoice_fne%%/static/src/css/%%.css'
          AND path NOT LIKE %s
    """, (module_name + '/%%',))

    cr.execute("""
        SELECT id FROM ir_asset
        WHERE bundle = 'web.assets_backend' AND path = %s
    """, (css_path,))
    if not cr.fetchone():
        cr.execute("""
            INSERT INTO ir_asset
                (name, bundle, directive, path, sequence, active,
                 create_uid, create_date, write_uid, write_date)
            VALUES
                ('Module Invoice FNE - feuille de style', 'web.assets_backend',
                 'append', %s, 16, TRUE, 1, NOW(), 1, NOW())
        """, (css_path,))

    cr.execute("""
        UPDATE ir_module_module SET icon = %s WHERE name = %s
    """, (icon_path, module_name))

    print("Assets FNE enregistrés pour le module %s (%s)" % (module_name, css_path))
