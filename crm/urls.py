from rest_framework.routers import DefaultRouter

from django.urls import path

from .views import (
    ContaViewSet,
    ConversaWhatsAppViewSet,
    FollowUpViewSet,
    MensagemWhatsAppViewSet,
    PropostaViewSet,
    whatsapp_verify,
    whatsapp_webhook,
)

router = DefaultRouter()
router.register(r"contas", ContaViewSet, basename="conta")
router.register(r"clientes", ContaViewSet, basename="cliente")
router.register(r"propostas", PropostaViewSet, basename="proposta")
router.register(r"conversas-whatsapp", ConversaWhatsAppViewSet, basename="conversa-whatsapp")
router.register(r"mensagens-whatsapp", MensagemWhatsAppViewSet, basename="mensagem-whatsapp")
router.register(r"followups", FollowUpViewSet, basename="followup")

urlpatterns = [
    path("whatsapp/webhook/", whatsapp_webhook, name="crm-whatsapp-webhook"),
    path("whatsapp/verify/", whatsapp_verify, name="crm-whatsapp-verify"),
] + router.urls
