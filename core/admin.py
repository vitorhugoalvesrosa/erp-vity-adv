from django.contrib import admin, messages

from core.admin_site import erp_admin_site
from core.configuration_forms import (
    A1CertificateForm,
    CompanyProfileForm,
    SMTPConfigurationForm,
)
from core.email_config import test_smtp_configuration
from core.models import A1Certificate, CompanyProfile, SMTPConfiguration


@admin.register(CompanyProfile, site=erp_admin_site)
class CompanyProfileAdmin(admin.ModelAdmin):
    form = CompanyProfileForm
    list_display = ("legal_name", "cnpj", "city", "state", "tax_regime")
    fieldsets = (
        (
            "Identificação",
            {
                "fields": (
                    "legal_name",
                    "trade_name",
                    "cnpj",
                    "tax_regime",
                    "cnae",
                    "municipal_registration",
                    "state_registration",
                )
            },
        ),
        (
            "Endereço",
            {
                "fields": (
                    "postal_code",
                    "street",
                    "street_number",
                    "address_complement",
                    "neighborhood",
                    "city",
                    "state",
                )
            },
        ),
        ("Contato", {"fields": ("phone", "email")}),
        (
            "Identidade visual",
            {
                "fields": (
                    "logo",
                    "remove_logo",
                    "primary_color",
                    "sidebar_color",
                    "accent_color",
                    "heading_font",
                )
            },
        ),
    )

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if obj and obj.has_logo:
            return fieldsets
        return [
            (name, {**options, "fields": tuple(f for f in options["fields"] if f != "remove_logo")})
            for name, options in fieldsets
        ]

    def has_add_permission(self, request):
        return not CompanyProfile.objects.exists()


@admin.action(description="Testar autenticação SMTP")
def test_smtp_connection(modeladmin, request, queryset):
    if queryset.count() != 1:
        modeladmin.message_user(request, "Selecione uma única configuração SMTP.", level=messages.ERROR)
        return
    ok, detail = test_smtp_configuration(queryset.first())
    modeladmin.message_user(request, detail, level=messages.SUCCESS if ok else messages.ERROR)


@admin.register(SMTPConfiguration, site=erp_admin_site)
class SMTPConfigurationAdmin(admin.ModelAdmin):
    form = SMTPConfigurationForm
    list_display = ("from_email", "host", "port", "is_active", "updated_at")
    fields = (
        "host",
        "port",
        "username",
        "smtp_password",
        "from_email",
        "use_tls",
        "use_ssl",
        "timeout",
        "is_active",
    )
    actions = (test_smtp_connection,)

    def has_add_permission(self, request):
        return not SMTPConfiguration.objects.exists()


@admin.register(A1Certificate, site=erp_admin_site)
class A1CertificateAdmin(admin.ModelAdmin):
    form = A1CertificateForm
    list_display = ("original_filename", "serial_number", "valid_until", "uploaded_at")
    readonly_fields = ("original_filename", "serial_number", "valid_until", "uploaded_at")
    fields = (
        "certificate_file",
        "certificate_password",
        "original_filename",
        "serial_number",
        "valid_until",
        "uploaded_at",
    )

    def has_add_permission(self, request):
        return not A1Certificate.objects.exists()
