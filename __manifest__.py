{
    'name': 'Module Invoice FNE - Odoo 19',
    'summary': "Certification des factures et des factures d'avoir auprès de la FNE (Côte d'Ivoire)",
    'version': '19.0.1.0.7',
    'category': 'Accounting',
    'license': 'LGPL-3',
    'author': 'SecurGuard',
    'depends': ['account'],
    'data': [
        'security/ir.model.access.csv',
        'data/fne_decimal_precision.xml',
        'views/account_move_views.xml',
        'views/res_config_settings_views.xml',
        'views/fne_send_wizard_views.xml',
        'views/refund_certify_wizard_views.xml',
        'views/fne_response_viewer_views.xml',
    ],
    'external_dependencies': {
        'python': ['requests'],
    },
    # La feuille de style n'est PAS déclarée ici : le manifeste est lu avec
    # ``ast.literal_eval`` (aucune expression possible) et le chemin doit
    # suivre le nom réel du dossier de l'addon, qui peut différer du nom
    # canonique de la variante. La déclaration est donc faite en base par
    # ``_register_assets`` (voir ``__init__.py``), avec un chemin calculé au
    # runtime. Idem pour l'icône : sans clé « icon », Odoo applique son
    # défaut ``{module}/static/description/icon.png``.
    'post_init_hook': '_register_assets',
    'installable': True,
    'application': False,
}
