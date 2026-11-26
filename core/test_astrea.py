from datetime import date
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from docx import Document

from core import ai
from core.access_control import MANAGER_GROUP, OPERATOR_GROUP, setup_access_groups
from core.models import AIConfiguration
from core.secure_storage import encrypt_text
from core.test_ai import FAKE_API_KEY, TEST_KEY
from crm.models import Conta
from juridico.models import (
    Atendimento,
    Despesa,
    Honorario,
    PecaJuridica,
    Processo,
    Publicacao,
    Tarefa,
    Timesheet,
)

MINUTA = """# CONTESTAÇÃO

## DOS FATOS

O réu **nega** os fatos narrados pelo autor [NOME DO AUTOR].

## DOS PEDIDOS

1. A improcedência dos pedidos;
2. A condenação em custas.
"""


@override_settings(CONFIG_ENCRYPTION_KEY=TEST_KEY)
class AstreaBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        groups = setup_access_groups()
        users = get_user_model().objects
        cls.manager = users.create_user("astrea-gerente", password="x", first_name="Ana")
        cls.manager.groups.add(groups[MANAGER_GROUP])
        cls.operator = users.create_user("astrea-operador", password="x")
        cls.operator.groups.add(groups[OPERATOR_GROUP])
        cls.cliente = Conta.objects.create(nome="Cliente Astrea", documento="55566677788")
        cls.processo = Processo.objects.create(
            numero_cnj="0009999-11.2026.8.19.0001",
            titulo="Ação indenizatória",
            cliente=cls.cliente,
            tribunal="TJRJ",
            responsavel=cls.manager,
        )

    def connect_ai(self):
        AIConfiguration.objects.create(
            provider="anthropic",
            model="claude-opus-5-5",
            api_key_encrypted=encrypt_text(FAKE_API_KEY),
            api_key_hint="1234",
        )


