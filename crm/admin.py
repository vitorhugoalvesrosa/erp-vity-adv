from django.contrib import admin

from core.admin_site import erp_admin_site
from .models import (
    Conta,
    ConversaWhatsApp,
    FollowUp,
    LeadDistributionRule,
    MensagemWhatsApp,
    Proposta,
)


@admin.register(Conta, site=erp_admin_site)
class ContaAdmin(admin.ModelAdmin):
    list_display = ("nome", "documento", "tipo_pessoa", "origem", "vendedor", "score_lead", "status", "data_criacao")
    list_filter = ("status", "tipo_pessoa", "origem", "vendedor", "data_criacao")
    search_fields = ("nome", "documento", "email", "telefone")


@admin.register(Proposta, site=erp_admin_site)
class PropostaAdmin(admin.ModelAdmin):
    list_display = ("id", "conta", "valor_total", "status", "data_validade")
    list_filter = ("status", "condicao_pagamento", "data_validade")
    search_fields = ("conta__nome",)


@admin.register(LeadDistributionRule, site=erp_admin_site)
class LeadDistributionRuleAdmin(admin.ModelAdmin):
    list_display = ("vendedor", "ativo", "atribuicoes")
    list_filter = ("ativo",)


@admin.register(ConversaWhatsApp, site=erp_admin_site)
class ConversaWhatsAppAdmin(admin.ModelAdmin):
    list_display = ("conta", "telefone", "status", "iniciada_em", "atualizada_em")
    list_filter = ("status",)
    search_fields = ("conta__nome", "telefone")


@admin.register(MensagemWhatsApp, site=erp_admin_site)
class MensagemWhatsAppAdmin(admin.ModelAdmin):
    list_display = ("conversa", "direcao", "gerada_por_ia", "criada_em")
    list_filter = ("direcao", "gerada_por_ia")
    search_fields = ("texto", "conversa__telefone")


@admin.register(FollowUp, site=erp_admin_site)
class FollowUpAdmin(admin.ModelAdmin):
    list_display = ("conta", "responsavel", "canal", "agendamento", "status")
    list_filter = ("canal", "status", "responsavel")
