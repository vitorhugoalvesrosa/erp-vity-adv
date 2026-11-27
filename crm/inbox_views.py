"""Telas do atendimento pelo WhatsApp: caixa de entrada, conversa e configuração."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .forms import ModeloForm, NovaConversaForm, WhatsAppConfigurationForm
from .models import ConversaWhatsApp, MensagemWhatsApp, WhatsAppConfiguration, _novo_verify_token
from .whatsapp import (
    WhatsAppError,
    abrir_conversa,
    baixar_midia,
    credenciais,
    enviar_arquivo,
    enviar_modelo,
    enviar_texto,
    formatar_telefone,
    get_configuration,
    listar_modelos,
    marcar_como_lida,
    testar_conexao,
)

PERM_VER = "crm.view_conversawhatsapp"
PERM_ATENDER = "crm.change_conversawhatsapp"
PERM_INICIAR = "crm.add_conversawhatsapp"
MENSAGENS_NA_TELA = 200
FILTROS = (
    ("abertas", "Abertas"),
    ("nao_lidas", "Não lidas"),
    ("fila", "Aguardando equipe"),
    ("minhas", "Minhas"),
    ("bot", "Com o bot"),
    ("encerradas", "Encerradas"),
)
INLINE_MIME_PREFIXES = ("image/", "audio/", "video/")


def _require(request, permission):
    if not request.user.has_perm(permission):
        raise PermissionDenied


def _is_fetch(request):
    return request.headers.get("X-Requested-With") == "fetch"


def _conversas(request):
    filtro = request.GET.get("f") or "abertas"
    busca = (request.GET.get("q") or "").strip()
    qs = ConversaWhatsApp.objects.select_related("conta", "atendente")
    if filtro == "nao_lidas":
        qs = qs.filter(nao_lidas__gt=0)
    elif filtro == "fila":
        qs = qs.filter(status=ConversaWhatsApp.Status.AGUARDANDO_VENDEDOR)
    elif filtro == "minhas":
        qs = qs.filter(atendente=request.user).exclude(status=ConversaWhatsApp.Status.ENCERRADA)
    elif filtro == "bot":
        qs = qs.filter(bot_ativo=True).exclude(status=ConversaWhatsApp.Status.ENCERRADA)
    elif filtro == "encerradas":
        qs = qs.filter(status=ConversaWhatsApp.Status.ENCERRADA)
    else:
        filtro = "abertas"
        qs = qs.exclude(status=ConversaWhatsApp.Status.ENCERRADA)
    if busca:
        digitos = "".join(ch for ch in busca if ch.isdigit())
        condicao = Q(nome_contato__icontains=busca) | Q(conta__nome__icontains=busca)
        if digitos:
            condicao |= Q(telefone__contains=digitos)
        qs = qs.filter(condicao)
    return filtro, busca, qs.order_by("-ultima_mensagem_em", "-pk")[:150]


def _total_nao_lidas():
    return (
        ConversaWhatsApp.objects.exclude(status=ConversaWhatsApp.Status.ENCERRADA).aggregate(
            total=Sum("nao_lidas")
        )["total"]
        or 0
    )


def _modelos_disponiveis():
    cred = credenciais()
    if not cred.business_account_id or not cred.pode_enviar:
        return []
    chave = f"whatsapp-modelos:{cred.business_account_id}"
    modelos = cache.get(chave)
    if modelos is None:
        try:
            modelos = listar_modelos(cred)
        except WhatsAppError:
            modelos = []
        cache.set(chave, modelos, 300)
    return modelos


@login_required
def inbox(request, pk=None):
    _require(request, PERM_VER)
    filtro, busca, conversas = _conversas(request)
    conversa = None
    mensagens_qs = []
    if pk:
        conversa = get_object_or_404(ConversaWhatsApp.objects.select_related("conta", "atendente"), pk=pk)
        if request.user.has_perm(PERM_ATENDER):
            marcar_como_lida(conversa)
        mensagens_qs = list(
            conversa.mensagens.select_related("enviada_por").order_by("-pk")[:MENSAGENS_NA_TELA]
        )[::-1]
    cred = credenciais()
    pode_atender = request.user.has_perm(PERM_ATENDER)
    return render(
        request,
        "crm/whatsapp/inbox.html",
        {
            "page_title": conversa.nome_exibicao if conversa else "Atendimento WhatsApp",
            "active_module": "whatsapp",
            "conversas": conversas,
            "conversa": conversa,
            "mensagens": mensagens_qs,
            "filtro": filtro,
            "filtros": FILTROS,
            "busca": busca,
            "configurado": cred.pode_enviar,
            "pode_atender": pode_atender,
            "pode_iniciar": request.user.has_perm(PERM_INICIAR),
            "modelos": _modelos_disponiveis() if conversa and pode_atender and not conversa.janela_aberta else [],
            "modelo_form": ModeloForm(),
            "telefone_formatado": formatar_telefone(conversa.telefone) if conversa else "",
            "total_nao_lidas": _total_nao_lidas(),
        },
    )


@login_required
@require_GET
def lista(request):
    _require(request, PERM_VER)
    filtro, busca, conversas = _conversas(request)
    selecionada = request.GET.get("c") or ""
    html = render_to_string(
        "crm/whatsapp/_lista.html",
        {
            "conversas": conversas,
            "conversa": {"pk": int(selecionada)} if selecionada.isdigit() else None,
            "filtro": filtro,
            "busca": busca,
        },
        request=request,
    )
    return JsonResponse({"html": html, "nao_lidas": _total_nao_lidas()})


def _mensagem_html(request, mensagem):
    return render_to_string("crm/whatsapp/_mensagem.html", {"mensagem": mensagem}, request=request)


@login_required
@require_GET
def atualizacoes(request, pk):
    _require(request, PERM_VER)
    conversa = get_object_or_404(ConversaWhatsApp.objects.select_related("conta", "atendente"), pk=pk)
    apos = request.GET.get("apos") or "0"
    apos = int(apos) if apos.isdigit() else 0
    novas = list(conversa.mensagens.select_related("enviada_por").filter(pk__gt=apos).order_by("pk")[:100])
    if novas and request.user.has_perm(PERM_ATENDER):
        marcar_como_lida(conversa)
    recentes = conversa.mensagens.filter(direcao=MensagemWhatsApp.Direcao.SAIDA).order_by("-pk")[:60]
    return JsonResponse(
        {
            "mensagens": [{"id": m.pk, "html": _mensagem_html(request, m)} for m in novas],
            "status": {str(m.pk): {"status": m.status, "erro": m.erro} for m in recentes},
            "cabecalho": render_to_string(
                "crm/whatsapp/_cabecalho.html",
                {
                    "conversa": conversa,
                    "pode_atender": request.user.has_perm(PERM_ATENDER),
                    "telefone_formatado": formatar_telefone(conversa.telefone),
                },
                request=request,
            ),
            "janela_aberta": conversa.janela_aberta,
        }
    )


def _assumir(conversa, user, status=ConversaWhatsApp.Status.AGUARDANDO_CLIENTE):
    if not conversa.atendente_id:
        conversa.atendente = user
    conversa.bot_ativo = False
    conversa.status = status
    conversa.save(update_fields=["atendente", "bot_ativo", "status", "atualizada_em"])


@login_required
@require_POST
def enviar(request, pk):
    _require(request, PERM_ATENDER)
    conversa = get_object_or_404(ConversaWhatsApp.objects.select_related("conta"), pk=pk)
    arquivo = request.FILES.get("arquivo")
    texto = request.POST.get("texto", "")
    ultima = conversa.mensagens.order_by("-pk").values_list("pk", flat=True).first() or 0
    try:
        if arquivo:
            mensagem = enviar_arquivo(conversa, arquivo, legenda=texto, usuario=request.user)
        else:
            mensagem = enviar_texto(conversa, texto, usuario=request.user)
    except WhatsAppError as exc:
        if _is_fetch(request):
            # Se o envio chegou a ser registrado, a mensagem aparece marcada como falha.
            falha = conversa.mensagens.filter(pk__gt=ultima).order_by("-pk").first()
            dados = {"erro": str(exc)}
            if falha:
                dados["mensagem"] = {"id": falha.pk, "html": _mensagem_html(request, falha)}
            return JsonResponse(dados, status=400)
        messages.error(request, str(exc))
        return redirect("whatsapp-conversa", pk=conversa.pk)
    # Quem responde pela equipe assume a conversa: o bot para de responder.
    _assumir(conversa, request.user)
    if _is_fetch(request):
        return JsonResponse({"mensagem": {"id": mensagem.pk, "html": _mensagem_html(request, mensagem)}})
    return redirect("whatsapp-conversa", pk=conversa.pk)


@login_required
@require_POST
def modelo(request, pk):
    _require(request, PERM_ATENDER)
    conversa = get_object_or_404(ConversaWhatsApp, pk=pk)
    form = ModeloForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Informe o modelo e o idioma.")
        return redirect("whatsapp-conversa", pk=conversa.pk)
    try:
        enviar_modelo(
            conversa,
            form.cleaned_data["modelo"],
            form.cleaned_data["idioma"],
            form.lista_parametros(),
            corpo=form.cleaned_data.get("corpo", ""),
            usuario=request.user,
        )
    except WhatsAppError as exc:
        messages.error(request, str(exc))
    else:
        _assumir(conversa, request.user)
        messages.success(request, "Modelo enviado. Quando o cliente responder, a conversa fica liberada por 24 h.")
    return redirect("whatsapp-conversa", pk=conversa.pk)


@login_required
@require_POST
def acao(request, pk):
    _require(request, PERM_ATENDER)
    conversa = get_object_or_404(ConversaWhatsApp, pk=pk)
    action = request.POST.get("action")
    if action == "assumir":
        conversa.atendente = request.user
        conversa.bot_ativo = False
        if conversa.status != ConversaWhatsApp.Status.ENCERRADA:
            conversa.status = ConversaWhatsApp.Status.AGUARDANDO_VENDEDOR
        mensagem = "Você assumiu a conversa. O bot não responde mais este contato."
    elif action == "devolver_bot":
        conversa.atendente = None
        conversa.bot_ativo = True
        conversa.status = ConversaWhatsApp.Status.ATIVA
        mensagem = "O bot voltou a responder este contato."
    elif action == "encerrar":
        conversa.status = ConversaWhatsApp.Status.ENCERRADA
        conversa.atendente = None
        conversa.bot_ativo = True
        conversa.nao_lidas = 0
        mensagem = "Conversa encerrada. Se o cliente escrever de novo, o bot recomeça o atendimento."
    elif action == "reabrir":
        conversa.status = ConversaWhatsApp.Status.AGUARDANDO_VENDEDOR
        conversa.atendente = request.user
        conversa.bot_ativo = False
        mensagem = "Conversa reaberta com você."
    else:
        return redirect("whatsapp-conversa", pk=conversa.pk)
    conversa.save()
    messages.success(request, mensagem)
    return redirect("whatsapp-conversa", pk=conversa.pk)


@login_required
def nova(request):
    _require(request, PERM_INICIAR)
    _require(request, PERM_ATENDER)
    form = NovaConversaForm(request.POST or None, initial={"telefone": request.GET.get("telefone", "")})
    modelo_form = ModeloForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        conversa = abrir_conversa(
            form.cleaned_data["telefone"],
            nome=form.cleaned_data["nome"],
            conta=form.cleaned_data["conta"],
        )
        if conversa.janela_aberta:
            messages.info(request, "O cliente falou com vocês nas últimas 24 h: responda direto pela conversa.")
            return redirect("whatsapp-conversa", pk=conversa.pk)
        if modelo_form.is_valid():
            try:
                enviar_modelo(
                    conversa,
                    modelo_form.cleaned_data["modelo"],
                    modelo_form.cleaned_data["idioma"],
                    modelo_form.lista_parametros(),
                    corpo=modelo_form.cleaned_data.get("corpo", ""),
                    usuario=request.user,
                )
            except WhatsAppError as exc:
                messages.error(request, str(exc))
            else:
                _assumir(conversa, request.user)
                messages.success(request, "Conversa iniciada. Assim que o cliente responder, você conversa livremente.")
            return redirect("whatsapp-conversa", pk=conversa.pk)
    contas = form.fields["conta"].queryset.values("pk", "nome", "telefone")
    return render(
        request,
        "crm/whatsapp/nova.html",
        {
            "page_title": "Nova conversa",
            "active_module": "whatsapp",
            "form": form,
            "modelo_form": modelo_form,
            "modelos": _modelos_disponiveis(),
            "configurado": credenciais().pode_enviar,
            "contas_telefones": {str(c["pk"]): {"telefone": c["telefone"], "nome": c["nome"]} for c in contas},
        },
    )


@login_required
@require_GET
def midia(request, pk):
    _require(request, PERM_VER)
    mensagem = get_object_or_404(MensagemWhatsApp, pk=pk)
    if not mensagem.midia_id:
        raise PermissionDenied
    try:
        conteudo, mime = baixar_midia(mensagem.midia_id)
    except WhatsAppError as exc:
        return HttpResponse(str(exc), status=502, content_type="text/plain; charset=utf-8")
    mime = (mensagem.midia_mime or mime).split(";")[0]
    inline = mime.startswith(INLINE_MIME_PREFIXES) and "svg" not in mime
    response = HttpResponse(conteudo, content_type=mime if inline else "application/octet-stream")
    nome = (mensagem.midia_nome or f"whatsapp-{mensagem.pk}").replace('"', "")
    response["Content-Disposition"] = f'{"inline" if inline else "attachment"}; filename="{nome}"'
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, max-age=3600"
    return response


# --- Administração ------------------------------------------------------------------


@login_required
def configuracao(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    config = get_configuration()
    action = request.POST.get("action") if request.method == "POST" else None

    if action == "test":
        cred = credenciais()
        if not config or not cred.pode_enviar:
            messages.error(request, "Salve a conexão com o ID do número e o token antes de testar.")
            return redirect("whatsapp-settings")
        try:
            numero, nome = testar_conexao(cred)
        except WhatsAppError as exc:
            ok, detalhe = False, str(exc)
        else:
            ok, detalhe = True, f"Conectado ao número {numero}" + (f" ({nome})." if nome else ".")
            config.display_phone_number = numero
            config.verified_name = nome
        config.last_test_at = timezone.now()
        config.last_test_ok = ok
        config.last_test_message = detalhe[:500]
        config.save()
        (messages.success if ok else messages.error)(request, detalhe)
        return redirect("whatsapp-settings")

    if action == "novo_token" and config:
        config.verify_token = _novo_verify_token()
        config.save(update_fields=["verify_token", "updated_at"])
        messages.success(request, "Novo token de verificação gerado. Atualize-o no painel da Meta.")
        return redirect("whatsapp-settings")

    form = WhatsAppConfigurationForm(request.POST if action == "save" else None, instance=config)
    if action == "save" and form.is_valid():
        form.save()
        messages.success(request, "Conexão do WhatsApp salva. Use “Testar conexão” para validar.")
        return redirect("whatsapp-settings")

    inicio_mes = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    no_mes = MensagemWhatsApp.objects.filter(criada_em__gte=inicio_mes).aggregate(
        recebidas=Count("pk", filter=Q(direcao=MensagemWhatsApp.Direcao.ENTRADA)),
        enviadas=Count("pk", filter=Q(direcao=MensagemWhatsApp.Direcao.SAIDA)),
        pelo_bot=Count("pk", filter=Q(direcao=MensagemWhatsApp.Direcao.SAIDA, gerada_por_ia=True)),
        falhas=Count("pk", filter=Q(status=MensagemWhatsApp.Status.FALHOU)),
    )
    from core.ai import feature_enabled
    from core.models import AIUsageLog

    return render(
        request,
        "crm/whatsapp/configuracao.html",
        {
            "page_title": "WhatsApp",
            "active_module": "administration",
            "form": form,
            "config": config,
            "webhook_url": request.build_absolute_uri(reverse("crm-whatsapp-webhook")),
            "no_mes": no_mes,
            "ia_ligada": feature_enabled(AIUsageLog.Feature.WHATSAPP),
            "conversas_abertas": ConversaWhatsApp.objects.exclude(status=ConversaWhatsApp.Status.ENCERRADA).count(),
        },
    )


@login_required
@require_POST
def configuracao_remover(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    WhatsAppConfiguration.objects.all().delete()
    messages.success(request, "WhatsApp desconectado. Token e chave secreta foram apagados do banco.")
    return redirect("whatsapp-settings")
