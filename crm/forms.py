import re

from django import forms
from django.core.exceptions import ImproperlyConfigured

from core.secure_storage import encrypt_text, validate_encryption_key

from .models import Conta, WhatsAppConfiguration
from .whatsapp import normalizar_telefone

TEXTAREA = {"class": "form-control", "rows": 3}


class WhatsAppConfigurationForm(forms.ModelForm):
    access_token = forms.CharField(
        label="Token de acesso permanente",
        required=False,
        strip=True,
        widget=forms.PasswordInput(attrs={"autocomplete": "off", "class": "form-control", "spellcheck": "false"}),
        help_text="Token de um usuário do sistema com a permissão whatsapp_business_messaging. "
        "Criptografado no banco e nunca exibido.",
    )
    app_secret = forms.CharField(
        label="Chave secreta do app",
        required=False,
        strip=True,
        widget=forms.PasswordInput(attrs={"autocomplete": "off", "class": "form-control", "spellcheck": "false"}),
        help_text="Meta for Developers → Configurações do app → Básico. Garante que só a Meta "
        "consiga entregar mensagens ao ERP.",
    )

    class Meta:
        model = WhatsAppConfiguration
        fields = (
            "phone_number_id",
            "business_account_id",
            "access_token",
            "app_secret",
            "is_active",
            "bot_enabled",
            "bot_instructions",
            "welcome_message",
            "handoff_message",
            "handoff_keywords",
        )
        widgets = {
            "phone_number_id": forms.TextInput(attrs={"class": "form-control", "inputmode": "numeric"}),
            "business_account_id": forms.TextInput(attrs={"class": "form-control", "inputmode": "numeric"}),
            "bot_instructions": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 5,
                    "placeholder": "Ex.: Atuamos em direito trabalhista, previdenciário e de família. "
                    "Atendimento de segunda a sexta, 9h às 18h. Endereço: ...",
                }
            ),
            "welcome_message": forms.Textarea(attrs=TEXTAREA),
            "handoff_message": forms.Textarea(attrs=TEXTAREA),
            "handoff_keywords": forms.TextInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.access_token_hint:
            self.fields["access_token"].help_text = (
                f"Token atual termina em …{self.instance.access_token_hint}. Em branco, mantém o atual."
            )
        if self.instance.pk and self.instance.has_app_secret:
            self.fields["app_secret"].help_text = "Chave já salva. Em branco, mantém a atual."

    def _clean_id(self, name):
        value = re.sub(r"\s", "", self.cleaned_data.get(name) or "")
        if value and not value.isdigit():
            raise forms.ValidationError("Use somente os números do ID mostrado pela Meta.")
        return value

    def clean_phone_number_id(self):
        return self._clean_id("phone_number_id")

    def clean_business_account_id(self):
        return self._clean_id("business_account_id")

    def clean(self):
        cleaned = super().clean()
        token = cleaned.get("access_token")
        secret = cleaned.get("app_secret")
        if not token and not self.instance.has_access_token:
            self.add_error("access_token", "Informe o token de acesso.")
        if not secret and not self.instance.has_app_secret:
            self.add_error("app_secret", "Informe a chave secreta do app.")
        if token and (len(token) < 20 or any(c.isspace() for c in token)):
            self.add_error("access_token", "O token informado não parece válido.")
        if secret and not re.fullmatch(r"[0-9a-fA-F]{16,64}", secret):
            self.add_error("app_secret", "A chave secreta do app tem 32 caracteres (0-9 e a-f).")
        if token or secret:
            try:
                validate_encryption_key()
            except ImproperlyConfigured as exc:
                self.add_error(None, str(exc))
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        token = self.cleaned_data.get("access_token")
        secret = self.cleaned_data.get("app_secret")
        if token:
            instance.access_token_encrypted = encrypt_text(token)
            instance.access_token_hint = token[-4:]
            instance.last_test_at = None
            instance.last_test_ok = False
            instance.last_test_message = ""
        if secret:
            instance.app_secret_encrypted = encrypt_text(secret.lower())
        if commit:
            instance.save()
        return instance


class NovaConversaForm(forms.Form):
    telefone = forms.CharField(
        label="WhatsApp do contato",
        max_length=30,
        widget=forms.TextInput(
            attrs={"class": "form-control", "inputmode": "tel", "placeholder": "(21) 99999-9999", "autocomplete": "off"}
        ),
        help_text="Com DDD. Para números de fora do Brasil, inclua o código do país.",
    )
    conta = forms.ModelChoiceField(
        label="Vincular a uma conta do CRM",
        queryset=Conta.objects.none(),
        required=False,
        empty_label="Criar um lead novo com este número",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    nome = forms.CharField(
        label="Nome do contato",
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["conta"].queryset = Conta.objects.order_by("nome")

    def clean_telefone(self):
        telefone = normalizar_telefone(self.cleaned_data["telefone"])
        if not 10 <= len(telefone) <= 15:
            raise forms.ValidationError("Informe um número válido, com DDD.")
        return telefone


class ModeloForm(forms.Form):
    """Envio de modelo aprovado (obrigatório para iniciar ou retomar após 24 h)."""

    modelo = forms.CharField(
        label="Modelo aprovado",
        max_length=512,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "ex.: retomar_atendimento"}),
    )
    idioma = forms.CharField(
        label="Idioma",
        max_length=10,
        initial="pt_BR",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    corpo = forms.CharField(required=False, widget=forms.HiddenInput)
    parametros = forms.CharField(
        label="Variáveis",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Uma por linha: {{1}}, {{2}}…"}),
        help_text="Uma por linha, na ordem do modelo.",
    )

    def lista_parametros(self):
        return [linha.strip() for linha in (self.cleaned_data.get("parametros") or "").splitlines() if linha.strip()]
