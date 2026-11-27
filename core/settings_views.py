"""Telas de Administração → Empresa e integrações (dados da empresa, e-mail e certificado A1)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .configuration_forms import A1CertificateForm, CompanyDataForm, SMTPConfigurationForm
from .email_config import test_smtp_configuration
from .models import A1Certificate, CompanyProfile, SMTPConfiguration

CERTIFICATE_WARNING_DAYS = 30


def _require_superuser(request):
    if not request.user.is_superuser:
        raise PermissionDenied


@login_required
def company_data(request):
    _require_superuser(request)
    profile = CompanyProfile.objects.first()
    form = CompanyDataForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Dados da empresa salvos.")
        return redirect("company-data")
    return render(
        request,
        "core/settings/company_data.html",
        {
            "page_title": "Dados da empresa",
            "active_module": "administration",
            "form": form,
            "profile": profile,
            "sections": (
                ("Identificação", ("legal_name", "trade_name", "cnpj", "tax_regime", "cnae",
                                   "municipal_registration", "state_registration")),
                ("Endereço", ("postal_code", "street", "street_number", "address_complement",
                              "neighborhood", "city", "state")),
                ("Contato", ("phone", "email")),
            ),
        },
    )


@login_required
def email_settings(request):
    _require_superuser(request)
    config = SMTPConfiguration.objects.first()
    action = request.POST.get("action") if request.method == "POST" else None

    if action == "test":
        if not config:
            messages.error(request, "Salve a configuração antes de testar.")
            return redirect("email-settings")
        ok, detail = test_smtp_configuration(config)
        (messages.success if ok else messages.error)(request, detail)
        return redirect("email-settings")

    form = SMTPConfigurationForm(request.POST if action == "save" else None, instance=config)
    if action == "save" and form.is_valid():
        form.save()
        messages.success(request, "Configuração de e-mail salva. Use “Testar conexão” para validar.")
        return redirect("email-settings")
    return render(
        request,
        "core/settings/email_settings.html",
        {
            "page_title": "E-mail (SMTP)",
            "active_module": "administration",
            "form": form,
            "config": config,
        },
    )


@login_required
@require_POST
def email_settings_remove(request):
    _require_superuser(request)
    SMTPConfiguration.objects.all().delete()
    messages.success(request, "Configuração SMTP removida. O sistema volta a usar as variáveis de ambiente, se houver.")
    return redirect("email-settings")


@login_required
def certificate_settings(request):
    _require_superuser(request)
    certificate = A1Certificate.objects.first()
    form = A1CertificateForm(
        request.POST or None,
        request.FILES or None,
        instance=certificate,
    )
    if request.method == "POST":
        if not request.FILES.get("certificate_file"):
            form.add_error("certificate_file", "Selecione o arquivo do certificado.")
        elif form.is_valid():
            form.save()
            messages.success(request, "Certificado A1 validado e salvo com criptografia.")
            return redirect("certificate-settings")
    days_left = None
    if certificate:
        days_left = (certificate.valid_until - timezone.now()).days
    return render(
        request,
        "core/settings/certificate_settings.html",
        {
            "page_title": "Certificado digital A1",
            "active_module": "administration",
            "form": form,
            "certificate": certificate,
            "days_left": days_left,
            "expiring": days_left is not None and days_left <= CERTIFICATE_WARNING_DAYS,
            "expired": days_left is not None and days_left < 0,
        },
    )


@login_required
@require_POST
def certificate_remove(request):
    _require_superuser(request)
    A1Certificate.objects.all().delete()
    messages.success(request, "Certificado A1 e senha apagados do banco.")
    return redirect("certificate-settings")
