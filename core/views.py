from django.shortcuts import render, redirect
from django.contrib.auth.models import User
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
import json
import random
import string
from datetime import datetime, timedelta
from .models import Profile, Skill, Interest, Opportunity, SavedOpportunity, Story, Comment, StoryLike
from django.db.models import Q
from django.utils import timezone
from django.contrib.auth.hashers import make_password, check_password
from django.core.cache import cache



# Store verification codes temporarily
verification_codes = {}
reset_tokens = {}

# ============================================================
# PAGE VIEWS
# ============================================================

def home(request):
    return render(request, 'core/index.html')

def signin(request):
    return render(request, "core/signin.html")

def forgot_password(request):
    return render(request, 'core/forgot-password.html')

def register(request):
    return render(request, 'core/register.html')


@login_required
def opportunities(request):
    """Display all opportunities - NO match score calculations, uses caching"""
    
    # Create a unique cache key for this user
    cache_key = f'opportunities_{request.user.id}'
    
    # Try to get from cache
    cached_data = cache.get(cache_key)
    
    if cached_data:
        print("📦 Using cached opportunities")
        return render(request, 'core/opportunities.html', {'opportunities': cached_data})
    
    print("🔄 Loading opportunities from database...")
    
    # Get ALL active opportunities - NO match score calculations!
    opportunities_list = Opportunity.objects.filter(is_active=True).order_by('-date_listed')
    opportunities_list = opportunities_list.prefetch_related('required_skills')
    
    # ✅ Set match_score to 0 for all (no calculation needed on opportunities page)
    for opp in opportunities_list:
        opp.match_score = 0
        opp.match_reason = "Check your personalized matches on the dashboard"
    
    # Cache for 1 hour
    cache.set(cache_key, opportunities_list, 3600)
    print(f"📊 Cached {opportunities_list.count()} opportunities")
    
    return render(request, 'core/opportunities.html', {'opportunities': opportunities_list})


def api_search_opportunities(request):
    """Search opportunities from database"""
    query = request.GET.get('q', '').strip()
    
    if not query:
        return JsonResponse({'success': True, 'opportunities': []})
    
    opportunities = Opportunity.objects.filter(
        Q(is_active=True) &
        (Q(title__icontains=query) |
        Q(company__icontains=query) |
        Q(location__icontains=query) |
        Q(sector__icontains=query) |
        Q(description__icontains=query))
    )[:20]
    
    opp_list = []
    for opp in opportunities:
        opp_list.append({
            'id': opp.id,
            'title': opp.title,
            'company': opp.company,
            'location': opp.location,
            'type': opp.opportunity_type,
            'description': opp.description[:150] if opp.description else '',
        })
    
    return JsonResponse({'success': True, 'opportunities': opp_list})


@login_required
def welcome(request):
    """Dashboard with personalized matches"""
    try:
        profile = Profile.objects.get(user=request.user)
        profile_completion = calculate_profile_completion(profile)
    except Profile.DoesNotExist:
        profile_completion = 0
    
    saved_count = SavedOpportunity.objects.filter(user=request.user).count()
    
    return render(request, 'core/welcome.html', {
        'profile_completion': profile_completion,
        'saved_count': saved_count,
    })


@login_required
def saved(request):
    """Display saved opportunities"""
    return render(request, 'core/saved.html')


def resources(request):
    return render(request, 'core/resources.html')


@login_required
def stories_view(request):
    stories = Story.objects.filter(status='approved').order_by('-submitted_at')
    return render(request, 'core/stories.html', {'stories': stories})


@login_required
def profile_view(request):
    user = request.user
    profile, created = Profile.objects.get_or_create(user=user)
    
    if request.method == 'POST':
        profile.degree = request.POST.get('degree', '')
        profile.institution = request.POST.get('institution', '')
        profile.location = request.POST.get('location', '')
        profile.custom_skills = request.POST.get('custom_skills', '')
        profile.profile_complete = True
        
        skills_str = request.POST.get('skills', '')
        profile.skills.clear()
        if skills_str:
            for skill_name in [s.strip() for s in skills_str.split(',') if s.strip()]:
                skill, _ = Skill.objects.get_or_create(name=skill_name)
                profile.skills.add(skill)
        
        interests_str = request.POST.get('interests', '')
        profile.interests.clear()
        if interests_str:
            for interest_name in [i.strip() for i in interests_str.split(',') if i.strip()]:
                interest, _ = Interest.objects.get_or_create(name=interest_name)
                profile.interests.add(interest)
        
        profile.save()
        
        # ✅ Clear cache when profile is updated
        cache_key = f'opportunities_{request.user.id}'
        cache.delete(cache_key)
        
        messages.success(request, 'Profile updated successfully!')
        return redirect('profile')
    
    skills_list = [s.name for s in profile.skills.all()]
    interests_list = [i.name for i in profile.interests.all()]
    profile_completion = calculate_profile_completion(profile)
    
    return render(request, 'core/profile.html', {
        'user': user,
        'profile': profile,
        'skills_list': skills_list,
        'interests_list': interests_list,
        'profile_completion': profile_completion,
    })


