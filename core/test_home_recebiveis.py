from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.access_control import MANAGER_GROUP, setup_access_groups
from crm.models import Conta, Proposta
from financeiro.models import ContaReceber, PedidoVenda


class CartoesRecebiveisTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        groups = setup_access_groups()
        cls.manager = get_user_model().objects.create_user("home-gerente", password="test-password")
        cls.manager.groups.add(groups[MANAGER_GROUP])
        hoje = timezone.localdate()
        conta = Conta.objects.create(nome="Cliente cartões", documento="44455566677")
        proposta = Proposta.objects.create(
            conta=conta, valor_total=Decimal("1000"), condicao_pagamento="a_vista", data_validade=hoje
        )
        pedido = PedidoVenda.objects.create(proposta=proposta, valor_final=Decimal("1000"), data_faturamento=hoje)
        ContaReceber.objects.filter(pedido_venda=pedido).delete()

        def parcela(valor, dias, status="pendente"):
            ContaReceber.objects.create(
                conta=conta, pedido_venda=pedido, valor_parcela=Decimal(valor),
                data_vencimento=hoje + timedelta(days=dias), status_pagamento=status,
            )

        parcela("100", 5)                # no prazo
        parcela("200", -3)               # vencida
        parcela("50", 2, "atrasado")     # marcada como atrasada
        parcela("300", -10, "pago")      # recebida
        parcela("400", 1, "pago")        # recebida

    def cartoes(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("home"))
        return response, {stat["label"]: stat for stat in response.context["stats"]}

    def test_separa_no_prazo_atrasado_e_recebido(self):
        response, stats = self.cartoes()
        self.assertEqual(stats["A receber"]["value"], Decimal("100"))
        atrasado = stats["Recebimentos atrasados"]
        self.assertEqual((atrasado["value"], atrasado["variant"]), (Decimal("250"), "danger"))
        self.assertEqual(atrasado["note"], "2 parcelas vencidas")
        recebido = stats["Recebido"]
        self.assertEqual((recebido["value"], recebido["variant"]), (Decimal("700"), "success"))
        self.assertContains(response, "stat-card-danger")
        self.assertContains(response, "stat-card-success")

    def test_sem_atraso_o_cartao_nao_fica_vermelho(self):
        ContaReceber.objects.exclude(status_pagamento="pago").update(
            status_pagamento="pendente", data_vencimento=timezone.localdate() + timedelta(days=30)
        )
        response, stats = self.cartoes()
        atrasado = stats["Recebimentos atrasados"]
        self.assertEqual((atrasado["value"], atrasado["variant"]), (0, ""))
        self.assertEqual(atrasado["note"], "Nenhuma parcela atrasada")
        self.assertNotContains(response, "stat-card-danger")
