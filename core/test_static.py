import re
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase

STATIC_TAG = re.compile(r"""\{%\s*static\s+['"]([^'"]+)['"]\s*%\}""")
CSS_URL = re.compile(r"""url\(\s*['"]?(?!data:|https?:|#)([^'")]+)['"]?\s*\)""")


class StaticReferencesTests(SimpleTestCase):
    """Em produção o ManifestStaticFilesStorage dá erro 500 se um arquivo citado não existir."""

    def test_templates_so_citam_estaticos_existentes(self):
        base = Path(settings.BASE_DIR)
        faltando = []
        for template in base.glob("**/templates/**/*.html"):
            if ".venv" in template.parts or "site-packages" in template.parts:
                continue
            for caminho in STATIC_TAG.findall(template.read_text(encoding="utf-8")):
                if not finders.find(caminho):
                    faltando.append(f"{template.relative_to(base)}: {caminho}")
        self.assertEqual(faltando, [])

    def test_css_so_cita_arquivos_existentes(self):
        css = finders.find("core/app.css")
        pasta = Path(css).parent
        faltando = [
            caminho
            for caminho in CSS_URL.findall(Path(css).read_text(encoding="utf-8"))
            if not (pasta / caminho).exists()
        ]
        self.assertEqual(faltando, [])
