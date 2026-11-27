from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch

from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from core.access_control import MANAGER_GROUP, setup_access_groups
from core.models import A1Certificate, CompanyProfile, SMTPConfiguration
from core.secure_storage import decrypt_text

TEST_KEY = Fernet.generate_key().decode()


def make_certificate(password=b"senha-certificado", days=200):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(datetime_timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Escritório Teste")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days))
        .sign(key, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        b"teste", key, cert, None, serialization.BestAvailableEncryption(password)
    )


@override_settings(CONFIG_ENCRYPTION_KEY=TEST_KEY)
class SettingsPagesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        groups = setup_access_groups()
        users = get_user_model().objects
        cls.admin = users.create_superuser("cfg-admin", "admin@example.com", "test-password")
        cls.manager = users.create_user("cfg-gerente", password="test-password")
        cls.manager.groups.add(groups[MANAGER_GROUP])

    def test_somente_superusuario_acessa(self):
        self.client.force_login(self.manager)
        for name in ("company-data", "email-settings", "certificate-settings"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_administracao_aponta_para_as_telas_do_erp(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("admin-settings"))
        for name in ("company-data", "email-settings", "certificate-settings"):
            self.assertContains(page, reverse(name))
        self.assertNotContains(page, "/admin/core/companyprofile/")

    def test_salva_dados_da_empresa(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("company-data"),
            {
                "legal_name": "Benites e Silva Advogados",
                "cnpj": "11.669.089/0001-01",
                "tax_regime": "simples",
                "city": "Rio de Janeiro",
                "state": "rj",
                "email": "contato@example.com",
            },
        )
        self.assertRedirects(response, reverse("company-data"))
        profile = CompanyProfile.objects.get()
        self.assertEqual(profile.cnpj, "11669089000101")
        self.assertEqual(profile.state, "RJ")
        self.assertEqual(profile.primary_color, "#0B7A83")
        self.assertContains(self.client.get(reverse("company-data")), "Benites e Silva Advogados")

    def test_dados_da_empresa_mantem_logo_e_cores(self):
        profile = CompanyProfile.objects.create(
            legal_name="Antigo", cnpj="11669089000101", primary_color="#123456",
            logo_data=b"png", logo_content_type="image/png",
        )
        self.client.force_login(self.admin)
        self.client.post(
            reverse("company-data"),
            {"legal_name": "Novo", "cnpj": "11669089000101", "tax_regime": "mei", "city": "Niterói", "state": "RJ"},
        )
        profile.refresh_from_db()
        self.assertEqual((profile.legal_name, profile.primary_color, profile.has_logo), ("Novo", "#123456", True))

    def test_salva_e_testa_smtp(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("email-settings"),
            {
                "action": "save",
                "host": "smtp.gmail.com",
                "port": "587",
                "username": "contato@example.com",
                "smtp_password": "senha-de-app",
                "from_email": "contato@example.com",
                "use_tls": "on",
                "timeout": "10",
                "is_active": "on",
            },
        )
        self.assertRedirects(response, reverse("email-settings"))
        config = SMTPConfiguration.objects.get()
        self.assertEqual(decrypt_text(config.password_encrypted), "senha-de-app")
        page = self.client.get(reverse("email-settings"))
        self.assertNotContains(page, "senha-de-app")

        with patch("core.settings_views.test_smtp_configuration", return_value=(True, "Autenticação SMTP concluída com sucesso.")):
            response = self.client.post(reverse("email-settings"), {"action": "test"}, follow=True)
        self.assertContains(response, "Autenticação SMTP concluída com sucesso.")

        self.client.post(reverse("email-settings-remove"))
        self.assertFalse(SMTPConfiguration.objects.exists())

    def test_envia_certificado_a1(self):
        self.client.force_login(self.admin)
        arquivo = SimpleUploadedFile("empresa.pfx", make_certificate(), content_type="application/x-pkcs12")
        response = self.client.post(
            reverse("certificate-settings"),
            {"certificate_file": arquivo, "certificate_password": "senha-certificado"},
        )
        self.assertRedirects(response, reverse("certificate-settings"))
        certificate = A1Certificate.objects.get()
        self.assertEqual(certificate.original_filename, "empresa.pfx")
        page = self.client.get(reverse("certificate-settings"))
        self.assertContains(page, "Válido")
        self.assertContains(page, "empresa.pfx")

        self.client.post(reverse("certificate-remove"))
        self.assertFalse(A1Certificate.objects.exists())

    def test_certificado_com_senha_errada_e_recusado(self):
        self.client.force_login(self.admin)
        arquivo = SimpleUploadedFile("empresa.pfx", make_certificate(), content_type="application/x-pkcs12")
        response = self.client.post(
            reverse("certificate-settings"),
            {"certificate_file": arquivo, "certificate_password": "errada"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confira o arquivo e a senha")
        self.assertFalse(A1Certificate.objects.exists())

    def test_certificado_proximo_do_vencimento_gera_alerta(self):
        self.client.force_login(self.admin)
        arquivo = SimpleUploadedFile("empresa.pfx", make_certificate(days=10), content_type="application/x-pkcs12")
        self.client.post(reverse("certificate-settings"), {"certificate_file": arquivo, "certificate_password": "senha-certificado"})
        self.assertContains(self.client.get(reverse("certificate-settings")), "Vence em breve")
