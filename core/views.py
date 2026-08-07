import datetime
import random
from django.utils import timezone
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import (Profile, Skill, Interest, Opportunity, 
                     SavedOpportunity, CVtip, MotivationalNudge, Story)
from .forms import RegistrationForm, ProfileCompletionForm, StoryForm
from .matching import (get_matched_opportunities, build_why_text, calculate_completion, 
                       calculate_match_score, get_match_strength, get_progress_items)
 
def landing(request):
    if request.user.is_authenticated:
        return redirect('dashboard')  
    featured_opportunities = Opportunity.objects.filter(
        is_active=True
    )[:6]
    return render(request, 'core/landing.html', {
        'featured_opportunities': featured_opportunities
    })

def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()

            
            Profile.objects.create(user=user)

            # Generate OTP
            otp = random.randint(100000, 999999)

            # Store in session
            request.session['otp'] = otp
            request.session['user_id'] = user.id

            # Print to terminal
            print(f"\n{'='*50}")
            print(f"CareerGPS OTP for {user.email}: {otp}")
            print(f"{'='*50}\n")

            return redirect('verify_otp')
    else:
        form = RegistrationForm()

    return render(request, 'core/register.html', {'form': form})

def verify_otp(request):
    if request.method == 'POST':
        entered_otp = request.POST.get('otp')
        stored_otp = str(request.session.get('otp'))
        user_id = request.session.get('user_id')

        if not user_id:
            messages.error(request, "Session expired. Please register again.")
            return redirect('register')

        if entered_otp == stored_otp:
            user = User.objects.get(id=user_id)
            user.is_active = True
            user.save()

            # Clear session
            del request.session['otp']
            del request.session['user_id']

            login(request, user)
            messages.success(
                request,
                f"Welcome to CareerGPS, {user.first_name}! "
                "Complete your profile to see personalised opportunities."
            )
            return redirect('dashboard')
        else:
            messages.error(request, "Invalid OTP. Please try again.")

    return render(request, 'core/verify_otp.html', {})

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    
    error = None
    
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        
        try:
            user = User.objects.get(email=email)
            user = authenticate(
                request,
                username=user.username,
                password=password
            )
            if user:
                login(request, user)
                return redirect('dashboard')
            else:
                error = "Invalid email or password."
        except User.DoesNotExist:
            error = "No account found with that email."
    
    return render(request, 'core/login.html', {'error': error})

def logout_confirm(request):
    return render(request, 'core/logout_confirm.html', {})

def logout_view(request):
    logout(request)
    messages.success(
        request, "You have been logged out successfully. See you soon!"     
    )
    return redirect('landing')

@login_required
def dashboard_view(request):
    profile = get_object_or_404(Profile, user=request.user)

    # --------------------------------------------------
    # Base statistics
    # --------------------------------------------------

    saved_count = SavedOpportunity.objects.filter(
        user=request.user
    ).count()

    week_from_now = timezone.now().date() + datetime.timedelta(days=7)
    closing_soon = SavedOpportunity.objects.filter(
          user=request.user,
          opportunity__application_deadline__lte=week_from_now,
          opportunity__application_deadline__gte=timezone.now().date()
     ).count()

    completion_percentage = calculate_completion(profile)

    context = {
        "profile": profile,
        "saved_count": saved_count,
        "closing_soon": closing_soon,
        "completion_percentage": completion_percentage
    }

    # --------------------------------------------------
    # PROFILE COMPLETE
    # --------------------------------------------------

    if profile.profile_complete:

        matched_results = get_matched_opportunities(profile)
        saved_ids = set(SavedOpportunity.objects.filter(user=request.user).values_list("opportunity_id", flat=True))

        for result in matched_results:
             result["is_saved"] = (
            result["opportunity"].id in saved_ids)

        top_matches = matched_results[:3]

        context.update({
            "top_matches": top_matches,
            "matches_today": len(top_matches),
            "show_personalised": True,
            "show_profile_prompt": False,
            "browse_url": "opportunities",
        })

        # Motivation
        nudge = (
            MotivationalNudge.objects.filter(
                trigger="general",
                is_active=True
            )
            .order_by("?")
            .first()
        )

    # --------------------------------------------------
    # PROFILE INCOMPLETE
    # --------------------------------------------------

    else:

        generic_opportunities = (
        Opportunity.objects.filter(is_active=True).order_by("-date_listed")[:3]
        )

        saved_ids = set(
        SavedOpportunity.objects.filter(user=request.user ).values_list("opportunity_id", flat=True)
)

        generic_results = []

        for opportunity in generic_opportunities:
             generic_results.append({"opportunity": opportunity,"match_strength": "General", "why_text": (
             "Complete your profile to unlock personalised recommendations."
              ), "is_saved": opportunity.id in saved_ids,})

        context.update({
            "top_matches": generic_results,
            "matches_today": 0,
            "show_personalised": False,
            "show_profile_prompt": True,
            "browse_url": "opportunities",})

        nudge = (
            MotivationalNudge.objects.filter(
                trigger="profile_incomplete",
                is_active=True
            )
            .order_by("?")
            .first()
        )

        # Fallback if none exist
        if not nudge:
            nudge = (
                MotivationalNudge.objects.filter(
                    trigger="general",
                    is_active=True
                )
                .order_by("?")
                .first()
            )

    context["nudge"] = nudge
    
    return render(request, "core/dashboard.html", context)

