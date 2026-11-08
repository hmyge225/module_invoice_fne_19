import json

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import FNE_SUCCESS_PAYLOAD, FneTestCase, mocked_fne_post


@tagged('post_install', '-at_install')
class TestFneSendWizard(FneTestCase):
    """Tests de l'assistant d'envoi de facture à la FNE."""

    def _create_wizard(self, invoice, **vals):
        defaults = {
            'invoice_id': invoice.id,
            'client_company_name': invoice.partner_id.name,
            'client_email': invoice.partner_id.email,
            'client_ncc': invoice.partner_id.vat,
        }
        defaults.update(vals)
        return self.env['fne.send.wizard'].create(defaults)

    # ------------------------------------------------------------------
    # Valeurs par défaut
    # ------------------------------------------------------------------
    def test_default_get_fills_client_informations(self):
        invoice = self._create_invoice()
        wizard = self.env['fne.send.wizard'].with_context(
            default_invoice_id=invoice.id).default_get([
                'invoice_id', 'client_company_name', 'client_phone',
                'client_email', 'client_ncc', 'establishment', 'point_of_sale',
            ])
        self.assertEqual(wizard['invoice_id'], invoice.id)
        self.assertEqual(wizard['client_company_name'], 'Client FNE')
        self.assertEqual(wizard['client_email'], 'client@example.com')
        self.assertEqual(wizard['client_ncc'], 'CI123456789')
        self.assertEqual(wizard['establishment'], 'BSTEC SARL')
        self.assertEqual(wizard['point_of_sale'], 'CAISSE BSTEC')

    def test_default_get_uses_configured_establishment(self):
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('module_invoice_fne.establishment', 'MA SOCIETE')
        params.set_param('module_invoice_fne.point_of_sale', 'CAISSE 2')
        invoice = self._create_invoice()
        wizard = self.env['fne.send.wizard'].with_context(
            default_invoice_id=invoice.id).default_get(['establishment', 'point_of_sale'])
        self.assertEqual(wizard['establishment'], 'MA SOCIETE')
        self.assertEqual(wizard['point_of_sale'], 'CAISSE 2')

    # ------------------------------------------------------------------
    # Normalisation du téléphone
    # ------------------------------------------------------------------
    def test_clean_phone_number_removes_international_prefix(self):
        wizard = self.env['fne.send.wizard']
        self.assertEqual(wizard._clean_phone_number('2252722471152'), '2722471152')
        self.assertEqual(wizard._clean_phone_number('+2252722471152'), '2722471152')
        self.assertEqual(wizard._clean_phone_number('225 27 22 47 11 52'), '2722471152')
        self.assertEqual(wizard._clean_phone_number('2230708167244'), '0708167244')

    def test_clean_phone_number_keeps_ivorian_prefix(self):
        wizard = self.env['fne.send.wizard']
        self.assertEqual(wizard._clean_phone_number('0708167244'), '0708167244')
        self.assertEqual(wizard._clean_phone_number('2722471152'), '2722471152')
        self.assertEqual(wizard._clean_phone_number('0102030405'), '0102030405')

    def test_clean_phone_number_handles_empty_value(self):
        self.assertEqual(self.env['fne.send.wizard']._clean_phone_number(''), '')

    # ------------------------------------------------------------------
    # Mapping des taxes
    # ------------------------------------------------------------------
    def test_tax_mapping(self):
        wizard = self.env['fne.send.wizard']
        self.assertEqual(wizard._get_tax_mapping(18.0), 'TVA')
        self.assertEqual(wizard._get_tax_mapping(9.0), 'TVAB')
        self.assertEqual(wizard._get_tax_mapping(0.0), 'TVAC')
        self.assertEqual(wizard._get_tax_mapping(19.0), 'TVAD')

    # ------------------------------------------------------------------
    # Construction du payload
    # ------------------------------------------------------------------
    def test_prepare_fne_data_builds_expected_payload(self):
        invoice = self._create_invoice()
        wizard = self._create_wizard(invoice)
        data = wizard._prepare_fne_data()

        self.assertEqual(data['invoiceType'], 'sale')
        self.assertEqual(data['paymentMethod'], 'deferred')
        self.assertEqual(data['template'], 'B2B')
        self.assertEqual(data['clientNcc'], 'CI123456789')
        self.assertEqual(data['clientCompanyName'], 'Client FNE')
        self.assertEqual(data['establishment'], 'BSTEC SARL')
        self.assertEqual(data['pointOfSale'], 'CAISSE BSTEC')
        self.assertEqual(len(data['items']), 1)

        item = data['items'][0]
        # Odoo préfixe la libellé de ligne par la référence produit
        self.assertIn('Article FNE 1', item['description'])
        self.assertEqual(item['reference'], 'ART-001')
        self.assertEqual(item['taxes'], ['TVA'])
        self.assertEqual(item['quantity'], 2)

    def test_prepare_fne_data_includes_conditional_fields(self):
        invoice = self._create_invoice()
        wizard = self._create_wizard(invoice, is_rne=True, rne='RNE-42')
        data = wizard._prepare_fne_data()
        self.assertTrue(data['isRne'])
        self.assertEqual(data['rne'], 'RNE-42')
        self.assertEqual(data['commercialMessage'], 'Toute une expertise à votre service')

    def test_prepare_fne_data_includes_foreign_currency(self):
        invoice = self._create_invoice()
        wizard = self._create_wizard(
            invoice, template='B2F', foreign_currency='EUR', foreign_currency_rate=655.957)
        data = wizard._prepare_fne_data()
        self.assertEqual(data['foreignCurrency'], 'EUR')
        self.assertEqual(data['foreignCurrencyRate'], 655.957)

    def test_prepare_fne_data_raises_without_product(self):
        invoice = self._create_invoice(lines=[(0, 0, {
            'name': 'Ligne sans produit',
            'quantity': 1,
            'price_unit': 100,
        })])
        wizard = self._create_wizard(invoice)
        with self.assertRaises(UserError):
            wizard._prepare_fne_data()

    # ------------------------------------------------------------------
    # Unité des montants : correction du bug d'unité mixed
    # ------------------------------------------------------------------
    def _mixed_price_invoice(self):
        """Une facture qui contient à la fois un prix entier et un prix décimal."""
        return self._create_invoice(lines=[
            (0, 0, {
                'product_id': self.product_1.id,
                'quantity': 2,
                'price_unit': 15000,
            }),
            (0, 0, {
                'product_id': self.product_2.id,
                'quantity': 1,
                'price_unit': 12.5,
            }),
        ])

    def test_amount_unit_is_uniform_in_unit_mode(self):
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('module_invoice_fne.amount_unit', 'unit')
        invoice = self._mixed_price_invoice()
        wizard = self._create_wizard(invoice)
        data = wizard._prepare_fne_data()

        amounts = [item['amount'] for item in data['items']]
        self.assertEqual(amounts, [15000, 12.5])

        # Invariant : le rapport amount / prix est le même pour tous les articles
        prices = [line.price_unit for line in invoice.invoice_line_ids]
        ratios = {round(amount / price, 6) for amount, price in zip(amounts, prices)}
        self.assertEqual(len(ratios), 1, f"Montants dans des unités différentes : {amounts}")

    def test_amount_unit_is_uniform_in_centime_mode(self):
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('module_invoice_fne.amount_unit', 'centime')
        invoice = self._mixed_price_invoice()
        wizard = self._create_wizard(invoice)
        data = wizard._prepare_fne_data()

        amounts = [item['amount'] for item in data['items']]
        self.assertEqual(amounts, [1500000, 1250])

        prices = [line.price_unit for line in invoice.invoice_line_ids]
        ratios = {round(amount / price, 6) for amount, price in zip(amounts, prices)}
        self.assertEqual(len(ratios), 1, f"Montants dans des unités différentes : {amounts}")

    def test_discount_is_included(self):
        invoice = self._create_invoice(lines=[(0, 0, {
            'product_id': self.product_1.id,
            'quantity': 1,
            'price_unit': 15000,
            'discount': 10,
        })])
        wizard = self._create_wizard(invoice)
        data = wizard._prepare_fne_data()
        self.assertEqual(data['items'][0]['discount'], 10)

    # ------------------------------------------------------------------
    # Validations
    # ------------------------------------------------------------------
    def test_b2b_requires_ncc(self):
        invoice = self._create_invoice()
        wizard = self._create_wizard(invoice, client_ncc=False)
        with self.assertRaises(UserError):
            wizard.action_send_to_fne()

    def test_invalid_email_is_rejected(self):
        invoice = self._create_invoice()
        wizard = self._create_wizard(invoice, client_email='pas-un-email')
        with self.assertRaises(UserError):
            wizard.action_send_to_fne()

    def test_rne_requires_number(self):
        invoice = self._create_invoice()
        wizard = self._create_wizard(invoice, is_rne=True, rne=False)
        with self.assertRaises(UserError):
            wizard.action_send_to_fne()

    def test_b2f_requires_currency_and_rate(self):
        invoice = self._create_invoice()
        wizard = self._create_wizard(invoice, template='B2F')
        with self.assertRaises(UserError):
            wizard.action_send_to_fne()

    def test_foreign_currency_requires_positive_rate(self):
        invoice = self._create_invoice()
        wizard = self._create_wizard(invoice, foreign_currency='EUR', foreign_currency_rate=0)
        with self.assertRaises(UserError):
            wizard.action_send_to_fne()

    # ------------------------------------------------------------------
    # Envoi effectif
    # ------------------------------------------------------------------
    def test_action_send_to_fne_success(self):
        invoice = self._create_invoice()
        wizard = self._create_wizard(invoice)

        with mocked_fne_post(status_code=200, payload=FNE_SUCCESS_PAYLOAD) as calls:
            action = wizard.action_send_to_fne()

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['url'], 'http://fne.local/ws/external/invoices')
        self.assertEqual(calls[0]['json']['clientNcc'], 'CI123456789')

        self.assertEqual(invoice.fne_state, 'success')
        self.assertEqual(invoice.fne_token, FNE_SUCCESS_PAYLOAD['token'])
        self.assertEqual(invoice.fne_reference, 'FNE-2026-0001')
        self.assertEqual(invoice.fne_ncc, 'CI123456789')
        self.assertEqual(json.loads(invoice.fne_response_data)['status_code'], 200)
        self.assertEqual(action['tag'], 'display_notification')

    def test_action_send_to_fne_sets_error_state_on_api_failure(self):
        invoice = self._create_invoice()
        wizard = self._create_wizard(invoice)

        with mocked_fne_post(status_code=500, text='erreur serveur'):
            action = wizard.action_send_to_fne()

        self.assertEqual(invoice.fne_state, 'error')
        self.assertIn('500', invoice.fne_error_message)
        self.assertEqual(action['params']['type'], 'danger')

    def test_action_send_to_fne_rejects_response_without_token(self):
        invoice = self._create_invoice()
        wizard = self._create_wizard(invoice)

        with mocked_fne_post(status_code=200, payload={'reference': 'R-1'}):
            action = wizard.action_send_to_fne()

        self.assertEqual(invoice.fne_state, 'error')
        self.assertTrue(invoice.fne_error_message)
        self.assertEqual(action['params']['type'], 'danger')
