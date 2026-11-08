import json

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import FNE_SUCCESS_PAYLOAD, FneTestCase, mocked_fne_post


@tagged('post_install', '-at_install')
class TestRefundCertifyWizard(FneTestCase):
    """Tests de l'assistant de certification des factures d'avoir."""

    def _refund_lines(self, with_unknown_product=False):
        lines = [
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
        ]
        if with_unknown_product:
            unknown = self.env['product.product'].create({
                'name': 'Article inconnu FNE',
                'default_code': 'ART-999',
                'list_price': 1000,
                'type': 'consu',
            })
            lines.append((0, 0, {
                'product_id': unknown.id,
                'quantity': 1,
                'price_unit': 1000,
            }))
        return lines

    def _open_wizard(self, refund):
        return self.env['refund.certify.wizard'].with_context(
            default_credit_note_id=refund.id).create({})

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------
    def test_default_get_prepares_article_lines(self):
        origin = self._certify_invoice(self._create_invoice())
        refund = self._create_refund(lines=self._refund_lines())
        refund.reversed_entry_id = origin.id

        wizard = self._open_wizard(refund)

        self.assertEqual(wizard.credit_note_id, refund)
        self.assertEqual(wizard.original_invoice, origin)
        self.assertEqual(wizard.fne_original_invoice_id, FNE_SUCCESS_PAYLOAD['invoice']['id'])
        self.assertEqual(wizard.original_invoice_reference, origin.name)
        self.assertEqual(len(wizard.item_line_ids), 2)

        lines = wizard.item_line_ids.sorted('id')
        self.assertEqual(lines[0].item_id, 'aaaaaaaa-1111-1111-1111-111111111111')
        self.assertEqual(lines[0].item_reference, 'ART-001')
        self.assertEqual(lines[0].original_quantity, 2)
        self.assertEqual(lines[0].new_quantity, 2)
        self.assertTrue(lines[0].is_matched)
        self.assertEqual(lines[1].item_id, 'bbbbbbbb-2222-2222-2222-222222222222')
        self.assertTrue(lines[1].is_matched)

    def test_default_get_warns_when_origin_is_not_certified(self):
        """Une facture d'origine non certifiée ne doit pas bloquer l'ouverture
        du wizard : l'utilisateur peut en choisir une autre."""
        origin = self._create_invoice()
        refund = self._create_refund(lines=self._refund_lines())
        refund.reversed_entry_id = origin.id

        wizard = self._open_wizard(refund)

        self.assertEqual(wizard.original_invoice, origin)
        self.assertTrue(wizard.origin_warning)
        self.assertIn('certifiée', wizard.origin_warning)

    def test_onchange_original_invoice_reloads_article_lines(self):
        """Changer la facture d'origine recharge les articles depuis la FNE."""
        origin = self._certify_invoice(self._create_invoice())
        other_origin = self._certify_invoice(self._create_invoice(), payload={
            'invoice': {
                'id': 'OTHER-ID',
                'items': [{
                    'id': 'OTHER-ITEM',
                    'reference': 'ART-001',
                    'description': '[ART-001] Autre article',
                    'quantity': 7,
                }],
            },
        })
        refund = self._create_refund(lines=[(0, 0, {
            'product_id': self.product_1.id,
            'quantity': 2,
            'price_unit': 15000,
        })])
        refund.reversed_entry_id = origin.id

        wizard = self._open_wizard(refund)
        self.assertEqual(wizard.item_line_ids.item_id, 'aaaaaaaa-1111-1111-1111-111111111111')

        wizard.original_invoice = other_origin
        wizard._onchange_original_invoice()

        self.assertEqual(wizard.item_line_ids.item_id, 'OTHER-ITEM')
        self.assertEqual(wizard.item_line_ids.original_quantity, 7)
        self.assertEqual(wizard.fne_original_invoice_id, 'OTHER-ID')

    def test_onchange_original_invoice_clears_lines_when_emptied(self):
        origin = self._certify_invoice(self._create_invoice())
        refund = self._create_refund(lines=self._refund_lines())
        refund.reversed_entry_id = origin.id

        wizard = self._open_wizard(refund)
        self.assertTrue(wizard.item_line_ids)

        wizard.original_invoice = False
        wizard._onchange_original_invoice()

        self.assertFalse(wizard.item_line_ids)
        self.assertTrue(wizard.origin_warning)

    def test_default_get_warns_when_origin_is_ambiguous(self):
        """Quand plusieurs factures d'origine correspondent, le wizard doit
        rester vide et signaler l'ambiguïté au lieu de choisir au hasard."""
        self._certify_invoice(self._create_invoice())
        self._certify_invoice(self._create_invoice())
        refund = self._create_refund(lines=self._refund_lines())

        wizard = self._open_wizard(refund)

        self.assertFalse(wizard.original_invoice)
        self.assertTrue(wizard.origin_warning)
        self.assertEqual(len(wizard.item_line_ids), 0)

    def test_default_get_raises_on_non_credit_note(self):
        invoice = self._create_invoice()
        with self.assertRaises(UserError):
            self.env['refund.certify.wizard'].with_context(
                default_credit_note_id=invoice.id).create({})

    # ------------------------------------------------------------------
    # Analyse de la réponse FNE
    # ------------------------------------------------------------------
    def test_parse_fne_response_extracts_items_and_id(self):
        origin = self._certify_invoice(self._create_invoice())
        wizard = self.env['refund.certify.wizard']
        data = wizard._parse_fne_response(origin)
        self.assertEqual(data['original_id'], FNE_SUCCESS_PAYLOAD['invoice']['id'])
        self.assertEqual(len(data['items']), 2)

    def test_parse_fne_response_supports_flat_structure(self):
        origin = self._certify_invoice(self._create_invoice())
        origin.fne_response_data = json.dumps({
            'id': 'FLAT-ID',
            'items': [{'id': 'ITEM-1', 'reference': 'ART-001', 'quantity': 3}],
        })
        wizard = self.env['refund.certify.wizard']
        data = wizard._parse_fne_response(origin)
        self.assertEqual(data['original_id'], 'FLAT-ID')
        self.assertEqual(data['items'][0]['id'], 'ITEM-1')

    def test_parse_fne_response_supports_data_structure(self):
        origin = self._certify_invoice(self._create_invoice())
        origin.fne_response_data = json.dumps({
            'data': {
                'id': 'DATA-ID',
                'items': [{'id': 'ITEM-2', 'reference': 'ART-002', 'quantity': 1}],
            },
        })
        wizard = self.env['refund.certify.wizard']
        data = wizard._parse_fne_response(origin)
        self.assertEqual(data['original_id'], 'DATA-ID')
        self.assertEqual(data['items'][0]['id'], 'ITEM-2')

    def test_parse_fne_response_raises_without_data(self):
        origin = self._create_invoice()
        wizard = self.env['refund.certify.wizard']
        with self.assertRaises(UserError):
            wizard._parse_fne_response(origin)

    def test_parse_fne_response_raises_on_invalid_json(self):
        origin = self._certify_invoice(self._create_invoice())
        origin.fne_response_data = '{pas du json'
        wizard = self.env['refund.certify.wizard']
        with self.assertRaises(UserError):
            wizard._parse_fne_response(origin)

    # ------------------------------------------------------------------
    # Appariement des articles
    # ------------------------------------------------------------------
    def test_items_are_matched_by_reference(self):
        origin = self._certify_invoice(self._create_invoice())
        refund = self._create_refund(lines=self._refund_lines())
        refund.reversed_entry_id = origin.id
        wizard = self._open_wizard(refund)
        lines = wizard.item_line_ids.sorted('id')
        self.assertEqual(lines[0].item_reference, 'ART-001')
        self.assertEqual(lines[1].item_reference, 'ART-002')

    def test_items_are_matched_by_keyword_in_description(self):
        """Aucune référence produit ne correspond, mais la description FNE
        contient la référence entre crochets."""
        origin = self._certify_invoice(self._create_invoice(), payload={
            'invoice': {
                'id': 'INV-ID',
                'items': [{
                    'id': 'FNE-1',
                    'reference': '',
                    'description': '[ART-001] Article de test',
                    'quantity': 4,
                }],
            },
        })
        refund = self._create_refund(lines=[(0, 0, {
            'product_id': self.product_1.id,
            'quantity': 3,
            'price_unit': 15000,
        })])
        refund.reversed_entry_id = origin.id
        wizard = self._open_wizard(refund)
        self.assertEqual(wizard.item_line_ids.item_id, 'FNE-1')
        self.assertEqual(wizard.item_line_ids.original_quantity, 4)

    def test_items_are_matched_by_description(self):
        origin = self._certify_invoice(self._create_invoice(), payload={
            'invoice': {
                'id': 'INV-ID',
                'items': [{
                    'id': 'FNE-2',
                    'reference': '',
                    'description': 'Article FNE 1',
                    'quantity': 5,
                }],
            },
        })
        refund = self._create_refund(lines=[(0, 0, {
            'product_id': self.product_1.id,
            'quantity': 2,
            'price_unit': 15000,
        })])
        refund.reversed_entry_id = origin.id
        wizard = self._open_wizard(refund)
        self.assertEqual(wizard.item_line_ids.item_id, 'FNE-2')

    def test_items_are_matched_by_position_as_last_resort(self):
        origin = self._certify_invoice(self._create_invoice(), payload={
            'invoice': {
                'id': 'INV-ID',
                'items': [
                    {'id': 'FNE-A', 'reference': 'X-1', 'description': 'AAA', 'quantity': 1},
                    {'id': 'FNE-B', 'reference': 'X-2', 'description': 'BBB', 'quantity': 2},
                ],
            },
        })
        refund = self._create_refund(lines=self._refund_lines())
        refund.reversed_entry_id = origin.id
        wizard = self._open_wizard(refund)
        lines = wizard.item_line_ids.sorted('id')
        self.assertEqual(lines[0].item_id, 'FNE-A')
        self.assertEqual(lines[1].item_id, 'FNE-B')

    def test_unmatched_item_is_flagged(self):
        origin = self._certify_invoice(self._create_invoice())
        refund = self._create_refund(lines=self._refund_lines(with_unknown_product=True))
        refund.reversed_entry_id = origin.id
        wizard = self._open_wizard(refund)
        unmatched = wizard.item_line_ids.filtered(lambda l: not l.is_matched)
        self.assertEqual(len(unmatched), 1)
        self.assertFalse(unmatched.item_id)

    # ------------------------------------------------------------------
    # Certification effective
    # ------------------------------------------------------------------
    def test_action_certify_refund_success(self):
        origin = self._certify_invoice(self._create_invoice())
        refund = self._create_refund(lines=self._refund_lines())
        refund.reversed_entry_id = origin.id
        wizard = self._open_wizard(refund)

        with mocked_fne_post(status_code=200, payload={
            'token': 'http://fne.local/verify/avoir-1',
            'reference': 'FNE-AVOIR-1',
            'ncc': 'CI123456789',
        }) as calls:
            wizard.action_certify_refund()

        self.assertEqual(len(calls), 1)
        expected_url = (
            'http://fne.local/ws/external/invoices/'
            f'{FNE_SUCCESS_PAYLOAD["invoice"]["id"]}/refund'
        )
        self.assertEqual(calls[0]['url'], expected_url)
        self.assertNotIn('54.247.95.108', calls[0]['url'])

        self.assertEqual(calls[0]['json'], {'items': [
            {'id': 'aaaaaaaa-1111-1111-1111-111111111111', 'quantity': 2},
            {'id': 'bbbbbbbb-2222-2222-2222-222222222222', 'quantity': 1},
        ]})

        self.assertEqual(refund.refund_fne_state, 'success')
        self.assertEqual(refund.refund_fne_reference, 'FNE-AVOIR-1')
        self.assertEqual(refund.refund_fne_token, 'http://fne.local/verify/avoir-1')
        self.assertEqual(
            json.loads(refund.refund_fne_response_data)['status_code'], 200)

    def test_fne_fields_are_not_readonly_on_the_model(self):
        """Les champs FNE de la ligne ne doivent PAS être `readonly` au niveau
        du modèle.

        Le client web Odoo retire du payload d'enregistrement tous les champs
        déclarés `readonly` sur le modèle (voir `record.js::_getChanges`).
        Les identifiants FNE étaient donc perdus à la création du wizard :
        les lignes étaient enregistrées vides et la certification échouait
        avec « les articles ne sont pas identifiables dans la FNE ».
        Le `readonly` est porté par la vue.
        """
        fields_info = self.env['refund.certify.wizard.line'].fields_get([
            'item_id', 'item_name', 'item_reference', 'original_quantity', 'is_matched',
        ])
        for field_name, info in fields_info.items():
            self.assertFalse(
                info.get('readonly'),
                f"Le champ {field_name} ne doit pas être readonly au niveau du modèle",
            )

    def test_wizard_fne_fields_are_not_readonly_on_the_model(self):
        """Même règle pour les champs du wizard lui-même (identifiant FNE de
        la facture d'origine, référence et avertissement) : sinon ils sont
        perdus à la création de l'assistant."""
        fields_info = self.env['refund.certify.wizard'].fields_get([
            'fne_original_invoice_id', 'original_invoice_reference', 'origin_warning',
        ])
        for field_name, info in fields_info.items():
            self.assertFalse(
                info.get('readonly'),
                f"Le champ {field_name} ne doit pas être readonly au niveau du modèle",
            )

    def test_wizard_keeps_the_fne_invoice_id_after_the_client_save(self):
        """L'identifiant FNE de la facture d'origine doit être persisté."""
        origin = self._certify_invoice(self._create_invoice())
        refund = self._create_refund(lines=self._refund_lines())
        refund.reversed_entry_id = origin.id

        wizard = self.env['refund.certify.wizard'].with_context(
            default_credit_note_id=refund.id).create({
                'item_line_ids': [(0, 0, {'new_quantity': 2})],
            })

        self.assertEqual(
            wizard.fne_original_invoice_id, FNE_SUCCESS_PAYLOAD['invoice']['id'])
        self.assertEqual(wizard.original_invoice_reference, origin.name)

    def test_certify_refund_works_with_the_real_web_client_payload(self):
        """Reproduit le payload réellement envoyé par le client web.

        Le client n'envoie que les champs modifiables : les lignes de
        l'assistant sont donc créées sans identifiant FNE. La certification
        doit malgré tout aboutir, les identifiants FNE étant reconstitués
        côté serveur.
        """
        origin = self._certify_invoice(self._create_invoice())
        refund = self._create_refund(lines=self._refund_lines())
        refund.reversed_entry_id = origin.id

        wizard = self.env['refund.certify.wizard'].with_context(
            default_credit_note_id=refund.id).create({
                # Payload réel du client web : uniquement les champs modifiables.
                'item_line_ids': [
                    (0, 0, {'new_quantity': 2}),
                    (0, 0, {'new_quantity': 1}),
                ],
            })

        # Le wizard est bien dans l'état "lignes vides"ersisté en base.
        self.assertEqual(wizard.item_line_ids.mapped('item_id'), [False, False])

        with mocked_fne_post(status_code=200, payload={
            'token': 'http://fne.local/verify/avoir-2',
            'reference': 'FNE-AVOIR-2',
        }) as calls:
            wizard.action_certify_refund()

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['json'], {'items': [
            {'id': 'aaaaaaaa-1111-1111-1111-111111111111', 'quantity': 2},
            {'id': 'bbbbbbbb-2222-2222-2222-222222222222', 'quantity': 1},
        ]})
        self.assertEqual(refund.refund_fne_state, 'success')

        # Les données FNE ont bien été restaurées sur les lignes.
        self.assertTrue(wizard.item_line_ids.mapped('item_id'))
        self.assertTrue(all(wizard.item_line_ids.mapped('is_matched')))

    def test_action_certify_refund_keeps_decimal_quantities(self):
        origin = self._certify_invoice(self._create_invoice(), payload={
            'invoice': {
                'id': 'INV-ID',
                'items': [{
                    'id': 'FNE-1',
                    'reference': 'ART-001',
                    'description': '[ART-001] Article',
                    'quantity': 4,
                }],
            },
        })
        refund = self._create_refund(lines=[(0, 0, {
            'product_id': self.product_1.id,
            'quantity': 1.5,
            'price_unit': 15000,
        })])
        refund.reversed_entry_id = origin.id
        wizard = self._open_wizard(refund)

        with mocked_fne_post(status_code=200, payload={'token': 'T', 'reference': 'R'}) as calls:
            wizard.action_certify_refund()

        self.assertEqual(calls[0]['json']['items'][0]['quantity'], 1.5)

    def test_action_certify_refund_fails_when_an_item_is_not_identified(self):
        """Un article sans identifiant FNE ne doit pas produire une fausse
        certification locale : la facture doit passer en erreur."""
        origin = self._certify_invoice(self._create_invoice())
        refund = self._create_refund(lines=self._refund_lines(with_unknown_product=True))
        refund.reversed_entry_id = origin.id
        wizard = self._open_wizard(refund)

        with mocked_fne_post(status_code=200, payload={'token': 'T'}) as calls:
            action = wizard.action_certify_refund()

        self.assertEqual(len(calls), 0, "Aucun appel API ne doit être fait")
        self.assertEqual(refund.refund_fne_state, 'error')
        self.assertFalse(refund.refund_fne_reference)
        self.assertFalse(
            (refund.refund_fne_reference or '').startswith('LOCAL_'),
            "Aucune certification locale fictive ne doit être enregistrée")
        self.assertEqual(action['params']['type'], 'danger')

    def test_action_certify_refund_requires_origin_invoice(self):
        refund = self._create_refund(lines=self._refund_lines())
        wizard = self.env['refund.certify.wizard'].create({
            'credit_note_id': refund.id,
        })
        with self.assertRaises(UserError):
            wizard.action_certify_refund()

    def test_action_certify_refund_rejects_zero_quantity(self):
        origin = self._certify_invoice(self._create_invoice())
        refund = self._create_refund(lines=self._refund_lines())
        refund.reversed_entry_id = origin.id
        wizard = self._open_wizard(refund)

        wizard.item_line_ids[0].new_quantity = 0

        with mocked_fne_post(status_code=200, payload={'token': 'T'}) as calls:
            action = wizard.action_certify_refund()

        self.assertEqual(len(calls), 0)
        self.assertEqual(refund.refund_fne_state, 'error')
        self.assertEqual(action['params']['type'], 'danger')

    def test_action_certify_refund_records_api_error(self):
        origin = self._certify_invoice(self._create_invoice())
        refund = self._create_refund(lines=self._refund_lines())
        refund.reversed_entry_id = origin.id
        wizard = self._open_wizard(refund)

        with mocked_fne_post(status_code=500, text='boom'):
            action = wizard.action_certify_refund()

        self.assertEqual(refund.refund_fne_state, 'error')
        self.assertIn('500', refund.refund_fne_error_message)
        self.assertEqual(action['params']['type'], 'danger')
