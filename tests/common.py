import json
from contextlib import contextmanager
from unittest.mock import patch

import requests

from odoo import fields
from odoo.tests.common import TransactionCase

#: Tous les paramètres de configuration gérés par le module FNE.
#: Les tests doivent neutraliser/restaurer ce jeu complet pour ne pas
#: dépendre de la base sur laquelle ils s'exécutent (prod comprise).
FNE_PARAM_KEYS = (
    'module_invoice_fne.api_url',
    'module_invoice_fne.api_key',
    'module_invoice_fne.refund_api_url',
    'module_invoice_fne.amount_unit',
    'module_invoice_fne.establishment',
    'module_invoice_fne.point_of_sale',
    'module_invoice_fne.timeout',
)

#: Réponse HTTP minimale compatible avec l'API utilisée par le module.
FNE_SUCCESS_PAYLOAD = {
    'token': 'http://fne.local/verify/abc123',
    'reference': 'FNE-2026-0001',
    'ncc': 'CI123456789',
    'invoice': {
        'id': '11111111-2222-3333-4444-555555555555',
        'template': 'B2B',
        'clientNcc': 'CI123456789',
        'items': [
            {
                'id': 'aaaaaaaa-1111-1111-1111-111111111111',
                'reference': 'ART-001',
                'description': '[ART-001] Article de test',
                'quantity': 2,
            },
            {
                'id': 'bbbbbbbb-2222-2222-2222-222222222222',
                'reference': 'ART-002',
                'description': '[ART-002] Second article',
                'quantity': 1,
            },
        ],
    },
}


class FakeResponse:
    """Fausse réponse HTTP pour les tests."""

    def __init__(self, status_code=200, payload=None, text=''):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text else json.dumps(payload or {})
        self.headers = {'Content-Type': 'application/json'}
        self.content = self.text.encode()

    def json(self):
        if self._payload is None:
            raise ValueError('Pas de JSON valide')
        return self._payload


@contextmanager
def mocked_fne_post(status_code=200, payload=None, text='', exception=None):
    """Remplace ``requests.post`` et journalise les appels effectués."""
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None, **kwargs):
        calls.append({
            'url': url,
            'json': json,
            'headers': headers or {},
            'timeout': timeout,
        })
        if exception is not None:
            raise exception
        return FakeResponse(status_code, payload, text)

    with patch(
        'odoo.addons.module_invoice_fne_v19.models.account_move.requests.post',
        side_effect=fake_post,
    ):
        yield calls


class FneTestCase(TransactionCase):
    """Base commune : jeu de données minimal et helpers FNE."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.company = cls.env.company
        cls.partner = cls.env['res.partner'].create({
            'name': 'Client FNE',
            'vat': 'CI123456789',
            'email': 'client@example.com',
            'phone': '+225 27 22 47 11 52',
        })
        cls.other_partner = cls.env['res.partner'].create({
            'name': 'Autre Client FNE',
            'vat': 'CI987654321',
            'email': 'autre@example.com',
        })

        cls.tax_18 = cls.env['account.tax'].create({
            'name': 'TVA 18%',
            'amount': 18.0,
            'type_tax_use': 'sale',
        })
        cls.tax_0 = cls.env['account.tax'].create({
            'name': 'TVA 0%',
            'amount': 0.0,
            'type_tax_use': 'sale',
        })

        cls.product_1 = cls.env['product.product'].create({
            'name': 'Article FNE 1',
            'default_code': 'ART-001',
            'list_price': 15000,
            'type': 'consu',
            'taxes_id': [(6, 0, [cls.tax_18.id])],
        })
        cls.product_2 = cls.env['product.product'].create({
            'name': 'Article FNE 2',
            'default_code': 'ART-002',
            'list_price': 12.5,
            'type': 'consu',
            'taxes_id': [(6, 0, [cls.tax_0.id])],
        })

        # Les tests doivent être déterministes quelle que soit la base
        # (une base de prod contient déjà des paramètres FNE configurés).
        # On sauvegarde les valeurs existantes puis on force un jeu de
        # valeurs de test ; `tearDownClass` restaure l'état d'origine.
        cls._fne_param_backup = {}
        params = cls.env['ir.config_parameter'].sudo()
        for key in FNE_PARAM_KEYS:
            cls._fne_param_backup[key] = params.get_param(key)
            params.set_param(key, False)

        params.set_param(
            'module_invoice_fne.api_url', 'http://fne.local/ws/external/invoices')
        params.set_param(
            'module_invoice_fne.api_key', 'test-api-key')
        params.set_param(
            'module_invoice_fne.amount_unit', 'unit')
        params.set_param(
            'module_invoice_fne.timeout', '30')

    @classmethod
    def tearDownClass(cls):
        params = cls.env['ir.config_parameter'].sudo()
        for key, value in (cls._fne_param_backup or {}).items():
            params.set_param(key, value or False)
        super().tearDownClass()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _create_invoice(self, partner=None, lines=None, move_type='out_invoice', ref=None):
        """Crée une facture client (ou fournisseur) minimale."""
        partner = partner or self.partner
        if lines is None:
            lines = [(0, 0, {
                'product_id': self.product_1.id,
                'quantity': 2,
                'price_unit': 15000,
            })]
        vals = {
            'move_type': move_type,
            'partner_id': partner.id,
            'invoice_date': fields.Date.context_today(self.env.user),
            'invoice_line_ids': lines,
        }
        if ref:
            vals['ref'] = ref
        return self.env['account.move'].create(vals)

    def _certify_invoice(self, invoice, payload=None):
        """Marque une facture comme certifiée FNE avec une réponse simulée."""
        payload = payload if payload is not None else FNE_SUCCESS_PAYLOAD
        invoice.write({
            'fne_state': 'success',
            'fne_token': payload.get('token', ''),
            'fne_url': payload.get('token', ''),
            'fne_reference': payload.get('reference', ''),
            'fne_ncc': payload.get('ncc', ''),
            'fne_response_data': json.dumps(payload),
        })
        return invoice

    def _create_refund(self, origin=None, partner=None, lines=None, ref=None):
        """Crée une facture d'avoir."""
        return self._create_invoice(
            partner=partner,
            lines=lines,
            move_type='out_refund',
            ref=ref,
        )
