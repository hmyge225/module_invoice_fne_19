{
    'name': 'Module Invoice FNE - Odoo 19',
    'summary': "Certification des factures et des factures d'avoir auprès de la FNE (Côte d'Ivoire)",
    'version': '19.0.1.0.6',
    'category': 'Accounting',
    'license': 'LGPL-3',
    'author': 'SecurGuard',
    'icon': '/module_invoice_fne_v19/static/description/icon.png',
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
    'assets': {
        # Le chemin est déclaré en glob et non en fichier unique : si la
        # feuille de style est momentanément introuvable sur le disque
        # (fenêtre de déploiement sur un bind mount), Odoo l'ignore avec un
        # simple avertissement au lieu de faire échouer la compilation de
        # tout le bundle backend (« Erreur de style » sur toute l'application).
        'web.assets_backend': [
            'module_invoice_fne_v19/static/src/css/*.css',
        ],
    },
    'installable': True,
    'application': False,
}
