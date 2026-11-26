"""Telas jurídicas: peças com IA, kanban de tarefas e atendimentos."""

import json
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from juridico.ai import gerar_peca
from juridico.docx_export import peca_para_docx
from juridico.models import Atendimento, PecaJuridica, Processo, Tarefa

from .ai import AIError, feature_enabled
from .forms import PecaConteudoForm, PecaJuridicaForm
from .models import AIUsageLog

KANBAN_COLUMNS = (
    (Tarefa.Status.PENDENTE, "A fazer"),
    (Tarefa.Status.EM_ANDAMENTO, "Fazendo"),
    (Tarefa.Status.CONCLUIDA, "Concluída"),
)
PRIORIDADE_ORDEM = {"urgente": 0, "alta": 1, "media": 2, "baixa": 3}


def _require(request, permission):
    if not request.user.has_perm(permission):
        raise PermissionDenied


# --- Peças jurídicas -------------------------------------------------------------------


@login_required
def pecas(request):
    _require(request, "juridico.view_pecajuridica")
    queryset = PecaJuridica.objects.select_related("processo", "criado_por")
    busca = request.GET.get("q", "").strip()
    if busca:
        queryset = queryset.filter(
            Q(titulo__icontains=busca)
            | Q(processo__numero_cnj__icontains=busca)
            | Q(processo__titulo__icontains=busca)
        )
    page_obj = Paginator(queryset, 20).get_page(request.GET.get("page"))
    return render(
        request,
        "core/pecas.html",
        {
            "page_title": "Peças com IA",
            "active_module": "pecas",
            "page_obj": page_obj,
            "search": busca,
            "tipos": PecaJuridica.Tipo.choices,
            "ai_enabled": feature_enabled(AIUsageLog.Feature.DRAFT),
        },
    )


@login_required
def peca_nova(request):
    _require(request, "juridico.add_pecajuridica")
    initial = {}
    if request.method == "GET":
        if request.GET.get("tipo") in PecaJuridica.Tipo.values:
            initial["tipo"] = request.GET["tipo"]
        if request.GET.get("processo", "").isdigit():
            processo = Processo.objects.filter(pk=request.GET["processo"]).first()
            if processo:
                initial["processo"] = processo.pk
                initial["titulo"] = f"{processo.titulo}"[:255]
    form = PecaJuridicaForm(request.POST or None, initial=initial)
    ai_enabled = feature_enabled(AIUsageLog.Feature.DRAFT)
    if request.method == "POST" and form.is_valid():
        peca = form.save(commit=False)
        peca.criado_por = request.user
        peca.gerada_por_ia = ai_enabled
        peca.save()
        if ai_enabled:
            try:
                gerar_peca(peca, user=request.user)
                messages.success(request, "Minuta gerada pela IA. Revise antes de usar.")
            except AIError as exc:
                messages.error(request, f"A peça foi salva, mas a IA não gerou a minuta: {exc}")
        else:
            messages.info(request, "Peça salva sem IA. Escreva o conteúdo manualmente.")
        return redirect("peca-detalhe", pk=peca.pk)
    return render(
        request,
        "core/peca_form.html",
        {
            "page_title": "Nova peça",
            "active_module": "pecas",
            "form": form,
            "ai_enabled": ai_enabled,
        },
    )


@login_required
def peca_detalhe(request, pk):
    _require(request, "juridico.view_pecajuridica")
    peca = get_object_or_404(PecaJuridica.objects.select_related("processo", "criado_por"), pk=pk)
    can_change = request.user.has_perm("juridico.change_pecajuridica")
    form = PecaConteudoForm(request.POST or None, instance=peca)
    if request.method == "POST":
        _require(request, "juridico.change_pecajuridica")
        if request.POST.get("action") == "regenerar":
            try:
                gerar_peca(peca, user=request.user)
                messages.success(request, "Nova minuta gerada. A versão anterior foi substituída.")
            except AIError as exc:
                messages.error(request, str(exc))
            return redirect("peca-detalhe", pk=peca.pk)
        if form.is_valid():
            form.save()
            messages.success(request, "Peça salva.")
            return redirect("peca-detalhe", pk=peca.pk)
    return render(
        request,
        "core/peca_detalhe.html",
        {
            "page_title": peca.titulo,
            "active_module": "pecas",
            "peca": peca,
            "form": form,
            "can_change": can_change,
            "ai_enabled": feature_enabled(AIUsageLog.Feature.DRAFT),
        },
    )


