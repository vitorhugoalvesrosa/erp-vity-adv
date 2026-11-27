import re
from pathlib import Path

from cryptography.hazmat.primitives.serialization import pkcs12
from django import forms
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.utils import timezone

from core.models import A1Certificate, AIConfiguration, CompanyProfile, SMTPConfiguration
from core.secure_storage import encrypt_bytes, encrypt_text, validate_encryption_key


LOGO_CONTENT_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


class CompanyProfileForm(forms.ModelForm):
    cnpj = forms.CharField(
        label="CNPJ",
        max_length=18,
        help_text="Informe com ou sem pontuação; aceita o novo padrão alfanumérico.",
    )
    logo = forms.FileField(
        label="Logo",
        required=False,
        help_text="PNG, JPG ou WebP de até 2 MB. Em branco, mantém o logo atual.",
        widget=forms.ClearableFileInput(
            attrs={"accept": "image/png,image/jpeg,image/webp"}
        ),
    )
    remove_logo = forms.BooleanField(label="Remover logo atual", required=False)

    class Meta:
        model = CompanyProfile
        exclude = ("created_at", "updated_at")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ("primary_color", "sidebar_color", "accent_color"):
            self.fields[field_name].required = False
            self.fields[field_name].widget = forms.TextInput(
                attrs={"type": "color", "class": "vTextField"}
            )
        if not self.instance.has_logo:
            del self.fields["remove_logo"]

    def clean_primary_color(self):
        return self.cleaned_data.get("primary_color") or (
            self.instance.primary_color
            if self.instance.pk
            else CompanyProfile._meta.get_field("primary_color").get_default()
        )

    def clean_accent_color(self):
        return self.cleaned_data.get("accent_color") or (
            self.instance.accent_color
            if self.instance.pk
            else CompanyProfile._meta.get_field("accent_color").get_default()
        )

    def clean_sidebar_color(self):
        return self.cleaned_data.get("sidebar_color") or (
            self.instance.sidebar_color
            if self.instance.pk
            else CompanyProfile._meta.get_field("sidebar_color").get_default()
        )

    def clean_logo(self):
        logo = self.cleaned_data.get("logo")
        if not logo:
            return None
        if logo.size > 2 * 1024 * 1024:
            raise ValidationError("O logo não pode exceder 2 MB.")
        if Path(logo.name).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValidationError("Envie um logo PNG, JPG ou WebP.")
        try:
            from PIL import Image

            image = Image.open(logo)
            image.verify()
            if image.format not in LOGO_CONTENT_TYPES:
                raise ValidationError("Envie um logo PNG, JPG ou WebP.")
            if image.width > 2048 or image.height > 2048:
                raise ValidationError("As dimensões do logo não podem exceder 2048 px.")
            logo.erp_content_type = LOGO_CONTENT_TYPES[image.format]
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError("O arquivo enviado não é uma imagem válida.") from exc
        finally:
            logo.seek(0)
        return logo

    def clean_cnpj(self):
        return re.sub(r"[\s./-]", "", self.cleaned_data["cnpj"]).upper()

    def save(self, commit=True):
        instance = super().save(commit=False)
        logo = self.cleaned_data.get("logo")
        if logo:
            instance.logo_data = logo.read()
            instance.logo_content_type = logo.erp_content_type
        elif self.cleaned_data.get("remove_logo"):
            instance.logo_data = b""
            instance.logo_content_type = ""
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class CompanyVisualIdentityForm(CompanyProfileForm):
    field_order = (
        "legal_name",
        "trade_name",
        "cnpj",
        "logo",
        "remove_logo",
    )

    class Meta:
        model = CompanyProfile
        fields = (
            "legal_name",
            "trade_name",
            "cnpj",
            "primary_color",
            "sidebar_color",
            "accent_color",
        )


class CompanyDataForm(forms.ModelForm):
    """Dados cadastrais da empresa (o logo e as cores ficam em Identidade visual)."""

    cnpj = forms.CharField(
        label="CNPJ",
        max_length=18,
        help_text="Com ou sem pontuação; aceita o novo padrão alfanumérico.",
    )

    class Meta:
        model = CompanyProfile
        fields = (
            "legal_name",
            "trade_name",
            "cnpj",
            "tax_regime",
            "cnae",
            "municipal_registration",
            "state_registration",
            "postal_code",
            "street",
            "street_number",
            "address_complement",
            "neighborhood",
            "city",
            "state",
            "phone",
            "email",
        )
        widgets = {
            "postal_code": forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "postal-code"}),
            "phone": forms.TextInput(attrs={"inputmode": "tel", "autocomplete": "tel"}),
            "state": forms.TextInput(attrs={"maxlength": 2, "style": "text-transform: uppercase"}),
        }

    def clean_cnpj(self):
        return re.sub(r"[\s./-]", "", self.cleaned_data["cnpj"]).upper()

    def clean_state(self):
        return (self.cleaned_data.get("state") or "").upper()


