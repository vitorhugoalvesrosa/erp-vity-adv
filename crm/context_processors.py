from django.db.models import Sum

from .models import ConversaWhatsApp


def whatsapp(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not user.has_perm("crm.view_conversawhatsapp"):
        return {}
    total = (
        ConversaWhatsApp.objects.exclude(status=ConversaWhatsApp.Status.ENCERRADA).aggregate(
            total=Sum("nao_lidas")
        )["total"]
        or 0
    )
    return {"whatsapp_nao_lidas": total}
