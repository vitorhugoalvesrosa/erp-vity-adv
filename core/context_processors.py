from .models import CompanyProfile


def company_profile(request):
    profile = CompanyProfile.objects.defer("logo_data").first()
    return {
        "company_profile": profile,
        "sidebar_color": profile.sidebar_color if profile else "#0D3B45",
        "company_display_name": (
            profile.trade_name or profile.legal_name if profile else "ERP Vity ADV"
        ),
    }