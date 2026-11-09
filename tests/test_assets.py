import glob
import os

from odoo.modules.module import get_module_path
from odoo.tests.common import tagged

from .common import FneTestCase

# Nom technique réel du module, déduit du package de tests. Le même fichier
# fonctionne donc pour les variantes v18 et v19 sans adaptation.
MODULE_NAME = __package__.split('.')[2]


@tagged('post_install', '-at_install')
class TestFneAssets(FneTestCase):
    """Garde-fous sur les assets statiques du module.

    La feuille de style est déclarée en base (``ir.asset``) avec un chemin
    calculé à partir du nom réel du dossier de l'addon : si ce chemin ne
    correspond pas au dossier réellement déployé, Odoo ne le résout pas et
    la compilation du bundle backend entier échoue (« Erreur de style », puis
    erreur 500 sur toutes les pages). Ces tests vérifient que la
    déclaration suit bien le nom du module et que le fichier existe.
    """

    def _asset_records(self):
        return self.env['ir.asset'].sudo().search([
            ('bundle', '=', 'web.assets_backend'),
            ('path', 'like', '/module_invoice_fne%/static/src/css/*.css'),
        ])

    def _expected_path(self):
        return '/%s/static/src/css/*.css' % MODULE_NAME

    def test_asset_record_declares_the_stylesheet(self):
        records = self._asset_records()
        self.assertTrue(
            records,
            "Aucune déclaration ir.asset pour la feuille de style du module.")
        self.assertEqual(
            records.mapped('path'),
            [self._expected_path()],
            "La déclaration doit pointer vers le nom réel du module "
            "(%s), sinon Odoo ne résout pas le chemin." % MODULE_NAME)

    def test_declared_stylesheet_exists_on_disk(self):
        module_path = get_module_path(MODULE_NAME)
        self.assertTrue(module_path, "Le module %s est introuvable." % MODULE_NAME)

        pattern = self._expected_path().replace(MODULE_NAME, module_path, 1)
        matches = glob.glob(pattern)
        self.assertTrue(
            matches,
            "Feuille de style déclarée mais introuvable sur le disque : %s"
            % pattern)
        for path in matches:
            self.assertGreater(
                os.path.getsize(path), 0,
                "L'asset %s est vide." % path)

    def test_stylesheet_is_resolvable_by_odoo(self):
        """Odoo doit pouvoir résoudre l'asset vers un fichier réel."""
        paths = self.env['ir.asset']._get_asset_paths(
            'web.assets_backend', {})
        resolved = [path for path, _full_path, _bundle, _modified in paths
                    if path.endswith('fne.css')]
        self.assertTrue(
            resolved,
            "L'asset fne.css n'est pas résolu dans le bundle "
            "web.assets_backend.")

    def test_module_icon_resolves(self):
        module = self.env['ir.module.module'].sudo().search(
            [('name', '=', MODULE_NAME)], limit=1)
        self.assertTrue(module, "Module %s introuvable." % MODULE_NAME)
        icon = module.icon or ''
        self.assertTrue(
            icon,
            "L'icône du module doit être renseignée.")
        module_path = get_module_path(MODULE_NAME)
        path = icon.replace(MODULE_NAME, module_path, 1)
        self.assertTrue(
            os.path.isfile(path),
            "L'icône %s est introuvable sur le disque." % path)
