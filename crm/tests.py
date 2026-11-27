import hashlib
import hmac
import json
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from financeiro.models import ContaReceber

from .models import (
    Conta,
    LeadDistributionRule,
    MensagemWhatsApp,
    Proposta,
)
from .services import (
    identificar_tipo_pessoa,
    distribuir_lead,
    qualificar_mensagem,
    responder_com_ia,
)


class CrmWhatsAppTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.seller_one = user_model.objects.create_user(
            username="seller-one",
            password="test-password",
        )
        self.seller_two = user_model.objects.create_user(
            username="seller-two",
            password="test-password",
        )
        LeadDistributionRule.objects.create(
            vendedor=self.seller_one,
            ativo=True,
        )
        LeadDistributionRule.objects.create(
            vendedor=self.seller_two,
            ativo=True,
        )

    def test_identifica_pf_e_pj(self):
        self.assertEqual(
            identificar_tipo_pessoa("123.456.789-09"),
            Conta.TipoPessoa.PF,
        )
        self.assertEqual(
            identificar_tipo_pessoa("12.345.678/0001-90"),
            Conta.TipoPessoa.PJ,
        )

    def test_distribui_leads_em_rota_redonda(self):
        first = Conta.objects.create(
            nome="Lead 1",
            documento="WAPP:550000000001",
            telefone="550000000001",
            origem="whatsapp",
        )
        second = Conta.objects.create(
            nome="Lead 2",
            documento="WAPP:550000000002",
            telefone="550000000002",
            origem="whatsapp",
        )

        distribuir_lead(first)
        distribuir_lead(second)

        self.assertEqual(first.vendedor, self.seller_one)
        self.assertEqual(second.vendedor, self.seller_two)

    def test_qualificacao_incrementa_score_e_identifica_pj(self):
        conta = Conta.objects.create(
            nome="Empresa Lead",
            documento="WAPP:550000000003",
            telefone="550000000003",
            origem="whatsapp",
        )

        qualificar_mensagem(
            conta,
            "Minha empresa precisa urgentemente de um processo. CNPJ 12.345.678/0001-90",
        )

        conta.refresh_from_db()
        self.assertEqual(conta.tipo_pessoa, Conta.TipoPessoa.PJ)
        self.assertGreaterEqual(conta.score_lead, 70)
        self.assertTrue(conta.qualificacao["urgencia"])

    @override_settings(WHATSAPP_APP_SECRET="0123456789abcdef0123456789abcdef")
    def test_webhook_recebe_mensagem_e_registra_conversa(self):
        body = json.dumps(
            {
                "entry": [
                    {
                        "changes": [
                            {
                                "value": {
                                    "messages": [
                                        {
                                            "id": "wamid.test",
                                            "from": "5511999999999",
                                            "text": {"body": "Quero uma consulta urgente"},
                                        }
                                    ]
                                }
                            }
                        ]
                    }
                ]
            }
        ).encode()
        signature = hmac.new(b"0123456789abcdef0123456789abcdef", body, hashlib.sha256).hexdigest()
        response = self.client.post(
            reverse("crm-whatsapp-webhook"),
            data=body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=f"sha256={signature}",
        )

        self.assertEqual(response.status_code, 200)
        conta = Conta.objects.get(telefone="5511999999999")
        self.assertEqual(conta.origem, "whatsapp")
        self.assertEqual(conta.vendedor, self.seller_one)
        self.assertTrue(
            MensagemWhatsApp.objects.filter(
                conversa__conta=conta,
                direcao=MensagemWhatsApp.Direcao.ENTRADA,
            ).exists()
        )


class PropostaParcelamentoTests(TestCase):
    def test_parcelas_30_60_90_somam_o_valor_total(self):
        conta = Conta.objects.create(nome="Cliente", documento="11122233344")
        proposta = Proposta.objects.create(
            conta=conta,
            valor_total=Decimal("100.00"),
            condicao_pagamento=Proposta.CondicaoPagamento.TRINTA_SIXENTA_NOVENTA,
            data_validade=date(2026, 1, 10),
        )

        proposta.status = Proposta.Status.GANHA
        proposta.save()

        valores = list(
            ContaReceber.objects.filter(pedido_venda__proposta=proposta)
            .order_by("data_vencimento")
            .values_list("valor_parcela", flat=True)
        )
        self.assertEqual(
            valores,
            [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")],
        )
        self.assertEqual(sum(valores), Decimal("100.00"))


@override_settings(AI_API_URL="", AI_API_KEY="")
class RespostaPadraoTests(TestCase):
    def test_pede_documento_quando_tipo_pessoa_nao_informado(self):
        resposta = responder_com_ia(
            "Oi",
            {"tipo_pessoa": Conta.TipoPessoa.NAO_INFORMADO},
        )
        self.assertIn("CPF", resposta)

    def test_nao_pede_documento_quando_tipo_pessoa_conhecido(self):
        resposta = responder_com_ia("Oi", {"tipo_pessoa": Conta.TipoPessoa.PF})
        self.assertNotIn("CPF", resposta)
