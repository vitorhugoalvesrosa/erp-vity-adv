import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from juridico.models import IntegracaoTribunal


class Resposta:
    def __init__(self, dados):
        self._corpo = json.dumps(dados).encode()

    def read(self):
        return self._corpo

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@override_settings(DATAJUD_API_KEY="chave-de-teste")
class SincronizarDataJudTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser("dj-admin", password="test-password")
        cls.integracao = IntegracaoTribunal.objects.create(
            nome="TJRJ DataJud", uf="RJ", tribunal="TJRJ",
            endpoint="https://api-publica.datajud.cnj.jus.br/api_publica_tjrj/_search",
        )

    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(self.admin)
        self.url = f"/api/juridico/integracoes-tribunais/{self.integracao.pk}/sincronizar/"

    def test_numero_formatado_vai_so_com_os_digitos(self):
        enviados = []

        def falso_urlopen(request, timeout):
            enviados.append(json.loads(request.data))
            return Resposta({"hits": {"hits": []}})

        with mock.patch("juridico.views.urllib.request.urlopen", falso_urlopen):
            response = self.api.post(self.url, {"numero_cnj": " 0001234-56.2025.8.19.0001 "}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(enviados[0]["query"]["match"]["numeroProcesso"], "00012345620258190001")

    def test_numero_incompleto_e_recusado(self):
        with mock.patch("juridico.views.urllib.request.urlopen") as urlopen:
            response = self.api.post(self.url, {"numero_cnj": "0001234-56.2025"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("20 dígitos", response.json()["detail"])
        urlopen.assert_not_called()

    def test_numero_vazio_continua_pedindo_o_numero(self):
        response = self.api.post(self.url, {"numero_cnj": "  "}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Informe numero_cnj.")
