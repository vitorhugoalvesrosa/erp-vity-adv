from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("juridico", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Audiencia",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("titulo", models.CharField(max_length=255, verbose_name="Título")),
                ("data_hora", models.DateTimeField(verbose_name="Data e hora")),
                ("modalidade", models.CharField(default="presencial", max_length=50, verbose_name="Modalidade")),
                ("local", models.CharField(blank=True, max_length=255, verbose_name="Local ou link")),
                ("status", models.CharField(choices=[("agendada", "Agendada"), ("realizada", "Realizada"), ("adiada", "Adiada"), ("cancelada", "Cancelada")], default="agendada", max_length=20)),
                ("observacoes", models.TextField(blank=True, verbose_name="Observações")),
                ("criada_em", models.DateTimeField(auto_now_add=True)),
                ("atualizada_em", models.DateTimeField(auto_now=True)),
                ("processo", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="audiencias", to="juridico.processo")),
                ("responsavel", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="audiencias_responsavel", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["data_hora"], "verbose_name": "Audiência", "verbose_name_plural": "Audiências"},
        ),
        migrations.CreateModel(
            name="IntegracaoTribunal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nome", models.CharField(max_length=100, verbose_name="Nome")),
                ("provider", models.CharField(choices=[("datajud", "CNJ DataJud"), ("pje", "PJe"), ("esaj", "e-SAJ"), ("projudi", "Projudi"), ("outro", "Outro")], default="datajud", max_length=20, verbose_name="Provedor")),
                ("uf", models.CharField(max_length=2, verbose_name="UF")),
                ("tribunal", models.CharField(max_length=100, verbose_name="Tribunal")),
                ("endpoint", models.URLField(max_length=500, verbose_name="Endpoint")),
                ("api_key_encrypted", models.BinaryField(default=bytes, editable=False, verbose_name="Chave criptografada")),
                ("ativo", models.BooleanField(default=True)),
                ("ultima_sincronizacao", models.DateTimeField(blank=True, null=True)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["uf", "tribunal"],
                "unique_together": {("provider", "uf", "tribunal")},
                "verbose_name": "Integração judicial",
                "verbose_name_plural": "Integrações judiciais",
            },
        ),
    ]
