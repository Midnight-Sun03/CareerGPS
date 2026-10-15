from django.urls import path
from . import views
from . import community_views
from . import legal_views

urlpatterns = [
    path('terms/', legal_views.terms, name='terms'),
    path('privacy/', legal_views.privacy, name='privacy'),
    path('about/', legal_views.about, name='about'),
    path('contact/', legal_views.contact, name='contact'),
    path('contact/requests/<int:pk>/', legal_views.privacy_request_detail, name='privacy_request_detail'),
    path('accessibility/', legal_views.accessibility, name='accessibility'),
    path('stories/<int:story_id>/report/', community_views.report_story, name='report_story'),
    path('opportunities/<int:opportunity_id>/report/', community_views.report_opportunity, name='report_opportunity'),
    path('career-intelligence/', community_views.career_intelligence, name='career_intelligence'),
    # Landing
    path('', views.landing, name='landing'),

    # Authentication
    path(
        "register/",
        views.register_view,
        name="register",
    ),

    path(
        "verify-otp/",
        views.verify_otp,
        name="verify_otp",
    ),

    path(
        "verify-otp/resend/",
        views.resend_otp,
        name="resend_otp",
    ),

    path(
        "login/",
        views.login_view,
        name="login",
    ),
    path('logout/confirm/', views.logout_confirm, name='logout_confirm'),
    path('logout/', views.logout_view, name='logout'),
    path("resources/cv-enhancer/", views.cv_enhancer_view, name="cv_enhancer"),
    path("resources/cover-letter/", views.cover_letter_generator_view, name="cover_letter_generator"),
    path("generate-cv/", views.generate_cv, name="generate_cv"),
    path("generate-cover-letter/", views.generate_cover_letter, name="generate_cover_letter"),

    # Dashboard
    path('dashboard/', views.dashboard_view, name='dashboard'),

    # Profile
    path('profile/complete/', views.profile_complete_view, name='profile_complete'),
    path('profile/', views.profile_view, name='profile'),
    path('profile/personal/', views.profile_personal_view, name='profile_personal'),
    path('profile/<slug:section>/', views.profile_section_view, name='profile_section'),
    path('profile/personal/save/', views.update_personal_details, name='profile_personal_save'),
    path('profile/sections/<slug:section>/add/', views.add_profile_section, name='profile_section_add'),
    path('profile/sections/<slug:section>/<int:record_id>/edit/', views.edit_profile_section, name='profile_section_edit'),
    path('profile/sections/<slug:section>/<int:record_id>/delete/', views.delete_profile_section, name='profile_section_delete'),

    # Opportunities
    path('opportunities/', views.opportunities_view, name='opportunities'),
    path('opportunities/<int:opportunity_id>/', views.opportunity_detail_view, name='opportunity_detail'),

    # Save/Bookmark
    path('save/<int:opportunity_id>/', views.toggle_save_view, name='toggle_save'),
    path('saved/', views.saved_view, name='saved'),

    # Stories
    path('stories/', views.stories_view, name='stories'),
    path('stories/submit/', views.submit_story_view, name='submit_story'),

    # Resources
    path('resources/', views.resources_view, name='resources'),

    # Settings
    path('settings/', views.settings_view, name='settings'),
    path('settings/data-export/', legal_views.data_export, name='data_export'),
    path('settings/delete-account/', legal_views.account_closure_request, name='account_closure'),
    path(
        'settings/delete-account/confirm/',
        legal_views.account_closure_confirm,
        name='account_closure_confirm',
    ),

    # Notifications
    path(
        "notifications/",
        views.notifications_view,
        name="notifications",
    ),

    path(
        "notifications/<int:notification_id>/open/",
        views.open_notification,
        name="notification_open",
    ),

    path(
        "notifications/read-all/",
        views.mark_all_notifications_read,
        name="notifications_read_all",
    ),
]
