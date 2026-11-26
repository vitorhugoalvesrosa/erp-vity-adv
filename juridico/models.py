from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from crm.models import Conta


class Processo(models.Model):
    class Area(models.TextChoices):
        CIVEL = "civel", "Cível"
        TRABALHISTA = "trabalhista", "Trabalhista"
        TRIBUTARIO = "tributario", "Tributário"
        EMPRESARIAL = "empresarial", "Empresarial"
        CRIMINAL = "criminal", "Criminal"
        FAMILIA = "familia", "Família"
        OUTRA = "outra", "Outra"

    class Status(models.TextChoices):
        ATIVO = "ativo", "Ativo"
        SUSPENSO = "suspenso", "Suspenso"
        ARQUIVADO = "arquivado", "Arquivado"
        ENCERRADO = "encerrado", "Encerrado"

    numero_cnj = models.CharField("Número CNJ", max_length=25, unique=True)
    titulo = models.CharField("Título", max_length=255)
    cliente = models.ForeignKey(
        Conta,
        on_delete=models.PROTECT,
        related_name="processos",
        verbose_name="Cliente",
    )
    parte_contraria = models.CharField("Parte contrária", max_length=255, blank=True)
    tribunal = models.CharField("Tribunal", max_length=100)
    vara = models.CharField("Vara/comarca", max_length=120, blank=True)
    area = models.CharField(max_length=20, choices=Area.choices, default=Area.CIVEL)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ATIVO,
    )
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="processos_responsavel",
        verbose_name="Responsável",
    )
    valor_causa = models.DecimalField(
        "Valor da causa",
        max_digits=14,
        decimal_places=2,
        default=0,
    )
    observacoes = models.TextField("Observações", blank=True)
    resumo_ia = models.TextField("Resumo gerado por IA", blank=True, editable=False)
    resumo_ia_em = models.DateTimeField(null=True, blank=True, editable=False)
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-atualizado_em"]
        verbose_name = "Processo"
        verbose_name_plural = "Processos"

    def __str__(self):
        return f"{self.numero_cnj} - {self.titulo}"


class Movimentacao(models.Model):
    processo = models.ForeignKey(
        Processo,
        on_delete=models.CASCADE,
        related_name="movimentacoes",
    )
    data = models.DateField("Data")
    descricao = models.CharField("Descrição", max_length=500)
    origem = models.CharField(
        "Origem",
        max_length=30,
        default="manual",
    )
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data", "-criada_em"]
        verbose_name = "Movimentação"
        verbose_name_plural = "Movimentações"


class Prazo(models.Model):
    class Status(models.TextChoices):
        ABERTO = "aberto", "Aberto"
        CONCLUIDO = "concluido", "Concluído"
        PERDIDO = "perdido", "Perdido"

    processo = models.ForeignKey(
        Processo,
        on_delete=models.CASCADE,
        related_name="prazos",
    )
    titulo = models.CharField("Título", max_length=255)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="prazos_responsavel",
    )
    data_vencimento = models.DateField("Vencimento")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ABERTO)
    observacoes = models.TextField("Observações", blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["data_vencimento", "titulo"]
        verbose_name = "Prazo"
        verbose_name_plural = "Prazos"


class Tarefa(models.Model):
    class Prioridade(models.TextChoices):
        BAIXA = "baixa", "Baixa"
        MEDIA = "media", "Média"
        ALTA = "alta", "Alta"
        URGENTE = "urgente", "Urgente"

    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        EM_ANDAMENTO = "em_andamento", "Em andamento"
        CONCLUIDA = "concluida", "Concluída"
        CANCELADA = "cancelada", "Cancelada"

    processo = models.ForeignKey(
        Processo,
        on_delete=models.CASCADE,
        related_name="tarefas",
        null=True,
        blank=True,
    )
    titulo = models.CharField("Título", max_length=255)
    descricao = models.TextField("Descrição", blank=True)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="tarefas_responsavel",
    )
    prioridade = models.CharField(
        max_length=20,
        choices=Prioridade.choices,
        default=Prioridade.MEDIA,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDENTE,
    )
    vencimento = models.DateField("Vencimento", null=True, blank=True)
    concluida_em = models.DateTimeField(null=True, blank=True)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["status", "vencimento", "-prioridade"]
        verbose_name = "Tarefa"
        verbose_name_plural = "Tarefas"


