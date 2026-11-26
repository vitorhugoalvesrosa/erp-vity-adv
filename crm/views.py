import hashlib
import hmac
import json
import urllib.request

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
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
from .services import distribuir_lead, qualificar_mensagem, responder_com_ia


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


def enviar_mensagem_whatsapp(telefone, texto):
    phone_id = getattr(settings, "WHATSAPP_PHONE_ID", "")
    token = getattr(settings, "WHATSAPP_ACCESS_TOKEN", "")
    if not phone_id or not token:
        return None

    url = f"https://graph.facebook.com/v21.0/{phone_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": telefone,
        "type": "text",
        "text": {"body": texto},
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    try:
        return data["messages"][0]["id"]
    except (KeyError, IndexError):
        return None


@require_GET
def whatsapp_verify(request):
    mode = request.GET.get("hub.mode")
    token = request.GET.get("hub.verify_token")
    challenge = request.GET.get("hub.challenge")
    expected = getattr(settings, "WHATSAPP_VERIFY_TOKEN", "")
    if mode == "subscribe" and expected and token == expected:
        return HttpResponse(challenge or "")
    return HttpResponse(status=403)


@csrf_exempt
@require_POST
@transaction.atomic
def whatsapp_webhook(request):
    app_secret = getattr(settings, "WHATSAPP_APP_SECRET", "")
    signature = request.headers.get("X-Hub-Signature-256", "")
    if app_secret:
        digest = hmac.new(
            app_secret.encode("utf-8"),
            request.body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, f"sha256={digest}"):
            return JsonResponse({"detail": "invalid signature"}, status=403)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid JSON"}, status=400)

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                telefone = message.get("from", "")
                texto = message.get("text", {}).get("body", "")
                provider_message_id = message.get("id", "")
                if not telefone:
                    continue

                documento = f"WAPP:{telefone[-12:]}"
                conta, _ = Conta.objects.select_for_update().get_or_create(
                    documento=documento,
                    defaults={
                        "nome": f"Lead WhatsApp {telefone}",
                        "telefone": telefone,
                        "tipo_pessoa": Conta.TipoPessoa.NAO_INFORMADO,
                        "origem": "whatsapp",
                        "status": Conta.Status.LEAD,
                    },
                )
                if not conta.origem:
                    conta.origem = "whatsapp"
                    conta.save(update_fields=["origem"])

                conversa, _ = ConversaWhatsApp.objects.get_or_create(
                    conta=conta,
                    telefone=telefone,
                    defaults={"status": ConversaWhatsApp.Status.ATIVA},
                )
                if provider_message_id:
                    if MensagemWhatsApp.objects.filter(
                        provider_message_id=provider_message_id
                    ).exists():
                        continue
                entrada = MensagemWhatsApp.objects.create(
                    conversa=conversa,
                    provider_message_id=provider_message_id,
                    direcao=MensagemWhatsApp.Direcao.ENTRADA,
                    texto=texto,
                    dados_provedor=message,
                )

                distribuir_lead(conta)
                qualificacao = qualificar_mensagem(conta, texto)
                qualificacao["tipo_pessoa"] = conta.tipo_pessoa
                resposta = responder_com_ia(
                    texto,
                    qualificacao,
                    conta.nome if conta.nome else "",
                )
                provider_response_id = enviar_mensagem_whatsapp(telefone, resposta)
                MensagemWhatsApp.objects.create(
                    conversa=conversa,
                    provider_message_id=provider_response_id or "",
                    direcao=MensagemWhatsApp.Direcao.SAIDA,
                    texto=resposta,
                    gerada_por_ia=True,
                )
                conversa.status = ConversaWhatsApp.Status.AGUARDANDO_VENDEDOR
                conversa.save(update_fields=["status", "atualizada_em"])

    return JsonResponse({"received": True})
