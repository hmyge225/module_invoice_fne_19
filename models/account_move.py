import json
import logging

try:
    import requests
except ImportError:  # pragma: no cover - requests est déclaré en external_dependencies
    requests = None

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

#: Paramètres de configuration manipulables dans Réglages > Comptabilité > Configuration FNE.
PARAM_API_URL = 'module_invoice_fne.api_url'
PARAM_API_KEY = 'module_invoice_fne.api_key'
PARAM_REFUND_API_URL = 'module_invoice_fne.refund_api_url'
PARAM_ESTABLISHMENT = 'module_invoice_fne.establishment'
PARAM_POINT_OF_SALE = 'module_invoice_fne.point_of_sale'
PARAM_AMOUNT_UNIT = 'module_invoice_fne.amount_unit'
PARAM_TIMEOUT = 'module_invoice_fne.timeout'


class AccountMove(models.Model):
    _inherit = 'account.move'

    # ------------------------------------------------------------------
    # Facture ordinaire
    # ------------------------------------------------------------------
    fne_token = fields.Char(string='Token FNE', readonly=True, copy=False)
    fne_url = fields.Char(string='URL FNE', readonly=True, copy=False)
    fne_reference = fields.Char(string='Référence FNE', readonly=True, copy=False)
    fne_ncc = fields.Char(string='NCC FNE', readonly=True, copy=False)

    fne_state = fields.Selection([
        ('draft', 'Non envoyé'),
        ('pending', 'En cours'),
        ('success', 'Succès'),
        ('error', 'Erreur'),
    ], string='Statut FNE', default='draft', readonly=True, copy=False)

    fne_error_message = fields.Text(string="Message d'erreur FNE", readonly=True, copy=False)

    fne_response_data = fields.Text(
        string='Réponse FNE Complète', readonly=True, copy=False,
        help="Stocke la réponse JSON complète de l'API FNE pour les factures",
    )

    # ------------------------------------------------------------------
    # Facture d'avoir
    # ------------------------------------------------------------------
    refund_fne_token = fields.Char(string='Token FNE Avoir', readonly=True, copy=False)
    refund_fne_url = fields.Char(string='URL FNE Avoir', readonly=True, copy=False)
    refund_fne_reference = fields.Char(string='Référence FNE Avoir', readonly=True, copy=False)
    refund_fne_ncc = fields.Char(string='NCC FNE Avoir', readonly=True, copy=False)

    refund_fne_state = fields.Selection([
        ('draft', 'Non envoyé'),
        ('pending', 'En cours'),
        ('success', 'Succès'),
        ('error', 'Erreur'),
    ], string='Statut FNE Avoir', default='draft', readonly=True, copy=False)

    refund_fne_error_message = fields.Text(string="Message d'erreur FNE Avoir", readonly=True, copy=False)

    refund_fne_response_data = fields.Text(
        string='Réponse FNE Avoir Complète', readonly=True, copy=False,
        help="Stocke la réponse JSON complète de l'API FNE pour les factures d'avoir",
    )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_send_to_fne(self):
        """Ouvre le wizard pour envoyer la facture à la FNE"""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Envoyer à la FNE',
            'res_model': 'fne.send.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_invoice_id': self.id},
        }

    def action_certify_refund(self):
        """Ouvre le wizard pour certifier une facture d'avoir"""
        self.ensure_one()
        if self.move_type != 'out_refund':
            raise UserError("Cette action n'est disponible que pour les factures d'avoir")

        return {
            'type': 'ir.actions.act_window',
            'name': "Certifier Facture d'Avoir",
            'res_model': 'refund.certify.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_credit_note_id': self.id},
        }

    def action_view_fne_response(self):
        """Affiche la réponse complète de l'API FNE dans une popup"""
        self.ensure_one()

        if not self.fne_response_data:
            raise UserError('Aucune réponse FNE stockée pour cette facture')

        formatted_response = self._format_fne_response(self.fne_response_data)

        return {
            'type': 'ir.actions.act_window',
            'name': f'Réponse FNE Complète - {self.name}',
            'res_model': 'fne.response.viewer',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_invoice_name': self.name,
                'default_fne_reference': self.fne_reference,
                'default_response_data': formatted_response,
                'default_invoice_id': self.id,
            },
        }

    def action_debug_fne_data(self):
        """Méthode de debug pour afficher les données FNE de la facture d'origine"""
        self.ensure_one()

        if self.move_type != 'out_refund':
            raise UserError("Cette action n'est disponible que pour les factures d'avoir")

        original_invoice = self._find_fne_original_invoice(self)

        if not original_invoice:
            raise UserError("Aucune facture d'origine certifiée FNE trouvée")

        if not original_invoice.fne_response_data:
            raise UserError("Aucune donnée de réponse FNE trouvée pour la facture d'origine")

        formatted_response = self._format_fne_response(original_invoice.fne_response_data)

        return {
            'type': 'ir.actions.act_window',
            'name': f"Debug FNE - Facture d'origine: {original_invoice.name}",
            'res_model': 'fne.response.viewer',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_invoice_name': f"Facture d'origine: {original_invoice.name}",
                'default_fne_reference': original_invoice.fne_reference,
                'default_response_data': formatted_response,
                'default_invoice_id': original_invoice.id,
            },
        }

    # ------------------------------------------------------------------
    # Utilitaires FNE
    # ------------------------------------------------------------------
    @api.model
    def _fne_get_param(self, key, default=None):
        """Lit un paramètre de configuration du module."""
        value = self.env['ir.config_parameter'].sudo().get_param(key)
        return value if value not in (None, False, '') else default

    def _fne_headers(self):
        """En-têtes HTTP de l'API FNE (authentification Bearer)."""
        api_key = self._fne_get_param(PARAM_API_KEY)
        if not api_key:
            raise UserError(
                "Configuration API FNE manquante : renseignez la clé API dans "
                "Réglages > Comptabilité > Configuration FNE."
            )
        return {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }

    def _fne_timeout(self):
        """Timeout HTTP en secondes (30 par défaut)."""
        try:
            return int(self._fne_get_param(PARAM_TIMEOUT, 30))
        except (TypeError, ValueError):
            return 30

    def _fne_post(self, url, data):
        """POST un payload JSON sur l'API FNE et retourne le corps JSON décodé.

        Lève une `UserError` pour toute erreur réseau ou HTTP afin que le
        message remonte proprement à l'utilisateur et puisse être stocké dans
        le champ d'erreur de la facture.
        """
        if requests is None:
            raise UserError(
                "La librairie Python 'requests' n'est pas installée sur le serveur. "
                "Installez-la ou redémarrez le conteneur Odoo pour rejouer le "
                "requirements.txt de l'image."
            )
        if not url:
            raise UserError(
                "URL de l'API FNE manquante : renseignez-la dans "
                "Réglages > Comptabilité > Configuration FNE."
            )

        headers = self._fne_headers()
        timeout = self._fne_timeout()

        try:
            response = requests.post(url, json=data, headers=headers, timeout=timeout)
        except requests.exceptions.Timeout:
            raise UserError(f"Timeout lors de l'appel à l'API FNE ({timeout} secondes).")
        except requests.exceptions.ConnectionError:
            raise UserError("Impossible de se connecter à l'API FNE.")
        except requests.exceptions.RequestException as exc:
            raise UserError(f"Erreur réseau lors de l'appel à l'API FNE : {exc}")

        if response.status_code not in (200, 201):
            raise UserError(
                f"Erreur API FNE (code {response.status_code}) : {response.text}"
            )

        try:
            response_data = response.json()
        except ValueError:
            raise UserError(
                f"Réponse API invalide (pas de JSON valide) : {response.text}"
            )

        if not isinstance(response_data, dict):
            raise UserError(
                "Réponse API FNE inattendue : le corps JSON renvoyé n'est pas un objet."
            )

        response_data['status_code'] = response.status_code
        return response_data

    def _call_fne_api(self, data):
        """Envoie les données d'une facture à l'API FNE."""
        api_url = self._fne_get_param(PARAM_API_URL)
        if not api_url:
            raise UserError(
                "URL de l'API FNE manquante : renseignez-la dans "
                "Réglages > Comptabilité > Configuration FNE."
            )
        return self._fne_post(api_url, data)

    def _call_refund_api(self, api_url, data):
        """Envoie les données de certification d'une facture d'avoir à l'API FNE."""
        return self._fne_post(api_url, data)

    def _get_refund_api_url(self, fne_invoice_id):
        """Construit l'URL de certification d'avoir sans IP codée en dur.

        - si `module_invoice_fne.refund_api_url` est renseigné, c'est un gabarit
          contenant le jeton `{id}` (syntaxe de la procédure FNE) ou
          `{invoice_id}` (alias historique) ;
        - sinon l'URL est déduite de l'URL de l'API configurée.
        """
        invoice_id = str(fne_invoice_id or '').strip()
        if not invoice_id:
            raise UserError(
                "Identifiant de la facture FNE d'origine manquant : impossible de "
                "construire l'URL de certification d'avoir."
            )

        template = self._fne_get_param(PARAM_REFUND_API_URL)
        if template:
            url = template.strip()
            # La procédure FNE documente le jeton `{id}` ; `{invoice_id}` est
            # conservé comme alias pour les configurations déjà en place.
            url = url.replace('{invoice_id}', invoice_id).replace('{id}', invoice_id)
            if '{' in url or '}' in url:
                raise UserError(
                    "Le gabarit d'URL API FNE pour les factures d'avoir contient "
                    f"un jeton non reconnu : {template}. Utilisez {invoice_id} "
                    "comme identifiant de la facture d'origine, par exemple "
                    "http://<serveur-fne>/ws/external/invoices/{id}/refund."
                )
            return url

        base_url = (self._fne_get_param(PARAM_API_URL) or '').rstrip('/')
        if not base_url:
            raise UserError(
                "URL de l'API FNE manquante : renseignez-la dans "
                "Réglages > Comptabilité > Configuration FNE."
            )
        # L'URL de certification des factures se termine par `/invoices/sign` :
        # l'endpoint d'avoir est un chemin frère sous `/invoices`.
        if base_url.endswith('/sign'):
            base_url = base_url[:-len('/sign')]
        if not base_url.endswith('/invoices'):
            base_url = f'{base_url}/invoices'
        return f'{base_url}/{invoice_id}/refund'

    def _find_fne_original_invoice(self, credit_note):
        """Retrouve la facture d'origine certifiée FNE d'une facture d'avoir.

        Trois stratégies, de la plus fiable à la moins fiable :
        1. le lien `reversed_entry_id` posé par Odoo lors d'un avoir ;
        2. l'égalité de référence (`ref`) ;
        3. le même partenaire avec un montant proche (uniquement si un seul
           candidat correspond, afin de ne jamais certifier un avoir contre la
           mauvaise facture).
        """
        if getattr(credit_note, 'reversed_entry_id', False):
            return credit_note.reversed_entry_id

        if credit_note.ref:
            original = self.search([
                ('name', '=', credit_note.ref),
                ('move_type', '=', 'out_invoice'),
                ('fne_state', '=', 'success'),
            ], limit=1)
            if original:
                return original

        amount = abs(credit_note.amount_total)
        candidates = self.search([
            ('partner_id', '=', credit_note.partner_id.id),
            ('move_type', '=', 'out_invoice'),
            ('fne_state', '=', 'success'),
            ('amount_total', '>=', amount * 0.8),
            ('amount_total', '<=', amount * 1.2),
        ], order='date desc')

        if len(candidates) == 1:
            return candidates
        if len(candidates) > 1:
            _logger.warning(
                "Plusieurs factures d'origine correspondent à l'avoir %s pour le "
                "partenaire %s : %s. Certification bloquée pour éviter une "
                "certification contre la mauvaise facture.",
                credit_note.name, credit_note.partner_id.name, candidates.mapped('name'),
            )
        return self.browse()

    @api.model
    def _format_fne_response(self, raw_response):
        """Met en forme une réponse FNE brute pour l'affichage."""
        try:
            return json.dumps(json.loads(raw_response), indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            return raw_response
