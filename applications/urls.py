from django.urls import path

from . import views

app_name = "applications"

urlpatterns = [
    path("start/<int:opportunity_id>/", views.start_application, name="start"),
    path("<int:application_id>/", views.workspace, name="workspace"),
    path("<int:application_id>/status/", views.update_status, name="update_status"),
    path("<int:application_id>/notes/", views.save_notes, name="save_notes"),
    path("resolve-saved/<int:opportunity_id>/", views.resolve_saved_opportunity, name="resolve_saved"),
    path("documents/<int:document_id>/", views.generated_document_preview, name="document_preview"),
    path("documents/<int:document_id>/edit/", views.cv_document_editor, name="document_edit"),
    path("documents/<int:document_id>/edit/", views.cv_document_editor, name="cv_editor"),
    path("documents/<int:document_id>/content/", views.update_document_content, name="update_document_content"),
    path("documents/<int:document_id>/layout/", views.update_cv_layout, name="update_cv_layout"),
    path(
        "documents/<int:document_id>/download/<str:file_format>/",
        views.generated_document_download,
        name="document_download",
    ),
]
