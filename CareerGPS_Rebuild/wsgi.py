"""
WSGI config for CareerGPS_Rebuild project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'CareerGPS_Rebuild.settings')

application = get_wsgi_application()

# Entry points are imported by serving processes, not by django.setup().
from applications.services.semantic_warmup import warm_semantic_model_on_startup

warm_semantic_model_on_startup()
