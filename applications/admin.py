from django.contrib import admin

from .models import Application, GeneratedDocument


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("user", "opportunity", "status", "job_info_status", "updated_at")
    list_filter = ("status", "job_info_status")
    search_fields = ("user__username", "user__email", "opportunity__title", "opportunity__company")
    raw_id_fields = ("user", "opportunity")


@admin.register(GeneratedDocument)
class GeneratedDocumentAdmin(admin.ModelAdmin):
    list_display = ("user", "document_type", "opportunity", "template_key", "created_at")
    list_filter = ("document_type", "template_key", "renderer_backend")
    search_fields = ("user__username", "user__email", "opportunity__title")
    raw_id_fields = ("user", "application", "opportunity")