class Publicacao(models.Model):
    processo = models.ForeignKey(
        Processo,
        on_delete=models.SET_NULL,
        related_name="publicacoes",
        null=True,
        blank=True,
    )
    monitoramento = models.CharField("CPF/CNPJ/OAB monitorado", max_length=30)
    tribunal = models.CharField("Tribunal", max_length=100)
    titulo = models.CharField("Título", max_length=255)
    conteudo = models.TextField("Conteúdo")
    publicada_em = models.DateField("Data da publicação")
    capturada_em = models.DateTimeField(auto_now_add=True)
    urgente = models.BooleanField("Urgente", default=False)
    processada = models.BooleanField("Tratada", default=False)
    descartada = models.BooleanField("Descartada", default=False)
    tratada_em = models.DateTimeField("Tratada/descartada em", null=True, blank=True, editable=False)

    class ClassificacaoIA(models.TextChoices):
        INTIMACAO = "intimacao", "Intimação"
        CITACAO = "citacao", "Citação"
        DESPACHO = "despacho", "Despacho"
        DECISAO = "decisao", "Decisão interlocutória"
        SENTENCA = "sentenca", "Sentença"
        ACORDAO = "acordao", "Acórdão"
        AUDIENCIA = "audiencia", "Designação de audiência"
        PAUTA = "pauta", "Pauta de julgamento"
        EDITAL = "edital", "Edital"
        OUTRO = "outro", "Outro"

    resumo_ia = models.TextField("Resumo da IA", blank=True, editable=False)
    classificacao_ia = models.CharField(
        "Classificação da IA",
        max_length=20,
        choices=ClassificacaoIA.choices,
        blank=True,
        editable=False,
    )
    providencia_ia = models.CharField(
        "Providência sugerida", max_length=255, blank=True, editable=False
    )
    prazo_ia_dias = models.PositiveSmallIntegerField(
        "Prazo sugerido (dias)", null=True, blank=True, editable=False
    )
    prazo_ia_fundamento = models.CharField(
        "Fundamento do prazo", max_length=255, blank=True, editable=False
    )
    urgente_ia = models.BooleanField("Urgente (IA)", default=False, editable=False)
    analisada_em = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-publicada_em", "-capturada_em"]
        verbose_name = "Publicação"
        verbose_name_plural = "Publicações"


class Audiencia(models.Model):
    class Status(models.TextChoices):
        AGENDADA = "agendada", "Agendada"
        REALIZADA = "realizada", "Realizada"
        ADIADA = "adiada", "Adiada"
        CANCELADA = "cancelada", "Cancelada"

    processo = models.ForeignKey(
        Processo,
        on_delete=models.CASCADE,
        related_name="audiencias",
    )
    titulo = models.CharField("Título", max_length=255)
    data_hora = models.DateTimeField("Data e hora")
    modalidade = models.CharField("Modalidade", max_length=50, default="presencial")
    local = models.CharField("Local ou link", max_length=255, blank=True)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="audiencias_responsavel",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.AGENDADA,
    )
    observacoes = models.TextField("Observações", blank=True)
    criada_em = models.DateTimeField(auto_now_add=True)
    atualizada_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["data_hora"]
        verbose_name = "Audiência"
        verbose_name_plural = "Audiências"

    def __str__(self):
        return f"{self.titulo} - {self.data_hora:%d/%m/%Y %H:%M}"


class IntegracaoTribunal(models.Model):
    class Provider(models.TextChoices):
        DATAJUD = "datajud", "CNJ DataJud"
        PJE = "pje", "PJe"
        ESAJ = "esaj", "e-SAJ"
        PROJUDI = "projudi", "Projudi"
        OUTRO = "outro", "Outro"

    nome = models.CharField("Nome", max_length=100)
    provider = models.CharField(
        "Provedor",
        max_length=20,
        choices=Provider.choices,
        default=Provider.DATAJUD,
    )
    uf = models.CharField("UF", max_length=2)
    tribunal = models.CharField("Tribunal", max_length=100)
    endpoint = models.URLField("Endpoint", max_length=500)
    api_key_encrypted = models.BinaryField(
        "Chave criptografada",
        editable=False,
        default=bytes,
    )
    ativo = models.BooleanField(default=True)
    ultima_sincronizacao = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uf", "tribunal"]
        unique_together = ("provider", "uf", "tribunal")
        verbose_name = "Integração judicial"
        verbose_name_plural = "Integrações judiciais"

    def __str__(self):
        return f"{self.nome} ({self.uf})"


class Atendimento(models.Model):
    class Status(models.TextChoices):
        ABERTO = "aberto", "Em andamento"
        CONVERTIDO = "convertido", "Convertido em processo"
        ENCERRADO = "encerrado", "Encerrado"

    cliente = models.ForeignKey(Conta, on_delete=models.PROTECT, related_name="atendimentos", verbose_name="Cliente")
    assunto = models.CharField("Assunto", max_length=255)
    data = models.DateTimeField("Data do atendimento", default=timezone.now)
    canal = models.CharField(
        "Canal",
        max_length=20,
        choices=[
            ("presencial", "Presencial"),
            ("telefone", "Telefone"),
            ("video", "Videochamada"),
            ("whatsapp", "WhatsApp"),
            ("email", "E-mail"),
        ],
        default="presencial",
    )
    relato = models.TextField("Relato do atendimento", blank=True)
    responsavel = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="atendimentos_responsavel",
        verbose_name="Responsável",
    )
    processo = models.ForeignKey(
        Processo,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="atendimentos",
        verbose_name="Processo vinculado",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ABERTO)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data"]
        verbose_name = "Atendimento"
        verbose_name_plural = "Atendimentos"

    def __str__(self):
        return f"{self.assunto} — {self.cliente}"