def calculate_profile_completion(profile):
    total = 0
    max_points = 6
    
    if profile.degree: total += 1
    if profile.institution: total += 1
    if profile.location: total += 1
    if profile.skills.exists(): total += 1
    if profile.interests.exists(): total += 1
    if profile.cv: total += 1
    
    return int((total / max_points) * 100)


# ============================================================
# MATCH CALCULATION - OPTIMIZED VERSION
# ============================================================

def calculate_match_score_optimized(user, opportunity, user_interests, user_skills, user_degree, user_location):
    """
    Optimized match calculation using pre-fetched data
    """
    try:
        score = 0
        
        # Quick check: if no profile data, return 0 immediately
        if not user_interests and not user_skills and not user_degree and not user_location:
            return 0
        
        # ==========================================================
        # 1. INTERESTS MATCH (25 points)
        # ==========================================================
        if user_interests:
            opp_sector = (opportunity.sector or '').lower()
            opp_title = opportunity.title.lower()
            for interest in user_interests:
                interest_lower = interest.lower()
                if interest_lower in opp_sector or interest_lower in opp_title:
                    score += 25
                    break
        
        # ==========================================================
        # 2. QUALIFICATION MATCH (25 points)
        # ==========================================================
        if user_degree and opportunity.required_degree:
            user_degree_lower = user_degree.lower()
            opp_degree_lower = opportunity.required_degree.lower()
            
            if user_degree_lower in opp_degree_lower or opp_degree_lower in user_degree_lower:
                score += 25
            elif any(word in opp_degree_lower for word in user_degree_lower.split() if len(word) > 3):
                score += 17
        
        # ==========================================================
        # 3. SKILLS MATCH (25 points)
        # ==========================================================
        if user_skills:
            opp_skills = list(opportunity.required_skills.values_list('name', flat=True))
            if opp_skills:
                matched_skills = 0
                opp_skills_lower = [s.lower() for s in opp_skills]
                
                for skill in user_skills[:5]:
                    if skill and skill.lower() in opp_skills_lower:
                        matched_skills += 1
                
                if matched_skills > 0:
                    skill_percentage = min(matched_skills / len(opp_skills), 1.0)
                    score += int(25 * skill_percentage)
            else:
                # No skills required, give some credit if user has skills
                score += 12
        
        # ==========================================================
        # 4. LOCATION MATCH (25 points)
        # ==========================================================
        if user_location and opportunity.location:
            user_loc_lower = user_location.lower()
            opp_loc_lower = opportunity.location.lower()
            
            if user_loc_lower == opp_loc_lower:
                score += 25
            elif opp_loc_lower in user_loc_lower or user_loc_lower in opp_loc_lower:
                score += 15
        
        return min(score, 100)
        
    except Exception as e:
        print(f"Error in calculate_match_score_optimized: {str(e)}")
        return 0


def generate_match_reason_optimized(user, opportunity, user_interests, user_skills, user_degree, user_location):
    """
    Optimized match reason generation
    """
    try:
        reasons = []
        
        # Check interests
        for interest in user_interests:
            if interest and interest.lower() in (opportunity.sector or '').lower():
                reasons.append(f"Your interest in {interest}")
                break
            elif interest and interest.lower() in opportunity.title.lower():
                reasons.append(f"Your interest in {interest}")
                break
        
        # Check qualification
        if user_degree and opportunity.required_degree:
            if user_degree.lower() in opportunity.required_degree.lower():
                reasons.append(f"Your {user_degree} qualification")
        
        # Check skills
        opp_skills = list(opportunity.required_skills.values_list('name', flat=True))
        for skill in user_skills[:3]:
            if skill and skill.lower() in [s.lower() for s in opp_skills]:
                reasons.append(f"Your {skill} skill")
                break
        
        # Check location
        if user_location and opportunity.location:
            if user_location.lower() == opportunity.location.lower():
                reasons.append(f"Located in {user_location}")
        
        # If no specific reasons, return a meaningful default
        if not reasons:
            # Check if there's a match score
            match_score = calculate_match_score_optimized(user, opportunity, user_interests, user_skills, user_degree, user_location)
            if match_score > 0:
                return "Based on your profile preferences and skills"
            else:
                return "Complete your profile for better matches"
        
        return ' • '.join(reasons[:3])
        
    except Exception as e:
        print(f"Error generating match reason: {str(e)}")
        return "Based on your profile preferences"


