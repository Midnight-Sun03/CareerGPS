from django.contrib import admin
from .models import Profile, Skill, Interest, Opportunity, SavedOpportunity, CVTip, MotivationalNudge, Story, Comment, StoryLike

admin.site.register(Opportunity)
admin.site.register(Comment)
admin.site.register(StoryLike)