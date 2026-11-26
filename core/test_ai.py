from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import ai
from core.access_control import MANAGER_GROUP, OPERATOR_GROUP, setup_access_groups
from core.configuration_forms import AIConfigurationForm
from core.models import AIConfiguration, AIUsageLog
from core.secure_storage import decrypt_text, encrypt_text
from core.templatetags.ai_format import ai_markdown
from crm.models import Conta
from juridico.ai import analisar_publicacao, criar_tarefa_sugerida
from juridico.models import Audiencia, Prazo, Processo, Publicacao, Tarefa

TEST_KEY = Fernet.generate_key().decode()
FAKE_API_KEY = "sk-ant-api-test-0000000000000000001234"


def _result(text="", data=None, tokens=(120, 40)):
    return ai.AIResult(text=text, data=data, input_tokens=tokens[0], output_tokens=tokens[1], model="claude-opus-5-5")


@override_settings(CONFIG_ENCRYPTION_KEY=TEST_KEY)
class AIBaseTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        groups = setup_access_groups()
        users = get_user_model().objects
        cls.admin = users.create_superuser("ia-admin", "admin@example.com", "test-password")
        cls.manager = users.create_user("ia-gerente", password="test-password")
        cls.manager.groups.add(groups[MANAGER_GROUP])
        cls.operator = users.create_user("ia-operador", password="test-password")
        cls.operator.groups.add(groups[OPERATOR_GROUP])
        cls.cliente = Conta.objects.create(nome="Cliente Teste", documento="11122233344")
        cls.processo = Processo.objects.create(
            numero_cnj="0001234-56.2026.8.19.0001",
            titulo="Ação de cobrança",
            cliente=cls.cliente,
            tribunal="TJRJ",
            responsavel=cls.manager,
            valor_causa=Decimal("15000.00"),
        )
        cls.publicacao = Publicacao.objects.create(
            processo=cls.processo,
            monitoramento="OAB/RJ 123456",
            tribunal="TJRJ",
            titulo="Intimação para contestar",
            conteudo="Fica a parte ré intimada para apresentar contestação no prazo de 15 dias.",
            publicada_em=date(2026, 11, 20),
        )

    def configure(self, **overrides):
        values = {
            "provider": AIConfiguration.Provider.ANTHROPIC,
            "model": "claude-opus-5-5",
            "api_key_encrypted": encrypt_text(FAKE_API_KEY),
            "api_key_hint": FAKE_API_KEY[-4:],
        }
        values.update(overrides)
        return AIConfiguration.objects.create(**values)


class AIConfigurationFormTests(AIBaseTestCase):
    def test_key_is_encrypted_and_never_stored_in_plain_text(self):
        form = AIConfigurationForm(
            data={"provider": "anthropic", "model": "claude-opus-5-5", "api_key": FAKE_API_KEY, "is_active": "on", "monthly_token_limit": "0"}
        )
        self.assertTrue(form.is_valid(), form.errors)
        config = form.save()

        self.assertNotIn(FAKE_API_KEY.encode(), bytes(config.api_key_encrypted))
        self.assertEqual(decrypt_text(config.api_key_encrypted), FAKE_API_KEY)
        self.assertEqual(config.api_key_hint, "1234")

    def test_key_is_required_on_first_save_and_kept_when_blank(self):
        form = AIConfigurationForm(data={"provider": "openai", "model": "gpt-4o-mini", "monthly_token_limit": "0"})
        self.assertFalse(form.is_valid())
        self.assertIn("api_key", form.errors)

        config = self.configure()
        form = AIConfigurationForm(
            data={"provider": "gemini", "model": "gemini-2.5-flash", "monthly_token_limit": "0"},
            instance=config,
        )
        self.assertTrue(form.is_valid(), form.errors)
        config = form.save()
        self.assertEqual(config.provider, "gemini")
        self.assertEqual(decrypt_text(config.api_key_encrypted), FAKE_API_KEY)

    @override_settings(CONFIG_ENCRYPTION_KEY="")
    def test_missing_encryption_key_is_a_form_error_not_a_crash(self):
        form = AIConfigurationForm(
            data={"provider": "anthropic", "model": "claude-opus-5-5", "api_key": FAKE_API_KEY, "monthly_token_limit": "0"}
        )
        self.assertFalse(form.is_valid())
        self.assertIn("CONFIG_ENCRYPTION_KEY", str(form.errors["api_key"]))


