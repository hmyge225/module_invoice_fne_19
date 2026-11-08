{
    'name': 'Module Invoice FNE - Odoo 19',
    'summary': "Certification des factures et des factures d'avoir auprès de la FNE (Côte d'Ivoire)",
    'version': '19.0.1.0.5',
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
        'web.assets_backend': [
            'module_invoice_fne_v19/static/src/css/fne.css',
        ],
    },
    'installable': True,
    'application': False,
}
