from django.db import migrations

# Lista da planilha de controle financeiro do escritório.
ENTRADAS = ["Honorários advocatícios", "Outros recebimentos", "Caixa do mês"]
SAIDAS = [
    "Aluguel",
    "Condomínio",
    "IPTU",
    "Ástrea",
    "Minuta IA",
    "TIM S.A",
    "Locação de sala de reunião",
    "Luz",
    "Internet",
    "Anuidade OAB",
    "Pagamento de comissão",
    "Despesas diversas",
]


def criar(apps, schema_editor):
    Categoria = apps.get_model("financeiro", "CategoriaFinanceira")
    for tipo, nomes in (("entrada", ENTRADAS), ("saida", SAIDAS)):
        for nome in nomes:
            Categoria.objects.get_or_create(tipo=tipo, nome=nome)


class Migration(migrations.Migration):
    dependencies = [("financeiro", "0005_controle_financeiro")]
    operations = [migrations.RunPython(criar, migrations.RunPython.noop)]
