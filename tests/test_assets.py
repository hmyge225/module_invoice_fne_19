import glob
import os

from odoo.modules.module import get_manifest, get_module_path
from odoo.tests.common import tagged

from .common import FneTestCase

# Nom technique du module, déduit du package de tests. Le même fichier
# fonctionne donc pour les variantes v18 et v19 sans adaptation.
MODULE_NAME = __package__.split('.')[2]


@tagged('post_install', '-at_install')
class TestFneAssets(FneTestCase):
    """Garde-fous sur les assets statiques du module.

    Une feuille de style déclarée dans le manifeste mais absente du disque
    fait échouer la compilation de *tout* le bundle backend d'Odoo
    (« Erreur de style »), ce qui casse l'affichage pour tous les
    utilisateurs. Ces tests vérifient que le fichier existe bien et que
    son contenu n'est pas vide.
    """

    def _declared_assets(self):
        manifest = get_manifest(MODULE_NAME) or {}
        assets = manifest.get('assets') or {}
        return [str(item) for items in assets.values() for item in items]

    def _resolved_stylesheets(self):
        """Feuilles de style FNE déclarées dans le manifeste, résolues sur disque.

        Retourne une liste de tuples ``(déclaration, chemin réel)``.
        """
        module_path = get_module_path(MODULE_NAME)
        if not module_path:
            return []
        found = []
        for declaration in self._declared_assets():
            pattern = declaration.replace(MODULE_NAME, module_path, 1)
            for path in glob.glob(pattern):
                if path.endswith('.css') and os.path.basename(path).startswith('fne'):
                    found.append((declaration, path))
        return found

    def test_manifest_declares_the_stylesheet(self):
        self.assertTrue(
            self._resolved_stylesheets(),
            "Le manifeste doit déclarer la feuille de style fne.css "
            "dans les assets du backend.")

    def test_declared_stylesheet_exists_on_disk(self):
        module_path = get_module_path(MODULE_NAME)
        self.assertTrue(module_path, "Le module %s est introuvable." % MODULE_NAME)

        for declaration in self._declared_assets():
            pattern = declaration.replace(MODULE_NAME, module_path, 1)
            self.assertTrue(
                glob.glob(pattern),
                "Asset déclaré dans le manifeste mais introuvable sur le "
                "disque : %s" % declaration)

        for _declaration, path in self._resolved_stylesheets():
            self.assertGreater(
                os.path.getsize(path), 0,
                "L'asset %s est vide." % path)

    def test_stylesheet_is_resolvable_by_odoo(self):
        """Odoo doit pouvoir résoudre l'asset vers un fichier réel.

        C'est exactement la résolution qui échoue quand le fichier est
        absent : l'asset est alors interprété comme une URL et le bundle
        backend entier échoue (« Erreur de style »).
        """
        paths = self.env['ir.asset']._get_asset_paths(
            'web.assets_backend', {})
        resolved = [path for path, _full_path, _bundle, _modified in paths
                    if path.endswith('fne.css')]
        self.assertTrue(
            resolved,
            "L'asset fne.css n'est pas résolu dans le bundle "
            "web.assets_backend.")
