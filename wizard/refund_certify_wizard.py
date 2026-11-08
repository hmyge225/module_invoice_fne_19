import json
import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

#: Préfixe des identifiants d'article générés localement (non fournis par la FNE).
TEMP_ID_PREFIX = 'temp_'


class RefundCertifyWizard(models.TransientModel):
    _name = 'refund.certify.wizard'
    _description = "Assistant de certification des factures d'avoir"

    credit_note_id = fields.Many2one('account.move', string="Facture d'avoir", required=True)

    #: Facture d'origine retenue pour la certification. Sélectionnable par
    #: l'utilisateur lorsque la détection automatique est ambiguë.
    original_invoice = fields.Many2one(
        'account.move', string="Facture d'origine",
        domain="[('move_type', '=', 'out_invoice'), ('fne_state', '=', 'success')]",
        help="Facture client certifiée FNE sur laquelle porte cet avoir.",
    )

    #: Identifiant de la facture d'origine côté FNE (UUID renvoyé par l'API).
    #: `readonly` porté par la vue : le client web Odoo retire du payload
    #: d'enregistrement les champs `readonly` du modèle, ce qui ferait
    #: perdre cet identifiant à la création du wizard.
    fne_original_invoice_id = fields.Char(string="ID FNE de la facture d'origine")
    original_invoice_reference = fields.Char(string="Référence facture d'origine")

    #: Message d'avertissement affiché quand la facture d'origine n'a pas pu
    #: être déterminée automatiquement.
    origin_warning = fields.Text(string='Avertissement')

    item_line_ids = fields.One2many('refund.certify.wizard.line', 'wizard_id', string='Articles')

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)

        credit_note_id = self.env.context.get('default_credit_note_id')
        if not credit_note_id:
            return res

        credit_note = self.env['account.move'].browse(credit_note_id)
        if not credit_note or credit_note.move_type != 'out_refund':
            raise UserError("Cette action n'est disponible que pour les factures d'avoir")

        original_invoice = credit_note._find_fne_original_invoice(credit_note)
        if not original_invoice:
            res.update({
                'credit_note_id': credit_note.id,
                'origin_warning': (
                    "Aucune facture d'origine certifiée FNE n'a pu être déterminée "
                    "automatiquement pour cet avoir. Sélectionnez la facture client "
                    "certifiée correspondante ou faites-la certifier à la FNE avant "
                    "de certifier cet avoir."
                ),
            })
            return res

        if original_invoice.fne_state != 'success':
            res.update({
                'credit_note_id': credit_note.id,
                'original_invoice': original_invoice.id,
                'origin_warning': (
                    f"La facture d'origine {original_invoice.name} n'est pas certifiée "
                    "FNE. Sélectionnez une autre facture d'origine certifiée."
                ),
            })
            return res

        if not original_invoice.fne_response_data:
            res.update({
                'credit_note_id': credit_note.id,
                'original_invoice': original_invoice.id,
                'origin_warning': (
                    f"Aucune donnée de réponse FNE n'est disponible pour la facture "
                    f"{original_invoice.name}. La facture d'origine doit être certifiée "
                    "à la FNE avant de pouvoir certifier cet avoir."
                ),
            })
            return res

        fne_data = self._parse_fne_response(original_invoice)
        if not fne_data:
            raise UserError(
                "Impossible de parser les données de réponse FNE de la facture d'origine"
            )

        res.update({
            'credit_note_id': credit_note.id,
            'original_invoice': original_invoice.id,
            'fne_original_invoice_id': fne_data.get('original_id', ''),
            'original_invoice_reference': original_invoice.name,
        })

        item_lines = self._prepare_item_lines(fne_data, credit_note)
        if not item_lines:
            raise UserError("Aucune ligne d'article valide trouvée pour la certification")

        res['item_line_ids'] = [(0, 0, line) for line in item_lines]
        return res

    @api.onchange('original_invoice')
    def _onchange_original_invoice(self):
        """Recharge les articles lorsque la facture d'origine est modifiée."""
        if not self.original_invoice:
            self.item_line_ids = [(5, 0, 0)]
            self.fne_original_invoice_id = False
            self.original_invoice_reference = False
            self.origin_warning = (
                "Sélectionnez la facture client certifiée FNE sur laquelle porte cet avoir."
            )
            return

        origin = self.original_invoice
        if origin.fne_state != 'success':
            raise UserError(
                f"La facture d'origine {origin.name} n'est pas certifiée FNE."
            )
        if not origin.fne_response_data:
            raise UserError(
                f"Aucune donnée de réponse FNE trouvée pour la facture {origin.name}. "
                "La facture d'origine doit être certifiée d'abord."
            )

        fne_data = self._parse_fne_response(origin)
        item_lines = self._prepare_item_lines(fne_data, self.credit_note_id)

        self.item_line_ids = [(5, 0, 0)] + [(0, 0, line) for line in item_lines]
        self.fne_original_invoice_id = fne_data.get('original_id', '')
        self.original_invoice_reference = origin.name
        self.origin_warning = False

    # ------------------------------------------------------------------
    # Recherche de la facture d'origine
    # ------------------------------------------------------------------
    def _find_original_invoice(self, credit_note):
        """Trouve la facture d'origine liée à cette facture d'avoir."""
        return credit_note._find_fne_original_invoice(credit_note)

    # ------------------------------------------------------------------
    # Analyse de la réponse FNE
    # ------------------------------------------------------------------
    def _parse_fne_response(self, original_invoice):
        """Parse la réponse FNE stockée pour extraire les informations nécessaires."""
        if not original_invoice.fne_response_data:
            raise UserError(
                f"Aucune donnée de réponse FNE trouvée pour la facture {original_invoice.name}. "
                "La facture d'origine doit être certifiée d'abord."
            )

        try:
            fne_data = json.loads(original_invoice.fne_response_data)
        except ValueError as exc:
            raise UserError(f"Erreur lors du parsing de la réponse FNE : {exc}")

        if not isinstance(fne_data, dict):
            raise UserError("Réponse FNE inattendue : le corps JSON n'est pas un objet.")

        items, original_id = self._extract_items(fne_data)
        if not items:
            raise UserError(
                f"Aucun article trouvé dans la réponse FNE de la facture "
                f"{original_invoice.name}. Structure : {list(fne_data.keys())}"
            )

        valid_items = []
        for item in items:
            item_id = (item.get('id') or '').strip()
            if item_id:
                valid_items.append(item)
            else:
                _logger.warning("Article FNE sans identifiant, ignoré : %s", item)

        if not valid_items:
            raise UserError(
                "Aucun article avec un identifiant FNE valide n'a été trouvé dans la réponse"
            )

        if not original_id:
            original_id = original_invoice.fne_reference or original_invoice.name

        return {
            'original_id': original_id,
            'reference': original_invoice.name,
            'items': valid_items,
        }

    @api.model
    def _extract_items(self, fne_data):
        """Extrait les articles et l'identifiant de facture des différentes
        structures possibles de la réponse FNE."""
        for path in (('invoice', 'items'), ('items',), ('data', 'items'), ('response', 'invoice', 'items')):
            node = fne_data
            for key in path:
                if not isinstance(node, dict):
                    node = None
                    break
                node = node.get(key)
            if isinstance(node, list) and node:
                original_id = None
                for key_path in (('invoice', 'id'), ('id',), ('data', 'id'), ('response', 'invoice', 'id')):
                    id_node = fne_data
                    for key in key_path:
                        if not isinstance(id_node, dict):
                            id_node = None
                            break
                        id_node = id_node.get(key)
                    if id_node:
                        original_id = id_node
                        break
                return node, original_id
        return [], None

    # ------------------------------------------------------------------
    # Construction des lignes d'articles
    # ------------------------------------------------------------------
    def _prepare_item_lines(self, fne_data, credit_note):
        """Prépare les lignes d'articles pour le wizard avec les vrais IDs FNE."""
        fne_items = fne_data.get('items', [])
        if not fne_items:
            raise UserError("Aucun article trouvé dans la réponse FNE de la facture d'origine")

        credit_lines = credit_note.invoice_line_ids.filtered(lambda l: l.product_id)
        if not credit_lines:
            raise UserError("Aucun article trouvé dans la facture d'avoir")

        matches = self._match_items_with_fne(credit_lines, fne_items)

        lines = []
        for index, credit_line in enumerate(credit_lines):
            fne_item = matches.get(index)
            if fne_item:
                fne_item_id = (fne_item.get('id') or '').strip()
                fne_description = (fne_item.get('description') or '').strip()
                fne_reference = (fne_item.get('reference') or '').strip()
                fne_quantity = float(fne_item.get('quantity') or 0)
            else:
                fne_item_id = ''
                fne_description = ''
                fne_reference = ''
                fne_quantity = 0.0

            line_data = {
                'item_id': fne_item_id,
                'item_name': fne_description or credit_line.name or credit_line.product_id.name,
                'item_reference': fne_reference or credit_line.product_id.default_code or "",
                'original_quantity': fne_quantity or abs(float(credit_line.quantity)),
                'new_quantity': abs(float(credit_line.quantity)),
                'is_matched': bool(fne_item_id),
            }
            if not fne_item_id:
                _logger.warning(
                    "Aucun article FNE correspondant pour la ligne %s (%s) : la ligne "
                    "ne pourra pas être certifiée.",
                    index + 1, credit_line.product_id.default_code or credit_line.name,
                )
            lines.append(line_data)

        return lines

    @api.model
    def _match_items_with_fne(self, credit_lines, fne_items):
        """Associe chaque ligne d'avoir à un article FNE.

        Stratégies, de la plus fiable à la moins fiable :
        1. égalité de la référence produit ;
        2. présence de la référence entre crochets dans la description FNE ;
        3. description partiellement commune.

        La correspondance purement positionnelle n'est utilisée qu'en dernier
        recours, et uniquement si aucune ligne n'a pu être appariée par les
        stratégies 1 à 3 : elle risquerait sinon d'associer un mauvais
        identifiant FNE à un article.
        """
        matched_indexes = {}
        used_indexes = set()

        for index, credit_line in enumerate(credit_lines):
            match = self._find_fne_item(credit_line, fne_items, used_indexes)
            if match is not None:
                fne_index = match[0]
                matched_indexes[index] = fne_index
                used_indexes.add(fne_index)

        unmatched = [i for i in range(len(credit_lines)) if i not in matched_indexes]
        unused = [i for i in range(len(fne_items)) if i not in used_indexes]

        if unmatched and len(unmatched) == len(unused) and not matched_indexes:
            _logger.info(
                "Appariement positionnel des articles FNE utilisé pour %s ligne(s).",
                len(unmatched),
            )
            for credit_index, fne_index in zip(unmatched, unused):
                matched_indexes[credit_index] = fne_index

        return {index: fne_items[fne_index] for index, fne_index in matched_indexes.items()}

    @api.model
    def _find_fne_item(self, credit_line, fne_items, used_indexes):
        """Cherche l'article FNE correspondant à une ligne d'avoir."""
        reference = (credit_line.product_id.default_code or '').strip()

        # 1. Référence produit identique
        if reference:
            for fne_index, fne_item in enumerate(fne_items):
                if fne_index in used_indexes:
                    continue
                if (fne_item.get('reference') or '').strip() == reference:
                    return (fne_index, fne_item)

        # 2. Référence encadrée dans la description FNE
        if reference:
            token = f'[{reference.lower()}]'
            for fne_index, fne_item in enumerate(fne_items):
                if fne_index in used_indexes:
                    continue
                if token in (fne_item.get('description') or '').lower():
                    return (fne_index, fne_item)

        # 3. Description partiellement commune
        if credit_line.name:
            credit_desc = credit_line.name.strip().lower()
            for fne_index, fne_item in enumerate(fne_items):
                if fne_index in used_indexes:
                    continue
                fne_desc = (fne_item.get('description') or '').strip().lower()
                if not fne_desc:
                    continue
                if credit_desc in fne_desc or fne_desc in credit_desc:
                    return (fne_index, fne_item)

        return None

    # ------------------------------------------------------------------
    # Certification
    # ------------------------------------------------------------------
    def action_save_and_certify(self):
        """Sauvegarde les lignes puis certifie"""
        self.ensure_one()
        for line in self.item_line_ids:
            line.write({
                'item_id': line.item_id,
                'item_name': line.item_name,
                'item_reference': line.item_reference,
                'original_quantity': line.original_quantity,
                'new_quantity': line.new_quantity,
                'is_matched': line.is_matched,
            })
        return self.action_certify_refund()

    def _get_article_display_name(self, line):
        """Récupère le nom d'affichage d'un article de manière robuste"""
        if line.item_name and line.item_reference:
            if line.item_name.startswith(f"[{line.item_reference}]"):
                return line.item_name
            return f"[{line.item_reference}] {line.item_name}"
        if line.item_reference:
            return f"[{line.item_reference}]"
        if line.item_name:
            return line.item_name
        return f"Article {line.id}"

    @api.model
    def _format_refund_quantity(self, quantity):
        """Quantité transmise à l'API FNE pour un avoir (valeur positive)."""
        rounded = round(abs(float(quantity)), 3)
        return int(rounded) if rounded == int(rounded) else rounded

    def action_certify_refund(self):
        """Certifie la facture d'avoir via l'API FNE."""
        self.ensure_one()

        credit_note = self.credit_note_id
        original_invoice = self.original_invoice

        if not original_invoice:
            raise UserError(
                "La facture d'origine n'est pas déterminée. Indiquez la facture client "
                "certifiée FNE correspondante avant de certifier cet avoir."
            )

        if original_invoice.fne_state != 'success':
            raise UserError(
                f"La facture d'origine {original_invoice.name} n'est pas certifiée FNE."
            )

        credit_note.write({
            'refund_fne_state': 'pending',
            'refund_fne_error_message': False,
        })

        try:
            valid_items, unmatched = self._collect_valid_items()
            if unmatched:
                raise UserError(
                    "Les articles suivants de la facture d'origine ne sont pas "
                    "identifiables dans la FNE et ne peuvent pas être certifiés :\n• "
                    + "\n• ".join(unmatched)
                )

            fne_invoice_id = self._get_fne_invoice_id(original_invoice)
            api_data = {'items': valid_items}
            api_url = credit_note._get_refund_api_url(fne_invoice_id)

            _logger.info(
                "Certification d'avoir %s : POST %s avec %s article(s)",
                credit_note.name, api_url, len(valid_items),
            )

            response = credit_note._call_refund_api(api_url, api_data)

            if response.get('status_code') in (200, 201):
                credit_note.write({
                    'refund_fne_state': 'success',
                    'refund_fne_url': response.get('token', ''),
                    'refund_fne_reference': response.get('reference', ''),
                    'refund_fne_ncc': response.get('ncc', ''),
                    'refund_fne_token': response.get('token', ''),
                    'refund_fne_response_data': json.dumps(response),
                    'refund_fne_error_message': False,
                })

                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Succès FNE',
                        'message': (
                            'Facture d\'avoir certifiée avec succès ! '
                            'Le lien de vérification est disponible sur la facture.'
                        ),
                        'type': 'success',
                        'sticky': False,
                        'next': {
                            'type': 'ir.actions.act_window_close',
                        },
                    },
                }

            error_msg = response.get('error') or "Erreur inconnue de l'API FNE"
            credit_note.write({
                'refund_fne_state': 'error',
                'refund_fne_error_message': error_msg,
            })

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Erreur API FNE',
                    'message': f"Erreur lors de la certification : {error_msg}",
                    'type': 'danger',
                    'sticky': True,
                },
            }

        except Exception as exc:
            _logger.error("Erreur lors de la certification de l'avoir %s : %s", credit_note.name, exc)
            credit_note.write({
                'refund_fne_state': 'error',
                'refund_fne_error_message': str(exc),
            })

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Erreur',
                    'message': f"Erreur lors de la certification : {exc}",
                    'type': 'danger',
                    'sticky': True,
                },
            }

    def _resolve_fne_item_ids(self):
        """Détermine l'identifiant FNE de chaque ligne de l'assistant.

        Retourne un dictionnaire ``{index de ligne: identifiant FNE}``.

        - l'identifiant enregistré sur la ligne est prioritaire, à condition
          qu'il soit effectivement connu de la facture d'origine ;
        - sinon il est recalculé à partir de la réponse FNE de la facture
          d'origine et des lignes de la facture d'avoir, puis réécrit sur la
          ligne de l'assistant.

        Ce recalcul est un filet de sécurité : le client web Odoo retire du
        payload d'enregistrement les champs déclarés `readonly` au niveau du
        modèle, ce qui pouvait laisser les lignes sans identifiant FNE.
        """
        origin = self.original_invoice
        wizard_lines = self.item_line_ids
        resolved = {}

        fne_items = []
        fne_data = None
        if origin and origin.fne_response_data:
            try:
                fne_data = self._parse_fne_response(origin)
                fne_items = fne_data.get('items', [])
            except UserError:
                fne_items = []

        known_ids = {(item.get('id') or '').strip() for item in fne_items}
        known_ids.discard('')

        for index, line in enumerate(wizard_lines):
            item_id = (line.item_id or '').strip()
            if item_id and (not known_ids or item_id in known_ids):
                resolved[index] = item_id

        missing_indexes = [i for i in range(len(wizard_lines)) if i not in resolved]
        if not missing_indexes or fne_data is None:
            return resolved

        credit_lines = self.credit_note_id.invoice_line_ids.filtered(lambda l: l.product_id)
        if len(wizard_lines) != len(credit_lines):
            # Des lignes ont été ajoutées ou supprimées dans l'assistant :
            # l'alignement par position ne serait plus fiable.
            _logger.warning(
                "Résolution des identifiants FNE impossible : %s ligne(s) dans "
                "l'assistant pour %s ligne(s) sur la facture d'avoir.",
                len(wizard_lines), len(credit_lines),
            )
            return resolved

        matches = self._match_items_with_fne(credit_lines, fne_items)
        for index in missing_indexes:
            fne_item = matches.get(index)
            if not fne_item:
                continue
            fne_item_id = (fne_item.get('id') or '').strip()
            if not fne_item_id:
                continue
            credit_line = credit_lines[index]
            wizard_lines[index].write({
                'item_id': fne_item_id,
                'item_name': (fne_item.get('description') or '').strip()
                             or credit_line.name or credit_line.product_id.name,
                'item_reference': (fne_item.get('reference') or '').strip()
                                  or credit_line.product_id.default_code or '',
                'original_quantity': float(fne_item.get('quantity') or 0),
                'is_matched': True,
            })
            resolved[index] = fne_item_id
            _logger.info(
                "Identifiant FNE résolu pour la ligne %s : %s", index + 1, fne_item_id
            )

        return resolved

    def _collect_valid_items(self):
        """Sépare les lignes exploitables des lignes sans identifiant FNE."""
        valid_items = []
        unmatched = []

        fne_item_ids = self._resolve_fne_item_ids()

        for index, line in enumerate(self.item_line_ids):
            item_id = fne_item_ids.get(index, '')

            if not item_id:
                unmatched.append(self._get_article_display_name(line))
                continue

            if line.new_quantity == 0:
                raise UserError(
                    f"Article '{self._get_article_display_name(line)}' avec quantité nulle (0). "
                    "Vous ne pouvez pas certifier un article avec une quantité de 0."
                )

            if line.new_quantity < 0:
                raise UserError(
                    f"Article '{self._get_article_display_name(line)}' avec quantité négative : "
                    f"{line.new_quantity}"
                )

            valid_items.append({
                'id': item_id,
                'quantity': self._format_refund_quantity(line.new_quantity),
            })

        return valid_items, unmatched

    def _get_fne_invoice_id(self, original_invoice):
        """Récupère l'identifiant FNE de la facture d'origine."""
        fne_invoice_id = (self.fne_original_invoice_id or '').strip()
        if fne_invoice_id:
            return fne_invoice_id

        try:
            fne_data = json.loads(original_invoice.fne_response_data or '{}')
        except ValueError:
            fne_data = {}

        _, original_id = self._extract_items(fne_data)
        if not original_id:
            original_id = original_invoice.fne_reference or original_invoice.name

        if not original_id:
            raise UserError(
                "ID de facture FNE manquant dans les données de la facture d'origine"
            )
        return original_id

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------
    def action_check_lines(self):
        """Vérifie l'état des lignes et diagnostique le problème"""
        self.ensure_one()

        lines_in_db = self.env['refund.certify.wizard.line'].search([('wizard_id', '=', self.id)])
        _logger.info(
            "Wizard %s : %s ligne(s) en base, %s ligne(s) dans le wizard",
            self.id, len(lines_in_db), len(self.item_line_ids),
        )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Vérification Lignes',
                'message': (
                    f"Wizard : {self.id} — Lignes en base : {len(lines_in_db)}, "
                    f"Lignes dans le wizard : {len(self.item_line_ids)}"
                ),
                'type': 'info',
                'sticky': True,
            },
        }

    def action_test_mapping(self):
        """Affiche le mapping entre les lignes de l'avoir et les articles FNE"""
        self.ensure_one()

        original_invoice = self.original_invoice or self._find_original_invoice(self.credit_note_id)
        if not original_invoice:
            raise UserError("Aucune facture d'origine trouvée")

        _logger.info("=== TEST MAPPING POUR FACTURE D'AVOIR %s ===", self.credit_note_id.name)
        _logger.info("Facture d'origine : %s (FNE : %s)", original_invoice.name, original_invoice.fne_state)

        for line in self.credit_note_id.invoice_line_ids:
            _logger.info(
                "Ligne %s | Produit : %s | Ref : %s | Qté : %s",
                line.name, line.product_id.name, line.product_id.default_code, line.quantity,
            )

        if original_invoice.fne_response_data:
            fne_data = self._parse_fne_response(original_invoice)
            for item in fne_data.get('items', []):
                _logger.info(
                    "Article FNE | ID : %s | Ref : %s | Desc : %s | Qté : %s",
                    item.get('id'), item.get('reference'), item.get('description'),
                    item.get('quantity'),
                )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Test Mapping',
                'message': (
                    f"Vérifiez les logs pour les détails du mapping. "
                    f"Facture d'origine : {original_invoice.name}"
                ),
                'type': 'info',
                'sticky': False,
            },
        }

    def action_diagnose_fne_data(self):
        """Analyse avancée des données FNE de la facture d'origine"""
        self.ensure_one()

        original_invoice = self.original_invoice or self._find_original_invoice(self.credit_note_id)
        if not original_invoice:
            raise UserError("Aucune facture d'origine trouvée")

        _logger.info("=== DIAGNOSTIC FNE AVANCÉ ===")
        _logger.info("Facture d'origine : %s (FNE : %s)", original_invoice.name, original_invoice.fne_state)

        if not original_invoice.fne_response_data:
            _logger.error("Aucune donnée de réponse FNE trouvée")
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Diagnostic FNE',
                    'message': "Aucune donnée de réponse FNE sur la facture d'origine.",
                    'type': 'warning',
                    'sticky': False,
                },
            }

        try:
            fne_data = json.loads(original_invoice.fne_response_data)
        except ValueError as exc:
            _logger.error("Erreur JSON : %s", exc)
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Diagnostic FNE',
                    'message': f"Réponse FNE illisible : {exc}",
                    'type': 'danger',
                    'sticky': False,
                },
            }

        _logger.info("Structure complète FNE : %s", json.dumps(fne_data, indent=2, ensure_ascii=False))
        items, _original_id = self._extract_items(fne_data)
        _logger.info("%s article(s) trouvé(s) dans la réponse FNE", len(items))

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Diagnostic FNE',
                'message': (
                    f"Diagnostic terminé : {len(items)} article(s) dans la réponse FNE. "
                    "Vérifiez les logs pour le détail."
                ),
                'type': 'info',
                'sticky': False,
            },
        }


