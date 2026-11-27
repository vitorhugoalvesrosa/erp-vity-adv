import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

# A Meta só permite texto livre até 24 h depois da última mensagem do cliente.
JANELA_ATENDIMENTO = timedelta(hours=24)


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
        AGUARDANDO_VENDEDOR = "aguardando_vendedor", "Aguardando atendente"
        ENCERRADA = "encerrada", "Encerrada"

    conta = models.ForeignKey(
        Conta,
        on_delete=models.CASCADE,
        related_name="conversas_whatsapp",
    )
    telefone = models.CharField(max_length=30, db_index=True)
    nome_contato = models.CharField("Nome no WhatsApp", max_length=255, blank=True)
    provider_conversation_id = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=30,
        choices=Status.choices,
        default=Status.ATIVA,
    )
    bot_ativo = models.BooleanField(
        "Bot respondendo",
        default=True,
        help_text="Desligado quando um atendente assume a conversa.",
    )
    atendente = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversas_whatsapp",
    )
    nao_lidas = models.PositiveIntegerField(default=0)
    ultima_mensagem_em = models.DateTimeField(null=True, blank=True, db_index=True)
    ultima_entrada_em = models.DateTimeField(null=True, blank=True)
    ultima_mensagem_texto = models.CharField(max_length=255, blank=True)
    iniciada_em = models.DateTimeField(auto_now_add=True)
    atualizada_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-ultima_mensagem_em", "-atualizada_em"]
        verbose_name = "Conversa WhatsApp"
        verbose_name_plural = "Conversas WhatsApp"

    def __str__(self):
        return f"{self.nome_exibicao} - {self.get_status_display()}"

    @property
    def nome_exibicao(self):
        if self.conta_id and not self.conta.nome.startswith("Lead WhatsApp"):
            return self.conta.nome
        return self.nome_contato or self.telefone

    @property
    def janela_aberta(self):
        return bool(
            self.ultima_entrada_em
            and timezone.now() - self.ultima_entrada_em < JANELA_ATENDIMENTO
        )

    @property
    def janela_expira_em(self):
        if not self.ultima_entrada_em:
            return None
        return self.ultima_entrada_em + JANELA_ATENDIMENTO


class MensagemWhatsApp(models.Model):
    class Direcao(models.TextChoices):
        ENTRADA = "entrada", "Entrada"
        SAIDA = "saida", "Saída"

    class Tipo(models.TextChoices):
        TEXTO = "text", "Texto"
        IMAGEM = "image", "Imagem"
        AUDIO = "audio", "Áudio"
        VIDEO = "video", "Vídeo"
        DOCUMENTO = "document", "Documento"
        FIGURINHA = "sticker", "Figurinha"
        LOCALIZACAO = "location", "Localização"
        CONTATO = "contacts", "Contato"
        BOTAO = "interactive", "Resposta de botão"
        REACAO = "reaction", "Reação"
        MODELO = "template", "Modelo"
        OUTRO = "unsupported", "Não suportado"

    class Status(models.TextChoices):
        RECEBIDA = "recebida", "Recebida"
        PENDENTE = "pendente", "Enviando"
        ENVIADA = "enviada", "Enviada"
        ENTREGUE = "entregue", "Entregue"
        LIDA = "lida", "Lida"
        FALHOU = "falhou", "Falhou"

    conversa = models.ForeignKey(
        ConversaWhatsApp,
        on_delete=models.CASCADE,
        related_name="mensagens",
    )
    provider_message_id = models.CharField(max_length=255, blank=True, db_index=True)
    direcao = models.CharField(max_length=10, choices=Direcao.choices)
    tipo = models.CharField(max_length=20, choices=Tipo.choices, default=Tipo.TEXTO)
    texto = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RECEBIDA)
    erro = models.CharField(max_length=500, blank=True)
    gerada_por_ia = models.BooleanField(default=False)
    enviada_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mensagens_whatsapp",
    )
    midia_id = models.CharField(max_length=255, blank=True)
    midia_mime = models.CharField(max_length=100, blank=True)
    midia_nome = models.CharField(max_length=255, blank=True)
    dados_provedor = models.JSONField(default=dict, blank=True)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["criada_em", "pk"]
        verbose_name = "Mensagem WhatsApp"
        verbose_name_plural = "Mensagens WhatsApp"

    @property
    def tem_midia(self):
        return bool(self.midia_id)

    @property
    def e_imagem(self):
        return self.tipo in (self.Tipo.IMAGEM, self.Tipo.FIGURINHA)


def _novo_verify_token():
    return secrets.token_urlsafe(24)


class WhatsAppConfiguration(models.Model):
    """Número de WhatsApp conectado pela WhatsApp Cloud API. Existe no máximo um registro."""

    phone_number_id = models.CharField(
        "ID do número de telefone",
        max_length=40,
        help_text="Meta for Developers → WhatsApp → Configuração da API → ID do número de telefone.",
    )
    business_account_id = models.CharField(
        "ID da conta do WhatsApp Business",
        max_length=40,
        blank=True,
        help_text="Opcional. Permite listar os modelos aprovados para iniciar conversas.",
    )
    access_token_encrypted = models.BinaryField(editable=False, default=bytes)
    access_token_hint = models.CharField(max_length=8, blank=True, editable=False)
    app_secret_encrypted = models.BinaryField(editable=False, default=bytes)
    verify_token = models.CharField(
        "Token de verificação do webhook",
        max_length=100,
        default=_novo_verify_token,
    )
    is_active = models.BooleanField("Integração ativa", default=True)
    bot_enabled = models.BooleanField("Bot de atendimento ligado", default=True)
    bot_instructions = models.TextField(
        "Instruções para o bot",
        blank=True,
        help_text=(
            "Informações que o bot pode usar: áreas de atuação, endereço, horário, "
            "documentos que o cliente deve separar, etc."
        ),
    )
    welcome_message = models.TextField(
        "Mensagem de boas-vindas",
        default=(
            "Olá! Obrigado por falar com o nosso escritório. Para agilizar, conte em poucas "
            "palavras como podemos ajudar e informe seu nome. Um atendente vai continuar "
            "por aqui."
        ),
        help_text="Usada quando a IA não está ligada: é enviada na primeira mensagem do cliente.",
    )
    handoff_message = models.TextField(
        "Mensagem ao transferir para atendente",
        default="Certo! Vou passar sua conversa para um atendente da equipe. Em instantes alguém responde por aqui.",
    )
    handoff_keywords = models.CharField(
        "Palavras que chamam um atendente",
        max_length=255,
        default="atendente, humano, pessoa, falar com advogado",
        help_text="Separadas por vírgula. Quando o cliente escreve uma delas, o bot para e chama a equipe.",
    )
    display_phone_number = models.CharField(max_length=30, blank=True, editable=False)
    verified_name = models.CharField(max_length=255, blank=True, editable=False)
    last_test_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_test_ok = models.BooleanField(default=False, editable=False)
    last_test_message = models.CharField(max_length=500, blank=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Conexão WhatsApp"
        verbose_name_plural = "Conexão WhatsApp"

    def __str__(self):
        return self.display_phone_number or f"Número {self.phone_number_id}"

    @property
    def has_access_token(self):
        return bool(self.access_token_encrypted)

    @property
    def has_app_secret(self):
        return bool(self.app_secret_encrypted)

    def handoff_terms(self):
        return [t.strip().lower() for t in self.handoff_keywords.split(",") if t.strip()]


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
