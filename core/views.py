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
from .models import Profile, Skill, Interest, Opportunity, SavedOpportunity, Story

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
    """Display all opportunities from database - NO HARDCODING"""
    opportunities_list = Opportunity.objects.filter(is_active=True).order_by('-date_listed')
    return render(request, 'core/opportunities.html', {'opportunities': opportunities_list})

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
    """Display saved opportunities - NO HARDCODING"""
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
    if profile.degree: total += 20
    if profile.institution: total += 20
    if profile.location: total += 20
    if profile.skills.exists(): total += 20
    if profile.interests.exists(): total += 20
    return total

# ============================================================
# API ENDPOINTS
# ============================================================

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
    """Get full details of saved opportunities"""
    try:
        saved_items = SavedOpportunity.objects.filter(user=request.user).select_related('opportunity')
        opportunities = []
        for item in saved_items:
            opp = item.opportunity
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
                'match_score': calculate_match_score(request.user, opp),
                'match_reason': generate_match_reason(request.user, opp),
            })
        return JsonResponse({'success': True, 'opportunities': opportunities})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

@login_required
def api_personalized_opportunities(request):
    """Get personalized opportunities based on user profile - ONLY TOP 5"""
    try:
        profile = Profile.objects.get(user=request.user)
        user_interests = list(profile.interests.values_list('name', flat=True))
        user_skills = list(profile.skills.values_list('name', flat=True))
        user_degree = profile.degree.lower() if profile.degree else ''
        user_location = profile.location.lower() if profile.location else ''
        
        opportunities = Opportunity.objects.filter(is_active=True)
        personalized_opps = []
        
        for opp in opportunities:
            match_score = 0
            matched_items = []
            
            # Match interests (40%)
            for interest in user_interests:
                if interest.lower() in (opp.sector or '').lower() or interest.lower() in opp.title.lower():
                    match_score += 40
                    matched_items.append(f"Your interest in {interest}")
                    break
            
            # Match qualification (30%)
            if user_degree and opp.required_degree and user_degree in opp.required_degree.lower():
                match_score += 30
                matched_items.append(f"Your {profile.degree} qualification")
            
            # Match skills (20%)
            opp_skills = list(opp.required_skills.values_list('name', flat=True))
            for skill in user_skills[:3]:
                if skill.lower() in [s.lower() for s in opp_skills]:
                    match_score += min(len(user_skills) * 7, 20)
                    matched_items.append(f"Your {skill} skill")
                    break
            
            # Location bonus (10%)
            if user_location and opp.location and user_location in opp.location.lower():
                match_score += 10
                matched_items.append(f"Located in {profile.location}")
            
            if match_score > 0:
                personalized_opps.append({
                    'id': opp.id,
                    'title': opp.title,
                    'company': opp.company,
                    'location': opp.location,
                    'duration': opp.duration or 'Not specified',
                    'stipend': opp.stipend or 'Market related',
                    'deadline': opp.application_deadline.strftime('%d %b %Y') if opp.application_deadline else 'Open',
                    'type': opp.opportunity_type,
                    'match_score': min(match_score, 100),
                    'match_reason': ' • '.join(matched_items[:3]),
                    'redirect_url': opp.redirect_url,
                    'description': opp.description,
                    'eligibility': opp.eligibility or 'South African citizens',
                })
        
        # Sort by match score and get ONLY TOP 5
        personalized_opps.sort(key=lambda x: x['match_score'], reverse=True)
        top_5_matches = personalized_opps[:5]  # ONLY 5 matches
        
        saved_count = SavedOpportunity.objects.filter(user=request.user).count()
        
        return JsonResponse({
            'success': True,
            'opportunities': top_5_matches,  # ONLY TOP 5
            'profile_completion': calculate_profile_completion(profile),
            'saved_count': saved_count,
        })
    except Profile.DoesNotExist:
        return JsonResponse({'success': True, 'opportunities': [], 'profile_completion': 0, 'saved_count': 0})

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

