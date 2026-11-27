from django.urls import path

from . import inbox_views

urlpatterns = [
    path("whatsapp/", inbox_views.inbox, name="whatsapp-inbox"),
    path("whatsapp/lista/", inbox_views.lista, name="whatsapp-lista"),
    path("whatsapp/nova/", inbox_views.nova, name="whatsapp-nova"),
    path("whatsapp/<int:pk>/", inbox_views.inbox, name="whatsapp-conversa"),
    path("whatsapp/<int:pk>/atualizacoes/", inbox_views.atualizacoes, name="whatsapp-atualizacoes"),
    path("whatsapp/<int:pk>/enviar/", inbox_views.enviar, name="whatsapp-enviar"),
    path("whatsapp/<int:pk>/modelo/", inbox_views.modelo, name="whatsapp-modelo"),
    path("whatsapp/<int:pk>/acao/", inbox_views.acao, name="whatsapp-acao"),
    path("whatsapp/midia/<int:pk>/", inbox_views.midia, name="whatsapp-midia"),
    path("administracao/whatsapp/", inbox_views.configuracao, name="whatsapp-settings"),
    path("administracao/whatsapp/remover/", inbox_views.configuracao_remover, name="whatsapp-settings-remove"),
]
