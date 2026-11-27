"""Controle financeiro (livro caixa): lançamentos de entrada e saída no formato da planilha."""

from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from financeiro.models import CategoriaFinanceira, Lancamento, TipoLancamento
from financeiro.planilha import PlanilhaInvalida, exportar, importar

from .models import CompanyProfile

MESES = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)
TAMANHO_MAXIMO = 5 * 1024 * 1024


def _require(request, permission):
    if not request.user.has_perm(permission):
        raise PermissionDenied


def _filtrar(request):
    """Aplica os filtros da tela. Devolve (queryset, filtros)."""
    hoje = timezone.localdate()
    mes = request.GET.get("mes")
    if mes is None:
        mes = f"{hoje:%Y-%m}"
    inicio = fim = None
    if mes:
        try:
            ano, numero = (int(parte) for parte in mes.split("-"))
            inicio = date(ano, numero, 1)
        except (ValueError, TypeError):
            mes = ""
        else:
            fim = (inicio + timedelta(days=32)).replace(day=1)

    queryset = Lancamento.objects.select_related("categoria")
    if inicio:
        queryset = queryset.filter(data__gte=inicio, data__lt=fim)

    tipo = request.GET.get("tipo", "")
    if tipo in TipoLancamento.values:
        queryset = queryset.filter(tipo=tipo)
    else:
        tipo = ""

    categoria = request.GET.get("categoria", "")
    if categoria.isdigit():
        queryset = queryset.filter(categoria_id=int(categoria))
    else:
        categoria = ""

    busca = request.GET.get("q", "").strip()
    if busca:
        queryset = queryset.filter(
            Q(descricao__icontains=busca) | Q(observacoes__icontains=busca) | Q(categoria__nome__icontains=busca)
        )
    periodo = f"{MESES[inicio.month - 1]} de {inicio.year}" if inicio else "todo o período"
    return queryset, {"mes": mes, "tipo": tipo, "categoria": categoria, "busca": busca, "periodo": periodo}


def _totais(queryset):
    dados = queryset.aggregate(
        entradas=Sum("valor", filter=Q(tipo=TipoLancamento.ENTRADA)),
        saidas=Sum("valor", filter=Q(tipo=TipoLancamento.SAIDA)),
        quantidade=Count("pk"),
    )
    entradas = dados["entradas"] or 0
    saidas = dados["saidas"] or 0
    return {"entradas": entradas, "saidas": saidas, "saldo": entradas - saidas, "quantidade": dados["quantidade"]}


@login_required
def controle_financeiro(request):
    _require(request, "financeiro.view_lancamento")
    queryset, filtros = _filtrar(request)
    lancamentos = list(queryset[:1000])
    por_categoria = (
        queryset.values("categoria__nome", "tipo")
        .annotate(total=Sum("valor"), quantidade=Count("pk"))
        .order_by("tipo", "-total")
    )
    params = request.GET.copy()
    return render(
        request,
        "core/controle_financeiro.html",
        {
            "page_title": "Controle financeiro",
            "active_module": "lancamentos",
            "lancamentos": lancamentos,
            "limitado": len(lancamentos) == 1000,
            "totais": _totais(queryset),
            "por_categoria": por_categoria,
            "categorias": CategoriaFinanceira.objects.all(),
            "tipos": TipoLancamento.choices,
            "querystring": params.urlencode(),
            "can_add": request.user.has_perm("financeiro.add_lancamento"),
            "can_change": request.user.has_perm("financeiro.change_lancamento"),
            "can_delete": request.user.has_perm("financeiro.delete_lancamento"),
            **filtros,
        },
    )


@login_required
def controle_financeiro_exportar(request):
    _require(request, "financeiro.view_lancamento")
    queryset, filtros = _filtrar(request)
    perfil = CompanyProfile.objects.first()
    empresa = (perfil.trade_name or perfil.legal_name) if perfil else ""
    titulo = f"{empresa.upper()} — CONTROLE FINANCEIRO" if empresa else "CONTROLE FINANCEIRO"
    if filtros["mes"]:
        titulo += f" · {filtros['periodo'].upper()}"
    response = HttpResponse(
        exportar(queryset, titulo),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    nome = slugify(f"controle-financeiro-{filtros['mes'] or 'completo'}")
    response["Content-Disposition"] = f'attachment; filename="{nome}.xlsx"'
    return response


@login_required
@require_POST
def controle_financeiro_importar(request):
    _require(request, "financeiro.add_lancamento")
    arquivo = request.FILES.get("arquivo")
    if not arquivo:
        messages.error(request, "Escolha a planilha .xlsx para importar.")
    elif arquivo.size > TAMANHO_MAXIMO:
        messages.error(request, "A planilha passa de 5 MB. Divida o arquivo e tente de novo.")
    elif not arquivo.name.lower().endswith((".xlsx", ".xlsm")):
        messages.error(request, "Envie a planilha no formato .xlsx (Excel).")
    else:
        try:
            resumo = importar(arquivo, usuario=request.user)
        except PlanilhaInvalida as exc:
            messages.error(request, str(exc))
        else:
            texto = f"{resumo['criados']} lançamento{'s' if resumo['criados'] != 1 else ''} importado{'s' if resumo['criados'] != 1 else ''}."
            if resumo["duplicados"]:
                texto += f" {resumo['duplicados']} já existia{'m' if resumo['duplicados'] != 1 else ''} e não foi duplicado."
            if resumo["categorias_novas"]:
                texto += " Categorias criadas: " + ", ".join(resumo["categorias_novas"]) + "."
            messages.success(request, texto)
            for aviso in resumo["avisos"][:10]:
                messages.warning(request, aviso)
            if len(resumo["avisos"]) > 10:
                messages.warning(request, f"E mais {len(resumo['avisos']) - 10} aviso(s).")
            # Mostra todo o período: a planilha costuma ter vários meses.
            return redirect(f"{reverse('lancamentos')}?mes=")
    return redirect("lancamentos")
