import json
import re
import urllib.request

from django.conf import settings
from django.db.models import Count
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.response import Response

from .models import (
    Audiencia,
    IntegracaoTribunal,
    Movimentacao,
    Prazo,
    Processo,
    Publicacao,
    Tarefa,
)
from .serializers import (
    MovimentacaoSerializer,
    PrazoSerializer,
    ProcessoSerializer,
    PublicacaoSerializer,
    AudienciaSerializer,
    IntegracaoTribunalSerializer,
    TarefaSerializer,
)


class ProcessoViewSet(viewsets.ModelViewSet):
    queryset = Processo.objects.select_related("cliente", "responsavel").all()
    serializer_class = ProcessoSerializer
    filter_backends = (SearchFilter, OrderingFilter)
    search_fields = (
        "numero_cnj",
        "titulo",
        "parte_contraria",
        "tribunal",
        "vara",
        "cliente__nome",
        "cliente__documento",
    )
    ordering_fields = (
        "numero_cnj",
        "titulo",
        "area",
        "status",
        "valor_causa",
        "atualizado_em",
    )

    def get_queryset(self):
        queryset = super().get_queryset()
        area = self.request.query_params.get("area")
        status = self.request.query_params.get("status")
        tribunal = self.request.query_params.get("tribunal")
        responsavel = self.request.query_params.get("responsavel")
        if area:
            queryset = queryset.filter(area=area)
        if status:
            queryset = queryset.filter(status=status)
        if tribunal:
            queryset = queryset.filter(tribunal__icontains=tribunal)
        if responsavel:
            queryset = queryset.filter(responsavel_id=responsavel)
        return queryset

    @action(detail=False, methods=["get"])
    def resumo(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        por_status = dict(
            queryset.values("status").annotate(total=Count("id")).values_list(
                "status",
                "total",
            )
        )
        por_area = dict(
            queryset.values("area").annotate(total=Count("id")).values_list(
                "area",
                "total",
            )
        )
        return Response(
            {
                "total": queryset.count(),
                "por_status": por_status,
                "por_area": por_area,
            }
        )


class MovimentacaoViewSet(viewsets.ModelViewSet):
    queryset = Movimentacao.objects.select_related("processo").all()
    serializer_class = MovimentacaoSerializer
    filter_backends = (SearchFilter, OrderingFilter)
    search_fields = ("descricao", "origem", "processo__numero_cnj", "processo__titulo")
    ordering_fields = ("data", "criada_em", "origem")

    def get_queryset(self):
        queryset = super().get_queryset()
        processo = self.request.query_params.get("processo")
        origem = self.request.query_params.get("origem")
        if processo:
            queryset = queryset.filter(processo_id=processo)
        if origem:
            queryset = queryset.filter(origem__icontains=origem)
        return queryset


class PrazoViewSet(viewsets.ModelViewSet):
    queryset = Prazo.objects.select_related("processo", "responsavel").all()
    serializer_class = PrazoSerializer
    filter_backends = (SearchFilter, OrderingFilter)
    search_fields = ("titulo", "processo__numero_cnj", "responsavel__username")
    ordering_fields = ("data_vencimento", "status", "titulo")

    def get_queryset(self):
        queryset = super().get_queryset()
        status = self.request.query_params.get("status")
        responsavel = self.request.query_params.get("responsavel")
        vencidos = self.request.query_params.get("vencidos")
        if status:
            queryset = queryset.filter(status=status)
        if responsavel:
            queryset = queryset.filter(responsavel_id=responsavel)
        if vencidos == "true":
            queryset = queryset.filter(
                status=Prazo.Status.ABERTO,
                data_vencimento__lt=timezone.localdate(),
            )
        return queryset


class TarefaViewSet(viewsets.ModelViewSet):
    queryset = Tarefa.objects.select_related("processo", "responsavel").all()
    serializer_class = TarefaSerializer
    filter_backends = (SearchFilter, OrderingFilter)
    search_fields = ("titulo", "descricao", "processo__numero_cnj")
    ordering_fields = ("vencimento", "prioridade", "status", "criada_em")

    def get_queryset(self):
        queryset = super().get_queryset()
        status = self.request.query_params.get("status")
        prioridade = self.request.query_params.get("prioridade")
        responsavel = self.request.query_params.get("responsavel")
        processo = self.request.query_params.get("processo")
        if status:
            queryset = queryset.filter(status=status)
        if prioridade:
            queryset = queryset.filter(prioridade=prioridade)
        if responsavel:
            queryset = queryset.filter(responsavel_id=responsavel)
        if processo:
            queryset = queryset.filter(processo_id=processo)
        return queryset

    @action(detail=True, methods=["post"])
    def concluir(self, request, pk=None):
        tarefa = self.get_object()
        tarefa.status = Tarefa.Status.CONCLUIDA
        tarefa.concluida_em = timezone.now()
        tarefa.save(update_fields=["status", "concluida_em"])
        return Response(self.get_serializer(tarefa).data)


class PublicacaoViewSet(viewsets.ModelViewSet):
    queryset = Publicacao.objects.select_related("processo").all()
    serializer_class = PublicacaoSerializer
    filter_backends = (SearchFilter, OrderingFilter)
    search_fields = ("titulo", "conteudo", "monitoramento", "tribunal")
    ordering_fields = ("publicada_em", "capturada_em", "urgente", "processada")

    def get_queryset(self):
        queryset = super().get_queryset()
        urgente = self.request.query_params.get("urgente")
        processada = self.request.query_params.get("processada")
        processo = self.request.query_params.get("processo")
        tribunal = self.request.query_params.get("tribunal")
        if urgente == "true":
            queryset = queryset.filter(urgente=True)
        if processada == "true":
            queryset = queryset.filter(processada=True)
        elif processada == "false":
            queryset = queryset.filter(processada=False)
        if processo:
            queryset = queryset.filter(processo_id=processo)
        if tribunal:
            queryset = queryset.filter(tribunal__icontains=tribunal)
        return queryset

    @action(detail=True, methods=["post"], url_path="analisar-ia")
    def analisar_ia(self, request, pk=None):
        from core.ai import AIError

        from .ai import analisar_publicacao

        publicacao = self.get_object()
        try:
            analisar_publicacao(publicacao, user=request.user)
        except AIError as exc:
            return Response({"detail": str(exc)}, status=503)
        return Response(self.get_serializer(publicacao).data)

    @action(detail=True, methods=["post"])
    def processar(self, request, pk=None):
        publicacao = self.get_object()
        publicacao.processada = True
        publicacao.save(update_fields=["processada"])
        return Response(self.get_serializer(publicacao).data)


class AudienciaViewSet(viewsets.ModelViewSet):
    queryset = Audiencia.objects.select_related("processo", "responsavel").all()
    serializer_class = AudienciaSerializer
    filter_backends = (SearchFilter, OrderingFilter)
    search_fields = ("titulo", "local", "processo__numero_cnj")
    ordering_fields = ("data_hora", "status", "criada_em")


class IntegracaoTribunalViewSet(viewsets.ModelViewSet):
    queryset = IntegracaoTribunal.objects.all()
    serializer_class = IntegracaoTribunalSerializer

    @action(detail=True, methods=["post"])
    def sincronizar(self, request, pk=None):
        integracao = self.get_object()
        numero_cnj = re.sub(r"\D", "", str(request.data.get("numero_cnj", "")))
        if not numero_cnj:
            return Response(
                {"detail": "Informe numero_cnj."},
                status=400,
            )
        if len(numero_cnj) != 20:
            return Response(
                {"detail": "Número CNJ inválido: informe os 20 dígitos do processo."},
                status=400,
            )
        if not integracao.ativo:
            return Response(
                {"detail": "Integração desativada."},
                status=400,
            )

        api_key = getattr(settings, "DATAJUD_API_KEY", "")
        if integracao.provider == IntegracaoTribunal.Provider.DATAJUD and not api_key:
            return Response(
                {
                    "detail": (
                        "Configure DATAJUD_API_KEY no ambiente antes de sincronizar."
                    )
                },
                status=503,
            )

        payload = {
            "query": {
                "match": {
                    "numeroProcesso": numero_cnj,
                }
            },
            "size": 100,
        }
        provider_request = urllib.request.Request(
            integracao.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"APIKey {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(provider_request, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            return Response(
                {"detail": f"Falha na integração judicial: {exc}"},
                status=502,
            )

        integracao.ultima_sincronizacao = timezone.now()
        integracao.save(update_fields=["ultima_sincronizacao"])
        return Response(
            {
                "provider": integracao.provider,
                "tribunal": integracao.tribunal,
                "uf": integracao.uf,
                "resultado": data,
            }
        )
