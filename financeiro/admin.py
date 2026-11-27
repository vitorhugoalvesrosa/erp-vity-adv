from django.contrib import admin

from core.admin_site import erp_admin_site
from .models import CategoriaFinanceira, ContaPagar, ContaReceber, Contrato, Lancamento, Pagamento, PedidoVenda


@admin.register(PedidoVenda, site=erp_admin_site)
class PedidoVendaAdmin(admin.ModelAdmin):
    list_display = ("id", "proposta", "valor_final", "data_faturamento")
    list_filter = ("data_faturamento",)


@admin.register(Contrato, site=erp_admin_site)
class ContratoAdmin(admin.ModelAdmin):
    list_display = ("number", "pedido_venda", "status", "start_date", "end_date")
    list_filter = ("status", "start_date")
    search_fields = ("pedido_venda__proposta__conta__nome",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(ContaReceber, site=erp_admin_site)
class ContaReceberAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "conta",
        "pedido_venda",
        "valor_parcela",
        "data_vencimento",
        "status_pagamento",
    )
    list_filter = ("status_pagamento", "data_vencimento")
    search_fields = ("conta__nome",)


@admin.register(Pagamento, site=erp_admin_site)
class PagamentoAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "conta_receber",
        "valor",
        "data_pagamento",
        "metodo_pagamento",
    )
    list_filter = ("metodo_pagamento", "data_pagamento")
    search_fields = ("conta_receber__conta__nome",)


@admin.register(ContaPagar, site=erp_admin_site)
class ContaPagarAdmin(admin.ModelAdmin):
    list_display = ("descricao", "favorecido", "categoria", "valor", "data_vencimento", "status", "data_pagamento")
    list_filter = ("status", "categoria", "forma_pagamento")
    search_fields = ("descricao", "favorecido", "observacoes")
    date_hierarchy = "data_vencimento"


@admin.register(CategoriaFinanceira, site=erp_admin_site)
class CategoriaFinanceiraAdmin(admin.ModelAdmin):
    list_display = ("nome", "tipo", "ativa")
    list_filter = ("tipo", "ativa")
    search_fields = ("nome",)


@admin.register(Lancamento, site=erp_admin_site)
class LancamentoAdmin(admin.ModelAdmin):
    list_display = ("data", "tipo", "categoria", "descricao", "valor", "forma_pagamento")
    list_filter = ("tipo", "categoria", "data")
    search_fields = ("descricao", "observacoes")
    date_hierarchy = "data"