class Timesheet(models.Model):
    processo = models.ForeignKey(
        Processo,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="timesheets",
        verbose_name="Processo",
    )
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="timesheets",
        verbose_name="Profissional",
    )
    data = models.DateField("Data", default=timezone.localdate)
    minutos = models.PositiveIntegerField("Duração (minutos)")
    descricao = models.CharField("Atividade", max_length=255)
    valor_hora = models.DecimalField("Valor da hora", max_digits=10, decimal_places=2, default=0)
    faturado = models.BooleanField("Faturado", default=False)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data", "-criado_em"]
        verbose_name = "Timesheet"
        verbose_name_plural = "Timesheets"

    def __str__(self):
        return f"{self.descricao} ({self.duracao})"

    @property
    def duracao(self):
        horas, minutos = divmod(self.minutos, 60)
        return f"{horas}h{minutos:02d}"

    @property
    def valor(self):
        return (self.valor_hora * self.minutos / 60).quantize(Decimal("0.01"))


class Honorario(models.Model):
    class Tipo(models.TextChoices):
        FIXO = "fixo", "Fixo"
        MENSAL = "mensal", "Mensal"
        EXITO = "exito", "Êxito (%)"
        HORA = "hora", "Por hora"

    class Status(models.TextChoices):
        A_FATURAR = "a_faturar", "A faturar"
        FATURADO = "faturado", "Faturado"
        RECEBIDO = "recebido", "Recebido"
        CANCELADO = "cancelado", "Cancelado"

    processo = models.ForeignKey(Processo, on_delete=models.CASCADE, related_name="honorarios", verbose_name="Processo")
    descricao = models.CharField("Descrição", max_length=255)
    tipo = models.CharField("Tipo", max_length=10, choices=Tipo.choices, default=Tipo.FIXO)
    valor = models.DecimalField("Valor", max_digits=12, decimal_places=2, default=0)
    percentual_exito = models.DecimalField(
        "Percentual de êxito", max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Para honorário de êxito: percentual sobre o proveito econômico.",
    )
    vencimento = models.DateField("Vencimento", null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.A_FATURAR)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["vencimento", "-criado_em"]
        verbose_name = "Honorário"
        verbose_name_plural = "Honorários"

    def __str__(self):
        return f"{self.descricao} — {self.processo.numero_cnj}"

    def clean(self):
        super().clean()
        if self.tipo == self.Tipo.EXITO and not self.percentual_exito:
            raise ValidationError({"percentual_exito": "Informe o percentual do honorário de êxito."})
        if self.percentual_exito is not None and not (0 < self.percentual_exito <= 100):
            raise ValidationError({"percentual_exito": "O percentual deve estar entre 0 e 100."})


class Despesa(models.Model):
    class Status(models.TextChoices):
        A_REEMBOLSAR = "a_reembolsar", "A reembolsar"
        REEMBOLSADA = "reembolsada", "Reembolsada"
        NAO_REEMBOLSAVEL = "nao_reembolsavel", "Não reembolsável"

    processo = models.ForeignKey(Processo, on_delete=models.CASCADE, related_name="despesas", verbose_name="Processo")
    descricao = models.CharField("Descrição", max_length=255, help_text="Custas, diligências, cópias, deslocamento…")
    valor = models.DecimalField("Valor", max_digits=12, decimal_places=2)
    data = models.DateField("Data", default=timezone.localdate)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.A_REEMBOLSAR)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data", "-criado_em"]
        verbose_name = "Despesa"
        verbose_name_plural = "Despesas"

    def __str__(self):
        return f"{self.descricao} — R$ {self.valor}"


class PecaJuridica(models.Model):
    class Tipo(models.TextChoices):
        PETICAO_INICIAL = "peticao_inicial", "Petição inicial cível"
        RECLAMACAO_TRABALHISTA = "reclamacao_trabalhista", "Reclamação trabalhista"
        CONTESTACAO = "contestacao", "Contestação"
        REPLICA = "replica", "Réplica"
        ESPECIFICACAO_PROVAS = "especificacao_provas", "Especificação de provas"
        APELACAO = "apelacao", "Apelação"
        EMBARGOS_DECLARACAO = "embargos_declaracao", "Embargos de declaração"
        AGRAVO_INSTRUMENTO = "agravo_instrumento", "Agravo de instrumento"
        OUTRA = "outra", "Outra peça"

    tipo = models.CharField("Tipo de peça", max_length=30, choices=Tipo.choices)
    titulo = models.CharField("Título", max_length=255)
    processo = models.ForeignKey(
        Processo,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pecas",
        verbose_name="Processo",
    )
    instrucoes = models.TextField("Fatos e instruções")
    conteudo = models.TextField("Conteúdo", blank=True)
    gerada_por_ia = models.BooleanField(default=True, editable=False)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="pecas_criadas",
        editable=False,
    )
    criada_em = models.DateTimeField(auto_now_add=True)
    atualizada_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-atualizada_em"]
        verbose_name = "Peça jurídica"
        verbose_name_plural = "Peças jurídicas"

    def __str__(self):
        return self.titulo