# ============================================================
# API ENDPOINTS
# ============================================================

@login_required
def api_personalized_opportunities(request):
    """
    Get personalized opportunities based on user profile - ONLY TOP 5 with diversity
    Returns exactly 5 opportunities (or fewer if less than 5 matches exist)
    """
    try:
        profile = Profile.objects.get(user=request.user)
        user_interests = list(profile.interests.values_list('name', flat=True))
        user_skills = list(profile.skills.values_list('name', flat=True))
        user_degree = profile.degree.lower() if profile.degree else ''
        user_location = profile.location.lower() if profile.location else ''
        
        opportunities = Opportunity.objects.filter(is_active=True)
        personalized_opps = []
        
        for opp in opportunities:
            # ✅ Use optimized functions
            match_score = calculate_match_score_optimized(
                request.user, opp, user_interests, user_skills, user_degree, user_location
            )
            
            if match_score > 0:
                match_reason = generate_match_reason_optimized(
                    request.user, opp, user_interests, user_skills, user_degree, user_location
                )
                
                personalized_opps.append({
                    'id': opp.id,
                    'title': opp.title,
                    'company': opp.company,
                    'location': opp.location,
                    'duration': opp.duration or 'Not specified',
                    'stipend': opp.stipend or 'Market related',
                    'deadline': opp.application_deadline.strftime('%d %b %Y') if opp.application_deadline else 'Open',
                    'type': opp.opportunity_type,
                    'match_score': match_score,
                    'match_reason': match_reason,
                    'redirect_url': opp.redirect_url,
                    'description': opp.description,
                    'eligibility': opp.eligibility or 'South African citizens',
                })
        
        # Sort by match score (highest first)
        personalized_opps.sort(key=lambda x: x['match_score'], reverse=True)
        
        # Get top 5 with diversity
        top_5 = []
        types_seen = set()
        
        for opp in personalized_opps:
            if opp['type'] not in types_seen and len(top_5) < 5:
                top_5.append(opp)
                types_seen.add(opp['type'])
        
        for opp in personalized_opps:
            if len(top_5) >= 5:
                break
            if opp not in top_5:
                top_5.append(opp)
        
        total_matches = len(personalized_opps)
        saved_count = SavedOpportunity.objects.filter(user=request.user).count()
        
        return JsonResponse({
            'success': True,
            'opportunities': top_5,
            'all_matches_count': total_matches,
            'profile_completion': calculate_profile_completion(profile),
            'saved_count': saved_count,
        })
    except Profile.DoesNotExist:
        return JsonResponse({
            'success': True,
            'opportunities': [],
            'all_matches_count': 0,
            'profile_completion': 0,
            'saved_count': 0
        })
    except Exception as e:
        print(f"Error in api_personalized_opportunities: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def api_all_opportunities(request):
    """Get all opportunities for the opportunities page"""
    try:
        opportunities = Opportunity.objects.filter(is_active=True).order_by('-date_listed')
        opp_list = []
        for opp in opportunities:
            opp_list.append({
                'id': opp.id,
                'title': opp.title,
                'company': opp.company,
                'location': opp.location,
                'duration': opp.duration,
                'stipend': opp.stipend,
                'deadline': opp.application_deadline.strftime('%d %b %Y') if opp.application_deadline else 'Open',
                'type': opp.opportunity_type,
                'redirect_url': opp.redirect_url,
                'description': opp.description,
                'duties': opp.duties,
                'eligibility': opp.eligibility,
                'required_degree': opp.required_degree,
                'minimum_nqf': opp.minimum_nqf,
                'sector': opp.sector,
            })
        return JsonResponse({'success': True, 'opportunities': opp_list})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_save_opportunity(request):
    """Save or unsave an opportunity"""
    try:
        data = json.loads(request.body)
        opportunity_id = data.get('opportunity_id')
        action = data.get('action', 'save')
        
        opportunity = Opportunity.objects.get(id=opportunity_id, is_active=True)
        
        if action == 'save':
            saved, created = SavedOpportunity.objects.get_or_create(
                user=request.user, opportunity=opportunity
            )
            if created:
                saved_count = SavedOpportunity.objects.filter(user=request.user).count()
                return JsonResponse({'success': True, 'message': 'Opportunity saved', 'saved_count': saved_count})
            return JsonResponse({'success': False, 'error': 'Already saved'}, status=400)
        else:
            deleted, _ = SavedOpportunity.objects.filter(
                user=request.user, opportunity=opportunity
            ).delete()
            if deleted:
                saved_count = SavedOpportunity.objects.filter(user=request.user).count()
                return JsonResponse({'success': True, 'message': 'Opportunity removed', 'saved_count': saved_count})
            return JsonResponse({'success': False, 'error': 'Not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def api_get_saved_opportunities(request):
    """Get list of saved opportunity IDs for the current user"""
    try:
        saved = SavedOpportunity.objects.filter(user=request.user).values_list('opportunity_id', flat=True)
        saved_count = len(saved)
        return JsonResponse({'success': True, 'saved_ids': list(saved), 'saved_count': saved_count})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def api_get_saved_opportunities_details(request):
    """Get full details of saved opportunities with match scores"""
    try:
        # Get user profile data once
        try:
            profile = Profile.objects.select_related('user').prefetch_related('skills', 'interests').get(user=request.user)
            user_interests = list(profile.interests.values_list('name', flat=True))
            user_skills = list(profile.skills.values_list('name', flat=True))
            user_degree = profile.degree.lower() if profile.degree else ''
            user_location = profile.location.lower() if profile.location else ''
        except Profile.DoesNotExist:
            user_interests = []
            user_skills = []
            user_degree = ''
            user_location = ''
        
        saved_items = SavedOpportunity.objects.filter(user=request.user).select_related('opportunity')
        opportunities = []
        
        for item in saved_items:
            opp = item.opportunity
            
            # ✅ Calculate match scores for saved opportunities
            match_score = calculate_match_score_optimized(
                request.user, opp, user_interests, user_skills, user_degree, user_location
            )
            match_reason = generate_match_reason_optimized(
                request.user, opp, user_interests, user_skills, user_degree, user_location
            )
            
            opportunities.append({
                'id': opp.id,
                'title': opp.title,
                'company': opp.company,
                'location': opp.location,
                'duration': opp.duration,
                'stipend': opp.stipend,
                'deadline': opp.application_deadline.isoformat() if opp.application_deadline else None,
                'deadline_formatted': opp.application_deadline.strftime('%d %b %Y') if opp.application_deadline else 'Open',
                'type': opp.opportunity_type,
                'redirect_url': opp.redirect_url,
                'description': opp.description,
                'eligibility': opp.eligibility,
                'sector': opp.sector,
                'match_score': match_score,
                'match_reason': match_reason,
            })
        
        return JsonResponse({'success': True, 'opportunities': opportunities})
    except Exception as e:
        print(f"Error in api_get_saved_opportunities_details: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_track_apply_click(request):
    """Track when a user clicks Apply on an opportunity"""
    try:
        data = json.loads(request.body)
        opportunity_id = data.get('opportunity_id')
        
        if not opportunity_id:
            return JsonResponse({'error': 'Opportunity ID required'}, status=400)
        
        opportunity = Opportunity.objects.get(id=opportunity_id)
        opportunity.click_count = (opportunity.click_count or 0) + 1
        opportunity.last_clicked = timezone.now()
        opportunity.save()
        
        return JsonResponse({'success': True, 'message': 'Click tracked successfully'})
    except Opportunity.DoesNotExist:
        return JsonResponse({'error': 'Opportunity not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# ============================================================
# STORIES API
# ============================================================

@login_required
def api_get_stories(request):
    """Get all approved stories with profession data and like counts"""
    try:
        stories = Story.objects.filter(status='approved').order_by('-submitted_at')
        story_list = []
        now = timezone.now()
        
        for story in stories:
            time_diff = now - story.submitted_at
            
            if time_diff.days > 7:
                time_ago = f"{time_diff.days // 7}w ago"
            elif time_diff.days > 0:
                time_ago = f"{time_diff.days}d ago"
            elif time_diff.seconds > 3600:
                time_ago = f"{time_diff.seconds // 3600}h ago"
            elif time_diff.seconds > 60:
                time_ago = f"{time_diff.seconds // 60}m ago"
            else:
                time_ago = "Just now"
            
            like_count = story.likes.count()
            user_liked = story.likes.filter(user=request.user).exists()
            comment_count = story.comments.count()
            profession = getattr(story, 'profession', 'other')
            
            story_list.append({
                'id': story.id,
                'name': story.display_name,
                'profession': profession,
                'date': story.submitted_at.strftime('%d %b %Y'),
                'time': story.submitted_at.strftime('%H:%M'),
                'time_ago': time_ago,
                'content': story.content,
                'likes': like_count,
                'liked': user_liked,
                'comment_count': comment_count,
                'user_id': story.user.id if story.user else None,
            })
        return JsonResponse({'success': True, 'stories': story_list})
    except Exception as e:
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_post_story_with_profession(request):
    try:
        data = json.loads(request.body)
        name = data.get('name', '').strip()
        profession = data.get('profession', 'other').strip()
        content = data.get('content', '').strip()
        
        print(f"Received story - Name: {name}, Profession: {profession}, Content length: {len(content)}")
        
        if not name or not content:
            return JsonResponse({'error': 'Name and story content are required'}, status=400)
        
        story = Story.objects.create(
            user=request.user,
            display_name=name,
            title=f"Career Story - {profession}",
            content=content,
            status='approved'
        )
        
        if hasattr(story, 'profession'):
            story.profession = profession
            story.save(update_fields=['profession'])
        
        return JsonResponse({
            'success': True,
            'message': 'Story posted successfully',
            'story_id': story.id
        })
    except Exception as e:
        print(f"Error posting story: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_edit_story(request):
    try:
        data = json.loads(request.body)
        story_id = data.get('story_id')
        new_content = data.get('content', '').strip()
        
        if not new_content:
            return JsonResponse({'error': 'Story content cannot be empty'}, status=400)
        
        story = Story.objects.get(id=story_id)
        
        if story.user != request.user and not request.user.is_superuser:
            return JsonResponse({'error': 'You do not have permission to edit this story'}, status=403)
        
        story.content = new_content
        story.save()
        
        return JsonResponse({'success': True, 'message': 'Story updated successfully'})
    except Story.DoesNotExist:
        return JsonResponse({'error': 'Story not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_delete_story(request):
    try:
        data = json.loads(request.body)
        story_id = data.get('story_id')
        
        story = Story.objects.get(id=story_id)
        
        if story.user != request.user and not request.user.is_superuser:
            return JsonResponse({'error': 'You do not have permission to delete this story'}, status=403)
        
        story.delete()
        
        return JsonResponse({'success': True, 'message': 'Story deleted successfully'})
    except Story.DoesNotExist:
        return JsonResponse({'error': 'Story not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_toggle_story_like(request):
    try:
        data = json.loads(request.body)
        story_id = data.get('story_id')
        
        if not story_id:
            return JsonResponse({'error': 'Story ID required'}, status=400)
        
        story = Story.objects.get(id=story_id)
        existing_like = StoryLike.objects.filter(story=story, user=request.user).first()
        
        if existing_like:
            existing_like.delete()
            liked = False
        else:
            StoryLike.objects.create(story=story, user=request.user)
            liked = True
        
        like_count = story.likes.count()
        
        return JsonResponse({
            'success': True,
            'liked': liked,
            'likes': like_count
        })
    except Story.DoesNotExist:
        return JsonResponse({'error': 'Story not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# ============================================================
# COMMENT API
# ============================================================

@login_required
def api_get_comments(request):
    """Get comments for a story with replies"""
    try:
        story_id = request.GET.get('story_id')
        if not story_id:
            return JsonResponse({'error': 'Story ID required'}, status=400)
        
        story = Story.objects.get(id=story_id)
        comments = story.comments.filter(parent__isnull=True)
        
        comment_list = []
        for comment in comments:
            comment_list.append(get_comment_with_replies(comment, request.user))
        
        return JsonResponse({'success': True, 'comments': comment_list})
    except Story.DoesNotExist:
        return JsonResponse({'error': 'Story not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


def get_comment_with_replies(comment, user):
    """Recursively get comment with all replies"""
    replies = comment.replies.all()
    reply_list = []
    for reply in replies:
        reply_list.append(get_comment_with_replies(reply, user))
    
    return {
        'id': comment.id,
        'user_id': comment.user.id,
        'username': comment.user.get_full_name() or comment.user.username,
        'content': comment.content,
        'created_at': comment.created_at.isoformat(),
        'time_ago': get_time_ago(comment.created_at),
        'is_owner': comment.user.id == user.id,
        'is_edited': comment.is_edited,
        'reply_count': comment.replies.count(),
        'replies': reply_list,
    }


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_add_comment(request):
    """Add a comment or reply to a story"""
    try:
        data = json.loads(request.body)
        story_id = data.get('story_id')
        parent_id = data.get('parent_id')
        content = data.get('content', '').strip()
        
        if not story_id:
            return JsonResponse({'error': 'Story ID required'}, status=400)
        if not content:
            return JsonResponse({'error': 'Comment content is required'}, status=400)
        
        story = Story.objects.get(id=story_id)
        parent = None
        
        if parent_id:
            parent = Comment.objects.get(id=parent_id)
            if parent.story.id != story.id:
                return JsonResponse({'error': 'Invalid parent comment'}, status=400)
        
        comment = Comment.objects.create(
            story=story,
            user=request.user,
            parent=parent,
            content=content
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Comment added successfully',
            'comment': get_comment_with_replies(comment, request.user)
        })
    except Story.DoesNotExist:
        return JsonResponse({'error': 'Story not found'}, status=404)
    except Comment.DoesNotExist:
        return JsonResponse({'error': 'Parent comment not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_edit_comment(request):
    """Edit a comment"""
    try:
        data = json.loads(request.body)
        comment_id = data.get('comment_id')
        content = data.get('content', '').strip()
        
        if not comment_id:
            return JsonResponse({'error': 'Comment ID required'}, status=400)
        if not content:
            return JsonResponse({'error': 'Comment content cannot be empty'}, status=400)
        
        comment = Comment.objects.get(id=comment_id)
        
        if comment.user.id != request.user.id:
            return JsonResponse({'error': 'You do not have permission to edit this comment'}, status=403)
        
        comment.content = content
        comment.is_edited = True
        comment.save()
        
        return JsonResponse({
            'success': True,
            'message': 'Comment updated successfully',
            'comment': {
                'id': comment.id,
                'content': comment.content,
                'is_edited': comment.is_edited,
                'updated_at': comment.updated_at.isoformat(),
                'time_ago': get_time_ago(comment.updated_at),
            }
        })
    except Comment.DoesNotExist:
        return JsonResponse({'error': 'Comment not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_delete_comment(request):
    """Delete a comment and all its replies"""
    try:
        data = json.loads(request.body)
        comment_id = data.get('comment_id')
        
        if not comment_id:
            return JsonResponse({'error': 'Comment ID required'}, status=400)
        
        comment = Comment.objects.get(id=comment_id)
        
        if comment.user.id != request.user.id:
            return JsonResponse({'error': 'You do not have permission to delete this comment'}, status=403)
        
        comment.delete()
        
        return JsonResponse({'success': True, 'message': 'Comment deleted successfully'})
    except Comment.DoesNotExist:
        return JsonResponse({'error': 'Comment not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def api_search_users(request):
    """Search for users by username for @mention autocomplete"""
    try:
        query = request.GET.get('q', '').strip()
        if not query:
            return JsonResponse({'users': []})
        
        users = User.objects.filter(
            Q(username__icontains=query) |
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query)
        ).exclude(id=request.user.id)[:5]
        
        user_list = []
        for user in users:
            user_list.append({
                'id': user.id,
                'username': user.username,
                'display_name': user.get_full_name() or user.username,
            })
        
        return JsonResponse({'users': user_list})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


def get_time_ago(dt):
    """Helper function to calculate time ago"""
    now = timezone.now()
    diff = now - dt
    
    if diff.days > 7:
        return f"{diff.days // 7}w ago"
    elif diff.days > 0:
        return f"{diff.days}d ago"
    elif diff.seconds > 3600:
        return f"{diff.seconds // 3600}h ago"
    elif diff.seconds > 60:
        return f"{diff.seconds // 60}m ago"
    else:
        return "Just now"


# ============================================================
# AUTHENTICATION VIEWS
# ============================================================

def send_verification_email(to_email, code, first_name):
    subject = 'Verify Your Email - CareerGPS'
    message = f'Hello {first_name},\n\nYour verification code is: {code}\n\nThis code will expire in 10 minutes.\n\nBest regards,\nCareerGPS Team'
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [to_email], fail_silently=False)


def send_reset_email(to_email, code, first_name):
    subject = 'Reset Your Password - CareerGPS'
    message = f'Hello {first_name},\n\nYour password reset code is: {code}\n\nThis code will expire in 30 minutes.\n\nBest regards,\nCareerGPS Team'
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [to_email], fail_silently=False)


@csrf_exempt
@require_http_methods(["POST"])
def api_register(request):
    try:
        data = json.loads(request.body)
        first_name = data.get('first_name', '').strip()
        last_name = data.get('last_name', '').strip()
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')
        
        if not all([first_name, last_name, email, password]):
            return JsonResponse({'error': 'All fields are required'}, status=400)
        
        if User.objects.filter(username=email).exists():
            return JsonResponse({'error': 'Email already registered'}, status=400)
        
        if len(password) < 8:
            return JsonResponse({'error': 'Password must be at least 8 characters'}, status=400)
        
        code = ''.join(random.choices(string.digits, k=6))
        verification_codes[email] = {
            'code': code,
            'first_name': first_name,
            'last_name': last_name,
            'password': password
        }
        
        send_verification_email(email, code, first_name)
        
        return JsonResponse({'success': True, 'email': email})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_resend_verification(request):
    try:
        data = json.loads(request.body)
        email = data.get('email', '').strip().lower()
        
        if email not in verification_codes:
            return JsonResponse({'error': 'Email not found'}, status=404)
        
        new_code = ''.join(random.choices(string.digits, k=6))
        verification_codes[email]['code'] = new_code
        
        send_verification_email(email, new_code, verification_codes[email]['first_name'])
        
        return JsonResponse({'success': True, 'message': 'New verification code sent'})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_verify_email(request):
    try:
        data = json.loads(request.body)
        email = data.get('email', '').strip().lower()
        code = data.get('code', '').strip()
        
        if email not in verification_codes:
            return JsonResponse({'error': 'Invalid or expired verification'}, status=400)
        
        stored = verification_codes[email]
        
        if stored['code'] != code:
            return JsonResponse({'error': 'Invalid verification code'}, status=400)
        
        user = User.objects.create_user(
            username=email,
            email=email,
            password=stored['password'],
            first_name=stored['first_name'],
            last_name=stored['last_name']
        )
        Profile.objects.create(user=user)
        login(request, user)
        del verification_codes[email]
        
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_complete_profile(request):
    try:
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'Not authenticated'}, status=401)
        
        data = json.loads(request.body)
        profile, created = Profile.objects.get_or_create(user=request.user)
        
        degree = data.get('degree', '')
        if degree == 'other':
            degree = data.get('custom_degree', '')
        
        institution = data.get('institution', '')
        if institution == 'other':
            institution = data.get('custom_institution', '')
        
        location = data.get('location', '')
        if location == 'other':
            location = data.get('custom_location', '')
        
        profile.degree = degree
        profile.institution = institution
        profile.location = location
        profile.custom_skills = data.get('other_skills', '')
        profile.profile_complete = True
        
        skills_list = data.get('skills', [])
        if not skills_list:
            skills_list = data.get('custom_skills', [])
        
        for skill_name in skills_list:
            if skill_name and skill_name.strip():
                skill, _ = Skill.objects.get_or_create(name=skill_name.strip())
                profile.skills.add(skill)
        
        interests_list = data.get('interests', [])
        if not interests_list:
            interests_list = data.get('custom_interests', [])
        
        for interest_name in interests_list:
            if interest_name and interest_name.strip():
                interest, _ = Interest.objects.get_or_create(name=interest_name.strip())
                profile.interests.add(interest)
        
        profile.save()
        
        return JsonResponse({'success': True, 'message': 'Profile completed successfully'})
    except Exception as e:
        print(f"Error in api_complete_profile: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_signin(request):
    try:
        data = json.loads(request.body)
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')
        
        if not email or not password:
            return JsonResponse({'error': 'Email and password are required'}, status=400)
        
        user = authenticate(request, username=email, password=password)
        
        if user is not None:
            login(request, user)
            return JsonResponse({
                'success': True,
                'message': 'Login successful',
                'user_id': user.id,
                'redirect_url': '/welcome/'
            })
        else:
            if User.objects.filter(username=email).exists():
                return JsonResponse({'error': 'Incorrect password'}, status=401)
            else:
                return JsonResponse({'error': 'No account found'}, status=401)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
def api_signout(request):
    logout(request)
    return JsonResponse({'success': True})


@csrf_exempt
@require_http_methods(["POST"])
def api_forgot_password(request):
    try:
        data = json.loads(request.body)
        email = data.get('email', '').strip().lower()
        
        try:
            user = User.objects.get(email=email)
            code = ''.join(random.choices(string.digits, k=6))
            reset_tokens[email] = {'code': code, 'created_at': datetime.now(), 'user_id': user.id}
            send_reset_email(email, code, user.first_name or user.username)
            return JsonResponse({'success': True, 'message': 'Reset code sent'})
        except User.DoesNotExist:
            return JsonResponse({'success': True, 'message': 'If account exists, code sent'})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_resend_reset_code(request):
    try:
        data = json.loads(request.body)
        email = data.get('email', '').strip().lower()
        
        if email not in reset_tokens:
            return JsonResponse({'error': 'No reset request found'}, status=404)
        
        try:
            user = User.objects.get(email=email)
            new_code = ''.join(random.choices(string.digits, k=6))
            reset_tokens[email]['code'] = new_code
            reset_tokens[email]['created_at'] = datetime.now()
            send_reset_email(email, new_code, user.first_name or user.username)
            return JsonResponse({'success': True, 'message': 'New code sent'})
        except User.DoesNotExist:
            return JsonResponse({'error': 'User not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_verify_reset_code(request):
    try:
        data = json.loads(request.body)
        email = data.get('email', '').strip().lower()
        code = data.get('code', '').strip()
        
        if email not in reset_tokens:
            return JsonResponse({'error': 'Invalid or expired request'}, status=400)
        
        stored = reset_tokens[email]
        
        if datetime.now() - stored['created_at'] > timedelta(minutes=30):
            del reset_tokens[email]
            return JsonResponse({'error': 'Code expired'}, status=400)
        
        if stored['code'] != code:
            return JsonResponse({'error': 'Invalid code'}, status=400)
        
        return JsonResponse({'success': True, 'message': 'Code verified'})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def api_reset_password(request):
    try:
        data = json.loads(request.body)
        email = data.get('email', '').strip().lower()
        code = data.get('code', '').strip()
        new_password = data.get('new_password', '')
        confirm_password = data.get('confirm_password', '')
        
        if new_password != confirm_password:
            return JsonResponse({'error': 'Passwords do not match'}, status=400)
        
        if len(new_password) < 8:
            return JsonResponse({'error': 'Password must be at least 8 characters'}, status=400)
        
        if email not in reset_tokens:
            return JsonResponse({'error': 'Invalid request'}, status=400)
        
        stored = reset_tokens[email]
        
        if datetime.now() - stored['created_at'] > timedelta(minutes=30):
            del reset_tokens[email]
            return JsonResponse({'error': 'Code expired'}, status=400)
        
        if stored['code'] != code:
            return JsonResponse({'error': 'Invalid code'}, status=400)
        
        try:
            user = User.objects.get(email=email)
            user.set_password(new_password)
            user.save()
            del reset_tokens[email]
            return JsonResponse({'success': True, 'message': 'Password reset successfully'})
        except User.DoesNotExist:
            return JsonResponse({'error': 'User not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
def api_test(request):
    return JsonResponse({'success': True, 'message': 'API is working!'})


def api_occupations(request):
    """Return list of SA occupations for the homepage search"""
    occupations = [
        "Software Engineer", "Data Scientist", "Accountant", "Financial Analyst",
        "Marketing Manager", "Human Resources Manager", "Project Manager",
        "Civil Engineer", "Mechanical Engineer", "Electrical Engineer",
        "Nurse", "Doctor", "Teacher", "Lawyer", "Architect", "Business Analyst",
        "Product Manager", "UX Designer", "Graphic Designer", "Sales Representative",
        "IT Support Specialist", "Network Engineer", "Cybersecurity Analyst",
    ]
    return JsonResponse({'occupations': occupations})


# ============================================================
# CV UPLOAD API
# ============================================================

@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_upload_cv(request):
    """Upload CV for the logged-in user"""
    try:
        profile = Profile.objects.get(user=request.user)
        
        if 'cv' not in request.FILES:
            return JsonResponse({'error': 'No file provided'}, status=400)
        
        cv_file = request.FILES['cv']
        
        allowed_types = ['application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document']
        if cv_file.content_type not in allowed_types:
            return JsonResponse({'error': 'Only PDF and DOCX files are allowed'}, status=400)
        
        if cv_file.size > 5 * 1024 * 1024:
            return JsonResponse({'error': 'File size must be less than 5MB'}, status=400)
        
        if profile.cv:
            profile.cv.delete()
        
        profile.cv = cv_file
        profile.save()
        
        return JsonResponse({
            'success': True,
            'message': 'CV uploaded successfully',
            'cv_url': profile.cv.url if profile.cv else None
        })
    except Profile.DoesNotExist:
        return JsonResponse({'error': 'Profile not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_remove_cv(request):
    """Remove CV for the logged-in user"""
    try:
        profile = Profile.objects.get(user=request.user)
        
        if profile.cv:
            profile.cv.delete()
            profile.cv = None
            profile.save()
            return JsonResponse({'success': True, 'message': 'CV removed successfully'})
        else:
            return JsonResponse({'error': 'No CV found'}, status=404)
    except Profile.DoesNotExist:
        return JsonResponse({'error': 'Profile not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)