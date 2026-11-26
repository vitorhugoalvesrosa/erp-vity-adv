from django.urls import path

from . import ai_views, juridico_views, views

urlpatterns = [
    path("", views.home, name="home"),
    path("administracao/", views.administration, name="admin-settings"),
    path(
        "administracao/identidade-visual/",
        views.visual_identity,
        name="visual-identity",
    ),
    path("administracao/ia/", ai_views.ai_settings, name="ai-settings"),
    path("administracao/ia/desconectar/", ai_views.ai_settings_remove, name="ai-settings-remove"),
    path("assistente/", ai_views.assistant, name="ai-assistant"),
    path("app/publicacoes/<int:pk>/ia/", ai_views.publicacao_ai, name="publicacao-ai"),
    path("pecas/", juridico_views.pecas, name="pecas"),
    path("pecas/nova/", juridico_views.peca_nova, name="peca-nova"),
    path("pecas/<int:pk>/", juridico_views.peca_detalhe, name="peca-detalhe"),
    path("pecas/<int:pk>/docx/", juridico_views.peca_docx, name="peca-docx"),
    path("tarefas/quadro/", juridico_views.kanban, name="kanban"),
    path("tarefas/<int:pk>/mover/", juridico_views.kanban_mover, name="kanban-mover"),
    path(
        "app/atendimentos/<int:pk>/converter/",
        juridico_views.atendimento_converter,
        name="atendimento-converter",
    ),
    path("app/processos/<int:pk>/ia/", ai_views.processo_ai, name="processo-ai"),
    path("identidade/logo/", views.company_logo, name="company-logo"),
    path(
        "app/orcamentos/<int:proposal_id>/gerar-pedido/",
        views.generate_sale_order,
        name="generate-sale-order",
    ),
    path(
        "app/contratos/<int:pk>/pdf/",
        views.contract_pdf,
        name="contract-pdf",
    ),
    path("app/<slug:module>/", views.record_list, name="record-list"),
    path("app/<slug:module>/novo/", views.record_edit, name="record-create"),
    path(
        "app/<slug:module>/<int:pk>/editar/",
        views.record_edit,
        name="record-edit",
    ),
    path(
        "app/<slug:module>/<int:pk>/excluir/",
        views.record_delete,
        name="record-delete",
    ),
    path("emails/enviar/", views.send_notice, name="send-email-notice"),
]
