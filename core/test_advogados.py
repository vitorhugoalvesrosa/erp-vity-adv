import json
from io import BytesIO
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.access_control import MANAGER_GROUP, OPERATOR_GROUP, setup_access_groups
from crm.models import Conta
from juridico import djen
from juridico.models import Advogado, Processo, ProcessoEncontrado

CNJ_EXISTENTE = "0001234-56.2025.8.19.0001"
CNJ_NOVO = "5009876-11.2026.4.02.5101"


def item(numero, data="2026-11-10", polo_ativo="Maria Autora", polo_passivo="Banco Réu S.A."):
    return {
        "numero_processo": numero.replace("-", "").replace(".", ""),
        "siglaTribunal": "TJRJ",
        "nomeOrgao": "1ª Vara Cível da Capital",
        "nomeClasse": "Procedimento Comum Cível",
        "data_disponibilizacao": data,
        "link": "https://comunica.pje.jus.br/consulta/abc",
        "destinatarios": [{"nome": polo_ativo, "polo": "A"}, {"nome": polo_passivo, "polo": "P"}],
        "destinatarioadvogados": [{"advogado": {"nome": "Dra. Teste", "numero_oab": "12345", "uf_oab": "RJ"}}],
    }


class Resposta:
    def __init__(self, dados, restante="18"):
        self._corpo = json.dumps(dados).encode()
        self.headers = {"x-ratelimit-remaining": restante}

    def read(self):
        return self._corpo

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class AdvogadosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        groups = setup_access_groups()
        users = get_user_model().objects
        cls.manager = users.create_user("adv-gerente", password="test-password")
        cls.manager.groups.add(groups[MANAGER_GROUP])
        cls.operator = users.create_user("adv-operador", password="test-password")
        cls.operator.groups.add(groups[OPERATOR_GROUP])
        cls.cliente = Conta.objects.create(nome="Cliente OAB", documento="11122233344")
        cls.processo = Processo.objects.create(
            numero_cnj=CNJ_EXISTENTE, titulo="Ação existente", cliente=cls.cliente,
            tribunal="TJRJ", responsavel=cls.manager,
        )
        cls.advogado = Advogado.objects.create(nome="Dra. Teste", oab_numero="12345", oab_uf="RJ")

    def test_cadastro_normaliza_a_oab_e_aceita_parceiro(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("record-create", args=["advogados"]),
            {"nome": "Dr. Parceiro", "oab_numero": "OAB 098.765", "oab_uf": "SP", "tipo": "parceiro", "ativo": "on"},
        )
        self.assertRedirects(response, reverse("record-list", args=["advogados"]), fetch_redirect_response=False)
        parceiro = Advogado.objects.get(nome="Dr. Parceiro")
        self.assertEqual((parceiro.oab_numero, parceiro.tipo, parceiro.oab), ("98765", "parceiro", "OAB/SP 98765"))

    def test_oab_repetida_e_recusada(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("record-create", args=["advogados"]),
            {"nome": "Outra", "oab_numero": "12345", "oab_uf": "RJ", "tipo": "principal"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Advogado.objects.count(), 1)

    def test_busca_vincula_existentes_e_lista_os_novos(self):
        chamadas = []

        def falso_urlopen(request, timeout):
            chamadas.append(request.full_url)
            return Resposta({"count": 3, "items": [item(CNJ_EXISTENTE), item(CNJ_NOVO, "2026-11-01"), item(CNJ_NOVO, "2026-11-20")]})

        with mock.patch.object(djen, "urlopen", falso_urlopen):
            resumo = djen.sincronizar_advogado(self.advogado, dias=90, espera=lambda s: None)

        self.assertEqual(resumo, {"publicacoes": 3, "processos": 2, "vinculados": 1, "novos": 1})
        query = parse_qs(urlparse(chamadas[0]).query)
        self.assertEqual((query["numeroOab"], query["ufOab"]), (["12345"], ["RJ"]))
        self.assertIn(self.advogado, self.processo.advogados.all())
        novo = ProcessoEncontrado.objects.get(numero_cnj=CNJ_NOVO)
        self.assertEqual(novo.situacao, "novo")
        self.assertEqual((novo.total_publicacoes, str(novo.ultima_publicacao)), (2, "2026-11-20"))
        self.assertEqual((novo.polo_ativo, novo.polo_passivo), ("Maria Autora", "Banco Réu S.A."))
        self.assertEqual(ProcessoEncontrado.objects.get(numero_cnj=CNJ_EXISTENTE).processo, self.processo)

    def test_busca_pagina_quando_ha_mais_de_100(self):
        paginas = []

        def falso_urlopen(request, timeout):
            pagina = int(parse_qs(urlparse(request.full_url).query)["pagina"][0])
            paginas.append(pagina)
            quantidade = 100 if pagina == 1 else 20
            return Resposta({"count": 120, "items": [item(CNJ_NOVO)] * quantidade})

        esperas = []
        with mock.patch.object(djen, "urlopen", falso_urlopen):
            itens, total = djen.buscar_publicacoes("12345", "RJ", espera=esperas.append)
        self.assertEqual((paginas, len(itens), total), ([1, 2], 120, 120))
        self.assertEqual(esperas, [djen.INTERVALO])

    def test_bloqueio_fora_do_brasil_vira_mensagem(self):
        erro = HTTPError(djen.URL_OFICIAL, 403, "Forbidden", {}, BytesIO(b"cloudfront"))
        self.client.force_login(self.manager)
        with mock.patch.object(djen, "urlopen", side_effect=erro):
            response = self.client.post(reverse("advogado-buscar-djen", args=[self.advogado.pk]), {"dias": "365"}, follow=True)
        self.assertContains(response, "só atende conexões do Brasil")
        self.advogado.refresh_from_db()
        self.assertIn("Brasil", self.advogado.ultima_busca_mensagem)

    @override_settings(DJEN_BASE_URL="https://proxy.exemplo/api/comunicacao", DJEN_PROXY_TOKEN="segredo")
    def test_usa_o_ponto_de_acesso_configurado(self):
        pedidos = []

        def falso_urlopen(request, timeout):
            pedidos.append(request)
            return Resposta({"count": 0, "items": []})

        with mock.patch.object(djen, "urlopen", falso_urlopen):
            djen.buscar_publicacoes("12345", "RJ")
        self.assertTrue(pedidos[0].full_url.startswith("https://proxy.exemplo/api/comunicacao?"))
        self.assertEqual(pedidos[0].get_header("X-proxy-token"), "segredo")

    def test_cadastrar_encontrado_preenche_e_vincula(self):
        encontrado = ProcessoEncontrado.objects.create(
            numero_cnj=CNJ_NOVO, tribunal="TRF2", orgao="1ª Vara Federal", polo_ativo="Maria", polo_passivo="União"
        )
        encontrado.advogados.add(self.advogado)
        self.client.force_login(self.manager)
        ficha = self.client.get(reverse("advogado-detalhe", args=[self.advogado.pk]))
        self.assertContains(ficha, CNJ_NOVO)
        link = next(i.link_cadastro for i in ficha.context["novos"])
        formulario = self.client.get(link)
        self.assertContains(formulario, f'value="{CNJ_NOVO}"')
        self.assertEqual(formulario.context["form"].initial["advogados"], [str(self.advogado.pk)])

        query = urlparse(link).query
        response = self.client.post(
            f"{reverse('record-create', args=['processos'])}?{query}",
            {
                "numero_cnj": CNJ_NOVO, "titulo": "Maria x União", "cliente": self.cliente.pk, "tribunal": "TRF2",
                "area": "civel", "status": "ativo", "responsavel": self.manager.pk, "valor_causa": "0",
                "advogados": [self.advogado.pk],
            },
        )
        self.assertEqual(response.status_code, 302)
        processo = Processo.objects.get(numero_cnj=CNJ_NOVO)
        self.assertIn(self.advogado, processo.advogados.all())
        encontrado.refresh_from_db()
        self.assertEqual((encontrado.situacao, encontrado.processo), ("vinculado", processo))

    def test_ignorar_e_restaurar(self):
        encontrado = ProcessoEncontrado.objects.create(numero_cnj=CNJ_NOVO)
        encontrado.advogados.add(self.advogado)
        self.client.force_login(self.manager)
        url = reverse("encontrado-situacao", args=[encontrado.pk])
        self.client.post(url, {"acao": "ignorar", "advogado": self.advogado.pk})
        encontrado.refresh_from_db()
        self.assertEqual(encontrado.situacao, "ignorado")
        self.client.post(url, {"acao": "restaurar", "advogado": self.advogado.pk})
        encontrado.refresh_from_db()
        self.assertEqual(encontrado.situacao, "novo")

    def test_operador_ve_a_ficha_mas_nao_busca(self):
        self.client.force_login(self.operator)
        ficha = self.client.get(reverse("advogado-detalhe", args=[self.advogado.pk]))
        self.assertEqual(ficha.status_code, 200)
        self.assertNotContains(ficha, "Buscar processos")
        response = self.client.post(reverse("advogado-buscar-djen", args=[self.advogado.pk]))
        self.assertEqual(response.status_code, 403)

    def test_formatar_cnj(self):
        self.assertEqual(djen.formatar_cnj("00012345620258190001"), CNJ_EXISTENTE)

    @override_settings(DJEN_BASE_URL="  https://proxy.exemplo/api/comunicacao/ \n", DJEN_PROXY_TOKEN=" segredo ")
    def test_espacos_colados_nas_variaveis_sao_ignorados(self):
        pedidos = []

        def falso_urlopen(request, timeout):
            pedidos.append(request)
            return Resposta({"count": 0, "items": []})

        with mock.patch.object(djen, "urlopen", falso_urlopen):
            djen.buscar_publicacoes("12345", "RJ")
        self.assertTrue(pedidos[0].full_url.startswith("https://proxy.exemplo/api/comunicacao?"))
        self.assertEqual(pedidos[0].get_header("X-proxy-token"), "segredo")

    @override_settings(DJEN_BASE_URL="proxy.exemplo/api/comunicacao")
    def test_endereco_sem_https_vira_mensagem_e_nao_erro_500(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse("advogado-buscar-djen", args=[self.advogado.pk]), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "DJEN_BASE_URL está inválido")

    @override_settings(DJEN_BASE_URL="https://proxy.exemplo/api/comunicacao", DJEN_PROXY_TOKEN="errado")
    def test_senha_errada_do_ponto_de_acesso(self):
        erro = HTTPError("https://proxy.exemplo", 401, "Unauthorized", {}, BytesIO(b"{}"))
        self.client.force_login(self.manager)
        with mock.patch.object(djen, "urlopen", side_effect=erro):
            response = self.client.post(reverse("advogado-buscar-djen", args=[self.advogado.pk]), follow=True)
        self.assertContains(response, "recusou a senha")

    def test_falha_inesperada_nao_vira_erro_500(self):
        self.client.force_login(self.manager)
        with mock.patch("core.advogados_views.sincronizar_advogado", side_effect=KeyError("x")), \
                self.assertLogs("core.advogados_views", level="ERROR"):
            response = self.client.post(reverse("advogado-buscar-djen", args=[self.advogado.pk]), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Erro inesperado na busca (KeyError)")
