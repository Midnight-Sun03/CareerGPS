from django.urls import path
from . import views

urlpatterns = [
    # Landing
    path('', views.landing, name='landing'),

    # Authentication
    path('register/', views.register_view, name='register'),
    path('verify-otp/', views.verify_otp, name='verify_otp'),
    path('login/', views.login_view, name='login'),
    path('logout/confirm/', views.logout_confirm, name='logout_confirm'),
    path('logout/', views.logout_view, name='logout'),
    path('generate-cv/', views.generate_cv, name='generate_cv'),
    path('generate-cover-letter/', views.generate_cover_letter, name='generate_cover_letter'),
    path("resources/", views.resources_view, name="resources"),
    path("resources/cv-enhancer/", views.cv_enhancer_view, name="cv_enhancer"),
    path("resources/cover-letter/", views.cover_letter_generator_view, name="cover_letter_generator"),
    path("generate-cv/", views.generate_cv, name="generate_cv"),
    path("generate-cover-letter/", views.generate_cover_letter, name="generate_cover_letter"),

    # Dashboard
    path('dashboard/', views.dashboard_view, name='dashboard'),

    # Profile
    path('profile/complete/', views.profile_complete_view, name='profile_complete'),
    path('profile/', views.profile_view, name='profile'),

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
]
