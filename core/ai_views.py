"""Telas de IA: configuração da conta, publicações, processos e assistente."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from juridico.ai import (
    analisar_publicacao,
    criar_tarefa_sugerida,
    perguntar_assistente,
    resumir_processo,
)
from juridico.models import Processo, Publicacao

from .ai import (
    ANTHROPIC_MODELS,
    DEFAULT_MODELS,
    AIError,
    feature_enabled,
    get_configuration,
    test_connection,
    tokens_used_this_month,
)
from .configuration_forms import AIConfigurationForm
from .models import AIConfiguration, AIUsageLog
from .secure_storage import decrypt_text

ASSISTANT_SESSION_KEY = "ai_assistant_history"
ASSISTANT_MAX_TURNS = 10
ASSISTANT_MAX_QUESTION = 8000


def _require(request, permission):
    if not request.user.has_perm(permission):
        raise PermissionDenied


@login_required
def ai_settings(request):
    if not request.user.is_superuser:
        raise PermissionDenied

    config = get_configuration()
    if request.method == "POST" and request.POST.get("action") == "test":
        if not config or not config.has_api_key:
            messages.error(request, "Salve a conexão com uma chave antes de testar.")
            return redirect("ai-settings")
        try:
            api_key = decrypt_text(config.api_key_encrypted)
        except ImproperlyConfigured as exc:
            messages.error(request, str(exc))
            return redirect("ai-settings")
        ok, detail = test_connection(config.provider, api_key, config.model)
        config.last_test_at = timezone.now()
        config.last_test_ok = ok
        config.last_test_message = detail[:500]
        config.save(update_fields=["last_test_at", "last_test_ok", "last_test_message"])
        (messages.success if ok else messages.error)(request, detail)
        return redirect("ai-settings")

    initial = {}
    if not config:
        initial = {
            "provider": AIConfiguration.Provider.ANTHROPIC,
            "model": DEFAULT_MODELS[AIConfiguration.Provider.ANTHROPIC],
        }
    form = AIConfigurationForm(
        request.POST if request.method == "POST" else None,
        instance=config,
        initial=initial,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(
            request,
            "Conexão de IA salva. Use “Testar conexão” para validar a chave.",
        )
        return redirect("ai-settings")

    month_start = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_logs = AIUsageLog.objects.filter(created_at__gte=month_start)
    by_feature = list(
        month_logs.values("feature")
        .annotate(
            calls=Count("id"),
            tokens_in=Sum("input_tokens"),
            tokens_out=Sum("output_tokens"),
        )
        .order_by("-calls")
    )
    labels = dict(AIUsageLog.Feature.choices)
    for row in by_feature:
        row["label"] = labels.get(row["feature"], row["feature"])
        row["tokens"] = (row["tokens_in"] or 0) + (row["tokens_out"] or 0)
    used = tokens_used_this_month()
    limit = config.monthly_token_limit if config else 0
    return render(
        request,
        "core/ai_settings.html",
        {
            "page_title": "Inteligência artificial",
            "active_module": "administration",
            "form": form,
            "config": config,
            "anthropic_models": ANTHROPIC_MODELS,
            "default_models": {key.value: value for key, value in DEFAULT_MODELS.items()},
            "tokens_used": used,
            "token_limit": limit,
            "token_percent": min(100, round(used * 100 / limit)) if limit else None,
            "month_calls": month_logs.count(),
            "month_errors": month_logs.filter(success=False).count(),
            "by_feature": by_feature,
            "recent_logs": AIUsageLog.objects.select_related("user")[:15],
        },
    )


@login_required
def publicacao_ai(request, pk):
    _require(request, "juridico.view_publicacao")
    publicacao = get_object_or_404(Publicacao.objects.select_related("processo"), pk=pk)
    can_analyze = request.user.has_perm("juridico.change_publicacao") and feature_enabled(
        AIUsageLog.Feature.PUBLICATION
    )

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "analisar":
            _require(request, "juridico.change_publicacao")
            try:
                analisar_publicacao(publicacao, user=request.user)
                messages.success(request, "Publicação analisada pela IA.")
            except AIError as exc:
                messages.error(request, str(exc))
        elif action == "criar_tarefa":
            _require(request, "juridico.add_tarefa")
            if not publicacao.analisada_em:
                messages.error(request, "Analise a publicação antes de criar a tarefa sugerida.")
            else:
                tarefa = criar_tarefa_sugerida(publicacao, request.user)
                messages.success(request, f"Tarefa “{tarefa.titulo}” criada. Confira o vencimento.")
        elif action == "processar":
            _require(request, "juridico.change_publicacao")
            publicacao.processada = True
            publicacao.save(update_fields=["processada"])
            messages.success(request, "Publicação marcada como processada.")
        return redirect("publicacao-ai", pk=publicacao.pk)

    return render(
        request,
        "core/publicacao_ai.html",
        {
            "page_title": publicacao.titulo,
            "active_module": "publicacoes",
            "publicacao": publicacao,
            "can_analyze": can_analyze,
            "ai_enabled": feature_enabled(AIUsageLog.Feature.PUBLICATION),
            "can_create_task": request.user.has_perm("juridico.add_tarefa"),
            "can_change": request.user.has_perm("juridico.change_publicacao"),
        },
    )


@login_required
def processo_ai(request, pk):
    _require(request, "juridico.view_processo")
    processo = get_object_or_404(
        Processo.objects.select_related("cliente", "responsavel"), pk=pk
    )
    if request.method == "POST":
        _require(request, "juridico.change_processo")
        try:
            resumir_processo(processo, user=request.user)
            messages.success(request, "Resumo do processo atualizado pela IA.")
        except AIError as exc:
            messages.error(request, str(exc))
        return redirect("processo-ai", pk=processo.pk)

    hoje = timezone.localdate()
    return render(
        request,
        "core/processo_ai.html",
        {
            "page_title": processo.numero_cnj,
            "active_module": "processos",
            "processo": processo,
            "today": hoje,
            "movimentacoes": processo.movimentacoes.all()[:8],
            "prazos_abertos": processo.prazos.filter(status="aberto").order_by("data_vencimento")[:6],
            "audiencias": processo.audiencias.filter(data_hora__date__gte=hoje).order_by("data_hora")[:4],
            "can_summarize": request.user.has_perm("juridico.change_processo")
            and feature_enabled(AIUsageLog.Feature.CASE_SUMMARY),
            "ai_enabled": feature_enabled(AIUsageLog.Feature.CASE_SUMMARY),
        },
    )


@login_required
def assistant(request):
    _require(request, "juridico.change_processo")
    history = request.session.get(ASSISTANT_SESSION_KEY, [])
    processo_id = request.POST.get("processo") or request.GET.get("processo") or ""
    processo = None
    if processo_id.isdigit():
        processo = Processo.objects.filter(pk=int(processo_id)).select_related(
            "cliente", "responsavel"
        ).first()

    if request.method == "POST":
        if request.POST.get("action") == "limpar":
            request.session[ASSISTANT_SESSION_KEY] = []
            return redirect("ai-assistant")
        pergunta = (request.POST.get("pergunta") or "").strip()[:ASSISTANT_MAX_QUESTION]
        if pergunta:
            try:
                resposta = perguntar_assistente(
                    pergunta,
                    processo=processo,
                    historico=[{"role": t["role"], "content": t["content"]} for t in history],
                    user=request.user,
                )
            except AIError as exc:
                messages.error(request, str(exc))
            else:
                history = (
                    history
                    + [
                        {"role": "user", "content": pergunta},
                        {"role": "assistant", "content": resposta},
                    ]
                )[-ASSISTANT_MAX_TURNS * 2 :]
                request.session[ASSISTANT_SESSION_KEY] = history
        url = redirect("ai-assistant").url
        return redirect(f"{url}?processo={processo.pk}" if processo else url)

    return render(
        request,
        "core/ai_assistant.html",
        {
            "page_title": "Assistente jurídico",
            "active_module": "assistente",
            "history": history,
            "processo": processo,
            "processos": Processo.objects.select_related("cliente").order_by("-atualizado_em")[:200],
            "ai_enabled": feature_enabled(AIUsageLog.Feature.ASSISTANT),
            "sugestoes": [
                "Quais providências devo tomar nos próximos 7 dias?",
                "Resuma o caso em linguagem simples para enviar ao cliente.",
                "Liste os riscos processuais e os pontos fracos da nossa tese.",
                "Faça um rascunho de petição simples de juntada de documentos.",
            ],
        },
    )


@login_required
@require_POST
def ai_settings_remove(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    AIConfiguration.objects.all().delete()
    messages.success(request, "Conta de IA desconectada e chave removida do banco.")
    return redirect("ai-settings")
