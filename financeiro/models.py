from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from crm.models import Conta, Proposta


class PedidoVenda(models.Model):
    proposta = models.OneToOneField(
        Proposta,
        on_delete=models.CASCADE,
        related_name="pedido_venda",
    )
    valor_final = models.DecimalField(max_digits=12, decimal_places=2)
    data_faturamento = models.DateField()

    class Meta:
        ordering = ["-data_faturamento"]
        verbose_name = "Pedido de Venda"
        verbose_name_plural = "Pedidos de Venda"

    def __str__(self):
        return f"Pedido {self.pk} - {self.proposta.conta.nome}"


class Contrato(models.Model):
    class Status(models.TextChoices):
        RASCUNHO = "rascunho", "Rascunho"
        AGUARDANDO_ASSINATURA = "aguardando_assinatura", "Aguardando assinatura"
        ATIVO = "ativo", "Ativo"
        CONCLUIDO = "concluido", "Concluído"
        RESCINDIDO = "rescindido", "Rescindido"

    pedido_venda = models.OneToOneField(
        PedidoVenda,
        on_delete=models.PROTECT,
        related_name="contrato",
        verbose_name="Pedido de venda",
    )
    contract_date = models.DateField("Data do contrato", default=timezone.localdate)
    start_date = models.DateField("Início da vigência", default=timezone.localdate)
    end_date = models.DateField("Fim da vigência", blank=True, null=True)
    service_location = models.CharField("Local da prestação", max_length=255, blank=True)
    service_description = models.TextField("Objeto e escopo dos serviços")
    payment_terms = models.TextField("Condições de pagamento")
    client_representative = models.CharField(
        "Representante do contratante", max_length=255, blank=True
    )
    termination_notice_days = models.PositiveSmallIntegerField(
        "Aviso prévio para rescisão (dias)", default=30
    )
    additional_clauses = models.TextField("Condições adicionais", blank=True)
    status = models.CharField(
        "Status",
        max_length=24,
        choices=Status.choices,
        default=Status.RASCUNHO,
    )
    created_at = models.DateTimeField("Criado em", auto_now_add=True)
    updated_at = models.DateTimeField("Atualizado em", auto_now=True)

    class Meta:
        ordering = ["-contract_date", "-id"]
        verbose_name = "Contrato"
        verbose_name_plural = "Contratos"

    def clean(self):
        super().clean()
        if self.end_date and self.start_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "O fim da vigência deve ser posterior ao início."})

    @property
    def number(self):
        return f"CTR-{self.contract_date.year}-{self.pk:06d}" if self.pk else "Novo contrato"

    @property
    def cliente(self):
        return self.pedido_venda.proposta.conta.nome

    def __str__(self):
        return f"{self.number} - {self.pedido_venda.proposta.conta.nome}"


