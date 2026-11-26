from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models


class ERPUserBootstrap(models.Model):
    name = models.CharField(max_length=50, unique=True)
    completed_at = models.DateTimeField(auto_now_add=True)


class ERPAdminPasswordReset(models.Model):
    token_hash = models.CharField(max_length=64, unique=True)
    completed_at = models.DateTimeField(auto_now_add=True)


class CompanyProfile(models.Model):
    class TaxRegime(models.TextChoices):
        MEI = "mei", "MEI"
        SIMPLES = "simples", "Simples Nacional"
        PRESUMIDO = "presumido", "Lucro Presumido"
        REAL = "real", "Lucro Real"

    legal_name = models.CharField("Razão social", max_length=255)
    trade_name = models.CharField("Nome fantasia", max_length=255, blank=True)
    cnpj = models.CharField(
        "CNPJ",
        max_length=18,
        unique=True,
        validators=[
            RegexValidator(
                r"^[A-Za-z0-9]{12}\d{2}$",
                "Informe o CNPJ com 14 caracteres, sem pontuação.",
            )
        ],
        help_text="Informe os 14 caracteres sem pontuação; aceita o novo padrão alfanumérico.",
    )
    municipal_registration = models.CharField(
        "Inscrição municipal",
        max_length=40,
        blank=True,
    )
    state_registration = models.CharField(
        "Inscrição estadual",
        max_length=40,
        blank=True,
    )
    tax_regime = models.CharField(
        "Regime tributário",
        max_length=20,
        choices=TaxRegime.choices,
        default=TaxRegime.MEI,
    )
    cnae = models.CharField("CNAE principal", max_length=10, blank=True)
    postal_code = models.CharField("CEP", max_length=9, blank=True)
    street = models.CharField("Logradouro", max_length=255, blank=True)
    street_number = models.CharField("Número", max_length=20, blank=True)
    address_complement = models.CharField("Complemento", max_length=100, blank=True)
    neighborhood = models.CharField("Bairro", max_length=100, blank=True)
    city = models.CharField("Município", max_length=100, default="Rio de Janeiro")
    state = models.CharField("UF", max_length=2, default="RJ")
    phone = models.CharField("Telefone", max_length=30, blank=True)
    email = models.EmailField("E-mail da empresa", blank=True)
    # O logo fica no banco: o disco do Render é apagado a cada deploy.
    logo_data = models.BinaryField("Logo", editable=False, default=bytes)
    logo_content_type = models.CharField(max_length=30, blank=True, editable=False)
    primary_color = models.CharField(
        "Cor principal",
        max_length=7,
        default="#5968E8",
        validators=[
            RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Informe uma cor hexadecimal.")
        ],
    )
    sidebar_color = models.CharField(
        "Cor do menu lateral",
        max_length=7,
        default="#171D35",
        validators=[
            RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Informe uma cor hexadecimal.")
        ],
    )
    accent_color = models.CharField(
        "Cor de destaque",
        max_length=7,
        default="#36B89B",
        validators=[RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Informe uma cor hexadecimal.")],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Cadastro da empresa"
        verbose_name_plural = "Cadastro da empresa"

    def __str__(self):
        return self.trade_name or self.legal_name

    @property
    def has_logo(self):
        return bool(self.logo_content_type)


class SMTPConfiguration(models.Model):
    host = models.CharField("Servidor SMTP", max_length=255)
    port = models.PositiveIntegerField("Porta", default=587)
    username = models.CharField("Usuário SMTP", max_length=255)
    password_encrypted = models.BinaryField(
        "Senha SMTP criptografada",
        editable=False,
        default=bytes,
    )
    from_email = models.EmailField("E-mail remetente")
    use_tls = models.BooleanField("Usar TLS", default=True)
    use_ssl = models.BooleanField("Usar SSL", default=False)
    timeout = models.PositiveIntegerField("Timeout (segundos)", default=10)
    is_active = models.BooleanField("Ativa", default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuração SMTP"
        verbose_name_plural = "Configuração SMTP"

    def __str__(self):
        return f"{self.from_email} via {self.host}"


class A1Certificate(models.Model):
    encrypted_file = models.BinaryField(
        "Certificado criptografado",
        editable=False,
        default=bytes,
    )
    encrypted_password = models.BinaryField(
        "Senha criptografada",
        editable=False,
        default=bytes,
    )
    original_filename = models.CharField(max_length=255, editable=False)
    serial_number = models.CharField(max_length=100, editable=False)
    valid_until = models.DateTimeField("Válido até", editable=False)
    uploaded_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Certificado digital A1"
        verbose_name_plural = "Certificado digital A1"

    def __str__(self):
        return f"Certificado A1 válido até {self.valid_until:%d/%m/%Y}"


class AIConfiguration(models.Model):
    """Conta de IA conectada ao ERP. Existe no máximo um registro."""

    class Provider(models.TextChoices):
        ANTHROPIC = "anthropic", "Claude (Anthropic)"
        OPENAI = "openai", "OpenAI (ChatGPT)"
        GEMINI = "gemini", "Google Gemini"

    provider = models.CharField(
        "Provedor",
        max_length=20,
        choices=Provider.choices,
        default=Provider.ANTHROPIC,
    )
    model = models.CharField("Modelo", max_length=100)
    api_key_encrypted = models.BinaryField(
        "Chave de API criptografada",
        editable=False,
        default=bytes,
    )
    api_key_hint = models.CharField(max_length=12, blank=True, editable=False)
    is_active = models.BooleanField("IA ativa", default=True)
    monthly_token_limit = models.PositiveIntegerField(
        "Limite mensal de tokens",
        default=0,
        help_text="0 = sem limite. Ao atingir o limite, a IA fica pausada até o mês seguinte.",
    )
    enable_publications = models.BooleanField("Analisar publicações", default=True)
    enable_case_summary = models.BooleanField("Resumo de processos", default=True)
    enable_assistant = models.BooleanField("Assistente jurídico", default=True)
    enable_whatsapp = models.BooleanField("Atendimento inicial no WhatsApp", default=False)
    last_test_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_test_ok = models.BooleanField(default=False, editable=False)
    last_test_message = models.CharField(max_length=500, blank=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Conexão de IA"
        verbose_name_plural = "Conexão de IA"

    def __str__(self):
        return f"{self.get_provider_display()} · {self.model}"

    @property
    def has_api_key(self):
        return bool(self.api_key_encrypted)


class AIUsageLog(models.Model):
    class Feature(models.TextChoices):
        PUBLICATION = "publicacao", "Análise de publicação"
        CASE_SUMMARY = "resumo_processo", "Resumo de processo"
        ASSISTANT = "assistente", "Assistente jurídico"
        WHATSAPP = "whatsapp", "Atendimento WhatsApp"
        TEST = "teste", "Teste de conexão"

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_usage",
    )
    feature = models.CharField(max_length=30, choices=Feature.choices)
    provider = models.CharField(max_length=20)
    model = models.CharField(max_length=100)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    success = models.BooleanField(default=True)
    error_message = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Uso de IA"
        verbose_name_plural = "Uso de IA"

    @property
    def total_tokens(self):
        return self.input_tokens + self.output_tokens