class RefundCertifyWizardLine(models.TransientModel):
    _name = 'refund.certify.wizard.line'
    _description = "Ligne d'article pour certification avoir"

    # NOTE IMPORTANTE sur les `readonly` :
    # le client web Odoo filtre du payload d'enregistrement tous les champs
    # déclarés `readonly` au niveau du MODÈLE (voir `record.js::_getChanges`).
    # Les données FNE de ces lignes étaient donc perdues à la création du
    # wizard : la ligne était enregistrée sans identifiant FNE, sans
    # description et sans quantité d'origine.
    # Le `readonly` est donc porté par la VUE (`readonly="1"`), ce qui garde
    # l'affichage non modifiable tout en transmettant les valeurs au serveur.
    # `new_quantity` reste le seul champ réellement modifiable.

    wizard_id = fields.Many2one(
        'refund.certify.wizard', string='Wizard', required=True, ondelete='cascade')
    item_id = fields.Char(
        string='ID FNE',
        help="Identifiant unique de l'article dans le système FNE")
    item_name = fields.Text(string='Description',
                            help="Description complète de l'article")
    item_reference = fields.Char(string='Référence', help="Référence produit")
    original_quantity = fields.Float(
        string='Quantité Originale', digits='FNE Quantité Article')
    new_quantity = fields.Float(
        string='Quantité Avoir', required=True, digits='FNE Quantité Article',
        help="Quantité à certifier pour cette facture d'avoir")
    is_matched = fields.Boolean(
        string='Identifié FNE',
        help="Coché si l'article a pu être apparié à un article de la FNE.")

    @api.onchange('new_quantity')
    def _onchange_new_quantity(self):
        """Validation de la quantité saisie"""
        if self.new_quantity < 0:
            return {
                'warning': {
                    'title': 'Attention',
                    'message': 'La quantité ne peut pas être négative.',
                }
            }
        if self.new_quantity == 0:
            return {
                'warning': {
                    'title': 'Quantité invalide',
                    'message': (
                        'La quantité ne peut pas être égale à 0. Vous ne pouvez pas '
                        'certifier un article avec une quantité nulle.'
                    ),
                }
            }
        if self.new_quantity > self.original_quantity:
            return {
                'warning': {
                    'title': 'Attention',
                    'message': (
                        f'La quantité saisie ({self.new_quantity}) est supérieure à la '
                        f'quantité originale ({self.original_quantity}).'
                    ),
                }
            }
