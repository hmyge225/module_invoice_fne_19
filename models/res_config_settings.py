from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    fne_api_url = fields.Char(
        string='URL API FNE',
        config_parameter='module_invoice_fne.api_url',
        help="URL d.normalisation des factures, par exemple "
             "http://<serveur-fne>/ws/external/invoices",
    )
    fne_api_key = fields.Char(
        string='Clé API FNE',
        config_parameter='module_invoice_fne.api_key',
    )
    fne_refund_api_url = fields.Char(
        string="URL API FNE (factures d'avoir)",
        config_parameter='module_invoice_fne.refund_api_url',
        help="Facultatif. Gabarit contenant le jeton {id} (identifiant de la "
             "facture d'origine), par exemple "
             "http://<serveur-fne>/ws/external/invoices/{id}/refund. "
             "Si vide, l'URL est déduite de l'URL API FNE ci-dessus.",
    )
    fne_establishment = fields.Char(
        string="Établissement FNE",
        config_parameter='module_invoice_fne.establishment',
        help="Nom de l'établissement tel qu'il est enregistré dans le système FNE.",
    )
    fne_point_of_sale = fields.Char(
        string='Point de vente FNE',
        config_parameter='module_invoice_fne.point_of_sale',
        help="Nom de la caisse tel qu'il est enregistré dans le système FNE.",
    )
    fne_amount_unit = fields.Selection([
        ('unit', "Unité de la devise (FCFA)"),
        ('centime', 'Centime'),
    ], string='Unité des montants FNE',
        config_parameter='module_invoice_fne.amount_unit',
        default='unit',
        help="Unité dans laquelle le prix unitaire de chaque article est transmis "
             "à l'API FNE. Cette valeur s'applique de façon uniforme à tous les "
             "articles d'une même facture.")
    fne_timeout = fields.Integer(
        string='Timeout API (secondes)',
        config_parameter='module_invoice_fne.timeout',
        default=30,
    )
