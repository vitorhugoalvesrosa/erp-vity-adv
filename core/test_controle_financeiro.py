from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook, load_workbook

from core.access_control import MANAGER_GROUP, OPERATOR_GROUP, setup_access_groups
from financeiro.models import CategoriaFinanceira, Lancamento


def planilha_exemplo():
    livro = Workbook()
    folha = livro.active
    folha.title = "Outubro"
    folha.merge_cells("A1:H1")
    folha["A1"] = "BENITES SILVA ADVOGADOS ASSOCIADOS — CONTROLE FINANCEIRO"
    folha.append(
        ["Data", "Tipo", "Categoria", "Descrição / Detalhamento", "Entrada (R$)", "Saída (R$)",
         "Forma de pagamento", "Observações"]
    )
    folha.append(["01/10/2026", "Entrada", "Caixa do mês", "Divisão Escritório mês setembro", "R$ 4.620,67", None, "Pix", ""])
    folha.append([date(2026, 10, 5), "Saída", "Aluguel", "Aluguel sala", None, 2500, "Boleto", "Venc. dia 5"])
    folha.append([date(2026, 10, 7), "Saída", "Cartório", "Autenticações", None, "R$ 80,00", "Cheque", ""])
    folha.append([None, None, None, "TOTAL", 4620.67, 2580, None, None])
    buffer = BytesIO()
    livro.save(buffer)
    return buffer.getvalue()


class ControleFinanceiroTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        groups = setup_access_groups()
        users = get_user_model().objects
        cls.manager = users.create_user("cf-gerente", password="test-password")
        cls.manager.groups.add(groups[MANAGER_GROUP])
        cls.operator = users.create_user("cf-operador", password="test-password")
        cls.operator.groups.add(groups[OPERATOR_GROUP])

    def categoria(self, nome, tipo):
        return CategoriaFinanceira.objects.get(nome=nome, tipo=tipo)

    def test_categorias_da_planilha_ja_cadastradas(self):
        self.assertEqual(self.categoria("Honorários advocatícios", "entrada").sigla, "CR")
        self.assertEqual(self.categoria("Aluguel", "saida").sigla, "CP")
        self.assertEqual(CategoriaFinanceira.objects.count(), 15)

    def test_cadastro_de_lancamento_volta_para_o_controle(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("record-create", args=["lancamentos"]),
            {
                "data": "2026-10-01",
                "tipo": "entrada",
                "categoria": self.categoria("Honorários advocatícios", "entrada").pk,
                "descricao": "Honorários contrato",
                "valor": "1500.00",
                "forma_pagamento": "pix",
                "observacoes": "",
            },
        )
        self.assertRedirects(response, reverse("lancamentos"), fetch_redirect_response=False)
        self.assertEqual(Lancamento.objects.get().valor, Decimal("1500.00"))

    def test_categoria_precisa_ser_do_mesmo_tipo(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("record-create", args=["lancamentos"]),
            {
                "data": "2026-10-01",
                "tipo": "entrada",
                "categoria": self.categoria("Aluguel", "saida").pk,
                "descricao": "Errado",
                "valor": "10",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "é de saída")
        self.assertFalse(Lancamento.objects.exists())

    def test_tela_mostra_totais_e_filtra_por_mes(self):
        aluguel = self.categoria("Aluguel", "saida")
        honorarios = self.categoria("Honorários advocatícios", "entrada")
        Lancamento.objects.create(data=date(2026, 10, 1), tipo="entrada", categoria=honorarios, descricao="A", valor=1000)
        Lancamento.objects.create(data=date(2026, 10, 5), tipo="saida", categoria=aluguel, descricao="B", valor=300)
        Lancamento.objects.create(data=date(2026, 9, 5), tipo="saida", categoria=aluguel, descricao="Setembro", valor=999)
        self.client.force_login(self.operator)
        response = self.client.get(reverse("lancamentos"), {"mes": "2026-10"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["totais"]["saldo"], Decimal("700"))
        self.assertNotContains(response, "Setembro")
        self.assertContains(response, "CONTROLE FINANCEIRO")
        # Operador só consulta: sem botão de novo lançamento nem importação.
        self.assertNotContains(response, "Importar planilha")

    def test_importa_planilha_cria_categoria_e_nao_duplica(self):
        self.client.force_login(self.manager)
        url = reverse("lancamentos-importar")
        arquivo = SimpleUploadedFile("controle.xlsx", planilha_exemplo())
        response = self.client.post(url, {"arquivo": arquivo})
        self.assertRedirects(response, f"{reverse('lancamentos')}?mes=", fetch_redirect_response=False)
        self.assertEqual(Lancamento.objects.count(), 3)
        caixa = Lancamento.objects.get(categoria__nome="Caixa do mês")
        self.assertEqual((caixa.tipo, caixa.valor, caixa.data), ("entrada", Decimal("4620.67"), date(2026, 10, 1)))
        cartorio = Lancamento.objects.get(categoria__nome="Cartório")
        self.assertEqual(cartorio.categoria.tipo, "saida")
        self.assertIn("Forma: Cheque", cartorio.observacoes)
        self.assertEqual(Lancamento.objects.get(categoria__nome="Aluguel").forma_pagamento, "boleto")

        self.client.post(url, {"arquivo": SimpleUploadedFile("controle.xlsx", planilha_exemplo())})
        self.assertEqual(Lancamento.objects.count(), 3)

    def test_importacao_recusa_arquivo_invalido(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("lancamentos-importar"),
            {"arquivo": SimpleUploadedFile("controle.xlsx", b"nao e excel")},
            follow=True,
        )
        self.assertContains(response, "Não foi possível abrir o arquivo")
        self.assertFalse(Lancamento.objects.exists())

    def test_operador_nao_importa(self):
        self.client.force_login(self.operator)
        response = self.client.post(
            reverse("lancamentos-importar"), {"arquivo": SimpleUploadedFile("c.xlsx", planilha_exemplo())}
        )
        self.assertEqual(response.status_code, 403)

    def test_exporta_excel_no_formato_da_planilha(self):
        Lancamento.objects.create(
            data=date(2026, 10, 1), tipo="entrada", categoria=self.categoria("Caixa do mês", "entrada"),
            descricao="Divisão", valor=Decimal("4620.67"),
        )
        self.client.force_login(self.operator)
        response = self.client.get(reverse("lancamentos-exportar"), {"mes": "2026-10"})
        self.assertEqual(response.status_code, 200)
        folha = load_workbook(BytesIO(response.content)).active
        self.assertIn("CONTROLE FINANCEIRO", folha["A1"].value)
        self.assertEqual(folha["E2"].value, "Entrada (R$)")
        self.assertEqual(folha["E3"].value, 4620.67)

    def test_excluir_categoria_em_uso_mostra_aviso(self):
        aluguel = self.categoria("Aluguel", "saida")
        Lancamento.objects.create(data=date(2026, 10, 5), tipo="saida", categoria=aluguel, descricao="B", valor=1)
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("record-delete", args=["categorias-financeiras", aluguel.pk]), follow=True
        )
        self.assertContains(response, "Não é possível excluir")
        self.assertTrue(CategoriaFinanceira.objects.filter(pk=aluguel.pk).exists())

    def test_edicao_mostra_a_data_salva(self):
        lancamento = Lancamento.objects.create(
            data=date(2026, 10, 1), tipo="saida", categoria=self.categoria("Luz", "saida"), descricao="Luz", valor=1
        )
        self.client.force_login(self.manager)
        response = self.client.get(reverse("record-edit", args=["lancamentos", lancamento.pk]))
        # O <input type="date"> só aceita AAAA-MM-DD; em dd/mm/aaaa a data some na edição.
        self.assertContains(response, 'value="2026-10-01"')
