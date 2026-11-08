import json
import logging
import re

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

#: Correspondance entre le taux de TVA Odoo et le code attendu par la FNE.
FNE_TAX_MAPPING = {
    18.0: 'TVA',    # TVA sur HT 18,00% - A
    9.0: 'TVAB',    # TVA sur HT 09,00% - B
    0.0: 'TVAC',    # TVA sur HT 00,00% - C
}

#: Code FNE utilisé quand aucune taxe Odoo ne correspond au mapping.
FNE_TAX_DEFAULT = 'TVAD'

EMAIL_PATTERN = re.compile(r'^[\w.\-]+@[\w.\-]+\.[a-zA-Z]{2,}$')


class FneSendWizard(models.TransientModel):
    _name = 'fne.send.wizard'
    _description = "Assistant d'envoi FNE"

    invoice_id = fields.Many2one('account.move', string='Facture', required=True)

    invoice_type = fields.Selection([
        ('sale', 'Vente'),
        ('purchase', 'Proforma'),
    ], string='Type de facture', required=True, default='sale')

    payment_method = fields.Selection([
        ('card', 'Carte Bancaire'),
        ('check', 'Chèque'),
        ('cash', 'Espèces'),
        ('mobile-money', 'Mobile Money'),
        ('transfer', 'Virement'),
        ('deferred', 'A Terme'),
    ], string='Méthode de paiement', required=True, default='deferred')

    template = fields.Selection([
        ('B2B', 'B2B (Entreprise)'),
        ('B2C', 'B2C (Consommateur Final)'),
        ('B2G', 'B2G (Etat et Collectivités)'),
        ('B2F', 'B2F (Client International)'),
    ], string='Type de facturation', required=True, default='B2B')

    is_rne = fields.Boolean(string='Reliée à un reçu', default=False)
    rne = fields.Char(string='Numéro du reçu')

    # Informations client
    client_ncc = fields.Char(string='NCC du client')
    client_seller_name = fields.Char(string='Nom du vendeur', default='BSTEC')
    client_company_name = fields.Char(string='Nom du client', required=True)
    client_phone = fields.Char(string='Téléphone du client')
    client_email = fields.Char(string='Email du client', required=True)

    # Informations établissement
    establishment = fields.Char(
        string="Nom de l'établissement",
        default='BSTEC SARL',
        help="Ce nom doit être le même que celui de l'établissement dans le système FNE",
    )

    point_of_sale = fields.Char(
        string='Point de vente',
        required=True,
        default='CAISSE BSTEC',
        help="Ce nom doit être le même que celui de la caisse dans le système FNE",
    )

    # Messages
    commercial_message = fields.Text(
        string='Message commercial', default='Toute une expertise à votre service')
    footer = fields.Text(string='Footer', default='A bientôt !')

    # Devise étrangère
    foreign_currency = fields.Selection([
        ('XOF', 'XOF - Franc CFA'),
        ('USD', 'USD - Dollar Américain'),
        ('EUR', 'EUR - Euro'),
        ('JPY', 'JPY - Yen Japonais'),
        ('CAD', 'CAD - Dollar Canadien'),
        ('GBP', 'GBP - Livre Sterling Britannique'),
        ('AUD', 'AUD - Dollar Australien'),
        ('CNH', 'CNH - Yuan Chinois'),
        ('CHF', 'CHF - Franc Suisse'),
        ('HKD', 'HKD - Dollar Hong Kong'),
        ('NZD', 'NZD - Dollar Néo-Zélandais'),
    ], string='Monnaie étrangère')
    foreign_currency_rate = fields.Float(
        string='Taux de change',
        default=0.0,
        help="Obligatoire si une monnaie étrangère est sélectionnée",
    )

    # ------------------------------------------------------------------
    # Valeurs par défaut
    # ------------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        invoice_id = self.env.context.get('default_invoice_id')
        if invoice_id:
            invoice = self.env['account.move'].browse(invoice_id)
            if invoice:
                res.update({
                    'invoice_id': invoice.id,
                    'client_company_name': invoice.partner_id.name or '',
                    'client_phone': self._clean_phone_number(invoice.partner_id.phone or ''),
                    'client_email': invoice.partner_id.email or '',
                    'client_ncc': invoice.partner_id.vat or '',
                    'establishment': self._fne_param('module_invoice_fne.establishment', 'BSTEC SARL'),
                    'point_of_sale': self._fne_param('module_invoice_fne.point_of_sale', 'CAISSE BSTEC'),
                })
        return res

    # ------------------------------------------------------------------
    # Onchange
    # ------------------------------------------------------------------
    @api.onchange('template')
    def _onchange_template(self):
        """Validation pour template B2B et B2F"""
        if self.template == 'B2B':
            if not self.client_ncc:
                return {
                    'warning': {
                        'title': 'Attention',
                        'message': 'Le NCC du client est obligatoire pour les factures B2B',
                    }
                }

    @api.onchange('is_rne')
    def _onchange_is_rne(self):
        if not self.is_rne:
            self.rne = False

    @api.onchange('foreign_currency')
    def _onchange_foreign_currency(self):
        """Réinitialise le taux si aucune devise n'est sélectionnée"""
        if not self.foreign_currency:
            self.foreign_currency_rate = 0.0

    @api.onchange('client_phone')
    def _onchange_client_phone(self):
        """Nettoie le numéro de téléphone selon les règles FNE ivoiriennes"""
        if self.client_phone:
            self.client_phone = self._clean_phone_number(self.client_phone)

    @api.onchange('client_email')
    def _onchange_client_email(self):
        """Valide le format de l'email"""
        if self.client_email and not self._is_valid_email(self.client_email):
            return {
                'warning': {
                    'title': "Format d'email incorrect",
                    'message': 'Veuillez saisir une adresse email valide (exemple: nom@domaine.com)',
                }
            }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @api.model
    def _fne_param(self, key, default=None):
        value = self.env['ir.config_parameter'].sudo().get_param(key)
        return value if value not in (None, False, '') else default

    def _is_valid_email(self, email):
        """Vérifie si l'email a un format valide"""
        return bool(EMAIL_PATTERN.match(email or ''))

    def _get_tax_mapping(self, tax_amount):
        """Convertit le taux de taxe Odoo vers le format FNE"""
        return FNE_TAX_MAPPING.get(round(float(tax_amount), 2), FNE_TAX_DEFAULT)

    def _format_price_for_fne(self, price):
        """Formate le prix unitaire pour la FNE.

        L'unité est identique pour tous les articles d'une facture (paramètre
        `module_invoice_fne.amount_unit`) : c'était la source d'un bug où un
        prix entier était envoyé en unités alors qu'un prix décimal était
        envoyé en centimes dans la même facture.
        """
        unit = self._fne_param('module_invoice_fne.amount_unit', 'unit')
        if unit == 'centime':
            return int(round(float(price) * 100))

        rounded = round(float(price), 2)
        return int(rounded) if rounded == int(rounded) else rounded

    def _format_discount_for_fne(self, discount):
        """Formate la remise pour FNE en conservant les décimales significatives"""
        rounded = round(float(discount), 2)
        return int(rounded) if rounded == int(rounded) else rounded

    def _clean_phone_number(self, phone):
        """Nettoie le numéro de téléphone selon les règles FNE ivoiriennes

        Exemples de transformation :
        - 2252722471152 → 2722471152 (supprime 225)
        - 2230708167244 → 0708167244 (supprime 223)
        - 0708167244 → 0708167244 (reste intact, préfixe ivoirien)
        - +2252722471152 → 2722471152 (supprime + et 225)
        - 225 27 22 47 11 52 → 2722471152 (supprime espaces et 225)
        """
        if not phone:
            return phone

        # Ne conserver que les chiffres (supprime +, espaces, points, tirets...)
        phone = re.sub(r'[^0-9]', '', phone.strip())

        # Préfixes ivoiriens autorisés : on les laisse intacts
        ivoirian_prefixes = ('01', '05', '07', '21', '25', '27')
        if phone.startswith(ivoirian_prefixes):
            return phone

        # Préfixes internationaux courants à supprimer
        for prefix in ('225', '223', '226', '227', '228', '229', '240'):
            if phone.startswith(prefix):
                return phone[len(prefix):]

        return phone

    # ------------------------------------------------------------------
    # Envoi
    # ------------------------------------------------------------------
    def action_send_to_fne(self):
        self.ensure_one()

        self._validate_send_wizard()

        data = self._prepare_fne_data()

        invoice = self.invoice_id
        invoice.write({'fne_state': 'pending', 'fne_error_message': False})

        try:
            response = invoice._call_fne_api(data)
            _logger.info("Réponse FNE complète: %s", response)
        except Exception as exc:
            _logger.error("Erreur FNE complète: %s", exc)
            invoice.write({
                'fne_state': 'error',
                'fne_error_message': str(exc),
            })
            return self._fne_error_notification(str(exc))

        if response.get('status_code') in (200, 201) and response.get('token'):
            invoice.write({
                'fne_url': response['token'],
                'fne_reference': response.get('reference', ''),
                'fne_ncc': response.get('ncc', ''),
                'fne_token': response.get('token', ''),
                'fne_response_data': json.dumps(response),
                'fne_state': 'success',
                'fne_error_message': False,
            })

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Succès FNE',
                    'message': 'Facture envoyée avec succès ! Le lien de vérification est disponible sur la facture.',
                    'type': 'success',
                    'sticky': False,
                    'next': {
                        'type': 'ir.actions.act_window_close',
                    },
                },
            }

        error_msg = f"Réponse inattendue de l'API FNE : {response}"
        invoice.write({
            'fne_state': 'error',
            'fne_error_message': error_msg,
        })
        return self._fne_error_notification(error_msg)

    def _fne_error_notification(self, message):
        """Notification d'erreur renvoyée à l'utilisateur.

        Une notification est preferable à une exception : le statut d'erreur
        et le message sont alors enregistrés sur la facture et restent
        visibles, et l'assistant reste ouvert pour correction.
        """
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': "Erreur d'envoi à la FNE",
                'message': message,
                'type': 'danger',
                'sticky': True,
            },
        }

    def _validate_send_wizard(self):
        """Contrôle les données saisies avant l'appel à l'API."""
        if self.template == 'B2B' and not self.client_ncc:
            raise UserError('Le NCC du client est obligatoire pour les factures B2B')

        if self.is_rne and not self.rne:
            raise UserError("Le numéro de reçu est obligatoire quand la facture est reliée à un reçu")

        if not self.client_email or not self._is_valid_email(self.client_email):
            raise UserError("L'adresse email doit avoir un format valide (exemple: nom@domaine.com)")

        # B2F : devise et taux obligatoires
        if self.template == 'B2F':
            if not self.foreign_currency or not self.foreign_currency_rate or self.foreign_currency_rate <= 0:
                raise UserError(
                    'Les champs foreignCurrency et foreignCurrencyRate sont obligatoires '
                    'pour la transaction B2F avec des devises différentes.'
                )

        # Devise étrangère pour les autres templates
        if self.foreign_currency and self.foreign_currency_rate <= 0:
            raise UserError('Le taux de change doit être supérieur à 0 pour une devise étrangère')

    def _prepare_fne_data(self):
        """Prépare les données pour l'API FNE"""
        self.ensure_one()

        items = []
        for line in self.invoice_id.invoice_line_ids.filtered(lambda l: l.product_id):
            item = {
                "quantity": line.quantity,
                "amount": self._format_price_for_fne(line.price_unit),
                "description": line.name or line.product_id.name or '',
            }

            if line.tax_ids:
                item["taxes"] = [self._get_tax_mapping(line.tax_ids[0].amount)]
            else:
                item["taxes"] = [FNE_TAX_DEFAULT]

            item["measurementUnit"] = line.product_uom_id.name if line.product_uom_id else 'U'

            if line.product_id.default_code:
                item["reference"] = line.product_id.default_code

            if line.discount > 0:
                item["discount"] = self._format_discount_for_fne(line.discount)

            items.append(item)

        if not items:
            raise UserError('La facture doit contenir au moins un article avec un produit')

        for item in items:
            if item['amount'] <= 0:
                raise UserError(
                    f"Le montant de l'article \"{item['description']}\" doit être positif"
                )
            if item['quantity'] <= 0:
                raise UserError(
                    f"La quantité de l'article \"{item['description']}\" doit être positive"
                )

        data = {
            "invoiceType": self.invoice_type,
            "paymentMethod": self.payment_method,
            "template": self.template,
            "isRne": self.is_rne,
            "clientCompanyName": self.client_company_name,
            "clientPhone": str(self.client_phone) if self.client_phone else "",
            "clientEmail": self.client_email,
            "pointOfSale": self.point_of_sale,
            "establishment": self.establishment,
            "items": items,
        }

        if self.template == 'B2B' and self.client_ncc:
            data["clientNcc"] = self.client_ncc

        if self.client_seller_name:
            data["clientSellerName"] = self.client_seller_name

        if self.is_rne and self.rne:
            data["rne"] = self.rne

        if self.commercial_message:
            data["commercialMessage"] = self.commercial_message

        if self.footer:
            data["footer"] = self.footer

        if self.foreign_currency:
            data["foreignCurrency"] = self.foreign_currency
            if self.foreign_currency_rate > 0:
                data["foreignCurrencyRate"] = self.foreign_currency_rate
            else:
                raise UserError(
                    "Le taux de change est obligatoire quand une devise étrangère est spécifiée"
                )

        return data
