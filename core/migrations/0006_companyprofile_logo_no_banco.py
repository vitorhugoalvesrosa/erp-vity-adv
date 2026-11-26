from mimetypes import guess_type

from django.db import migrations, models


def copiar_logo_para_o_banco(apps, schema_editor):
    CompanyProfile = apps.get_model("core", "CompanyProfile")
    for profile in CompanyProfile.objects.exclude(logo=""):
        try:
            with profile.logo.open("rb") as logo:
                profile.logo_data = logo.read()
        except (FileNotFoundError, OSError):
            # O arquivo já foi apagado do disco (ex.: deploy no Render); o logo
            # precisa ser enviado de novo.
            continue
        profile.logo_content_type = guess_type(profile.logo.name)[0] or "image/png"
        profile.save(update_fields=["logo_data", "logo_content_type"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_companyprofile_sidebar_color"),
    ]

    operations = [
        migrations.AddField(
            model_name="companyprofile",
            name="logo_data",
            field=models.BinaryField(default=bytes, editable=False, verbose_name="Logo"),
        ),
        migrations.AddField(
            model_name="companyprofile",
            name="logo_content_type",
            field=models.CharField(blank=True, editable=False, max_length=30),
        ),
        migrations.RunPython(copiar_logo_para_o_banco, migrations.RunPython.noop),
        migrations.RemoveField(model_name="companyprofile", name="logo"),
    ]