@login_required
def opportunities_view(request):

    profile = get_object_or_404(Profile, user=request.user)

    # --------------------------------------------------
    # Get opportunities
    # --------------------------------------------------

    if profile.profile_complete:
        matched_results = get_matched_opportunities(profile)
    else:
        opportunities = Opportunity.objects.filter(
            is_active=True
        ).order_by("-date_listed")

        matched_results = [
            {
                "opportunity": opportunity,
                "score": None,
                "match_strength": None,
                "why_text": "",
                "cv_tips": [],
            }
            for opportunity in opportunities
        ]
    
    saved_ids = set(
    SavedOpportunity.objects.filter(
        user=request.user
    ).values_list("opportunity_id", flat=True))

    for result in matched_results:
         result["is_saved"] = result["opportunity"].id in saved_ids

    # --------------------------------------------------
    # Search
    # --------------------------------------------------

    search = request.GET.get("search", "").strip()

    if search:
        matched_results = [
            result
            for result in matched_results
            if (
                search.lower()
                in result["opportunity"].title.lower()
                or
                search.lower()
                in result["opportunity"].company.lower()
                or 
                search.lower() 
                in (result["opportunity"].location or "").lower()
                or 
                search.lower() 
                in (result["opportunity"].sector or "").lower()
                or search.lower() 
                in (result["opportunity"].required_qualification or "").lower()
            )
        ]

     # --------------------------------------------------
     # Opportunity Type Filter
     # --------------------------------------------------

    opportunity_type = request.GET.get("type", "all")

    if opportunity_type != "all":
        matched_results = [
        result
        for result in matched_results
        if result["opportunity"].opportunity_type == opportunity_type
    ]
    
    context = {
    "results": matched_results,
    "profile_complete": profile.profile_complete,
    "search": search,
    "selected_type": opportunity_type,
    }

    return render(request, "core/opportunities.html", context)

@login_required
def opportunity_detail_view(request, opportunity_id):

    opportunity = get_object_or_404(
        Opportunity,
        id=opportunity_id,
        is_active=True
    )

    profile = get_object_or_404(
        Profile,
        user=request.user
    )

    is_saved = SavedOpportunity.objects.filter(
        user=request.user,
        opportunity=opportunity
    ).exists()

    match_data = None

    if profile.profile_complete:
        score, matched_reasons = calculate_match_score(
            profile,
            opportunity
        )

        match_strength = get_match_strength(score)

        why_text = build_why_text(
            matched_reasons,
            match_strength
        )

        cv_tips = CVtip.objects.filter(
            opportunity_type=opportunity.opportunity_type,
            is_active=True
        ).filter(
            related_skills__in=profile.skills.all()
        ).distinct()[:3]

        if not cv_tips.exists():
            cv_tips = CVtip.objects.filter(
                opportunity_type=opportunity.opportunity_type,
                is_active=True
            )[:3]

        match_data = {
            "score": score,
            "match_strength": match_strength,
            "why_text": why_text,
            "matched_reasons": matched_reasons,
            "cv_tips": cv_tips,
        }

    context = {
        "opportunity": opportunity,
        "profile": profile,
        "match_data": match_data,
        "is_saved": is_saved,
    }

    return render( request, "core/opportunity_detail.html", context)