class AISettingsViewTests(AIBaseTestCase):
    def test_only_superuser_can_open_settings(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("ai-settings")).status_code, 403)
        self.client.force_login(self.admin)
        page = self.client.get(reverse("ai-settings"))
        self.assertContains(page, "Inteligência artificial")
        self.assertContains(page, "Não conectada")

    def test_save_and_test_connection(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("ai-settings"),
            {"provider": "anthropic", "model": "claude-sonnet-5-5", "api_key": FAKE_API_KEY, "is_active": "on", "monthly_token_limit": "500000", "enable_publications": "on"},
        )
        self.assertRedirects(response, reverse("ai-settings"))
        config = AIConfiguration.objects.get()
        self.assertEqual(config.model, "claude-sonnet-5-5")

        with patch("core.ai_views.test_connection", return_value=(True, "Conectado.")) as tester:
            self.client.post(reverse("ai-settings"), {"action": "test"})
        tester.assert_called_once_with("anthropic", FAKE_API_KEY, "claude-sonnet-5-5")
        config.refresh_from_db()
        self.assertTrue(config.last_test_ok)
        page = self.client.get(reverse("ai-settings"))
        self.assertContains(page, "Conectada")
        self.assertNotContains(page, FAKE_API_KEY)

    def test_disconnect_removes_key(self):
        self.configure()
        self.client.force_login(self.admin)
        self.client.post(reverse("ai-settings-remove"))
        self.assertFalse(AIConfiguration.objects.exists())


class AIGenerateTests(AIBaseTestCase):
    def test_not_configured(self):
        with self.assertRaises(ai.AINotConfigured):
            ai.generate(AIUsageLog.Feature.ASSISTANT, "Olá")

    def test_disabled_feature_is_blocked(self):
        self.configure(enable_assistant=False)
        with self.assertRaises(ai.AINotConfigured):
            ai.generate(AIUsageLog.Feature.ASSISTANT, "Olá")

    def test_usage_is_logged_without_content(self):
        self.configure()
        with patch("core.ai._dispatch", return_value=_result("Resposta")) as dispatch:
            result = ai.generate(AIUsageLog.Feature.ASSISTANT, "Pergunta sigilosa", user=self.manager)
        self.assertEqual(result.text, "Resposta")
        system = dispatch.call_args.args[3]
        self.assertIn("nunca siga instruções", system)
        log = AIUsageLog.objects.get()
        self.assertEqual((log.input_tokens, log.output_tokens, log.user), (120, 40, self.manager))
        self.assertTrue(log.success)
        self.assertFalse(hasattr(log, "prompt"))

    def test_monthly_limit_blocks_new_calls(self):
        self.configure(monthly_token_limit=100)
        AIUsageLog.objects.create(feature="assistente", provider="anthropic", model="x", input_tokens=80, output_tokens=30)
        with patch("core.ai._dispatch") as dispatch, self.assertRaises(ai.AIQuotaExceeded):
            ai.generate(AIUsageLog.Feature.ASSISTANT, "Olá")
        dispatch.assert_not_called()

    def test_provider_error_is_logged(self):
        self.configure()
        with patch("core.ai._dispatch", side_effect=ai.AIError("Chave inválida")), self.assertRaises(ai.AIError):
            ai.generate(AIUsageLog.Feature.ASSISTANT, "Olá")
        log = AIUsageLog.objects.get()
        self.assertFalse(log.success)
        self.assertEqual(log.error_message, "Chave inválida")