def calculate_match_score(user, opportunity):
    """Calculate match score between user profile and opportunity"""
    try:
        profile = Profile.objects.get(user=user)
        score = 0
        user_interests = list(profile.interests.values_list('name', flat=True))
        user_skills = list(profile.skills.values_list('name', flat=True))
        
        for interest in user_interests:
            if interest.lower() in (opportunity.sector or '').lower():
                score += 40
                break
        
        if profile.degree and opportunity.required_degree and profile.degree.lower() in opportunity.required_degree.lower():
            score += 30
        
        opp_skills = list(opportunity.required_skills.values_list('name', flat=True))
        for skill in user_skills[:3]:
            if skill.lower() in [s.lower() for s in opp_skills]:
                score += min(len(user_skills) * 7, 20)
                break
        
        if profile.location and opportunity.location and profile.location.lower() in opportunity.location.lower():
            score += 10
        
        return min(score, 100)
    except:
        return 75

def generate_match_reason(user, opportunity):
    """Generate match reason text"""
    try:
        profile = Profile.objects.get(user=user)
        reasons = []
        
        user_interests = list(profile.interests.values_list('name', flat=True))
        for interest in user_interests:
            if interest.lower() in (opportunity.sector or '').lower():
                reasons.append(f"Your interest in {interest}")
                break
        
        if profile.degree and opportunity.required_degree and profile.degree.lower() in opportunity.required_degree.lower():
            reasons.append(f"Your {profile.degree} qualification")
        
        user_skills = list(profile.skills.values_list('name', flat=True))
        opp_skills = list(opportunity.required_skills.values_list('name', flat=True))
        for skill in user_skills[:3]:
            if skill.lower() in [s.lower() for s in opp_skills]:
                reasons.append(f"Your {skill} skill")
                break
        
        if profile.location and opportunity.location and profile.location.lower() in opportunity.location.lower():
            reasons.append(f"Located in {profile.location}")
        
        return ' • '.join(reasons[:3]) if reasons else "Based on your profile preferences"
    except:
        return "Based on your profile preferences"

# ============================================================
# STORIES API
# ============================================================

@login_required
@csrf_exempt
@require_http_methods(["POST"])
def api_post_story(request):
    try:
        data = json.loads(request.body)
        content = data.get('content', '').strip()
        display_name = data.get('display_name', request.user.get_full_name() or request.user.username)
        
        if not content:
            return JsonResponse({'error': 'Story content is required'}, status=400)
        
        story = Story.objects.create(
            user=request.user,
            display_name=display_name,
            title="Community Story",
            content=content,
            status='approved'
        )
        
        return JsonResponse({'success': True, 'message': 'Story posted successfully', 'story_id': story.id})
    except Exception as e:
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
            'code': code, 'first_name': first_name, 'last_name': last_name, 'password': password
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
            username=email, email=email, password=stored['password'],
            first_name=stored['first_name'], last_name=stored['last_name']
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
        
        for skill_name in data.get('skills', []):
            if skill_name:
                skill, _ = Skill.objects.get_or_create(name=skill_name)
                profile.skills.add(skill)
        
        for skill_name in data.get('custom_skills', []):
            if skill_name:
                skill, _ = Skill.objects.get_or_create(name=skill_name)
                profile.skills.add(skill)
        
        for interest_name in data.get('interests', []):
            if interest_name:
                interest, _ = Interest.objects.get_or_create(name=interest_name)
                profile.interests.add(interest)
        
        for interest_name in data.get('custom_interests', []):
            if interest_name:
                interest, _ = Interest.objects.get_or_create(name=interest_name)
                profile.interests.add(interest)
        
        profile.save()
        
        return JsonResponse({'success': True, 'message': 'Profile completed successfully'})
    except Exception as e:
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
                'success': True, 'message': 'Login successful',
                'user_id': user.id, 'redirect_url': '/welcome/'
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