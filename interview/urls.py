from django.urls import path
from . import views

urlpatterns = [
    # Pages
    path('', views.interview_home, name='interview_home'),
    path('setup/', views.interview_setup, name='interview_setup'),
    path('<int:interview_id>/live/', views.interview_live, name='interview_live'),
    path('<int:interview_id>/feedback/', views.interview_feedback, name='interview_feedback'),
    path('saved/', views.interview_saved, name='interview_saved'),
    
    # API endpoints
    path('api/create/', views.api_create_interview, name='api_create_interview'),
    path('api/<int:interview_id>/generate-questions/', views.api_generate_questions, name='api_generate_questions'),
    path('api/<int:interview_id>/complete/', views.api_complete_interview, name='api_complete_interview'),
    path('api/<int:interview_id>/upload-recording/', views.api_upload_recording, name='api_upload_recording'),
    path('api/<int:interview_id>/delete/', views.api_delete_interview, name='api_delete_interview'),
    path('api/<int:interview_id>/', views.api_get_interview, name='api_get_interview'),
    path('api/question/<int:question_id>/submit-answer/', views.api_submit_answer, name='api_submit_answer'),
    path('api/occupations/', views.api_occupations, name='api_occupations'),
]