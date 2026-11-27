import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import ai
from core.access_control import MANAGER_GROUP, OPERATOR_GROUP, setup_access_groups
from core.models import AIConfiguration
from core.secure_storage import encrypt_text

from . import whatsapp
from .forms import WhatsAppConfigurationForm
from .models import Conta, ConversaWhatsApp, MensagemWhatsApp, WhatsAppConfiguration

TEST_KEY = Fernet.generate_key().decode()
APP_SECRET = "0123456789abcdef0123456789abcdef"
TOKEN = "EAAG-token-de-teste-0000000000000000"


def evento(*messages, statuses=(), contacts=()):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "contacts": list(contacts),
                            "messages": list(messages),
                            "statuses": list(statuses),
                        },
                    }
                ]
            }
        ],
    }


def texto(wamid, body, telefone="5521988887777"):
    return {"id": wamid, "from": telefone, "type": "text", "text": {"body": body}}


class FakeGraph:
    """Substitui a Graph API e guarda o que foi enviado."""

    def __init__(self):
        self.calls = []
        self.counter = 0
        self.fail_with = None

    def __call__(self, method, path, cred, *, payload=None, body=None, content_type=None, raw=False):
        self.calls.append({"method": method, "path": path, "payload": payload})
        if self.fail_with:
            raise whatsapp.WhatsAppError(self.fail_with)
        if path.endswith("/media"):
            return {"id": "media-123"}
        if path.endswith("/messages"):
            if payload and payload.get("status") == "read":
                return {"success": True}
            self.counter += 1
            to = payload["to"]
            return {"contacts": [{"wa_id": to}], "messages": [{"id": f"wamid.out{self.counter}"}]}
        return {"display_phone_number": "+55 21 3333-4444", "verified_name": "Escritório Teste"}

    @property
    def sent(self):
        return [c["payload"] for c in self.calls if c["path"].endswith("/messages") and c["payload"].get("type")]


@override_settings(CONFIG_ENCRYPTION_KEY=TEST_KEY)
class WhatsAppBaseTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        groups = setup_access_groups()
        users = get_user_model().objects
        cls.admin = users.create_superuser("wa-admin", "admin@example.com", "test-password")
        cls.manager = users.create_user("wa-gerente", password="test-password", first_name="Ana")
        cls.manager.groups.add(groups[MANAGER_GROUP])
        cls.operator = users.create_user("wa-operador", password="test-password")
        cls.operator.groups.add(groups[OPERATOR_GROUP])

    def setUp(self):
        self.graph = FakeGraph()
        patcher = patch.object(whatsapp, "_graph_request", self.graph)
        patcher.start()
        self.addCleanup(patcher.stop)

    def configure(self, **overrides):
        values = {
            "phone_number_id": "1234567890",
            "business_account_id": "",
            "access_token_encrypted": encrypt_text(TOKEN),
            "access_token_hint": TOKEN[-4:],
            "app_secret_encrypted": encrypt_text(APP_SECRET),
            "verify_token": "verificar-123",
        }
        values.update(overrides)
        return WhatsAppConfiguration.objects.create(**values)

    def post_webhook(self, payload, secret=APP_SECRET):
        body = json.dumps(payload).encode()
        headers = {}
        if secret:
            digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            headers["HTTP_X_HUB_SIGNATURE_256"] = f"sha256={digest}"
        return self.client.post(
            reverse("crm-whatsapp-webhook"), data=body, content_type="application/json", **headers
        )

    def conversa_aberta(self, **extra):
        conta = Conta.objects.create(nome="Maria Cliente", documento="12345678909", telefone="5521988887777")
        values = {"conta": conta, "telefone": "5521988887777", "ultima_entrada_em": timezone.now()}
        values.update(extra)
        return ConversaWhatsApp.objects.create(**values)


