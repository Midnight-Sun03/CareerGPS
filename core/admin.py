from django.contrib import admin
from .models import Profile, CVtip, Story, SavedOpportunity, Skill, Opportunity, MotivationalNudge, Interest, Project, WorkExperience, Certification


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)

@admin.register(Interest)
class InterestAdmin(admin.ModelAdmin):
    list_display = ('name',)
    search_fields = ('name',)

admin.site.register(Project)
admin.site.register(WorkExperience)
admin.site.register(Certification)

@admin.register(Opportunity)
class OpportunityAdmin(admin.ModelAdmin):
    list_display = ('title', 'company', 'opportunity_type', 'location', 'application_deadline', 'is_active')
    search_fields = ('title', 'company', 'location',)
    list_filter = ('opportunity_type', 'is_active', 'location')

@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'qualification', 'institution', 'location', 'profile_complete')
    search_fields = ('user__username', 'qualification', 'institution')
    list_filter = ('profile_complete',)

@admin.register(SavedOpportunity)
class SavedOpportunityAdmin(admin.ModelAdmin):
    list_display = ('user', 'opportunity', 'saved_at')
    search_fields = ('user__username', 'opportunity__title')

@admin.register(CVtip)
class CVtipAdmin(admin.ModelAdmin):
    list_display = ('title', 'opportunity_type', 'is_active')
    search_fields = ('title', 'content')
    list_filter = ('opportunity_type', 'is_active')

@admin.register(MotivationalNudge)
class MotivationalNudgeAdmin(admin.ModelAdmin):
    list_display = ('title', 'trigger', 'trigger_value', 'is_active')
    search_fields = ('title', 'message')
    list_filter = ('trigger', 'is_active')

@admin.register(Story)
class StoryAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'title', 'status', 'submitted_at')
    search_fields = ('display_name', 'title', 'content')
    list_filter = ('status',) 
    
