from django.urls import path
from django.conf.urls.static import static
from django.conf import settings
from . import views

urlpatterns = [
    # Page URLs
    path('', views.home, name='home'),
    path('signin/', views.signin, name='signin'),
    path('forgot-password/', views.forgot_password, name='forgot-password'),
    path('register/', views.register, name='register'),
    path('welcome/', views.welcome, name='welcome'),
    path('opportunities/', views.opportunities, name='opportunities'),
    path('profile/', views.profile_view, name='profile'),
    path('saved/', views.saved, name='saved'),
    path('resources/', views.resources, name='resources'),
    path('stories/', views.stories_view, name='stories'),
    
    # Registration APIs
    path('api/register/', views.api_register, name='api_register'),
    path('api/resend-verification/', views.api_resend_verification, name='api_resend_verification'),
    path('api/verify-email/', views.api_verify_email, name='api_verify_email'),
    path('api/complete-profile/', views.api_complete_profile, name='api_complete_profile'),
    
    # Authentication APIs
    path('api/signin/', views.api_signin, name='api_signin'),
    path('api/signout/', views.api_signout, name='api_signout'),
    path('api/test/', views.api_test, name='api_test'),
    
    # Forgot Password APIs
    path('api/forgot-password/', views.api_forgot_password, name='api_forgot_password'),
    path('api/resend-reset-code/', views.api_resend_reset_code, name='api_resend_reset_code'),
    path('api/verify-reset-code/', views.api_verify_reset_code, name='api_verify_reset_code'),
    path('api/reset-password/', views.api_reset_password, name='api_reset_password'),
    
    # ============================================================
    # OPPORTUNITY APIs - Only what we need
    # ============================================================
    # REMOVED: api_get_opportunities_paginated (not used anymore)
    # REMOVED: api_get_opportunity_detail (not used anymore)
    
    path('api/save-opportunity/', views.api_save_opportunity, name='api_save_opportunity'),
    path('api/get-saved-opportunities/', views.api_get_saved_opportunities, name='api_get_saved_opportunities'),
    path('api/get-saved-opportunities-details/', views.api_get_saved_opportunities_details, name='api_get_saved_opportunities_details'),
    path('api/personalized-opportunities/', views.api_personalized_opportunities, name='api_personalized_opportunities'),
    path('api/all-opportunities/', views.api_all_opportunities, name='api_all_opportunities'),
    path('api/search-opportunities/', views.api_search_opportunities, name='api_search_opportunities'),
    path('api/track-apply-click/', views.api_track_apply_click, name='track_apply_click'),
    
    # ============================================================
    # STORIES APIs
    # ============================================================
    path('api/get-stories/', views.api_get_stories, name='api_get_stories'),
    path('api/post-story/', views.api_post_story_with_profession, name='api_post_story'),
    path('api/edit-story/', views.api_edit_story, name='api_edit_story'),
    path('api/delete-story/', views.api_delete_story, name='api_delete_story'),
    path('api/toggle-like/', views.api_toggle_story_like, name='api_toggle_like'),
    
    # ============================================================
    # COMMENT APIs
    # ============================================================
    path('api/get-comments/', views.api_get_comments, name='api_get_comments'),
    path('api/add-comment/', views.api_add_comment, name='api_add_comment'),
    path('api/edit-comment/', views.api_edit_comment, name='api_edit_comment'),
    path('api/delete-comment/', views.api_delete_comment, name='api_delete_comment'),
    path('api/search-users/', views.api_search_users, name='api_search_users'),
    
    # ============================================================
    # CV UPLOAD APIs
    # ============================================================
    path('api/upload-cv/', views.api_upload_cv, name='api_upload_cv'),
    path('api/remove-cv/', views.api_remove_cv, name='api_remove_cv'),
    
    # ============================================================
    # OTHER APIs
    # ============================================================
    path('api/occupations/', views.api_occupations, name='api_occupations'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)