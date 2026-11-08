from odoo.tests.common import tagged

from .common import FneTestCase


@tagged('post_install', '-at_install')
class TestResConfigSettingsFne(FneTestCase):
    """Tests des réglages du module FNE."""

    def test_settings_store_the_api_configuration(self):
        settings = self.env['res.config.settings'].create({
            'fne_api_url': 'https://fne.example/ws/external/invoices',
            'fne_api_key': 'super-secret',
            'fne_establishment': 'MA SOCIETE',
            'fne_point_of_sale': 'CAISSE 1',
            'fne_amount_unit': 'centime',
        })
        settings.execute()

        params = self.env['ir.config_parameter'].sudo()
        self.assertEqual(
            params.get_param('module_invoice_fne.api_url'),
            'https://fne.example/ws/external/invoices')
        self.assertEqual(params.get_param('module_invoice_fne.api_key'), 'super-secret')
        self.assertEqual(params.get_param('module_invoice_fne.establishment'), 'MA SOCIETE')
        self.assertEqual(params.get_param('module_invoice_fne.point_of_sale'), 'CAISSE 1')
        self.assertEqual(params.get_param('module_invoice_fne.amount_unit'), 'centime')

    def test_settings_store_the_refund_url_template(self):
        settings = self.env['res.config.settings'].create({
            'fne_refund_api_url': 'https://fne.example/ws/{invoice_id}/avoir',
        })
        settings.execute()
        params = self.env['ir.config_parameter'].sudo()
        self.assertEqual(
            params.get_param('module_invoice_fne.refund_api_url'),
            'https://fne.example/ws/{invoice_id}/avoir')

    def test_amount_unit_defaults_to_unit(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'module_invoice_fne.amount_unit', False)
        wizard = self.env['fne.send.wizard']
        self.assertEqual(
            wizard._fne_param('module_invoice_fne.amount_unit', 'unit'), 'unit')