@login_required
def profile_view(request):

    profile = get_object_or_404( Profile, user=request.user)

    if request.method == "POST":
        form = ProfileCompletionForm(
            request.POST,
            instance=profile
        )

        if form.is_valid():
             profile = form.save(commit=False)
             if profile.qualification and profile.institution and profile.location:
                     profile.profile_complete = True
                     profile.save()
                     form.save_m2m()  
                     messages.success(request,
                     "Your profile has been updated successfully.")
             return redirect("profile")

    else:

        form = ProfileCompletionForm( instance=profile)
    
    print("qualification:", repr(profile.qualification))
    print("institution:", repr(profile.institution))
    print("location:", repr(profile.location))
    print("skills exists:", profile.skills.exists())
    print("interests exists:", profile.interests.exists())
    print("completion:", calculate_completion(profile))

    context = {
    "profile": profile,
    "form": form,
    "completion_percentage": calculate_completion(profile),
    "progress_items": get_progress_items(profile),
}

    return render( request, "core/profile.html", context)

@login_required
def profile_complete_view(request):
    profile = get_object_or_404(Profile, user=request.user)

    if request.method == "POST":
        form = ProfileCompletionForm(
            request.POST,
            request.FILES,
            instance=profile
        )

        if form.is_valid():
            profile = form.save(commit=False)
            profile.save()
            form.save_m2m()

            # Check after saving M2M
            has_skills = profile.skills.exists()

            if profile.qualification and profile.institution and profile.location and has_skills:
                profile.profile_complete = True
            else:
                profile.profile_complete = False

            profile.save()

            messages.success(
                request,
                f"Profile updated successfully, {request.user.first_name}!"
            )

            return redirect("dashboard")
    else:
        form = ProfileCompletionForm(instance=profile)

    return render(request, "core/profile_complete.html", {
        "form": form,
        "profile": profile,
        "skills": Skill.objects.all(),
        "interests": Interest.objects.all(),
    })

@login_required
def toggle_save_view(request, opportunity_id):
    """
    Saves or removes an opportunity from the user's saved list.
    """

    opportunity = get_object_or_404(
        Opportunity,
        id=opportunity_id,
        is_active=True
    )

    saved_opportunity, created = SavedOpportunity.objects.get_or_create(
        user=request.user,
        opportunity=opportunity
    )

    if created:
        messages.success(
            request,
            f'"{opportunity.title}" has been added to your saved opportunities.'
        )
    else:
        saved_opportunity.delete()

        messages.info(
            request,
            f'"{opportunity.title}" has been removed from your saved opportunities.'
        )

    return redirect( request.META.get("HTTP_REFERER", "dashboard"))

@login_required
def saved_view(request):
    """
    Displays all opportunities saved by the current user.
    """

    saved_opportunities = (
        SavedOpportunity.objects
        .filter(user=request.user)
        .select_related("opportunity")
        .order_by("-saved_at")
    )

    return render(request, "core/saved.html", {
            "saved_opportunities": saved_opportunities,
            "saved_count": saved_opportunities.count(),
        })

@login_required
def stories_view(request):
    """
    Display approved community stories.
    """

    stories = (
        Story.objects
        .filter(status="approved")
        .select_related("user")
        .order_by("-submitted_at")
    )

    context = {
        "stories": stories,
    }

    return render(request, "core/stories.html", context)

@login_required
def submit_story_view(request):
    print(request.method)

    profile = get_object_or_404(
        Profile,
        user=request.user
    )

    if request.method == "POST":
        form = StoryForm(request.POST)

        if form.is_valid():
            story = form.save(commit=False)
            story.user = request.user
            story.display_name = request.user.get_full_name()
            story.qualification = profile.qualification
            story.institution = profile.institution
            story.status = "pending"

            story.save()

            messages.success(
                request,
                "Thank you for sharing your experience! "
                "Your story has been submitted and will appear once it has been reviewed."
            )

            return redirect("stories")
        
        else:
            print(form.errors)

        
    else:
        form = StoryForm()

    return render(request, "core/submit_story.html", {"form": form})

@login_required
def resources_view(request):

    profile = get_object_or_404(Profile, user=request.user)

    if profile.profile_complete and profile.skills.exists():
        cv_tips = (
            CVtip.objects.filter(
                is_active=True,
                related_skills__in=profile.skills.all()
            )
            .distinct()
            .order_by("opportunity_type", "-created_at")
        )

        # If nothing matched their skills,
        # show the general resources instead.
        if not cv_tips.exists():
            cv_tips = CVtip.objects.filter(
                is_active=True
            ).order_by(
                "opportunity_type",
                "-created_at"
            )

    else:
        cv_tips = CVtip.objects.filter(
            is_active=True
        ).order_by(
            "opportunity_type",
            "-created_at"
        )

    return render(request, "core/resources.html", { "profile": profile,
            "cv_tips": cv_tips})   

