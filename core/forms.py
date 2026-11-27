from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm
from django.db import models
from django.utils import timezone

from crm.models import Conta, Proposta
from financeiro.models import (
    CategoriaFinanceira,
    Contrato,
    ContaPagar,
    ContaReceber,
    Lancamento,
    PedidoVenda,
)
from juridico.models import (
    UFS,
    Advogado,
    ModeloDocumento,
    Atendimento,
    Audiencia,
    Despesa,
    Honorario,
    Movimentacao,
    PecaJuridica,
    Prazo,
    Processo,
    Publicacao,
    Tarefa,
    Timesheet,
)
from produtos.models import Produto


class ERPAuthenticationForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update(
            {"class": "form-control", "autocomplete": "username"}
        )
        self.fields["password"].widget.attrs.update(
            {"class": "form-control", "autocomplete": "current-password"}
        )


class ERPPasswordResetForm(PasswordResetForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].widget.attrs.update(
            {"class": "form-control", "autocomplete": "email"}
        )


class ERPModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxSelectMultiple):
                field.widget.attrs["class"] = "form-checkbox-list"
            elif isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs["class"] = "form-checkbox"
            elif isinstance(field, forms.DateTimeField):
                field.widget = forms.DateTimeInput(
                    attrs={"type": "datetime-local", "class": "form-control"},
                    format="%Y-%m-%dT%H:%M",
                )
                field.input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M"]
            elif isinstance(field, forms.DateField):
                field.widget.input_type = "date"
                # O campo de data do navegador só aceita AAAA-MM-DD; em dd/mm/aaaa ele aparece vazio.
                field.widget.format = "%Y-%m-%d"
                field.widget.attrs["class"] = "form-control"
                if "%Y-%m-%d" not in field.input_formats:
                    field.input_formats = ["%Y-%m-%d", *field.input_formats]
            elif isinstance(field, forms.DecimalField):
                field.widget.attrs.update({"class": "form-control", "step": "0.01"})
            elif isinstance(field, forms.IntegerField):
                field.widget.attrs.update({"class": "form-control", "step": "1"})
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs["class"] = "form-select"
            else:
                field.widget.attrs["class"] = "form-control"


CAMPOS_PF = ("nacionalidade", "estado_civil", "profissao", "rg")
CAMPOS_PJ = (
    "representante_nome",
    "representante_cpf",
    "representante_rg",
    "representante_nacionalidade",
    "representante_estado_civil",
)


class ContaForm(ERPModelForm):
    class Meta:
        model = Conta
        fields = (
            "tipo_pessoa",
            "nome",
            "documento",
            "email",
            "telefone",
            "status",
            *CAMPOS_PF,
            *CAMPOS_PJ,
            "cep",
            "endereco",
            "bairro",
            "cidade",
            "uf",
        )
        labels = {"nome": "Nome / razão social", "documento": "CPF / CNPJ", "tipo_pessoa": "Tipo de pessoa"}
        widgets = {"uf": forms.Select(choices=[("", "—"), *UFS])}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tipo_pessoa"].required = False
        self.fields["documento"].help_text = "Só os números ou com pontuação."
        for nome in ("estado_civil", "representante_estado_civil"):
            self.fields[nome].widget.attrs["list"] = "opcoes-estado-civil"
        for nome in ("nacionalidade", "representante_nacionalidade"):
            self.fields[nome].widget.attrs["list"] = "opcoes-nacionalidade"
        self.fields["cep"].widget.attrs.update({"inputmode": "numeric", "autocomplete": "postal-code"})
        for nome in CAMPOS_PF:
            self.fields[nome].widget.attrs["data-pessoa"] = "pf"
        for nome in CAMPOS_PJ:
            self.fields[nome].widget.attrs["data-pessoa"] = "pj"

    def clean(self):
        cleaned = super().clean()
        tipo = cleaned.get("tipo_pessoa") or Conta.TipoPessoa.NAO_INFORMADO
        cleaned["tipo_pessoa"] = tipo
        digitos = "".join(ch for ch in cleaned.get("documento") or "" if ch.isdigit())
        if tipo == Conta.TipoPessoa.PF and digitos and len(digitos) != 11:
            self.add_error("documento", "CPF precisa ter 11 dígitos.")
        if tipo == Conta.TipoPessoa.PJ and digitos and len(digitos) != 14:
            self.add_error("documento", "CNPJ precisa ter 14 dígitos.")
        cpf_rep = "".join(ch for ch in cleaned.get("representante_cpf") or "" if ch.isdigit())
        if cpf_rep and len(cpf_rep) != 11:
            self.add_error("representante_cpf", "CPF precisa ter 11 dígitos.")
        if cleaned.get("uf"):
            cleaned["uf"] = cleaned["uf"].upper()
        return cleaned


