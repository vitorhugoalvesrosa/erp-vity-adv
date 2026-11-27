from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.access_control import MANAGER_GROUP, OPERATOR_GROUP, setup_access_groups
from core.forms import somar_meses
from financeiro.models import ContaPagar


class ContasPagarTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        groups = setup_access_groups()
        users = get_user_model().objects
        cls.manager = users.create_user("cp-gerente", password="test-password")
        cls.manager.groups.add(groups[MANAGER_GROUP])
        cls.operator = users.create_user("cp-operador", password="test-password")
        cls.operator.groups.add(groups[OPERATOR_GROUP])

    def setUp(self):
        self.hoje = timezone.localdate()

    def conta(self, **extra):
        values = {"descricao": "Aluguel", "valor": Decimal("2500.00"), "data_vencimento": self.hoje}
        values.update(extra)
        return ContaPagar.objects.create(**values)

    def test_cadastro_repete_mensalmente_e_registra_quem_criou(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("record-create", args=["contas-pagar"]),
            {
                "descricao": "Aluguel da sala",
                "favorecido": "Imobiliária Centro",
                "categoria": "aluguel",
                "valor": "2500.00",
                "data_vencimento": "2027-01-31",
                "forma_pagamento": "boleto",
                "repetir": "3",
            },
        )
        self.assertRedirects(response, reverse("contas-pagar"))
        vencimentos = list(ContaPagar.objects.order_by("data_vencimento").values_list("data_vencimento", flat=True))
        self.assertEqual(vencimentos, [date(2027, 1, 31), date(2027, 2, 28), date(2027, 3, 31)])
        self.assertTrue(all(c.criado_por == self.manager for c in ContaPagar.objects.all()))
        self.assertTrue(all(c.status == ContaPagar.Status.A_PAGAR for c in ContaPagar.objects.all()))

    def test_somar_meses_ajusta_fim_do_mes(self):
        self.assertEqual(somar_meses(date(2028, 1, 31), 1), date(2028, 2, 29))
        self.assertEqual(somar_meses(date(2027, 11, 15), 3), date(2028, 2, 15))

    def test_lista_mostra_indicadores_e_situacao(self):
        self.conta(descricao="Contador", data_vencimento=self.hoje - timedelta(days=3), valor=Decimal("800"))
        self.conta(descricao="Internet", data_vencimento=self.hoje + timedelta(days=2), valor=Decimal("150"))
        self.conta(descricao="Luz", status=ContaPagar.Status.PAGO, data_pagamento=self.hoje, valor=Decimal("300"), valor_pago=Decimal("310"))
        self.client.force_login(self.manager)
        response = self.client.get(reverse("contas-pagar"))
        self.assertEqual(response.context["kpis"]["atrasadas"], {"total": Decimal("800"), "quantidade": 1})
        self.assertEqual(response.context["kpis"]["semana"]["quantidade"], 1)
        self.assertEqual(response.context["kpis"]["pagas_mes"]["total"], Decimal("310"))
        self.assertContains(response, "Atrasada")
        self.assertNotContains(response, "Luz")

        pagas = self.client.get(reverse("contas-pagar"), {"f": "pagas"})
        self.assertContains(pagas, "Luz")
        self.assertEqual(pagas.context["total_filtrado"], Decimal("310"))

    def test_filtros_por_categoria_mes_e_busca(self):
        self.conta(descricao="Guia de custas", categoria="custas", data_vencimento=date(2027, 5, 10))
        self.conta(descricao="Aluguel maio", categoria="aluguel", data_vencimento=date(2027, 5, 5))
        self.conta(descricao="Aluguel junho", categoria="aluguel", data_vencimento=date(2027, 6, 5))
        self.client.force_login(self.manager)
        response = self.client.get(reverse("contas-pagar"), {"categoria": "aluguel", "mes": "2027-05"})
        self.assertEqual([c.descricao for c in response.context["contas"]], ["Aluguel maio"])
        busca = self.client.get(reverse("contas-pagar"), {"q": "custas"})
        self.assertEqual([c.descricao for c in busca.context["contas"]], ["Guia de custas"])

    def test_registrar_e_desfazer_pagamento(self):
        conta = self.conta()
        self.client.force_login(self.manager)
        page = self.client.get(reverse("conta-pagar-pagar", args=[conta.pk]))
        self.assertContains(page, "2500")
        self.assertContains(page, f'value="{self.hoje.isoformat()}"')
        response = self.client.post(
            reverse("conta-pagar-pagar", args=[conta.pk]),
            {"data_pagamento": self.hoje.isoformat(), "valor_pago": "2550.00", "forma_pagamento": "pix"},
        )
        self.assertRedirects(response, reverse("contas-pagar"))
        conta.refresh_from_db()
        self.assertEqual((conta.status, conta.valor_pago, conta.forma_pagamento), ("pago", Decimal("2550.00"), "pix"))

        self.client.post(reverse("conta-pagar-estornar", args=[conta.pk]))
        conta.refresh_from_db()
        self.assertEqual(conta.status, ContaPagar.Status.A_PAGAR)
        self.assertIsNone(conta.data_pagamento)
        self.assertIsNone(conta.valor_pago)

    def test_redirecionamento_seguro_apos_pagar(self):
        conta = self.conta()
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("conta-pagar-pagar", args=[conta.pk]),
            {"data_pagamento": self.hoje.isoformat(), "valor_pago": "2500", "forma_pagamento": "pix", "next": "https://exemplo.com/"},
        )
        self.assertRedirects(response, reverse("contas-pagar"))

    def test_edicao_marca_pago_com_data_de_hoje(self):
        conta = self.conta()
        self.client.force_login(self.manager)
        self.client.post(
            reverse("record-edit", args=["contas-pagar", conta.pk]),
            {"descricao": "Aluguel", "categoria": "aluguel", "valor": "2500", "data_vencimento": self.hoje.isoformat(),
             "forma_pagamento": "pix", "status": "pago"},
        )
        conta.refresh_from_db()
        self.assertEqual((conta.status, conta.data_pagamento), ("pago", self.hoje))

    def test_operador_so_visualiza(self):
        conta = self.conta()
        self.client.force_login(self.operator)
        page = self.client.get(reverse("contas-pagar"))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Nova conta a pagar")
        self.assertEqual(self.client.get(reverse("conta-pagar-pagar", args=[conta.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("record-create", args=["contas-pagar"])).status_code, 403)

    def test_tela_antiga_de_pagamentos_saiu_do_menu(self):
        self.client.force_login(self.manager)
        home = self.client.get(reverse("home"))
        self.assertContains(home, reverse("contas-pagar"))
        self.assertContains(home, "A pagar")
        self.assertEqual(self.client.get("/app/pagamentos/").status_code, 404)
