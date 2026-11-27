"""Ficha do advogado: processos vinculados e busca dos processos da OAB no DJEN."""

import logging

from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from juridico.djen import DJENErro, sincronizar_advogado
from juridico.models import Advogado, ProcessoEncontrado

logger = logging.getLogger(__name__)

PERIODOS = ((30, "Últimos 30 dias"), (90, "Últimos 3 meses"), (180, "Últimos 6 meses"), (365, "Último ano"))


def _require(request, permission):
    if not request.user.has_perm(permission):
        raise PermissionDenied


def _link_cadastro(encontrado, advogado):
    partes = [p for p in (encontrado.polo_ativo, encontrado.polo_passivo) if p]
    titulo = " x ".join(partes) or encontrado.classe or encontrado.numero_cnj
    params = {
        "numero_cnj": encontrado.numero_cnj,
        "titulo": titulo[:255],
        "tribunal": encontrado.tribunal,
        "vara": encontrado.orgao[:120],
        "parte_contraria": encontrado.polo_passivo[:255],
        "observacoes": (
            f"Encontrado no DJEN pela {advogado.oab}. Classe: {encontrado.classe or '—'}. "
            f"Polo ativo: {encontrado.polo_ativo or '—'}. Polo passivo: {encontrado.polo_passivo or '—'}."
        )[:500],
        "encontrado": encontrado.pk,
    }
    query = urlencode(params) + "&" + urlencode([("advogados", a.pk) for a in encontrado.advogados.all()])
    return f"{reverse('record-create', args=['processos'])}?{query}"


@login_required
def advogado_detalhe(request, pk):
    _require(request, "juridico.view_advogado")
    advogado = get_object_or_404(Advogado, pk=pk)
    processos = advogado.processos.select_related("cliente").order_by("-atualizado_em")
    novos = list(
        advogado.processos_encontrados.filter(situacao=ProcessoEncontrado.Situacao.NOVO).prefetch_related("advogados")
    )
    for item in novos:
        item.link_cadastro = _link_cadastro(item, advogado)
    ignorados = advogado.processos_encontrados.filter(situacao=ProcessoEncontrado.Situacao.IGNORADO)
    return render(
        request,
        "core/advogado_detalhe.html",
        {
            "page_title": advogado.nome,
            "active_module": "advogados",
            "advogado": advogado,
            "processos": processos,
            "novos": novos,
            "ignorados": ignorados,
            "periodos": PERIODOS,
            "proxy_configurado": bool(settings.DJEN_BASE_URL),
            "can_search": request.user.has_perm("juridico.change_advogado"),
            "can_add_processo": request.user.has_perm("juridico.add_processo"),
        },
    )


@login_required
@require_POST
def advogado_buscar_djen(request, pk):
    _require(request, "juridico.change_advogado")
    advogado = get_object_or_404(Advogado, pk=pk)
    try:
        dias = int(request.POST.get("dias", 365))
    except ValueError:
        dias = 365
    dias = dias if dias in dict(PERIODOS) else 365
    try:
        resumo = sincronizar_advogado(advogado, dias=dias)
    except DJENErro as exc:
        messages.error(request, str(exc))
    except Exception as exc:  # nunca deixar a busca virar erro 500
        logger.exception("Falha inesperada na busca do DJEN para o advogado %s", advogado.pk)
        messages.error(
            request,
            f"Erro inesperado na busca ({type(exc).__name__}). Tente de novo; se repetir, verifique o log do Render.",
        )
    else:
        if not resumo["processos"]:
            messages.info(request, f"Nenhuma publicação da {advogado.oab} no período no DJEN.")
        else:
            texto = (
                f"{resumo['processos']} processo(s) encontrados em {resumo['publicacoes']} publicação(ões): "
                f"{resumo['vinculados']} já estavam no ERP e foram vinculados, {resumo['novos']} ainda não cadastrados."
            )
            messages.success(request, texto)
            if resumo.get("incompleto"):
                messages.warning(request, "Havia mais publicações do que o limite de uma busca. Busque com um período menor.")
    return redirect("advogado-detalhe", pk=advogado.pk)


@login_required
@require_POST
def encontrado_situacao(request, pk):
    _require(request, "juridico.change_advogado")
    encontrado = get_object_or_404(ProcessoEncontrado, pk=pk)
    acao = request.POST.get("acao")
    if acao == "ignorar" and encontrado.situacao == ProcessoEncontrado.Situacao.NOVO:
        encontrado.situacao = ProcessoEncontrado.Situacao.IGNORADO
    elif acao == "restaurar" and encontrado.situacao == ProcessoEncontrado.Situacao.IGNORADO:
        encontrado.situacao = ProcessoEncontrado.Situacao.NOVO
    encontrado.save(update_fields=["situacao", "atualizado_em"])
    voltar = request.POST.get("advogado", "")
    if voltar.isdigit():
        return redirect("advogado-detalhe", pk=int(voltar))
    return redirect("record-list", module="advogados")
