from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from crm.models import Conta
from core.access_control import MANAGER_GROUP, setup_access_groups
from .models import Audiencia, Processo


class JuridicoAudienciaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        groups = setup_access_groups()
        cls.manager = get_user_model().objects.create_user(
            username="audience-manager",
            password="test-password",
            is_staff=True,
        )
        cls.manager.groups.add(groups[MANAGER_GROUP])
        cls.conta = Conta.objects.create(
            nome="Cliente Audiência",
            documento="12345678901",
            tipo_pessoa=Conta.TipoPessoa.PF,
        )
        cls.processo = Processo.objects.create(
            numero_cnj="0000001-00.2026.8.19.0001",
            titulo="Audiência cível",
            cliente=cls.conta,
            tribunal="TJRJ",
            responsavel=cls.manager,
        )

    def test_api_cria_audiencia(self):
        client = APIClient()
        client.force_authenticate(user=self.manager)
        response = client.post(
            "/api/juridico/audiencias/",
            {
                "processo": self.processo.pk,
                "titulo": "Audiência de conciliação",
                "data_hora": "2026-12-10T14:30:00-03:00",
                "modalidade": "videoconferencia",
                "local": "https://videojuridico.example.com/sala",
                "responsavel": self.manager.pk,
                "status": "agendada",
                "observacoes": "",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            Audiencia.objects.filter(
                processo=self.processo,
                titulo="Audiência de conciliação",
            ).exists()
        )