@login_required
def peca_docx(request, pk):
    _require(request, "juridico.view_pecajuridica")
    peca = get_object_or_404(PecaJuridica, pk=pk)
    response = HttpResponse(
        peca_para_docx(peca),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    filename = slugify(peca.titulo)[:80] or f"peca-{peca.pk}"
    response["Content-Disposition"] = f'attachment; filename="{filename}.docx"'
    return response


# --- Kanban de tarefas -----------------------------------------------------------------


@login_required
def kanban(request):
    _require(request, "juridico.view_tarefa")
    escopo = request.GET.get("escopo", "minhas")
    tarefas = Tarefa.objects.select_related("processo", "responsavel").exclude(
        status=Tarefa.Status.CANCELADA
    )
    if escopo == "minhas":
        tarefas = tarefas.filter(responsavel=request.user)
    responsavel = request.GET.get("responsavel", "")
    if escopo == "todas" and responsavel.isdigit():
        tarefas = tarefas.filter(responsavel_id=responsavel)
    # Concluídas antigas não poluem o quadro.
    limite = timezone.now() - timezone.timedelta(days=14)
    tarefas = tarefas.exclude(status=Tarefa.Status.CONCLUIDA, concluida_em__lt=limite)

    hoje = timezone.localdate()
    colunas = []
    for status, titulo in KANBAN_COLUMNS:
        cards = sorted(
            (t for t in tarefas if t.status == status),
            key=lambda t: (t.vencimento is None, t.vencimento or hoje, PRIORIDADE_ORDEM.get(t.prioridade, 9)),
        )
        colunas.append({"status": status, "titulo": titulo, "cards": cards})
    return render(
        request,
        "core/kanban.html",
        {
            "page_title": "Quadro de tarefas",
            "active_module": "kanban",
            "colunas": colunas,
            "escopo": escopo,
            "responsavel": responsavel,
            "usuarios": get_user_model().objects.filter(is_active=True).order_by("username"),
            "today": hoje,
            "can_change": request.user.has_perm("juridico.change_tarefa"),
            "status_values": json.dumps([status for status, _ in KANBAN_COLUMNS]),
        },
    )


@login_required
@require_POST
def kanban_mover(request, pk):
    _require(request, "juridico.change_tarefa")
    tarefa = get_object_or_404(Tarefa, pk=pk)
    status = request.POST.get("status")
    if status not in {value for value, _ in KANBAN_COLUMNS}:
        return JsonResponse({"detail": "Status inválido."}, status=400)
    tarefa.status = status
    tarefa.concluida_em = timezone.now() if status == Tarefa.Status.CONCLUIDA else None
    tarefa.save(update_fields=["status", "concluida_em"])
    if request.headers.get("x-requested-with") == "fetch":
        return JsonResponse({"id": tarefa.pk, "status": tarefa.status})
    return redirect(request.POST.get("next") or "kanban")


# --- Atendimentos ----------------------------------------------------------------------


@login_required
def atendimento_converter(request, pk):
    """Abre o cadastro de processo preenchido com os dados do atendimento."""
    _require(request, "juridico.add_processo")
    atendimento = get_object_or_404(Atendimento.objects.select_related("cliente"), pk=pk)
    params = urlencode(
        {
            "cliente": atendimento.cliente_id,
            "titulo": atendimento.assunto,
            "observacoes": f"Originado do atendimento de {timezone.localtime(atendimento.data):%d/%m/%Y}.\n\n{atendimento.relato}"[:500],
            "atendimento": atendimento.pk,
        }
    )
    return redirect(f"{reverse('record-create', args=['processos'])}?{params}")
