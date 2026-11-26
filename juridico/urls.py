from rest_framework.routers import DefaultRouter

from .views import (
    AudienciaViewSet,
    IntegracaoTribunalViewSet,
    MovimentacaoViewSet,
    PrazoViewSet,
    ProcessoViewSet,
    PublicacaoViewSet,
    TarefaViewSet,
)

router = DefaultRouter()
router.register("processos", ProcessoViewSet)
router.register("movimentacoes", MovimentacaoViewSet)
router.register("prazos", PrazoViewSet)
router.register("tarefas", TarefaViewSet)
router.register("publicacoes", PublicacaoViewSet)
router.register("audiencias", AudienciaViewSet)
router.register("integracoes-tribunais", IntegracaoTribunalViewSet)

urlpatterns = router.urls
