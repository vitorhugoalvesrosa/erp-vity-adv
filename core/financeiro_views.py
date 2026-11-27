"""Contas a pagar: lista com indicadores e filtros, registro e estorno de pagamento."""

from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from financeiro.models import ContaPagar

from .forms import RegistrarPagamentoForm

FILTROS = (
    ("abertas", "Em aberto"),
    ("atrasadas", "Atrasadas"),
    ("semana", "Próximos 7 dias"),
    ("pagas", "Pagas"),
    ("canceladas", "Canceladas"),
    ("todas", "Todas"),
)
TONS = {
    "atrasado": "danger",
    "vence_hoje": "warning",
    "a_pagar": "info",
    "pago": "success",
    "cancelado": "neutral",
}


def _require(request, permission):
    if not request.user.has_perm(permission):
        raise PermissionDenied


def _resumo(queryset, campo="valor"):
    dados = queryset.aggregate(total=Sum(campo), quantidade=Count("pk"))
    return {"total": dados["total"] or 0, "quantidade": dados["quantidade"]}


def _primeiro_dia_do_mes(data):
    return data.replace(day=1)


@login_required
def contas_pagar(request):
    _require(request, "financeiro.view_contapagar")
    hoje = timezone.localdate()
    semana = hoje + timedelta(days=7)
    inicio_mes = _primeiro_dia_do_mes(hoje)
    todas = ContaPagar.objects.all()
    abertas = todas.filter(status=ContaPagar.Status.A_PAGAR)

    kpis = {
        "abertas": _resumo(abertas),
        "atrasadas": _resumo(abertas.filter(data_vencimento__lt=hoje)),
        "semana": _resumo(abertas.filter(data_vencimento__gte=hoje, data_vencimento__lte=semana)),
        "pagas_mes": _resumo(
            todas.filter(status=ContaPagar.Status.PAGO, data_pagamento__gte=inicio_mes, data_pagamento__lte=hoje)
            .annotate(efetivo=Coalesce("valor_pago", "valor")),
            campo="efetivo",
        ),
    }

    filtro = request.GET.get("f") or "abertas"
    if filtro == "atrasadas":
        queryset = abertas.filter(data_vencimento__lt=hoje)
    elif filtro == "semana":
        queryset = abertas.filter(data_vencimento__gte=hoje, data_vencimento__lte=semana)
    elif filtro == "pagas":
        queryset = todas.filter(status=ContaPagar.Status.PAGO).order_by("-data_pagamento", "-pk")
    elif filtro == "canceladas":
        queryset = todas.filter(status=ContaPagar.Status.CANCELADO)
    elif filtro == "todas":
        queryset = todas
    else:
        filtro = "abertas"
        queryset = abertas

    categoria = request.GET.get("categoria", "")
    if categoria in ContaPagar.Categoria.values:
        queryset = queryset.filter(categoria=categoria)
    else:
        categoria = ""

    mes = request.GET.get("mes", "")
    try:
        ano, numero = (int(parte) for parte in mes.split("-"))
        inicio = date(ano, numero, 1)
    except (ValueError, TypeError):
        mes = ""
    else:
        fim = (inicio + timedelta(days=32)).replace(day=1)
        campo = "data_pagamento" if filtro == "pagas" else "data_vencimento"
        queryset = queryset.filter(**{f"{campo}__gte": inicio, f"{campo}__lt": fim})

    busca = request.GET.get("q", "").strip()
    if busca:
        queryset = queryset.filter(
            Q(descricao__icontains=busca)
            | Q(favorecido__icontains=busca)
            | Q(cliente__nome__icontains=busca)
            | Q(processo__numero_cnj__icontains=busca)
            | Q(observacoes__icontains=busca)
        )

    queryset = queryset.select_related("cliente", "processo")
    total_filtrado = queryset.annotate(efetivo=Coalesce("valor_pago", "valor")).aggregate(
        total=Sum("efetivo" if filtro == "pagas" else "valor")
    )["total"] or 0
    page_obj = Paginator(queryset, 30).get_page(request.GET.get("page"))
    contas = list(page_obj.object_list)
    for conta in contas:
        conta.tom = TONS.get(conta.situacao, "neutral")

    params = request.GET.copy()
    params.pop("page", None)
    return render(
        request,
        "core/contas_pagar.html",
        {
            "page_title": "Contas a pagar",
            "active_module": "contas-pagar",
            "kpis": kpis,
            "filtros": FILTROS,
            "filtro": filtro,
            "categorias": ContaPagar.Categoria.choices,
            "categoria": categoria,
            "mes": mes,
            "busca": busca,
            "contas": contas,
            "page_obj": page_obj,
            "querystring": params.urlencode(),
            "total_filtrado": total_filtrado,
            "can_add": request.user.has_perm("financeiro.add_contapagar"),
            "can_change": request.user.has_perm("financeiro.change_contapagar"),
            "can_delete": request.user.has_perm("financeiro.delete_contapagar"),
        },
    )


def _voltar(request):
    destino = request.POST.get("next") or request.GET.get("next") or ""
    if destino.startswith("/app/contas-pagar/") and "//" not in destino:
        return redirect(destino)
    return redirect("contas-pagar")


@login_required
def registrar_pagamento(request, pk):
    _require(request, "financeiro.change_contapagar")
    conta = get_object_or_404(ContaPagar.objects.select_related("cliente", "processo"), pk=pk)
    if conta.status == ContaPagar.Status.CANCELADO:
        messages.error(request, "Esta conta está cancelada. Reabra-a na edição antes de pagar.")
        return _voltar(request)
    form = RegistrarPagamentoForm(request.POST or None, instance=conta)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Pagamento de “{conta.descricao}” registrado.")
        return _voltar(request)
    return render(
        request,
        "core/conta_pagar_pagamento.html",
        {
            "page_title": "Registrar pagamento",
            "active_module": "contas-pagar",
            "conta": conta,
            "form": form,
            "next": request.GET.get("next", ""),
        },
    )


@login_required
@require_POST
def estornar_pagamento(request, pk):
    _require(request, "financeiro.change_contapagar")
    conta = get_object_or_404(ContaPagar, pk=pk)
    if conta.status == ContaPagar.Status.PAGO:
        conta.status = ContaPagar.Status.A_PAGAR
        conta.data_pagamento = None
        conta.valor_pago = None
        conta.save(update_fields=["status", "data_pagamento", "valor_pago", "atualizado_em"])
        messages.success(request, f"Pagamento de “{conta.descricao}” desfeito. A conta voltou para em aberto.")
    return _voltar(request)