class PecaJuridicaTests(AstreaBase):
    def test_generate_edit_and_download_docx(self):
        self.connect_ai()
        self.client.force_login(self.manager)
        with patch("core.ai._dispatch", return_value=ai.AIResult(text=MINUTA, input_tokens=900, output_tokens=2500)) as dispatch:
            response = self.client.post(
                reverse("peca-nova"),
                {"tipo": "contestacao", "titulo": "Contestação — indenizatória", "processo": self.processo.pk, "instrucoes": "Cliente nega o dano."},
            )
        peca = PecaJuridica.objects.get()
        self.assertRedirects(response, reverse("peca-detalhe", args=[peca.pk]))
        prompt = dispatch.call_args.args[4][-1]["content"]
        self.assertIn("0009999-11.2026.8.19.0001", prompt)
        self.assertIn("art. 337", prompt)
        self.assertEqual(dispatch.call_args.kwargs["max_tokens"], 32000)
        self.assertEqual(peca.criado_por, self.manager)
        self.assertIn("DOS FATOS", peca.conteudo)

        page = self.client.get(reverse("peca-detalhe", args=[peca.pk]))
        self.assertContains(page, "<strong>nega</strong>", html=False)
        self.assertContains(page, "Baixar .docx")

        self.client.post(reverse("peca-detalhe", args=[peca.pk]), {"titulo": "Contestação final", "conteudo": MINUTA + "\nTexto revisado."})
        peca.refresh_from_db()
        self.assertEqual(peca.titulo, "Contestação final")

        download = self.client.get(reverse("peca-docx", args=[peca.pk]))
        self.assertEqual(download["Content-Type"], "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertIn('filename="contestacao-final.docx"', download["Content-Disposition"])
        doc = Document(BytesIO(download.content))
        texts = [p.text for p in doc.paragraphs]
        self.assertIn("CONTESTAÇÃO", texts)
        bold = [r.text for p in doc.paragraphs for r in p.runs if r.bold]
        self.assertIn("nega", bold)

    def test_without_ai_creates_blank_draft(self):
        self.client.force_login(self.manager)
        self.client.post(reverse("peca-nova"), {"tipo": "outra", "titulo": "Notificação", "instrucoes": "Notificar devedor."})
        peca = PecaJuridica.objects.get()
        self.assertEqual(peca.conteudo, "")
        self.assertFalse(peca.gerada_por_ia)

    def test_ai_failure_keeps_the_draft(self):
        self.connect_ai()
        self.client.force_login(self.manager)
        with patch("core.ai._dispatch", side_effect=ai.AIError("Limite de uso do Claude atingido.")):
            self.client.post(reverse("peca-nova"), {"tipo": "replica", "titulo": "Réplica", "instrucoes": "x"})
        self.assertEqual(PecaJuridica.objects.count(), 1)

    def test_operator_can_read_but_not_create(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(reverse("pecas")).status_code, 200)
        self.assertEqual(self.client.get(reverse("peca-nova")).status_code, 403)


class KanbanTests(AstreaBase):
    def test_board_and_move(self):
        tarefa = Tarefa.objects.create(titulo="Protocolar réplica", responsavel=self.manager, processo=self.processo, prioridade="urgente")
        self.client.force_login(self.manager)
        board = self.client.get(reverse("kanban"))
        self.assertContains(board, "Protocolar réplica")
        self.assertContains(board, "A fazer")

        response = self.client.post(reverse("kanban-mover", args=[tarefa.pk]), {"status": "concluida"}, HTTP_X_REQUESTED_WITH="fetch")
        self.assertEqual(response.json()["status"], "concluida")
        tarefa.refresh_from_db()
        self.assertIsNotNone(tarefa.concluida_em)

        self.client.post(reverse("kanban-mover", args=[tarefa.pk]), {"status": "pendente"})
        tarefa.refresh_from_db()
        self.assertIsNone(tarefa.concluida_em)
        self.assertEqual(self.client.post(reverse("kanban-mover", args=[tarefa.pk]), {"status": "hackeado"}).status_code, 400)

    def test_operator_cannot_move(self):
        tarefa = Tarefa.objects.create(titulo="T", responsavel=self.manager)
        self.client.force_login(self.operator)
        self.assertEqual(self.client.post(reverse("kanban-mover", args=[tarefa.pk]), {"status": "concluida"}).status_code, 403)

    def test_only_my_tasks_by_default(self):
        outro = get_user_model().objects.create_user("outro")
        Tarefa.objects.create(titulo="Tarefa do outro", responsavel=outro)
        self.client.force_login(self.manager)
        self.assertNotContains(self.client.get(reverse("kanban")), "Tarefa do outro")
        self.assertContains(self.client.get(reverse("kanban") + "?escopo=todas"), "Tarefa do outro")


class FinanceiroProcessoTests(AstreaBase):
    def test_case_finance_summary(self):
        Timesheet.objects.create(processo=self.processo, usuario=self.manager, minutos=90, descricao="Reunião", valor_hora=Decimal("300"))
        Honorario.objects.create(processo=self.processo, descricao="Entrada", valor=Decimal("5000"), status="recebido")
        Honorario.objects.create(processo=self.processo, descricao="Êxito", tipo="exito", percentual_exito=Decimal("20"), valor=0)
        Despesa.objects.create(processo=self.processo, descricao="Custas", valor=Decimal("350.50"))
        self.client.force_login(self.manager)
        page = self.client.get(reverse("processo-ai", args=[self.processo.pk]))
        self.assertContains(page, "Financeiro do processo")
        self.assertContains(page, "R$ 5.000,00")
        self.assertContains(page, "R$ 350,50")
        self.assertContains(page, "1,5 h")
        self.assertContains(page, "êxito 20%")
        self.assertEqual(Timesheet.objects.get().valor, Decimal("450.00"))

    def test_exito_requires_percentage(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("record-create", args=["honorarios"]),
            {"processo": self.processo.pk, "descricao": "Êxito", "tipo": "exito", "valor": "0", "status": "a_faturar"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Honorario.objects.exists())

    def test_timer_prefills_timesheet_form(self):
        self.client.force_login(self.manager)
        page = self.client.get(reverse("record-create", args=["timesheets"]) + f"?minutos=47&processo={self.processo.pk}&ignorado=1")
        form = page.context["form"]
        self.assertEqual(form.initial["minutos"], "47")
        self.assertEqual(str(form.initial["processo"]), str(self.processo.pk))
        self.assertEqual(form.initial["usuario"], self.manager.pk)
        self.assertNotIn("ignorado", form.initial)
        self.assertContains(self.client.get(reverse("home")), 'id="timer"')


class AtendimentoTests(AstreaBase):
    def test_convert_atendimento_into_processo(self):
        atendimento = Atendimento.objects.create(cliente=self.cliente, assunto="Cobrança indevida", relato="Banco cobrou tarifa.", responsavel=self.manager)
        self.client.force_login(self.manager)
        lista = self.client.get(reverse("record-list", args=["atendimentos"]))
        self.assertContains(lista, "Converter em processo")

        redirect_url = self.client.get(reverse("atendimento-converter", args=[atendimento.pk])).url
        form_page = self.client.get(redirect_url)
        self.assertEqual(form_page.context["form"].initial["titulo"], "Cobrança indevida")
        response = self.client.post(
            redirect_url,
            {
                "numero_cnj": "0001111-22.2026.8.19.0001",
                "titulo": "Cobrança indevida",
                "cliente": self.cliente.pk,
                "tribunal": "TJRJ",
                "area": "civel",
                "status": "ativo",
                "responsavel": self.manager.pk,
                "valor_causa": "1000",
            },
        )
        self.assertEqual(response.status_code, 302)
        atendimento.refresh_from_db()
        self.assertEqual(atendimento.status, Atendimento.Status.CONVERTIDO)
        self.assertEqual(atendimento.processo.numero_cnj, "0001111-22.2026.8.19.0001")


class PublicacaoTriagemTests(AstreaBase):
    def test_treat_discard_reopen_and_kpis(self):
        pub = Publicacao.objects.create(processo=self.processo, monitoramento="OAB", tribunal="TJRJ", titulo="Despacho", conteudo="Cite-se.", publicada_em=date(2026, 11, 25), urgente=True)
        Publicacao.objects.create(monitoramento="OAB", tribunal="TJRJ", titulo="Outra", conteudo="x", publicada_em=date(2026, 11, 25))
        self.client.force_login(self.manager)
        lista = self.client.get(reverse("record-list", args=["publicacoes"]))
        self.assertEqual(lista.context["publication_kpis"]["nao_tratadas"], 2)
        self.assertEqual(lista.context["publication_kpis"]["urgentes"], 1)

        url = reverse("publicacao-ai", args=[pub.pk])
        self.client.post(url, {"action": "descartar"})
        pub.refresh_from_db()
        self.assertTrue(pub.descartada)
        self.assertEqual(pub.tratada_em.date(), timezone.localdate())
        kpis = self.client.get(reverse("record-list", args=["publicacoes"])).context["publication_kpis"]
        self.assertEqual((kpis["nao_tratadas"], kpis["descartadas_hoje"]), (1, 1))

        filtradas = self.client.get(reverse("record-list", args=["publicacoes"]) + "?status=pendentes")
        self.assertEqual(filtradas.context["page_obj"].paginator.count, 1)

        self.client.post(url, {"action": "reabrir"})
        pub.refresh_from_db()
        self.assertFalse(pub.descartada or pub.processada)