class ContaReceber(models.Model):
    class StatusPagamento(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        PAGO = "pago", "Pago"
        ATRASADO = "atrasado", "Atrasado"

    conta = models.ForeignKey(
        Conta,
        on_delete=models.CASCADE,
        related_name="contas_receber",
    )
    pedido_venda = models.ForeignKey(
        PedidoVenda,
        on_delete=models.CASCADE,
        related_name="contas_receber",
    )
    valor_parcela = models.DecimalField(max_digits=12, decimal_places=2)
    data_vencimento = models.DateField()
    status_pagamento = models.CharField(
        max_length=20,
        choices=StatusPagamento.choices,
        default=StatusPagamento.PENDENTE,
    )

    class Meta:
        ordering = ["data_vencimento"]
        verbose_name = "Conta a Receber"
        verbose_name_plural = "Contas a Receber"

    def __str__(self):
        return f"{self.conta.nome} - {self.valor_parcela}"


class Pagamento(models.Model):
    class MetodoPagamento(models.TextChoices):
        PIX = "pix", "Pix"
        BOLETO = "boleto", "Boleto"
        CARTAO = "cartao", "Cartão"
        TRANSFERENCIA = "transferencia", "Transferência"

    conta_receber = models.ForeignKey(
        ContaReceber,
        on_delete=models.CASCADE,
        related_name="pagamentos",
    )
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    data_pagamento = models.DateField()
    metodo_pagamento = models.CharField(
        max_length=20,
        choices=MetodoPagamento.choices,
        default=MetodoPagamento.PIX,
    )

    class Meta:
        ordering = ["-data_pagamento"]
        verbose_name = "Pagamento"
        verbose_name_plural = "Pagamentos"

    def __str__(self):
        return f"Pagamento {self.pk} - {self.valor}"


class ContaPagar(models.Model):
    """Saída de dinheiro do escritório: aluguel, folha, fornecedores, custas, impostos…"""

    class Categoria(models.TextChoices):
        ALUGUEL = "aluguel", "Aluguel e condomínio"
        FOLHA = "folha", "Salários e pró-labore"
        ENCARGOS = "encargos", "Encargos e benefícios"
        IMPOSTOS = "impostos", "Impostos e taxas"
        CUSTAS = "custas", "Custas processuais"
        PARCEIROS = "parceiros", "Correspondentes e parceiros"
        SERVICOS = "servicos", "Serviços (contabilidade, TI, limpeza)"
        SISTEMAS = "sistemas", "Sistemas e assinaturas"
        CONSUMO = "consumo", "Água, luz, internet e telefone"
        MATERIAL = "material", "Material de escritório"
        MARKETING = "marketing", "Marketing"
        TARIFAS = "tarifas", "Tarifas bancárias"
        OUTROS = "outros", "Outros"

    class Status(models.TextChoices):
        A_PAGAR = "a_pagar", "A pagar"
        PAGO = "pago", "Pago"
        CANCELADO = "cancelado", "Cancelado"

    class FormaPagamento(models.TextChoices):
        PIX = "pix", "Pix"
        BOLETO = "boleto", "Boleto"
        TRANSFERENCIA = "transferencia", "Transferência"
        CARTAO_CREDITO = "cartao_credito", "Cartão de crédito"
        CARTAO_DEBITO = "cartao_debito", "Cartão de débito"
        DEBITO_AUTOMATICO = "debito_automatico", "Débito automático"
        DINHEIRO = "dinheiro", "Dinheiro"

    descricao = models.CharField("Descrição", max_length=255, help_text="Ex.: Aluguel da sala, Guia de custas, Contador.")
    favorecido = models.CharField("Favorecido", max_length=255, blank=True, help_text="Quem recebe o pagamento.")
    categoria = models.CharField("Categoria", max_length=20, choices=Categoria.choices, default=Categoria.OUTROS)
    valor = models.DecimalField("Valor", max_digits=12, decimal_places=2)
    data_vencimento = models.DateField("Vencimento")
    forma_pagamento = models.CharField(
        "Forma de pagamento", max_length=20, choices=FormaPagamento.choices, default=FormaPagamento.PIX
    )
    status = models.CharField("Status", max_length=20, choices=Status.choices, default=Status.A_PAGAR)
    data_pagamento = models.DateField("Data do pagamento", null=True, blank=True)
    valor_pago = models.DecimalField(
        "Valor pago", max_digits=12, decimal_places=2, null=True, blank=True,
        help_text="Com juros, multa ou desconto. Em branco, considera o valor da conta.",
    )
    cliente = models.ForeignKey(
        Conta, on_delete=models.SET_NULL, null=True, blank=True, related_name="contas_pagar",
        verbose_name="Cliente relacionado",
    )
    processo = models.ForeignKey(
        "juridico.Processo", on_delete=models.SET_NULL, null=True, blank=True, related_name="contas_pagar",
        verbose_name="Processo relacionado",
    )
    codigo_barras = models.CharField("Código de barras / chave Pix", max_length=255, blank=True)
    observacoes = models.TextField("Observações", blank=True)
    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="contas_pagar_criadas", editable=False,
    )
    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["data_vencimento", "pk"]
        verbose_name = "Conta a pagar"
        verbose_name_plural = "Contas a pagar"

    def __str__(self):
        return f"{self.descricao} — R$ {self.valor}"

    @property
    def situacao(self):
        """Status exibido: considera vencimento para as contas em aberto."""
        if self.status != self.Status.A_PAGAR:
            return self.status
        hoje = timezone.localdate()
        if self.data_vencimento < hoje:
            return "atrasado"
        if self.data_vencimento == hoje:
            return "vence_hoje"
        return self.status

    @property
    def situacao_display(self):
        return {"atrasado": "Atrasada", "vence_hoje": "Vence hoje"}.get(self.situacao) or self.get_status_display()

    @property
    def valor_efetivo(self):
        return self.valor_pago if self.valor_pago is not None else self.valor