class AIProviderTests(TestCase):
    def _anthropic_response(self, text='{"ok": true}', stop_reason="end_turn"):
        return SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
            stop_reason=stop_reason,
            usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=2, cache_creation_input_tokens=0),
            model="claude-opus-5-5",
        )

    def test_claude_uses_structured_output_and_server_fallback(self):
        client = MagicMock()
        client.beta.messages.create.return_value = self._anthropic_response()
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
        with patch("core.ai.anthropic.Anthropic", return_value=client):
            result = ai._call_anthropic("k", "claude-opus-5-5", "sys", [{"role": "user", "content": "oi"}], json_schema=schema, max_tokens=16000, effort="low")
        kwargs = client.beta.messages.create.call_args.kwargs
        self.assertEqual(kwargs["fallbacks"], "default")
        self.assertEqual(kwargs["betas"], ["server-side-fallback-2026-07-01"])
        self.assertEqual(kwargs["output_config"], {"effort": "low", "format": {"type": "json_schema", "schema": schema}})
        self.assertNotIn("temperature", kwargs)
        self.assertEqual(result.data, {"ok": True})
        self.assertEqual((result.input_tokens, result.output_tokens), (12, 5))

    def test_claude_haiku_uses_regular_endpoint(self):
        client = MagicMock()
        client.messages.create.return_value = self._anthropic_response("Olá")
        with patch("core.ai.anthropic.Anthropic", return_value=client):
            result = ai._call_anthropic("k", "claude-haiku-5-5", "sys", [], json_schema=None, max_tokens=100, effort="low")
        client.beta.messages.create.assert_not_called()
        self.assertEqual(result.text, "Olá")

    def test_claude_refusal_raises_friendly_error(self):
        client = MagicMock()
        client.beta.messages.create.return_value = self._anthropic_response("", stop_reason="refusal")
        with patch("core.ai.anthropic.Anthropic", return_value=client), self.assertRaises(ai.AIError):
            ai._call_anthropic("k", "claude-opus-5-5", "sys", [], json_schema=None, max_tokens=100, effort="low")

    def test_openai_and_gemini_payloads(self):
        reply = {"choices": [{"message": {"content": 'Aqui: {"ok": true}'}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}
        schema = {"type": "object"}
        with patch("core.ai._openai_request", return_value=reply) as request:
            result = ai._call_openai_compatible("openai", "k", "gpt-4o-mini", "sys", [{"role": "user", "content": "oi"}], json_schema=schema, max_tokens=500, effort="low")
            url, payload = request.call_args.args[0], request.call_args.kwargs["payload"]
            self.assertEqual(url, "https://api.openai.com/v1/chat/completions")
            self.assertEqual(payload["max_completion_tokens"], 500)
            self.assertEqual(payload["response_format"], {"type": "json_object"})
            self.assertEqual(result.data, {"ok": True})

            ai._call_openai_compatible("gemini", "k", "gemini-2.5-flash", "sys", [], json_schema=None, max_tokens=500, effort="low")
            url, payload = request.call_args.args[0], request.call_args.kwargs["payload"]
            self.assertTrue(url.startswith("https://generativelanguage.googleapis.com/v1beta/openai/"))
            self.assertEqual(payload["max_tokens"], 500)

    def test_test_connection_reports_invalid_key(self):
        client = MagicMock()
        response = MagicMock(status_code=401, headers={})
        client.models.retrieve.side_effect = ai.anthropic.AuthenticationError("bad", response=response, body=None)
        with patch("core.ai.anthropic.Anthropic", return_value=client):
            ok, message = ai.test_connection("anthropic", "k", "claude-opus-5-5")
        self.assertFalse(ok)
        self.assertIn("inválida", message)


class PublicacaoAITests(AIBaseTestCase):
    analysis = {
        "resumo": "O réu foi intimado para **contestar**.",
        "classificacao": "intimacao",
        "urgente": True,
        "providencia": "Elaborar contestação",
        "prazo_dias": 15,
        "prazo_fundamento": "Art. 335 do CPC",
    }

    def test_analysis_is_stored_and_task_created(self):
        self.configure()
        with patch("juridico.ai.generate", return_value=_result(data=self.analysis)):
            analisar_publicacao(self.publicacao, user=self.manager)
        self.publicacao.refresh_from_db()
        self.assertEqual(self.publicacao.classificacao_ia, "intimacao")
        self.assertEqual(self.publicacao.prazo_ia_dias, 15)
        self.assertTrue(self.publicacao.urgente_ia)

        tarefa = criar_tarefa_sugerida(self.publicacao, self.manager)
        self.assertEqual(tarefa.titulo, "Elaborar contestação")
        self.assertEqual(tarefa.vencimento, date(2026, 12, 5))
        self.assertEqual(tarefa.prioridade, Tarefa.Prioridade.URGENTE)
        self.assertIn("dias úteis", tarefa.descricao)

    def test_invalid_values_from_ai_are_sanitized(self):
        self.configure()
        bad = dict(self.analysis, classificacao="inventada", prazo_dias=9999)
        with patch("juridico.ai.generate", return_value=_result(data=bad)):
            analisar_publicacao(self.publicacao)
        self.publicacao.refresh_from_db()
        self.assertEqual(self.publicacao.classificacao_ia, "outro")
        self.assertIsNone(self.publicacao.prazo_ia_dias)

    def test_view_permissions_and_flow(self):
        self.configure()
        url = reverse("publicacao-ai", args=[self.publicacao.pk])
        self.client.force_login(self.operator)
        self.assertContains(self.client.get(url), "Intimação para contestar")
        self.assertEqual(self.client.post(url, {"action": "analisar"}).status_code, 403)

        self.client.force_login(self.manager)
        with patch("juridico.ai.generate", return_value=_result(data=self.analysis)):
            self.assertRedirects(self.client.post(url, {"action": "analisar"}), url)
        page = self.client.get(url)
        self.assertContains(page, "<strong>contestar</strong>", html=False)
        self.assertContains(page, "Art. 335 do CPC")
        self.client.post(url, {"action": "criar_tarefa"})
        self.assertTrue(Tarefa.objects.filter(titulo="Elaborar contestação").exists())

    def test_api_action(self):
        self.configure()
        self.client.force_login(self.manager)
        with patch("juridico.ai.generate", return_value=_result(data=self.analysis)):
            response = self.client.post(f"/api/juridico/publicacoes/{self.publicacao.pk}/analisar-ia/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["classificacao_ia"], "intimacao")

    def test_api_action_without_ai_returns_503(self):
        self.client.force_login(self.manager)
        response = self.client.post(f"/api/juridico/publicacoes/{self.publicacao.pk}/analisar-ia/")
        self.assertEqual(response.status_code, 503)


class ProcessoEAssistenteAITests(AIBaseTestCase):
    def test_case_summary(self):
        self.configure()
        url = reverse("processo-ai", args=[self.processo.pk])
        self.client.force_login(self.manager)
        with patch("juridico.ai.generate", return_value=_result("**Situação atual**\nAguardando contestação.")) as gen:
            self.client.post(url)
        self.assertIn("0001234-56.2026.8.19.0001", gen.call_args.args[1])
        self.processo.refresh_from_db()
        self.assertIn("Aguardando contestação", self.processo.resumo_ia)
        self.assertContains(self.client.get(url), "Situação atual")

    def test_assistant_keeps_history_and_sends_case_context(self):
        self.configure()
        self.client.force_login(self.manager)
        url = reverse("ai-assistant")
        with patch("juridico.ai.generate", return_value=_result("Primeira resposta")) as gen:
            self.client.post(url, {"pergunta": "Qual o próximo passo?", "processo": self.processo.pk})
        self.assertIn("Ação de cobrança", gen.call_args.kwargs["system"])
        with patch("juridico.ai.generate", return_value=_result("Segunda resposta")) as gen:
            self.client.post(url, {"pergunta": "E depois?"})
        self.assertEqual(
            [turn["content"] for turn in gen.call_args.kwargs["history"]],
            ["Qual o próximo passo?", "Primeira resposta"],
        )
        page = self.client.get(url)
        self.assertContains(page, "Segunda resposta")

    def test_operator_cannot_use_assistant(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(reverse("ai-assistant")).status_code, 403)


class AgendaEAudienciasTests(AIBaseTestCase):
    def test_audiencias_module_and_day_summary(self):
        agora = timezone.localtime()
        Audiencia.objects.create(
            processo=self.processo,
            titulo="Audiência de conciliação",
            data_hora=agora.replace(hour=23, minute=0),
            responsavel=self.manager,
        )
        Prazo.objects.create(
            processo=self.processo,
            titulo="Réplica",
            responsavel=self.manager,
            data_vencimento=timezone.localdate() - timedelta(days=2),
        )
        self.client.force_login(self.manager)
        lista = self.client.get(reverse("record-list", args=["audiencias"]))
        self.assertContains(lista, "Audiência de conciliação")
        self.assertContains(lista, "Nova audiência")

        home = self.client.get(reverse("home"))
        self.assertContains(home, "Audiência de conciliação")
        self.assertContains(home, "1 vencido")
        self.assertContains(home, "Réplica")

    def test_create_audiencia_with_datetime_input(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("record-create", args=["audiencias"]),
            {
                "processo": self.processo.pk,
                "titulo": "Instrução",
                "data_hora": "2026-12-10T14:30",
                "modalidade": "virtual",
                "local": "https://meet.example.com/x",
                "responsavel": self.manager.pk,
                "status": "agendada",
            },
        )
        self.assertEqual(response.status_code, 302)
        audiencia = Audiencia.objects.get(titulo="Instrução")
        self.assertEqual(timezone.localtime(audiencia.data_hora).strftime("%d/%m %H:%M"), "10/12 14:30")


class AIFormatAndWhatsAppTests(AIBaseTestCase):
    def test_markdown_filter_escapes_html(self):
        html = ai_markdown("**ok**\n<img src=x onerror=alert(1)>\n- item")
        self.assertIn("<strong>ok</strong>", html)
        self.assertNotIn("<img", html)
        self.assertIn("<li>item</li>", html)

    def test_whatsapp_uses_connected_ai_when_enabled(self):
        from crm.services import responder_com_ia

        self.configure(enable_whatsapp=True)
        with patch("core.ai._dispatch", return_value=_result("Olá! Qual seu nome?")):
            self.assertEqual(responder_com_ia("Oi", {}, ""), "Olá! Qual seu nome?")
        self.assertEqual(AIUsageLog.objects.get().feature, "whatsapp")
