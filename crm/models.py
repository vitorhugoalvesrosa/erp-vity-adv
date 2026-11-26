from django.db import models
from django.conf import settings


class Conta(models.Model):
    class Status(models.TextChoices):
        LEAD = "lead", "Lead"
        NEGOCIACAO = "negociacao", "Negociação"
        CLIENTE = "cliente", "Cliente"

    class TipoPessoa(models.TextChoices):
        NAO_INFORMADO = "nao_informado", "Não informado"
        PF = "pf", "Pessoa Física"
        PJ = "pj", "Pessoa Jurídica"

    nome = models.CharField(max_length=255)
    documento = models.CharField(max_length=20, unique=True, help_text="CNPJ ou CPF")
    email = models.EmailField(blank=True, default="")
    telefone = models.CharField(max_length=30, blank=True, default="")
    tipo_pessoa = models.CharField(
        max_length=20,
        choices=TipoPessoa.choices,
        default=TipoPessoa.NAO_INFORMADO,
    )
    origem = models.CharField(max_length=50, blank=True, default="")
    vendedor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leads_responsavel",
    )
    qualificacao = models.JSONField(default=dict, blank=True)
    score_lead = models.PositiveSmallIntegerField(default=0)
    data_ultima_interacao = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.LEAD,
    )
    data_criacao = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data_criacao"]
        verbose_name = "Conta"
        verbose_name_plural = "Contas"

    def __str__(self):
        return self.nome


class Proposta(models.Model):
    class CondicaoPagamento(models.TextChoices):
        A_VISTA = "a_vista", "À vista"
        TRINTA_DIAS = "30_dias", "30 dias"
        TRINTA_SIXENTA_NOVENTA = "30_60_90", "30/60/90"

    class Status(models.TextChoices):
        RASCUNHO = "rascunho", "Rascunho"
        ENVIADA = "enviada", "Enviada"
        GANHA = "ganha", "Ganha"
        PERDIDA = "perdida", "Perdida"

    conta = models.ForeignKey(
        Conta,
        on_delete=models.CASCADE,
        related_name="propostas",
    )
    valor_total = models.DecimalField(max_digits=12, decimal_places=2)
    condicao_pagamento = models.CharField(
        max_length=20,
        choices=CondicaoPagamento.choices,
        default=CondicaoPagamento.A_VISTA,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RASCUNHO,
    )
    data_validade = models.DateField()

    class Meta:
        ordering = ["-data_validade"]
        verbose_name = "Proposta"
        verbose_name_plural = "Propostas"

    def __str__(self):
        return f"Proposta {self.pk} - {self.conta.nome}"


class LeadDistributionRule(models.Model):
    vendedor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="regras_distribuicao_leads",
    )
    ativo = models.BooleanField(default=True)
    atribuicoes = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["atribuicoes", "pk"]
        verbose_name = "Regra de distribuição de leads"
        verbose_name_plural = "Regras de distribuição de leads"

    def __str__(self):
        return self.vendedor.get_full_name() or self.vendedor.username


class ConversaWhatsApp(models.Model):
    class Status(models.TextChoices):
        ATIVA = "ativa", "Ativa"
        AGUARDANDO_CLIENTE = "aguardando_cliente", "Aguardando cliente"
        AGUARDANDO_VENDEDOR = "aguardando_vendedor", "Aguardando vendedor"
        ENCERRADA = "encerrada", "Encerrada"

    conta = models.ForeignKey(
        Conta,
        on_delete=models.CASCADE,
        related_name="conversas_whatsapp",
    )
    telefone = models.CharField(max_length=30)
    provider_conversation_id = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.ATIVA,
    )
    iniciada_em = models.DateTimeField(auto_now_add=True)
    atualizada_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-atualizada_em"]
        verbose_name = "Conversa WhatsApp"
        verbose_name_plural = "Conversas WhatsApp"

    def __str__(self):
        return f"{self.telefone} - {self.status}"


class MensagemWhatsApp(models.Model):
    class Direcao(models.TextChoices):
        ENTRADA = "entrada", "Entrada"
        SAIDA = "saida", "Saída"

    conversa = models.ForeignKey(
        ConversaWhatsApp,
        on_delete=models.CASCADE,
        related_name="mensagens",
    )
    provider_message_id = models.CharField(max_length=255, blank=True)
    direcao = models.CharField(max_length=10, choices=Direcao.choices)
    texto = models.TextField(blank=True)
    gerada_por_ia = models.BooleanField(default=False)
    dados_provedor = models.JSONField(default=dict, blank=True)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["criada_em"]
        verbose_name = "Mensagem WhatsApp"
        verbose_name_plural = "Mensagens WhatsApp"


class FollowUp(models.Model):
    class Canal(models.TextChoices):
        WHATSAPP = "whatsapp", "WhatsApp"
        EMAIL = "email", "E-mail"
        TELEFONE = "telefone", "Telefone"

    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        CONCLUIDO = "concluido", "Concluído"
        CANCELADO = "cancelado", "Cancelado"

    conta = models.ForeignKey(
        Conta,
        on_delete=models.CASCADE,
        related_name="followups",
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="followups_responsavel",
    )
    canal = models.CharField(max_length=20, choices=Canal.choices, default=Canal.WHATSAPP)
    agendamento = models.DateTimeField()
    assunto = models.CharField(max_length=255)
    notas = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDENTE)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["agendamento"]
        verbose_name = "Follow-up"
        verbose_name_plural = "Follow-ups"
