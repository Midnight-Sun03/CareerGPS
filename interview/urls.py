from django.urls import path

from . import views


urlpatterns = [
    path(
        "",
        views.interview_home,
        name="interview_home",
    ),

    # Compatibility with old links.
    path(
        "setup/",
        views.interview_setup,
        name="interview_setup",
    ),

    path(
        "opportunity/<int:opportunity_id>/prepare/",
        views.interview_prepare,
        name="interview_prepare",
    ),

    path(
        "<int:interview_id>/live/",
        views.interview_live,
        name="interview_live",
    ),

    path(
        "<int:interview_id>/feedback/",
        views.interview_feedback,
        name="interview_feedback",
    ),

    path(
        "<int:interview_id>/transcripts/",
        views.interview_transcript_review,
        name="interview_transcript_review",
    ),

    path(
        "media/<int:question_id>/<str:media_type>/",
        views.interview_media,
        name="interview_media",
    ),

    path(
        "saved/",
        views.interview_saved,
        name="interview_saved",
    ),

    path(
        "api/create/",
        views.api_create_interview,
        name="api_create_interview",
    ),

    path(
        "api/question/<int:question_id>/answer-written/",
        views.api_submit_written_answer,
        name="api_submit_written_answer",
    ),

    path(
        "api/question/<int:question_id>/answer-voice/",
        views.api_submit_voice_answer,
        name="api_submit_voice_answer",
    ),

    path(
        "api/<int:interview_id>/complete/",
        views.api_complete_interview,
        name="api_complete_interview",
    ),

    path(
        "api/<int:interview_id>/transcripts/",
        views.api_review_transcripts,
        name="api_review_transcripts",
    ),

    path(
        "api/<int:interview_id>/delete/",
        views.api_delete_interview,
        name="api_delete_interview",
    ),

    path(
        "api/<int:interview_id>/",
        views.api_get_interview,
        name="api_get_interview",
    ),
]
