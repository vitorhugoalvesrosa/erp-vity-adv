import hashlib
import hmac
import json
import logging

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods
from rest_framework import viewsets

from .models import (
    Conta,
    ConversaWhatsApp,
    FollowUp,
    MensagemWhatsApp,
    Proposta,
)
from .serializers import (
    ContaSerializer,
    ConversaWhatsAppSerializer,
    FollowUpSerializer,
    MensagemWhatsAppSerializer,
    PropostaSerializer,
)
from .whatsapp import credenciais, processar_webhook

logger = logging.getLogger(__name__)


class ContaViewSet(viewsets.ModelViewSet):
    queryset = Conta.objects.all().order_by("-data_criacao")
    serializer_class = ContaSerializer


class PropostaViewSet(viewsets.ModelViewSet):
    queryset = Proposta.objects.select_related("conta").all().order_by("-data_validade")
    serializer_class = PropostaSerializer


class ConversaWhatsAppViewSet(viewsets.ModelViewSet):
    queryset = ConversaWhatsApp.objects.select_related("conta").all()
    serializer_class = ConversaWhatsAppSerializer


class MensagemWhatsAppViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = MensagemWhatsApp.objects.select_related("conversa").all()
    serializer_class = MensagemWhatsAppSerializer


class FollowUpViewSet(viewsets.ModelViewSet):
    queryset = FollowUp.objects.select_related("conta", "responsavel").all()
    serializer_class = FollowUpSerializer


def _verificar_webhook(request):
    mode = request.GET.get("hub.mode")
    token = request.GET.get("hub.verify_token") or ""
    challenge = request.GET.get("hub.challenge")
    expected = credenciais().verify_token
    if mode == "subscribe" and expected and hmac.compare_digest(token, expected):
        return HttpResponse(challenge or "", content_type="text/plain")
    return HttpResponse(status=403)


@require_GET
def whatsapp_verify(request):
    return _verificar_webhook(request)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def whatsapp_webhook(request):
    # A Meta usa a mesma URL para validar (GET) e para entregar os eventos (POST).
    if request.method == "GET":
        return _verificar_webhook(request)

    app_secret = credenciais().app_secret
    if app_secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        digest = hmac.new(app_secret.encode("utf-8"), request.body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, f"sha256={digest}"):
            return JsonResponse({"detail": "invalid signature"}, status=403)
    elif not settings.DEBUG:
        # Sem a chave secreta do app não há como saber se o evento veio da Meta.
        logger.warning("Webhook do WhatsApp recusado: chave secreta do app não configurada.")
        return JsonResponse({"detail": "app secret not configured"}, status=403)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"detail": "invalid JSON"}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"detail": "invalid payload"}, status=400)

    processar_webhook(payload)
    return JsonResponse({"received": True})
