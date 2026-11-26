from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="conta",
            name="data_ultima_interacao",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="conta",
            name="origem",
            field=models.CharField(blank=True, default="", max_length=50),
        ),
        migrations.AddField(
            model_name="conta",
            name="qualificacao",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="conta",
            name="score_lead",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="conta",
            name="tipo_pessoa",
            field=models.CharField(
                choices=[
                    ("nao_informado", "Não informado"),
                    ("pf", "Pessoa Física"),
                    ("pj", "Pessoa Jurídica"),
                ],
                default="nao_informado",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="conta",
            name="vendedor",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="leads_responsavel",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.CreateModel(
            name="LeadDistributionRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ativo", models.BooleanField(default=True)),
                ("atribuicoes", models.PositiveIntegerField(default=0)),
                ("vendedor", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="regras_distribuicao_leads", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["atribuicoes", "pk"],
                "verbose_name": "Regra de distribuição de leads",
                "verbose_name_plural": "Regras de distribuição de leads",
            },
        ),
        migrations.CreateModel(
            name="ConversaWhatsApp",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("telefone", models.CharField(max_length=30)),
                ("provider_conversation_id", models.CharField(blank=True, max_length=255)),
                ("status", models.CharField(choices=[("ativa", "Ativa"), ("aguardando_cliente", "Aguardando cliente"), ("aguardando_vendedor", "Aguardando vendedor"), ("encerrada", "Encerrada")], default="ativa", max_length=30)),
                ("iniciada_em", models.DateTimeField(auto_now_add=True)),
                ("atualizada_em", models.DateTimeField(auto_now=True)),
                ("conta", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="conversas_whatsapp", to="crm.conta")),
            ],
            options={"ordering": ["-atualizada_em"], "verbose_name": "Conversa WhatsApp", "verbose_name_plural": "Conversas WhatsApp"},
        ),
        migrations.CreateModel(
            name="MensagemWhatsApp",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider_message_id", models.CharField(blank=True, max_length=255)),
                ("direcao", models.CharField(choices=[("entrada", "Entrada"), ("saida", "Saída")], max_length=10)),
                ("texto", models.TextField(blank=True)),
                ("gerada_por_ia", models.BooleanField(default=False)),
                ("dados_provedor", models.JSONField(blank=True, default=dict)),
                ("criada_em", models.DateTimeField(auto_now_add=True)),
                ("conversa", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="mensagens", to="crm.conversawhatsapp")),
            ],
            options={"ordering": ["criada_em"], "verbose_name": "Mensagem WhatsApp", "verbose_name_plural": "Mensagens WhatsApp"},
        ),
        migrations.CreateModel(
            name="FollowUp",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("canal", models.CharField(choices=[("whatsapp", "WhatsApp"), ("email", "E-mail"), ("telefone", "Telefone")], default="whatsapp", max_length=20)),
                ("agendamento", models.DateTimeField()),
                ("assunto", models.CharField(max_length=255)),
                ("notas", models.TextField(blank=True)),
                ("status", models.CharField(choices=[("pendente", "Pendente"), ("concluido", "Concluído"), ("cancelado", "Cancelado")], default="pendente", max_length=20)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("conta", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="followups", to="crm.conta")),
                ("responsavel", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="followups_responsavel", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["agendamento"], "verbose_name": "Follow-up", "verbose_name_plural": "Follow-ups"},
        ),
    ]
