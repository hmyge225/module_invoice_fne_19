import json

from odoo import api, fields, models


class FneResponseViewer(models.TransientModel):
    _name = 'fne.response.viewer'
    _description = 'Visualiseur de réponse FNE'

    invoice_name = fields.Char(string='Facture', readonly=True)
    fne_reference = fields.Char(string='Référence FNE', readonly=True)
    response_data = fields.Text(string='Réponse FNE Complète', readonly=True)
    invoice_id = fields.Many2one('account.move', string='Facture liée', readonly=True)

    # Champs extraits pour affichage structuré
    token = fields.Char(string='Token', readonly=True)
    url = fields.Char(string='URL', readonly=True)
    reference = fields.Char(string='Référence', readonly=True)
    ncc = fields.Char(string='NCC', readonly=True)
    status_code = fields.Char(string='Code de statut', readonly=True)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)

        # Extraire les données structurées si disponibles
        if res.get('response_data'):
            try:
                data = json.loads(res['response_data'])
            except (TypeError, ValueError):
                return res

            if isinstance(data, dict):
                res.update({
                    'token': data.get('token', ''),
                    'url': data.get('url', ''),
                    'reference': data.get('reference', ''),
                    'ncc': data.get('ncc', ''),
                    'status_code': str(data.get('status_code', '')),
                })

        return res
