import json

import requests

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import FNE_SUCCESS_PAYLOAD, FneTestCase, mocked_fne_post


@tagged('post_install', '-at_install')
class TestAccountMoveFne(FneTestCase):
    """Tests du modèle account.move étendu par le module FNE."""

    def test_action_send_to_fne_opens_the_wizard(self):
        invoice = self._create_invoice()
        action = invoice.action_send_to_fne()
        self.assertEqual(action['res_model'], 'fne.send.wizard')
        self.assertEqual(action['target'], 'new')
        self.assertEqual(action['context']['default_invoice_id'], invoice.id)

    def test_action_certify_refund_requires_credit_note(self):
        invoice = self._create_invoice()
        with self.assertRaises(UserError):
            invoice.action_certify_refund()

    def test_action_certify_refund_opens_the_wizard(self):
        refund = self._create_refund()
        action = refund.action_certify_refund()
        self.assertEqual(action['res_model'], 'refund.certify.wizard')
        self.assertEqual(action['context']['default_credit_note_id'], refund.id)

    def test_action_view_fne_response_requires_stored_data(self):
        invoice = self._create_invoice()
        with self.assertRaises(UserError):
            invoice.action_view_fne_response()

    def test_action_view_fne_response_formats_json(self):
        invoice = self._certify_invoice(self._create_invoice())
        action = invoice.action_view_fne_response()
        self.assertEqual(action['res_model'], 'fne.response.viewer')
        self.assertEqual(action['context']['default_invoice_id'], invoice.id)
        # Le JSON doit être re-indenté pour l'affichage
        self.assertIn('\n', action['context']['default_response_data'])

    # ------------------------------------------------------------------
    # URL de certification d'avoir : plus d'IP codée en dur
    # ------------------------------------------------------------------
    def test_refund_api_url_is_derived_from_configured_base_url(self):
        invoice = self._create_invoice()
        url = invoice._get_refund_api_url('INV-123')
        self.assertEqual(url, 'http://fne.local/ws/external/invoices/INV-123/refund')
        self.assertNotIn('54.247.95.108', url)

    def test_refund_api_url_uses_configured_template(self):
        params = self.env['ir.config_parameter'].sudo()
        params.set_param(
            'module_invoice_fne.refund_api_url',
            'https://fne.example/ws/invoices/{invoice_id}/avoir')
        invoice = self._create_invoice()
        url = invoice._get_refund_api_url('INV-456')
        self.assertEqual(url, 'https://fne.example/ws/invoices/INV-456/avoir')

    def test_refund_api_url_supports_the_documented_id_placeholder(self):
        """La procédure FNE documente le jeton `{id}` pour l'endpoint d'avoir.

        Le gabarit configuré en production utilise `{id}` : il doit être
        substitué, sinon l'API reçoit littéralement `{id}` et répond
        « invalid input syntax for type uuid: "{id}" » (HTTP 500).
        """
        params = self.env['ir.config_parameter'].sudo()
        params.set_param(
            'module_invoice_fne.refund_api_url',
            'http://fne.local/ws/external/invoices/{id}/refund')
        invoice = self._create_invoice()
        url = invoice._get_refund_api_url('4f15d0f8-f943-409d-a48e-0d88f6772036')
        self.assertEqual(
            url,
            'http://fne.local/ws/external/invoices/'
            '4f15d0f8-f943-409d-a48e-0d88f6772036/refund')
        self.assertNotIn('{id}', url)

    def test_refund_api_url_with_unknown_placeholder_raises(self):
        """Un jeton non reconnu doit échouer clairement, pas silencieusement."""
        params = self.env['ir.config_parameter'].sudo()
        params.set_param(
            'module_invoice_fne.refund_api_url',
            'http://fne.local/ws/external/invoices/{facture}/refund')
        invoice = self._create_invoice()
        with self.assertRaises(UserError):
            invoice._get_refund_api_url('INV-789')

    def test_refund_api_url_is_derived_from_the_sign_endpoint(self):
        """L'URL de certification des factures finit par `/invoices/sign`.

        L'endpoint d'avoir est un chemin frère sous `/invoices` :
        `.../invoices/{id}/refund`.
        """
        params = self.env['ir.config_parameter'].sudo()
        params.set_param(
            'module_invoice_fne.api_url',
            'http://fne.local/ws/external/invoices/sign')
        params.set_param('module_invoice_fne.refund_api_url', False)
        invoice = self._create_invoice()
        url = invoice._get_refund_api_url('INV-321')
        self.assertEqual(url, 'http://fne.local/ws/external/invoices/INV-321/refund')

    def test_refund_api_url_without_any_configuration_raises(self):
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('module_invoice_fne.api_url', False)
        invoice = self._create_invoice()
        with self.assertRaises(UserError):
            invoice._get_refund_api_url('INV-789')

    # ------------------------------------------------------------------
    # Appels HTTP
    # ------------------------------------------------------------------
    def test_fne_post_raises_on_http_error(self):
        invoice = self._create_invoice()
        with mocked_fne_post(status_code=500, text='boom'):
            with self.assertRaises(UserError) as ctx:
                invoice._call_fne_api({'invoiceType': 'sale'})
        self.assertIn('500', str(ctx.exception))

    def test_fne_post_raises_on_timeout(self):
        invoice = self._create_invoice()
        with mocked_fne_post(exception=requests.exceptions.Timeout()):
            with self.assertRaises(UserError) as ctx:
                invoice._call_fne_api({'invoiceType': 'sale'})
        self.assertIn('Timeout', str(ctx.exception))

    def test_fne_post_raises_on_connection_error(self):
        invoice = self._create_invoice()
        with mocked_fne_post(exception=requests.exceptions.ConnectionError()):
            with self.assertRaises(UserError) as ctx:
                invoice._call_fne_api({'invoiceType': 'sale'})
        self.assertIn('connecter', str(ctx.exception))

    def test_fne_post_raises_on_invalid_json(self):
        invoice = self._create_invoice()
        with mocked_fne_post(status_code=200, payload=None, text='not json'):
            with self.assertRaises(UserError) as ctx:
                invoice._call_fne_api({'invoiceType': 'sale'})
        self.assertIn('JSON', str(ctx.exception))

    def test_fne_post_sends_bearer_token_and_returns_payload(self):
        invoice = self._create_invoice()
        with mocked_fne_post(status_code=201, payload=FNE_SUCCESS_PAYLOAD) as calls:
            result = invoice._call_fne_api({'invoiceType': 'sale'})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['headers']['Authorization'], 'Bearer test-api-key')
        self.assertEqual(calls[0]['url'], 'http://fne.local/ws/external/invoices')
        self.assertEqual(result['status_code'], 201)
        self.assertEqual(result['reference'], 'FNE-2026-0001')

    def test_fne_post_raises_when_api_key_missing(self):
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('module_invoice_fne.api_key', False)
        invoice = self._create_invoice()
        with self.assertRaises(UserError):
            invoice._call_fne_api({'invoiceType': 'sale'})

    # ------------------------------------------------------------------
    # Recherche de la facture d'origine
    # ------------------------------------------------------------------
    def test_find_original_invoice_uses_reversed_entry_id(self):
        invoice = self._certify_invoice(self._create_invoice())
        refund = self._create_refund()
        refund.reversed_entry_id = invoice.id
        self.assertEqual(refund._find_fne_original_invoice(refund), invoice)

    def test_find_original_invoice_uses_reference(self):
        invoice = self._certify_invoice(self._create_invoice(ref='FNE-REF-1'))
        refund = self._create_refund(ref='FNE-REF-1')
        self.assertEqual(refund._find_fne_original_invoice(refund), invoice)

    def test_find_original_invoice_by_partner_and_amount(self):
        invoice = self._certify_invoice(self._create_invoice())
        refund = self._create_refund()
        self.assertEqual(refund._find_fne_original_invoice(refund), invoice)

    def test_find_original_invoice_returns_empty_when_ambiguous(self):
        """Deux factures d'origine plausibles : aucune ne doit être choisie
        arbitrairement, pour ne pas certifier l'avoir contre la mauvaise
        facture."""
        self._certify_invoice(self._create_invoice())
        self._certify_invoice(self._create_invoice())
        refund = self._create_refund()
        self.assertFalse(refund._find_fne_original_invoice(refund))

    def test_find_original_invoice_returns_empty_when_none(self):
        refund = self._create_refund()
        self.assertFalse(refund._find_fne_original_invoice(refund))

    def test_find_original_invoice_ignores_uncertified_invoices(self):
        self._create_invoice()  # non certifiée
        refund = self._create_refund()
        self.assertFalse(refund._find_fne_original_invoice(refund))

    # ------------------------------------------------------------------
    # Champs FNE
    # ------------------------------------------------------------------
    def test_fne_fields_are_not_copied_on_duplication(self):
        invoice = self._certify_invoice(self._create_invoice())
        duplicate = invoice.copy()
        self.assertFalse(duplicate.fne_token)
        self.assertFalse(duplicate.fne_response_data)
        self.assertEqual(duplicate.fne_state, 'draft')

    def test_format_fne_response_handles_invalid_json(self):
        formatted = self.env['account.move']._format_fne_response('pas du json')
        self.assertEqual(formatted, 'pas du json')