class WebhookTests(WhatsAppBaseTestCase):
    def test_verificacao_usa_a_mesma_url_do_webhook(self):
        self.configure()
        url = reverse("crm-whatsapp-webhook")
        ok = self.client.get(url, {"hub.mode": "subscribe", "hub.verify_token": "verificar-123", "hub.challenge": "42"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.content, b"42")
        errado = self.client.get(url, {"hub.mode": "subscribe", "hub.verify_token": "outro", "hub.challenge": "42"})
        self.assertEqual(errado.status_code, 403)

    def test_recusa_eventos_sem_assinatura_valida(self):
        self.configure()
        self.assertEqual(self.post_webhook(evento(texto("wamid.1", "oi")), secret=None).status_code, 403)
        self.assertEqual(self.post_webhook(evento(texto("wamid.1", "oi")), secret="f" * 32).status_code, 403)
        self.assertFalse(MensagemWhatsApp.objects.exists())

    def test_recusa_eventos_quando_nao_ha_chave_secreta(self):
        self.assertEqual(self.post_webhook(evento(texto("wamid.1", "oi")), secret=None).status_code, 403)

    def test_registra_mensagem_com_nome_do_perfil_e_ignora_duplicada(self):
        self.configure(bot_enabled=False)
        payload = evento(
            texto("wamid.1", "Bom dia, preciso de ajuda"),
            contacts=[{"wa_id": "5521988887777", "profile": {"name": "João Silva"}}],
        )
        self.assertEqual(self.post_webhook(payload).status_code, 200)
        self.assertEqual(self.post_webhook(payload).status_code, 200)

        conversa = ConversaWhatsApp.objects.get()
        self.assertEqual(conversa.mensagens.count(), 1)
        self.assertEqual(conversa.conta.nome, "João Silva")
        self.assertEqual(conversa.nome_contato, "João Silva")
        self.assertEqual(conversa.nao_lidas, 1)
        self.assertTrue(conversa.janela_aberta)
        self.assertEqual(conversa.ultima_mensagem_texto, "Bom dia, preciso de ajuda")
        self.assertEqual(self.graph.sent, [])

    def test_mensagem_de_cliente_existente_vai_para_a_conversa_dele(self):
        self.configure(bot_enabled=False)
        conversa = self.conversa_aberta()
        self.post_webhook(evento(texto("wamid.9", "Oi de novo")))
        self.assertEqual(ConversaWhatsApp.objects.count(), 1)
        self.assertEqual(conversa.mensagens.get().texto, "Oi de novo")
        self.assertEqual(Conta.objects.count(), 1)

    def test_registra_midia_recebida(self):
        self.configure(bot_enabled=False)
        message = {
            "id": "wamid.doc",
            "from": "5521988887777",
            "type": "document",
            "document": {"id": "mid-1", "mime_type": "application/pdf", "filename": "rg.pdf", "caption": "Meu RG"},
        }
        self.post_webhook(evento(message))
        mensagem = MensagemWhatsApp.objects.get()
        self.assertEqual(mensagem.tipo, MensagemWhatsApp.Tipo.DOCUMENTO)
        self.assertEqual(mensagem.midia_id, "mid-1")
        self.assertEqual(mensagem.midia_nome, "rg.pdf")
        self.assertEqual(mensagem.texto, "Meu RG")

    def test_status_de_entrega_avanca_sem_voltar(self):
        self.configure()
        conversa = self.conversa_aberta()
        saida = MensagemWhatsApp.objects.create(
            conversa=conversa,
            direcao=MensagemWhatsApp.Direcao.SAIDA,
            provider_message_id="wamid.out",
            status=MensagemWhatsApp.Status.ENVIADA,
            texto="Olá",
        )
        self.post_webhook(evento(statuses=[{"id": "wamid.out", "status": "read"}]))
        self.post_webhook(evento(statuses=[{"id": "wamid.out", "status": "delivered"}]))
        saida.refresh_from_db()
        self.assertEqual(saida.status, MensagemWhatsApp.Status.LIDA)

        self.post_webhook(
            evento(statuses=[{"id": "wamid.out", "status": "failed", "errors": [{"title": "Número inválido"}]}])
        )
        saida.refresh_from_db()
        self.assertEqual(saida.status, MensagemWhatsApp.Status.FALHOU)
        self.assertEqual(saida.erro, "Número inválido")


class BotTests(WhatsAppBaseTestCase):
    def test_sem_ia_envia_boas_vindas_uma_vez_e_passa_para_a_equipe(self):
        self.configure(welcome_message="Bem-vindo ao escritório!")
        self.post_webhook(evento(texto("wamid.1", "Oi")))
        self.post_webhook(evento(texto("wamid.2", "Tem alguém aí?")))

        self.assertEqual([p["text"]["body"] for p in self.graph.sent], ["Bem-vindo ao escritório!"])
        conversa = ConversaWhatsApp.objects.get()
        self.assertFalse(conversa.bot_ativo)
        self.assertEqual(conversa.status, ConversaWhatsApp.Status.AGUARDANDO_VENDEDOR)
        saida = conversa.mensagens.get(direcao=MensagemWhatsApp.Direcao.SAIDA)
        self.assertEqual(saida.status, MensagemWhatsApp.Status.ENVIADA)
        self.assertTrue(saida.gerada_por_ia)

    def test_cliente_pede_atendente(self):
        self.configure(handoff_message="Chamando a equipe.")
        self.post_webhook(evento(texto("wamid.1", "Quero falar com um atendente, por favor")))
        self.assertEqual([p["text"]["body"] for p in self.graph.sent], ["Chamando a equipe."])
        self.assertFalse(ConversaWhatsApp.objects.get().bot_ativo)

    def test_bot_desligado_nao_responde(self):
        self.configure(bot_enabled=False)
        self.post_webhook(evento(texto("wamid.1", "Oi")))
        self.assertEqual(self.graph.sent, [])

    def test_ia_conversa_com_historico_e_transfere(self):
        self.configure()
        AIConfiguration.objects.create(
            provider=AIConfiguration.Provider.ANTHROPIC,
            model="claude-haiku-5-5",
            api_key_encrypted=encrypt_text("sk-ant-api-test-0000000000000000001234"),
            enable_whatsapp=True,
        )
        respostas = [
            {"resposta": "Olá! Qual é o seu nome?", "transferir_para_humano": False},
            {"resposta": "Obrigado, Carla. Vou chamar um advogado.", "transferir_para_humano": True},
        ]
        with patch.object(ai, "_dispatch", side_effect=[ai.AIResult(text=json.dumps(r), data=r) for r in respostas]) as dispatch:
            self.post_webhook(evento(texto("wamid.1", "Fui demitido sem justa causa")))
            self.post_webhook(evento(texto("wamid.2", "Meu nome é Carla")))

        self.assertEqual(
            [p["text"]["body"] for p in self.graph.sent],
            ["Olá! Qual é o seu nome?", "Obrigado, Carla. Vou chamar um advogado."],
        )
        segunda_chamada = dispatch.call_args_list[1].args[4]
        self.assertEqual(
            [m["role"] for m in segunda_chamada], ["user", "assistant", "user"]
        )
        self.assertEqual(segunda_chamada[-1]["content"], "Meu nome é Carla")
        conversa = ConversaWhatsApp.objects.get()
        self.assertFalse(conversa.bot_ativo)
        self.assertEqual(conversa.status, ConversaWhatsApp.Status.AGUARDANDO_VENDEDOR)

    def test_conversa_encerrada_volta_para_o_bot(self):
        self.configure(welcome_message="Bem-vindo!")
        conversa = self.conversa_aberta(status=ConversaWhatsApp.Status.ENCERRADA, bot_ativo=True)
        self.post_webhook(evento(texto("wamid.1", "Voltei")))
        conversa.refresh_from_db()
        self.assertEqual(len(self.graph.sent), 1)
        self.assertEqual(conversa.status, ConversaWhatsApp.Status.AGUARDANDO_VENDEDOR)


class AtendimentoViewsTests(WhatsAppBaseTestCase):
    def setUp(self):
        super().setUp()
        self.configure(bot_enabled=False)

    def test_atendente_responde_e_assume_a_conversa(self):
        conversa = self.conversa_aberta()
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("whatsapp-enviar", args=[conversa.pk]), {"texto": "Olá, Maria!"}, HTTP_X_REQUESTED_WITH="fetch"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Olá, Maria!", response.json()["mensagem"]["html"])
        self.assertEqual(self.graph.sent[-1]["to"], "5521988887777")
        conversa.refresh_from_db()
        self.assertEqual(conversa.atendente, self.manager)
        self.assertFalse(conversa.bot_ativo)
        self.assertEqual(conversa.status, ConversaWhatsApp.Status.AGUARDANDO_CLIENTE)
        mensagem = conversa.mensagens.get()
        self.assertEqual(mensagem.enviada_por, self.manager)
        self.assertEqual(mensagem.status, MensagemWhatsApp.Status.ENVIADA)

    def test_janela_fechada_bloqueia_texto_livre(self):
        conversa = self.conversa_aberta(ultima_entrada_em=timezone.now() - timedelta(hours=25))
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("whatsapp-enviar", args=[conversa.pk]), {"texto": "Oi"}, HTTP_X_REQUESTED_WITH="fetch"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("24 h", response.json()["erro"])
        self.assertEqual(self.graph.sent, [])

    def test_falha_de_envio_fica_registrada(self):
        conversa = self.conversa_aberta()
        self.graph.fail_with = "Token de acesso inválido ou expirado."
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("whatsapp-enviar", args=[conversa.pk]), {"texto": "Oi"}, HTTP_X_REQUESTED_WITH="fetch"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("mensagem", response.json())
        mensagem = conversa.mensagens.get()
        self.assertEqual(mensagem.status, MensagemWhatsApp.Status.FALHOU)
        self.assertIn("Token", mensagem.erro)

    def test_envio_de_arquivo(self):
        conversa = self.conversa_aberta()
        self.client.force_login(self.manager)
        arquivo = SimpleUploadedFile("procuracao.pdf", b"%PDF-1.4 teste", content_type="application/pdf")
        response = self.client.post(
            reverse("whatsapp-enviar", args=[conversa.pk]),
            {"texto": "Segue a procuração", "arquivo": arquivo},
            HTTP_X_REQUESTED_WITH="fetch",
        )
        self.assertEqual(response.status_code, 200)
        enviado = self.graph.sent[-1]
        self.assertEqual(enviado["type"], "document")
        self.assertEqual(enviado["document"], {"id": "media-123", "caption": "Segue a procuração", "filename": "procuracao.pdf"})

    def test_iniciar_conversa_com_modelo(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("whatsapp-nova"),
            {
                "telefone": "(21) 97777-6666",
                "nome": "Pedro Novo",
                "modelo": "retomar_atendimento",
                "idioma": "pt_BR",
                "corpo": "Olá, {{1}}! Podemos falar sobre o seu caso?",
                "parametros": "Pedro",
            },
        )
        conversa = ConversaWhatsApp.objects.get()
        self.assertRedirects(response, reverse("whatsapp-conversa", args=[conversa.pk]), fetch_redirect_response=False)
        enviado = self.graph.sent[-1]
        self.assertEqual(enviado["to"], "5521977776666")
        self.assertEqual(enviado["template"]["name"], "retomar_atendimento")
        self.assertEqual(enviado["template"]["components"][0]["parameters"], [{"type": "text", "text": "Pedro"}])
        self.assertEqual(conversa.conta.nome, "Pedro Novo")
        self.assertEqual(conversa.mensagens.get().texto, "Olá, Pedro! Podemos falar sobre o seu caso?")

    def test_operador_ve_mas_nao_responde(self):
        conversa = self.conversa_aberta(nao_lidas=2)
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(reverse("whatsapp-conversa", args=[conversa.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse("whatsapp-enviar", args=[conversa.pk]), {"texto": "x"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("whatsapp-nova")).status_code, 403)
        conversa.refresh_from_db()
        self.assertEqual(conversa.nao_lidas, 2)

    def test_abrir_conversa_marca_como_lida(self):
        conversa = self.conversa_aberta(nao_lidas=1)
        MensagemWhatsApp.objects.create(conversa=conversa, direcao="entrada", provider_message_id="wamid.in", texto="Oi")
        self.client.force_login(self.manager)
        response = self.client.get(reverse("whatsapp-conversa", args=[conversa.pk]))
        self.assertContains(response, "Maria Cliente")
        conversa.refresh_from_db()
        self.assertEqual(conversa.nao_lidas, 0)
        self.assertEqual(self.graph.calls[-1]["payload"], {"messaging_product": "whatsapp", "status": "read", "message_id": "wamid.in"})

    def test_atualizacoes_e_lista(self):
        conversa = self.conversa_aberta(nao_lidas=1)
        primeira = MensagemWhatsApp.objects.create(conversa=conversa, direcao="entrada", texto="Primeira")
        MensagemWhatsApp.objects.create(conversa=conversa, direcao="entrada", texto="Segunda")
        self.client.force_login(self.operator)
        data = self.client.get(reverse("whatsapp-atualizacoes", args=[conversa.pk]), {"apos": primeira.pk}).json()
        self.assertEqual(len(data["mensagens"]), 1)
        self.assertIn("Segunda", data["mensagens"][0]["html"])
        self.assertTrue(data["janela_aberta"])
        lista = self.client.get(reverse("whatsapp-lista")).json()
        self.assertIn("Maria Cliente", lista["html"])
        self.assertEqual(lista["nao_lidas"], 1)

    def test_acoes_da_conversa(self):
        conversa = self.conversa_aberta()
        self.client.force_login(self.manager)
        url = reverse("whatsapp-acao", args=[conversa.pk])
        self.client.post(url, {"action": "assumir"})
        conversa.refresh_from_db()
        self.assertEqual((conversa.atendente, conversa.bot_ativo), (self.manager, False))
        self.client.post(url, {"action": "encerrar"})
        conversa.refresh_from_db()
        self.assertEqual(conversa.status, ConversaWhatsApp.Status.ENCERRADA)
        self.assertTrue(conversa.bot_ativo)
        self.assertIsNone(conversa.atendente)

    def test_midia_html_nunca_e_exibida_inline(self):
        conversa = self.conversa_aberta()
        mensagem = MensagemWhatsApp.objects.create(
            conversa=conversa, direcao="entrada", tipo="document", midia_id="mid-1", midia_mime="text/html", midia_nome="x.html"
        )
        self.client.force_login(self.operator)
        with patch("crm.inbox_views.baixar_midia", return_value=(b"<script>alert(1)</script>", "text/html")):
            response = self.client.get(reverse("whatsapp-midia", args=[mensagem.pk]))
        self.assertEqual(response["Content-Type"], "application/octet-stream")
        self.assertTrue(response["Content-Disposition"].startswith("attachment"))


class ConfiguracaoTests(WhatsAppBaseTestCase):
    def test_somente_superusuario_configura(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("whatsapp-settings")).status_code, 403)

    def test_salva_credenciais_criptografadas_e_testa(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("whatsapp-settings"),
            {
                "action": "save",
                "phone_number_id": "1234567890",
                "access_token": TOKEN,
                "app_secret": APP_SECRET,
                "is_active": "on",
                "bot_enabled": "on",
                "welcome_message": "Olá",
                "handoff_message": "Já chamo alguém",
                "handoff_keywords": "atendente",
            },
        )
        self.assertRedirects(response, reverse("whatsapp-settings"))
        config = WhatsAppConfiguration.objects.get()
        self.assertNotIn(TOKEN.encode(), bytes(config.access_token_encrypted))
        cred = whatsapp.credenciais()
        self.assertEqual((cred.access_token, cred.app_secret), (TOKEN, APP_SECRET))
        self.assertTrue(config.verify_token)

        self.client.post(reverse("whatsapp-settings"), {"action": "test"})
        config.refresh_from_db()
        self.assertTrue(config.last_test_ok)
        self.assertEqual(config.display_phone_number, "+55 21 3333-4444")

        page = self.client.get(reverse("whatsapp-settings"))
        self.assertContains(page, "/api/crm/whatsapp/webhook/")
        self.assertNotContains(page, TOKEN)

    def test_formulario_exige_token_e_chave_validos(self):
        form = WhatsAppConfigurationForm(
            {"phone_number_id": "12a", "access_token": "curto", "app_secret": "nao-hex", "welcome_message": "x", "handoff_message": "x", "handoff_keywords": "x"}
        )
        self.assertFalse(form.is_valid())
        self.assertEqual(set(form.errors), {"phone_number_id", "access_token", "app_secret"})


class TelefoneTests(TestCase):
    def test_normaliza_numeros_brasileiros(self):
        self.assertEqual(whatsapp.normalizar_telefone("(21) 98888-7777"), "5521988887777")
        self.assertEqual(whatsapp.normalizar_telefone("021 3333-4444"), "552133334444")
        self.assertEqual(whatsapp.normalizar_telefone("+55 11 91234-5678"), "5511912345678")
        self.assertEqual(whatsapp.normalizar_telefone("+1 415 555 0100"), "14155550100")
        self.assertEqual(whatsapp.formatar_telefone("5521988887777"), "+55 (21) 98888-7777")
