from . import models
from . import wizard

# --------------------------------------------------------------------------
# Nom technique réel du module (= nom de son dossier dans les addons).
#
# Il peut différer du nom « canonique » de la variante (par exemple
# ``module_invoice_fne_v19``) si le dossier a été renommé sur le serveur.
# Odoo résout les chemins d'assets à partir du nom *réel* du dossier :
# un chemin déclaré en dur avec un autre nom n'est pas résolu et fait
# échouer la compilation du bundle backend (« Erreur de style », puis 500).
# --------------------------------------------------------------------------
MODULE_NAME = __name__.split('.')[-1]

#: Chemin (globs) de la feuille de style du module, dans le bundle backend.
CSS_ASSET_PATH = '/%s/static/src/css/*.css' % MODULE_NAME

#: Chemin de l'icône du module, calculé à partir du nom réel du dossier.
MODULE_ICON_PATH = '/%s/static/description/icon.png' % MODULE_NAME


def _register_assets(env):
    """Déclare les assets du module (feuille de style backend + icône).

    La déclaration est faite en base (``ir.asset``) et non dans le
    manifeste : le manifeste est interprété avec ``ast.literal_eval`` et ne
    peut donc contenir aucune expression. En base, le chemin est calculé au
    runtime à partir du nom réel du dossier, ce qui rend le module
    déployable sous n'importe quel nom technique.

    Cette fonction est appelée à l'installation (``post_init_hook``) ; la
    mise à jour d'un module déjà installé est prise en charge par le script
    de migration ``migrations/1.0.7/post-register-assets.py``.
    """
    env = env(su=True)
    assets = env['ir.asset']

    # Nettoyage des déclarations obsolètes (issues d'un ancien nom de dossier).
    obsolete = assets.search([
        ('bundle', '=', 'web.assets_backend'),
        ('path', 'like', '/module_invoice_fne%/static/src/css/*.css'),
    ])
    obsolete = obsolete.filtered(
        lambda asset: not asset.path.startswith('/%s/' % MODULE_NAME))
    obsolete.unlink()

    assets.create({
        'name': 'Module Invoice FNE - feuille de style',
        'bundle': 'web.assets_backend',
        'directive': 'append',
        'path': CSS_ASSET_PATH,
    })

    module = env['ir.module.module'].search([('name', '=', MODULE_NAME)], limit=1)
    if module:
        module.write({'icon': MODULE_ICON_PATH})
