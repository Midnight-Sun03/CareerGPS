"""Deployment checks for public facts that cannot be inferred from source code."""

from django.conf import settings
from django.core.checks import Warning, register


@register()
def public_operator_details(app_configs, **kwargs):
    if settings.DEBUG:
        return []
    required = {
        "name": "CAREERGPS_OPERATOR_NAME",
        "legal_status": "CAREERGPS_OPERATOR_LEGAL_STATUS",
        "physical_address": "CAREERGPS_OPERATOR_PHYSICAL_ADDRESS",
        "legal_service_address": "CAREERGPS_OPERATOR_LEGAL_SERVICE_ADDRESS",
        "telephone": "CAREERGPS_OPERATOR_TELEPHONE",
        "contact_email": "CAREERGPS_CONTACT_EMAIL",
        "privacy_email": "CAREERGPS_PRIVACY_EMAIL",
        "information_officer": "CAREERGPS_INFORMATION_OFFICER",
    }
    missing = [environment for field, environment in required.items()
               if not settings.CAREERGPS_OPERATOR.get(field)]
    if not missing:
        return []
    return [Warning(
        "Public operator/contact details are incomplete: " + ", ".join(missing) + ".",
        hint="Supply verified owner facts before public production launch; review registration details and Information Officer information where applicable.",
        id="careergps.W001",
    )]