class SMTPConfigurationForm(forms.ModelForm):
    smtp_password = forms.CharField(
        label="Senha SMTP",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="A senha é criptografada e nunca é exibida. Em branco, mantém a senha atual.",
    )

    class Meta:
        model = SMTPConfiguration
        fields = (
            "host",
            "port",
            "username",
            "from_email",
            "use_tls",
            "use_ssl",
            "timeout",
            "is_active",
        )

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("use_tls") and cleaned_data.get("use_ssl"):
            raise ValidationError("Use TLS ou SSL, não os dois ao mesmo tempo.")
        password = cleaned_data.get("smtp_password")
        if not self.instance.pk and not password:
            self.add_error("smtp_password", "Informe a senha SMTP.")
        elif cleaned_data.get("is_active") and not password and not self.instance.password_encrypted:
            self.add_error("smtp_password", "Informe a senha para ativar o SMTP.")
        if password:
            try:
                validate_encryption_key()
            except ImproperlyConfigured as exc:
                self.add_error("smtp_password", str(exc))
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        password = self.cleaned_data.get("smtp_password")
        if password:
            instance.password_encrypted = encrypt_text(password)
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class A1CertificateForm(forms.ModelForm):
    certificate_file = forms.FileField(
        label="Arquivo do certificado A1 (.pfx ou .p12)",
        required=False,
    )
    certificate_password = forms.CharField(
        label="Senha do certificado",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    class Meta:
        model = A1Certificate
        fields = ()

    def clean_certificate_file(self):
        certificate_file = self.cleaned_data.get("certificate_file")
        if not certificate_file:
            return None
        if Path(certificate_file.name).suffix.lower() not in {".pfx", ".p12"}:
            raise ValidationError("Envie um arquivo .pfx ou .p12.")
        if certificate_file.size > 5 * 1024 * 1024:
            raise ValidationError("O arquivo do certificado não pode exceder 5 MB.")
        return certificate_file

    def clean(self):
        cleaned_data = super().clean()
        certificate_file = cleaned_data.get("certificate_file")
        password = cleaned_data.get("certificate_password")
        if not self.instance.pk and not certificate_file:
            self.add_error("certificate_file", "Selecione o certificado A1.")
            return cleaned_data
        if certificate_file and not password:
            self.add_error("certificate_password", "Informe a senha do certificado.")
            return cleaned_data
        if certificate_file:
            try:
                validate_encryption_key()
            except ImproperlyConfigured as exc:
                self.add_error("certificate_file", str(exc))
                return cleaned_data
        if not certificate_file:
            return cleaned_data

        data = certificate_file.read()
        certificate_file.seek(0)
        try:
            private_key, certificate, _additional = pkcs12.load_key_and_certificates(
                data,
                password.encode("utf-8"),
            )
        except (ValueError, TypeError) as exc:
            raise ValidationError(
                "Não foi possível validar o certificado. Confira o arquivo e a senha."
            ) from exc
        if private_key is None or certificate is None:
            raise ValidationError("O arquivo não contém um certificado A1 válido.")
        valid_until = certificate.not_valid_after_utc
        if valid_until <= timezone.now():
            raise ValidationError("O certificado A1 está vencido.")

        cleaned_data["certificate_payload"] = {
            "file": encrypt_bytes(data),
            "password": encrypt_text(password),
            "filename": Path(certificate_file.name).name[:255],
            "serial_number": str(certificate.serial_number),
            "valid_until": valid_until,
        }
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        payload = self.cleaned_data.get("certificate_payload")
        if payload:
            instance.encrypted_file = payload["file"]
            instance.encrypted_password = payload["password"]
            instance.original_filename = payload["filename"]
            instance.serial_number = payload["serial_number"]
            instance.valid_until = payload["valid_until"]
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class AIConfigurationForm(forms.ModelForm):
    api_key = forms.CharField(
        label="Chave de API",
        required=False,
        strip=True,
        widget=forms.PasswordInput(
            attrs={"autocomplete": "off", "class": "form-control", "spellcheck": "false"}
        ),
        help_text="Criptografada no banco e nunca exibida. Em branco, mantém a chave atual.",
    )

    class Meta:
        model = AIConfiguration
        fields = (
            "provider",
            "model",
            "api_key",
            "is_active",
            "monthly_token_limit",
            "enable_publications",
            "enable_case_summary",
            "enable_assistant",
            "enable_drafts",
            "enable_whatsapp",
        )
        widgets = {
            "provider": forms.Select(attrs={"class": "form-select"}),
            "model": forms.TextInput(attrs={"class": "form-control", "list": "ai-model-options"}),
            "monthly_token_limit": forms.NumberInput(attrs={"class": "form-control", "step": "1000"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["model"].help_text = (
            "Para Claude, escolha na lista. Para OpenAI e Gemini, informe o nome exato do "
            "modelo liberado na sua conta."
        )
        if self.instance.pk and self.instance.api_key_hint:
            self.fields["api_key"].help_text = (
                f"Chave atual termina em …{self.instance.api_key_hint}. "
                "Em branco, mantém a chave atual."
            )

    def clean(self):
        cleaned_data = super().clean()
        api_key = cleaned_data.get("api_key")
        if not api_key and not self.instance.has_api_key:
            self.add_error("api_key", "Informe a chave de API do provedor.")
        if api_key:
            if len(api_key) < 20 or any(char.isspace() for char in api_key):
                self.add_error("api_key", "A chave informada não parece válida.")
            try:
                validate_encryption_key()
            except ImproperlyConfigured as exc:
                self.add_error("api_key", str(exc))
        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        api_key = self.cleaned_data.get("api_key")
        if api_key:
            instance.api_key_encrypted = encrypt_text(api_key)
            instance.api_key_hint = api_key[-4:]
            instance.last_test_at = None
            instance.last_test_ok = False
            instance.last_test_message = ""
        if commit:
            instance.save()
        return instance