class PropostaForm(ERPModelForm):
    class Meta:
        model = Proposta
        fields = ("conta", "valor_total", "condicao_pagamento", "status", "data_validade")


class ProdutoForm(ERPModelForm):
    class Meta:
        model = Produto
        fields = (
            "nome",
            "descricao",
            "sku",
            "preco",
            "eh_servico",
            "categoria_estetica",
            "duracao_minutos",
            "quantidade_estoque",
            "ativo",
        )


class PedidoVendaForm(ERPModelForm):
    class Meta:
        model = PedidoVenda
        fields = ("proposta", "valor_final", "data_faturamento")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = Proposta.objects.filter(status=Proposta.Status.GANHA)
        if self.instance.pk:
            queryset = Proposta.objects.filter(
                models.Q(status=Proposta.Status.GANHA)
                | models.Q(pk=self.instance.proposta_id)
            )
        self.fields["proposta"].queryset = queryset.order_by("-data_validade")


class ContratoForm(ERPModelForm):
    class Meta:
        model = Contrato
        fields = (
            "pedido_venda",
            "contract_date",
            "start_date",
            "end_date",
            "service_location",
            "service_description",
            "payment_terms",
            "client_representative",
            "termination_notice_days",
            "additional_clauses",
            "status",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        queryset = PedidoVenda.objects.filter(
            proposta__status=Proposta.Status.GANHA,
            contrato__isnull=True,
        )
        if self.instance.pk:
            queryset = PedidoVenda.objects.filter(
                models.Q(
                    proposta__status=Proposta.Status.GANHA,
                    contrato__isnull=True,
                )
                | models.Q(pk=self.instance.pedido_venda_id)
            )
        self.fields["pedido_venda"].queryset = queryset.select_related(
            "proposta__conta"
        )
        self.fields["service_description"].widget.attrs["rows"] = 5
        self.fields["payment_terms"].widget.attrs["rows"] = 4
        self.fields["additional_clauses"].widget.attrs["rows"] = 4


class ContaReceberForm(ERPModelForm):
    class Meta:
        model = ContaReceber
        fields = (
            "conta",
            "pedido_venda",
            "valor_parcela",
            "data_vencimento",
            "status_pagamento",
        )


def somar_meses(data, meses):
    """Mesmo dia nos meses seguintes; no fim do mês, usa o último dia (31/01 → 28/02)."""
    import calendar

    total = data.month - 1 + meses
    ano, mes = data.year + total // 12, total % 12 + 1
    return data.replace(year=ano, month=mes, day=min(data.day, calendar.monthrange(ano, mes)[1]))


class ContaPagarForm(ERPModelForm):
    repetir = forms.IntegerField(
        label="Repetir mensalmente",
        min_value=1,
        max_value=36,
        initial=1,
        required=False,
        help_text="Quantidade de meses (ex.: 12 para o aluguel do ano). 1 cria só esta conta.",
    )

    class Meta:
        model = ContaPagar
        fields = (
            "descricao",
            "favorecido",
            "categoria",
            "valor",
            "data_vencimento",
            "forma_pagamento",
            "codigo_barras",
            "cliente",
            "processo",
            "status",
            "data_pagamento",
            "valor_pago",
            "observacoes",
        )
        widgets = {"observacoes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.created_extra = []
        self.fields["repetir"].widget.attrs.update({"class": "form-control", "step": "1"})
        self.fields["cliente"].empty_label = "Nenhum"
        self.fields["processo"].empty_label = "Nenhum"
        self.fields["processo"].queryset = self.fields["processo"].queryset.select_related("cliente")
        if self.instance.pk:
            del self.fields["repetir"]
        else:
            # No cadastro a conta nasce em aberto; o pagamento é registrado depois.
            for name in ("status", "data_pagamento", "valor_pago"):
                del self.fields[name]

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get("status")
        if status == ContaPagar.Status.PAGO and not cleaned.get("data_pagamento"):
            cleaned["data_pagamento"] = timezone.localdate()
        if status and status != ContaPagar.Status.PAGO:
            cleaned["data_pagamento"] = None
            cleaned["valor_pago"] = None
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=commit)
        repetir = self.cleaned_data.get("repetir") or 1
        if commit and not self.created_extra and repetir > 1:
            base = instance.data_vencimento
            for meses in range(1, repetir):
                copia = ContaPagar.objects.get(pk=instance.pk)
                copia.pk = None
                copia.data_vencimento = somar_meses(base, meses)
                copia.save()
                self.created_extra.append(copia)
        return instance


class RegistrarPagamentoForm(forms.ModelForm):
    class Meta:
        model = ContaPagar
        fields = ("data_pagamento", "valor_pago", "forma_pagamento")
        widgets = {
            "data_pagamento": forms.DateInput(attrs={"type": "date", "class": "form-control"}, format="%Y-%m-%d"),
            "valor_pago": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "forma_pagamento": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["data_pagamento"].required = True
        if not self.initial.get("data_pagamento"):
            self.initial["data_pagamento"] = timezone.localdate()
        if self.instance.valor_pago is None:
            self.initial["valor_pago"] = self.instance.valor

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.status = ContaPagar.Status.PAGO
        if instance.valor_pago is None:
            instance.valor_pago = instance.valor
        if commit:
            instance.save()
        return instance


class ProcessoForm(ERPModelForm):
    class Meta:
        model = Processo
        fields = (
            "numero_cnj",
            "titulo",
            "cliente",
            "parte_contraria",
            "tribunal",
            "vara",
            "area",
            "status",
            "responsavel",
            "valor_causa",
            "advogados",
            "observacoes",
        )
        widgets = {"advogados": forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ativos = models.Q(ativo=True)
        if self.instance.pk:
            ativos |= models.Q(pk__in=self.instance.advogados.values("pk"))
        self.fields["advogados"].queryset = Advogado.objects.filter(ativos)


class AdvogadoForm(ERPModelForm):
    oab_numero = forms.CharField(
        label="Número da OAB", max_length=20, help_text="Só os números, sem a UF. Ex.: 123456."
    )

    class Meta:
        model = Advogado
        fields = ("nome", "oab_numero", "oab_uf", "tipo", "email", "telefone", "usuario", "ativo", "observacoes")
        widgets = {"observacoes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["usuario"].empty_label = "Nenhum"
        self.fields["oab_numero"].widget.attrs.update({"inputmode": "numeric", "autocomplete": "off"})

    def clean_oab_numero(self):
        numero = "".join(ch for ch in self.cleaned_data.get("oab_numero", "") if ch.isdigit()).lstrip("0")
        if not numero:
            raise forms.ValidationError("Informe o número da OAB.")
        if len(numero) > 10:
            raise forms.ValidationError("Número da OAB longo demais.")
        return numero


class MovimentacaoForm(ERPModelForm):
    class Meta:
        model = Movimentacao
        fields = ("processo", "data", "descricao", "origem")


class PrazoForm(ERPModelForm):
    class Meta:
        model = Prazo
        fields = ("processo", "titulo", "responsavel", "data_vencimento", "status", "observacoes")


class TarefaForm(ERPModelForm):
    class Meta:
        model = Tarefa
        fields = (
            "processo",
            "titulo",
            "descricao",
            "responsavel",
            "prioridade",
            "status",
            "vencimento",
        )


class PublicacaoForm(ERPModelForm):
    class Meta:
        model = Publicacao
        fields = (
            "processo",
            "monitoramento",
            "tribunal",
            "titulo",
            "conteudo",
            "publicada_em",
            "urgente",
            "processada",
        )


class AudienciaForm(ERPModelForm):
    class Meta:
        model = Audiencia
        fields = (
            "processo",
            "titulo",
            "data_hora",
            "modalidade",
            "local",
            "responsavel",
            "status",
            "observacoes",
        )


class AtendimentoForm(ERPModelForm):
    class Meta:
        model = Atendimento
        fields = ("cliente", "assunto", "data", "canal", "responsavel", "processo", "status", "relato")


class TimesheetForm(ERPModelForm):
    class Meta:
        model = Timesheet
        fields = ("processo", "usuario", "data", "minutos", "descricao", "valor_hora", "faturado")


class HonorarioForm(ERPModelForm):
    class Meta:
        model = Honorario
        fields = ("processo", "descricao", "tipo", "valor", "percentual_exito", "vencimento", "status")


class DespesaForm(ERPModelForm):
    class Meta:
        model = Despesa
        fields = ("processo", "descricao", "valor", "data", "status")


class PecaJuridicaForm(ERPModelForm):
    class Meta:
        model = PecaJuridica
        fields = ("tipo", "titulo", "processo", "instrucoes")
        widgets = {
            "instrucoes": forms.Textarea(
                attrs={
                    "rows": 10,
                    "placeholder": (
                        "Descreva os fatos, o que o cliente quer, provas disponíveis, valores, "
                        "datas e qualquer tese que deva ser usada."
                    ),
                }
            )
        }


class PecaConteudoForm(ERPModelForm):
    class Meta:
        model = PecaJuridica
        fields = ("titulo", "conteudo")
        widgets = {"conteudo": forms.Textarea(attrs={"rows": 30, "class": "form-control peca-editor"})}


class EmailNoticeForm(forms.Form):
    recipients = forms.ModelMultipleChoiceField(
        queryset=get_user_model().objects.none(),
        label="Destinatários",
        help_text="Somente usuários ativos com endereço de e-mail cadastrado.",
    )
    subject = forms.CharField(label="Assunto", max_length=200)
    message = forms.CharField(
        label="Mensagem",
        widget=forms.Textarea(attrs={"rows": 8}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["recipients"].queryset = get_user_model().objects.filter(
            is_active=True,
            email__gt="",
        ).order_by("username")
        self.fields["recipients"].widget.attrs["class"] = "form-select form-select-multiple"
        self.fields["subject"].widget.attrs["class"] = "form-control"
        self.fields["message"].widget.attrs["class"] = "form-control"

    def clean_subject(self):
        subject = self.cleaned_data["subject"]
        if "\r" in subject or "\n" in subject:
            raise forms.ValidationError("O assunto deve estar em uma única linha.")
        return subject


class CategoriaFinanceiraForm(ERPModelForm):
    class Meta:
        model = CategoriaFinanceira
        fields = ("tipo", "nome", "ativa")


class _CategoriaSelect(forms.Select):
    """Marca cada opção com o tipo (entrada/saída) para o formulário filtrar a lista."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        instance = getattr(value, "instance", None)
        if instance is not None:
            option["attrs"]["data-tipo"] = instance.tipo
        return option


class LancamentoForm(ERPModelForm):
    class Meta:
        model = Lancamento
        fields = ("data", "tipo", "categoria", "descricao", "valor", "forma_pagamento", "observacoes")
        widgets = {"categoria": _CategoriaSelect(attrs={"data-filter-by": "id_tipo"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ativas = CategoriaFinanceira.objects.filter(ativa=True)
        if self.instance.pk and self.instance.categoria_id:
            ativas = CategoriaFinanceira.objects.filter(
                models.Q(ativa=True) | models.Q(pk=self.instance.categoria_id)
            )
        self.fields["categoria"].queryset = ativas
        self.fields["forma_pagamento"].choices = [("", "—")] + list(ContaPagar.FormaPagamento.choices)


class ModeloDocumentoForm(ERPModelForm):
    class Meta:
        model = ModeloDocumento
        fields = (
            "nome", "titulo", "aplica_a", "corpo", "local", "assinatura",
            "incluir_telefone", "usar_logo", "rodape", "ordem", "ativo",
        )
        widgets = {"corpo": forms.Textarea(attrs={"rows": 16})}
