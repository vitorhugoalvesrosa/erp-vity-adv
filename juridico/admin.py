from django.contrib import admin

from core.admin_site import erp_admin_site
from .models import (
    Atendimento,
    Audiencia,
    Despesa,
    Honorario,
    PecaJuridica,
    Timesheet,
    IntegracaoTribunal,
    Movimentacao,
    Prazo,
    Processo,
    Publicacao,
    Tarefa,
)


class MovimentacaoInline(admin.TabularInline):
    model = Movimentacao
    extra = 0


class PrazoInline(admin.TabularInline):
    model = Prazo
    extra = 0


@admin.register(Processo, site=erp_admin_site)
class ProcessoAdmin(admin.ModelAdmin):
    list_display = (
        "numero_cnj",
        "titulo",
        "cliente",
        "area",
        "status",
        "responsavel",
        "atualizado_em",
    )
    list_filter = ("area", "status", "tribunal", "responsavel")
    search_fields = ("numero_cnj", "titulo", "parte_contraria", "cliente__nome")
    inlines = (MovimentacaoInline, PrazoInline)


@admin.register(Tarefa, site=erp_admin_site)
class TarefaAdmin(admin.ModelAdmin):
    list_display = ("titulo", "processo", "responsavel", "prioridade", "vencimento", "status")
    list_filter = ("prioridade", "status", "responsavel")
    search_fields = ("titulo", "descricao", "processo__numero_cnj")


@admin.register(Publicacao, site=erp_admin_site)
class PublicacaoAdmin(admin.ModelAdmin):
    list_display = ("titulo", "tribunal", "monitoramento", "publicada_em", "urgente", "processada")
    list_filter = ("urgente", "processada", "tribunal")
    search_fields = ("titulo", "conteudo", "monitoramento")


@admin.register(Audiencia, site=erp_admin_site)
class AudienciaAdmin(admin.ModelAdmin):
    list_display = ("titulo", "processo", "data_hora", "modalidade", "responsavel", "status")
    list_filter = ("status", "modalidade", "responsavel")
    search_fields = ("titulo", "local", "processo__numero_cnj")


@admin.register(IntegracaoTribunal, site=erp_admin_site)
class IntegracaoTribunalAdmin(admin.ModelAdmin):
    list_display = ("nome", "provider", "uf", "tribunal", "ativo", "ultima_sincronizacao")
    list_filter = ("provider", "uf", "ativo")


@admin.register(Atendimento, site=erp_admin_site)
class AtendimentoAdmin(admin.ModelAdmin):
    list_display = ("assunto", "cliente", "data", "canal", "responsavel", "status")
    list_filter = ("status", "canal", "responsavel")
    search_fields = ("assunto", "relato", "cliente__nome")


@admin.register(Timesheet, site=erp_admin_site)
class TimesheetAdmin(admin.ModelAdmin):
    list_display = ("data", "descricao", "processo", "usuario", "minutos", "faturado")
    list_filter = ("faturado", "usuario")


@admin.register(Honorario, site=erp_admin_site)
class HonorarioAdmin(admin.ModelAdmin):
    list_display = ("descricao", "processo", "tipo", "valor", "vencimento", "status")
    list_filter = ("tipo", "status")


@admin.register(Despesa, site=erp_admin_site)
class DespesaAdmin(admin.ModelAdmin):
    list_display = ("data", "descricao", "processo", "valor", "status")
    list_filter = ("status",)


@admin.register(PecaJuridica, site=erp_admin_site)
class PecaJuridicaAdmin(admin.ModelAdmin):
    list_display = ("titulo", "tipo", "processo", "criado_por", "atualizada_em")
    list_filter = ("tipo",)
    search_fields = ("titulo", "instrucoes", "conteudo")
