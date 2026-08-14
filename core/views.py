import datetime
import random
import json
import os
import uuid
import pdfplumber
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from google import genai
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from docx import Document
from docx.shared import Pt, Inches
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from django.shortcuts import render
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

def _extract_text_from_cv(uploaded_file):
    """Takes a Django UploadedFile and returns its plain text content."""
    filename = uploaded_file.name.lower()

    if filename.endswith(".pdf"):
        text_parts = []
        with pdfplumber.open(uploaded_file) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        return "\n".join(text_parts)

    elif filename.endswith(".docx"):
        doc = Document(uploaded_file)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

    else:
        raise ValueError("Unsupported file type. Please upload a .pdf or .docx file.")
 
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

@login_required
def settings_view(request):
    return render(request, "core/settings.html")

def _build_cv_prompt(profile, job_requirements):
    return f"""
You are an elite CV writer specialising in repositioning candidates with little or 
unclear experience into stronger, employable professionals. Your objective is to 
transform raw or average CV content into results driven, comercially valuable cv 
aligned with SA hiring standards following these strict rules, the summary must be objective
(no descriptive words like 'motivated', 'hardworking', etc) every bullet point must show atleast 
one of the following: Outcomes, Deliverable, responsibility, measurable impact where possible. 
Remove all fluff and generic statements. Prioritise clarity, structure, and value over length (1 page cv). 
Write like a candidate has already operated in a professional environment. Using the candidate's profile below and the
job requirements provided, write a tailored CV. Respond ONLY with valid JSON,
no markdown fences, no commentary, matching exactly this shape:

{{
  "full_name": "string",
  "summary": "3-4 sentence professional summary tailored to the role",
  "key_projects": [
    {{
      "title": "string",
      "organisation": "string",
      "dates": "string",
      "description": "one sentence describing the project",
      "bullets": ["achievement 1", "achievement 2"]
    }}
  ],
  "work_experience": [
    {{
      "title": "string",
      "organisation": "string",
      "dates": "string",
      "bullets": ["achievement 1", "achievement 2"]
    }}
  ],
  "education": [
    {{"qualification": "string", "institution": "string", "dates": "string"}}
  ],
  "relevant_coursework": [
    {{"category": "string", "items": ["item1", "item2"]}}
  ],
  "certifications": [
    {{"title": "string", "dates": "string"}}
  ],
  "technical_skills": [
    {{"category": "string", "items": ["item1", "item2"]}}
  ],
  "soft_skills": ["skill1", "skill2"]
}}

Only include keys that have real content — omit "key_projects" entirely if the
candidate has no relevant projects, rather than inventing filler entries.

Candidate profile:
Name: {profile.get('full_name', '')}
Qualification: {profile.get('qualification', '')}
Institution: {profile.get('institution', '')}
Skills: {', '.join(profile.get('skills', []))}
Location: {profile.get('location', '')}

Job requirements pasted by the candidate:
\"\"\"{job_requirements}\"\"\"
"""

def _generate_cv_content(profile, job_requirements):
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    response = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=_build_cv_prompt(profile, job_requirements),
    )
    text = response.text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    return json.loads(text)

def _build_docx(cv_data, filepath):
    doc = Document()
    normal_style = doc.styles["Normal"]
    normal_style.font.name = "Calibri"
    normal_style.font.size = Pt(10.5)
    normal_style.paragraph_format.space_before = Pt(0)
    normal_style.paragraph_format.space_after = Pt(4)
    normal_style.paragraph_format.line_spacing = 1.0

    # ── Header: name centered, bold, larger; contact line centered below ──
    name_p = doc.add_paragraph()
    name_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    name_run = name_p.add_run(cv_data.get("full_name", "CV").upper())
    name_run.bold = True
    name_run.font.size = Pt(18)
    name_p.paragraph_format.space_after = Pt(2)

    contact_bits = [b for b in [cv_data.get("email"), cv_data.get("phone"), cv_data.get("location")] if b]
    if contact_bits:
        contact_p = doc.add_paragraph(" | ".join(contact_bits))
        contact_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        contact_p.runs[0].font.size = Pt(9)
        contact_p.paragraph_format.space_after = Pt(14)

    def add_section_heading(text):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after = Pt(4)
        run = p.add_run(text.upper())
        run.bold = True
        run.font.size = Pt(11)

    def add_entry_header(title_org, dates):
        # Title/org on the left, dates right-aligned on the SAME line via a right tab stop —
        # this achieves the same-line layout without a table or a visible divider/border.
        p = doc.add_paragraph()
        p.paragraph_format.tab_stops.add_tab_stop(Inches(6.3), WD_TAB_ALIGNMENT.RIGHT)
        title_run = p.add_run(title_org)
        title_run.bold = True
        title_run.font.size = Pt(10.5)
        if dates:
            p.add_run("\t")
            date_run = p.add_run(dates)
            date_run.italic = True
            date_run.font.size = Pt(10)
        p.paragraph_format.space_after = Pt(2)

    if cv_data.get("summary"):
        add_section_heading("Profile")
        summary_p = doc.add_paragraph(cv_data["summary"])
        summary_p.paragraph_format.space_after = Pt(6)
        summary_p.paragraph_format.line_spacing = 1.0

    if cv_data.get("key_projects"):
        add_section_heading("Key Projects")
        for proj in cv_data["key_projects"]:
            add_entry_header(f"{proj.get('title', '')} | {proj.get('organisation', '')}", proj.get("dates", ""))
            if proj.get("description"):
                doc.add_paragraph(proj["description"]).paragraph_format.space_after = Pt(2)
            for bullet in proj.get("bullets", []):
                bp = doc.add_paragraph(bullet, style="List Bullet")
                bp.paragraph_format.space_after = Pt(2)

    if cv_data.get("work_experience"):
        add_section_heading("Work Experience")
        for job in cv_data["work_experience"]:
            add_entry_header(f"{job.get('title', '')} | {job.get('organisation', '')}", job.get("dates", ""))
            for bullet in job.get("bullets", []):
                bp = doc.add_paragraph(bullet, style="List Bullet")
                bp.paragraph_format.space_after = Pt(2)
                bp.paragraph_format.line_spacing = 1.0

    if cv_data.get("education"):
        add_section_heading("Education")
        for edu in cv_data["education"]:
            add_entry_header(f"{edu.get('qualification', '')} | {edu.get('institution', '')}", edu.get("dates", ""))

    if cv_data.get("relevant_coursework"):
        add_section_heading("Relevant Coursework")
        for group in cv_data["relevant_coursework"]:
            p = doc.add_paragraph(style="List Bullet")
            r = p.add_run(f"{group.get('category', '')}: ")
            r.bold = True
            p.add_run(" | ".join(group.get("items", [])))
            p.paragraph_format.space_after = Pt(2)

    if cv_data.get("certifications"):
        add_section_heading("Certifications")
        for cert in cv_data["certifications"]:
            add_entry_header(cert.get("title", ""), cert.get("dates", ""))

    if cv_data.get("technical_skills"):
        add_section_heading("Technical Skills")
        for group in cv_data["technical_skills"]:
            p = doc.add_paragraph(style="List Bullet")
            r = p.add_run(f"{group.get('category', '')}: ")
            r.bold = True
            p.add_run(" | ".join(group.get("items", [])))
            p.paragraph_format.space_after = Pt(2)

    if cv_data.get("soft_skills"):
        add_section_heading("Soft Skills")
        doc.add_paragraph(" | ".join(cv_data["soft_skills"]))

    doc.save(filepath)

def _build_pdf(cv_data, filepath):
    styles = getSampleStyleSheet()

    name_style = ParagraphStyle("CVName", parent=styles["Title"], fontSize=17, alignment=1, spaceAfter=2, leading=20)
    contact_style = ParagraphStyle("CVContact", parent=styles["Normal"], fontSize=9, alignment=1, spaceAfter=14, leading=11)
    heading_style = ParagraphStyle("CVHeading", parent=styles["Heading2"], fontSize=11, spaceBefore=10, spaceAfter=4, leading=13)
    entry_title_style = ParagraphStyle("CVEntryTitle", parent=styles["Normal"], fontSize=10.5, fontName="Helvetica-Bold", spaceAfter=1, leading=13)
    entry_dates_style = ParagraphStyle("CVEntryDates", parent=styles["Normal"], fontSize=10, fontName="Helvetica-Oblique", alignment=2, spaceAfter=3, leading=12)
    body_style = ParagraphStyle("CVBody", parent=styles["BodyText"], fontSize=10, leading=13, spaceAfter=3)
    bullet_style = ParagraphStyle("CVBullet", parent=body_style, spaceAfter=2)

    doc = SimpleDocTemplate(filepath, pagesize=A4, topMargin=1.8 * cm, bottomMargin=1.8 * cm)
    story = []

    # ── Header: name centered, bold, larger; contact line centered below ──
    story.append(Paragraph(cv_data.get("full_name", "CV").upper(), name_style))
    contact_bits = [b for b in [cv_data.get("email"), cv_data.get("phone"), cv_data.get("location")] if b]
    if contact_bits:
        story.append(Paragraph(" | ".join(contact_bits), contact_style))

    def add_entry(title_org, dates):
        story.append(Paragraph(title_org, entry_title_style))
        if dates:
            story.append(Paragraph(dates, entry_dates_style))

    if cv_data.get("summary"):
        story.append(Paragraph("Profile", heading_style))
        story.append(Paragraph(cv_data["summary"], body_style))

    if cv_data.get("key_projects"):
        story.append(Paragraph("Key Projects", heading_style))
        for proj in cv_data["key_projects"]:
            add_entry(f"{proj.get('title', '')} | {proj.get('organisation', '')}", proj.get("dates", ""))
            if proj.get("description"):
                story.append(Paragraph(proj["description"], body_style))
            for bullet in proj.get("bullets", []):
                story.append(Paragraph(f"• {bullet}", bullet_style))
            story.append(Spacer(1, 4))

    if cv_data.get("work_experience"):
        story.append(Paragraph("Work Experience", heading_style))
        for job in cv_data["work_experience"]:
            add_entry(f"{job.get('title', '')} | {job.get('organisation', '')}", job.get("dates", ""))
            for bullet in job.get("bullets", []):
                story.append(Paragraph(f"• {bullet}", bullet_style))
            story.append(Spacer(1, 4))

    if cv_data.get("education"):
        story.append(Paragraph("Education", heading_style))
        for edu in cv_data["education"]:
            add_entry(f"{edu.get('qualification', '')} | {edu.get('institution', '')}", edu.get("dates", ""))

    if cv_data.get("relevant_coursework"):
        story.append(Paragraph("Relevant Coursework", heading_style))
        for group in cv_data["relevant_coursework"]:
            story.append(Paragraph(f"<b>{group.get('category', '')}:</b> {' | '.join(group.get('items', []))}", bullet_style))

    if cv_data.get("certifications"):
        story.append(Paragraph("Certifications", heading_style))
        for cert in cv_data["certifications"]:
            add_entry(cert.get("title", ""), cert.get("dates", ""))

    if cv_data.get("technical_skills"):
        story.append(Paragraph("Technical Skills", heading_style))
        for group in cv_data["technical_skills"]:
            story.append(Paragraph(f"<b>{group.get('category', '')}:</b> {' | '.join(group.get('items', []))}", bullet_style))

    if cv_data.get("soft_skills"):
        story.append(Paragraph("Soft Skills", heading_style))
        story.append(Paragraph(" | ".join(cv_data["soft_skills"]), body_style))

    doc.build(story)


@login_required
@require_POST
def generate_cv(request):
    job_requirements = request.POST.get("job_requirements", "").strip()
    if not job_requirements:
        return JsonResponse({"error": "Please paste the job requirements first."}, status=400)

    # TEMPORARY placeholder profile — Step 10c below will connect this to your real Profile model
    profile_obj = getattr(request.user, "profile", None)
    if profile_obj is None:
        return JsonResponse({"error": "Please complete your profile before generating a CV."}, status=400)

    skill_names = [s.name for s in profile_obj.skills.all()]
    if profile_obj.custom_skills:
        # custom_skills is free text — split on commas, strip whitespace, drop empties
        extra_skills = [s.strip() for s in profile_obj.custom_skills.split(",") if s.strip()]
        skill_names += extra_skills

    profile = {
        "full_name": request.user.get_full_name() or request.user.username,
        "qualification": profile_obj.qualification,
        "institution": profile_obj.institution,
        "skills": skill_names,
        "location": profile_obj.location,
    }

    try:
        cv_data = _generate_cv_content(profile, job_requirements)
    except Exception as e:
        return JsonResponse({"error": f"Generation failed: {e}"}, status=500)

    file_id = uuid.uuid4().hex
    out_dir = os.path.join(settings.MEDIA_ROOT, "generated_cvs")
    os.makedirs(out_dir, exist_ok=True)
    docx_path = os.path.join(out_dir, f"{file_id}.docx")
    pdf_path = os.path.join(out_dir, f"{file_id}.pdf")

    _build_docx(cv_data, docx_path)
    _build_pdf(cv_data, pdf_path)

    return JsonResponse({
        "docx_url": f"{settings.MEDIA_URL}generated_cvs/{file_id}.docx",
        "pdf_url": f"{settings.MEDIA_URL}generated_cvs/{file_id}.pdf",
    })

def _build_cover_letter_prompt(cv_text, job_requirements):
    return f"""
You are an expert professional cover letter writing assistant.

Your task is to create a highly tailored cover letter using ONLY:
1. The candidate's CV text.
2. The job/opportunity requirements provided below.

The cover letter must feel like it was written specifically for this
candidate and this employer. It must NOT simply repeat the candidate's CV.

IMPORTANT RULES:
- Base every factual claim strictly on the CV and job information provided.
- NEVER invent employers, responsibilities, achievements, qualifications,
  skills, projects, statistics, company facts, or experiences.
- Do not assume information that is not explicitly provided.
- If the job information does not provide a company name, contact name,
  company-specific detail, or specific business problem, do not invent one.
- Use natural, confident, professional language.
- Avoid generic, robotic, overly enthusiastic, or AI-sounding language.
- Do not use phrases such as "I am writing to apply for..."
  or "I am excited to apply for this position..."
- Do not turn the cover letter into another CV.
- Do not simply list qualifications, skills, or work experience.
- Every paragraph should have a clear purpose.

COVER LETTER STRUCTURE:

1. OPENING / HOOK
Start with a strong hook that demonstrates why the candidate is relevant
to the opportunity.

The opening should lead with one of the following, where supported by the
CV:
- A relevant result or achievement.
- A meaningful experience.
- A relevant technical/business insight.
- A problem the candidate has experience solving.
- A strength that directly relates to what the employer needs.

Do NOT begin with:
"I am writing to apply for..."
"I would like to apply for..."
"I am excited to apply for..."

The opening should naturally establish the role/opportunity without making
the fact that they are applying the main point of the paragraph.

2. THE STORY BEHIND THE CV
Do not repeat the CV as a list.

Instead, explain the connection between the candidate's experiences and
the opportunity. Show how their education, projects, work experience,
internships, certifications, or other relevant experiences have prepared
them to contribute to this particular role.

Focus on the meaning and relevance of their experience rather than merely
stating that they have it.

3. EMPLOYER-FOCUSED VALUE
The cover letter must focus on what the candidate can contribute to the
employer.

Identify the employer's needs from the job requirements and connect them
directly to evidence from the candidate's CV.

Use the structure:

Employer need → Candidate evidence → Potential contribution

Do not focus heavily on statements such as:
"I want to grow my skills."
"This role will help me achieve my goals."
"I want to gain experience."

The employer should clearly understand what the candidate can bring to
the organisation.

4. WHY THIS COMPANY / OPPORTUNITY
Where the job information provides enough evidence, explain why this
specific company, organisation, role, or opportunity is relevant to the
candidate.

Do not use generic statements that could apply to any company.

If the job description contains something specific about:
- the company's work,
- its products or services,
- the role,
- its technology,
- its business challenges,
- its values,
- its industry,
- or the responsibilities,

connect that detail to something genuinely supported by the candidate's CV.

If no company-specific information is provided, do NOT invent company
details. Instead, focus on the specific requirements and responsibilities
of the opportunity.

5. PERSONALISATION
Personalise the letter using at least three relevant elements whenever
the provided information allows it:

A. The company/organisation name.
B. One specific detail about the company's work, role, responsibilities,
   requirements, or opportunity.
C. One direct connection between the candidate's actual experience and
   that specific employer need.

The personalisation must feel natural rather than forced.

6. UNIQUE VALUE
Identify what makes this candidate particularly relevant to the role based
on the evidence available in the CV.

This could be a combination of:
- technical skills,
- business knowledge,
- academic background,
- projects,
- problem-solving experience,
- communication skills,
- leadership,
- customer experience,
- certifications,
- or other demonstrated strengths.

Do not claim that the candidate is "unique" unless the evidence supports
the statement. Instead, demonstrate their value through the connection
between their experience and the employer's needs.

7. CLOSING
End confidently and professionally.

Do NOT use:
"I hope to hear from you soon."
"Thank you for considering my application and I hope to hear from you."

Instead, use a closing that communicates the candidate's interest in
contributing to the organisation and invites a conversation or next step.

For example, the closing should communicate the idea:
"I'd welcome the opportunity to discuss how my experience could contribute
to [Company]."

Do not copy this example word-for-word every time.

OUTPUT REQUIREMENTS:

Respond ONLY with valid JSON.
Do NOT include markdown fences.
Do NOT include commentary outside the JSON.
Return exactly this structure:

{{
  "recipient_line": " Dear Recruiter, or a named contact if explicitly provided",
  "opening": "A strong 1-2 sentence hook that leads with relevance, insight, achievement, experience, or value rather than the intention to apply.",
  "body_paragraphs": [
    "Paragraph explaining the story behind the candidate's CV and how their relevant experience connects to the opportunity.",
    "Paragraph connecting the employer's specific needs to evidence from the candidate's CV and explaining the value the candidate can contribute.",
    "Optional paragraph explaining why this specific company, organisation, role, or opportunity is relevant, using specific information from the job requirements."
  ],
  "closing": "A confident 1-2 sentence closing that reinforces the candidate's potential contribution and invites a conversation or next step.",
  "signature_name": "The candidate's full name exactly as found in the CV."
}}

ADDITIONAL QUALITY CHECK BEFORE RESPONDING:

Before generating the final JSON, silently check that:

1. The opening does NOT begin with "I am writing to apply".
2. The opening contains a genuine hook based on available evidence.
3. The letter does not simply repeat the CV.
4. The letter explains the story or connection behind the candidate's
   experience.
5. The employer's needs are prioritised over the candidate's personal goals.
6. The letter clearly explains what the candidate can contribute.
7. The company/opportunity is personalised where information is available.
8. At least three personalisation elements are used when the provided
   information makes this possible.
9. Every claim can be traced to the CV or job requirements.
10. No information has been invented.
11. The closing invites a conversation or next step rather than saying
    "I hope to hear from you soon."
12. The language sounds like a real professional candidate, not a generic
    AI-generated template.

Candidate's CV text:
\"\"\"{cv_text}\"\"\"

Job requirements pasted by the candidate:
\"\"\"{job_requirements}\"\"\"

"""

def _generate_cover_letter_content(cv_text, job_requirements):
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    response = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=_build_cover_letter_prompt(cv_text, job_requirements),
    )
    text = response.text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    return json.loads(text)

def _build_cover_letter_docx(letter_data, filepath):
    doc = Document()
    normal_style = doc.styles["Normal"]
    normal_style.font.name = "Calibri"
    normal_style.font.size = Pt(11)
    normal_style.paragraph_format.space_before = Pt(0)
    normal_style.paragraph_format.space_after = Pt(8)
    normal_style.paragraph_format.line_spacing = 1.15

    doc.add_paragraph(letter_data.get("recipient_line", "Hiring Manager"))
    doc.add_paragraph(letter_data.get("opening", ""))

    for para in letter_data.get("body_paragraphs", []):
        doc.add_paragraph(para)

    doc.add_paragraph(letter_data.get("closing", ""))
    doc.add_paragraph("")
    doc.add_paragraph("Sincerely,")
    doc.add_paragraph(letter_data.get("signature_name", ""))

    doc.save(filepath)


def _build_cover_letter_pdf(letter_data, filepath):
    styles = getSampleStyleSheet()
    body_style = ParagraphStyle("CLBody", parent=styles["Normal"], fontSize=11, leading=15, spaceAfter=10)

    doc = SimpleDocTemplate(filepath, pagesize=A4, topMargin=2.2 * cm, bottomMargin=2.2 * cm)
    story = []

    story.append(Paragraph(letter_data.get("recipient_line", "Hiring Manager"), body_style))
    story.append(Paragraph(letter_data.get("opening", ""), body_style))

    for para in letter_data.get("body_paragraphs", []):
        story.append(Paragraph(para, body_style))

    story.append(Paragraph(letter_data.get("closing", ""), body_style))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Sincerely,", body_style))
    story.append(Paragraph(letter_data.get("signature_name", ""), body_style))

    doc.build(story)

@login_required
@require_POST
def generate_cover_letter(request):
    job_requirements = request.POST.get("job_requirements", "").strip()
    cv_file = request.FILES.get("cv_file")

    if not job_requirements:
        return JsonResponse({"error": "Please paste the job requirements first."}, status=400)
    if not cv_file:
        return JsonResponse({"error": "Please upload your CV first."}, status=400)

    max_size = 5 * 1024 * 1024  # 5MB
    if cv_file.size > max_size:
        return JsonResponse({"error": "That file is too large. Please upload a file under 5MB."}, status=400)

    try:
        cv_text = _extract_text_from_cv(cv_file)
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)

    if not cv_text.strip():
        return JsonResponse({"error": "Couldn't read any text from that file. Please try a different file."}, status=400)

    try:
        letter_data = _generate_cover_letter_content(cv_text, job_requirements)
    except Exception as e:
        return JsonResponse({"error": f"Generation failed: {e}"}, status=500)

    file_id = uuid.uuid4().hex
    out_dir = os.path.join(settings.MEDIA_ROOT, "generated_cover_letters")
    os.makedirs(out_dir, exist_ok=True)
    docx_path = os.path.join(out_dir, f"{file_id}.docx")
    pdf_path = os.path.join(out_dir, f"{file_id}.pdf")

    _build_cover_letter_docx(letter_data, docx_path)
    _build_cover_letter_pdf(letter_data, pdf_path)

    return JsonResponse({
        "docx_url": f"{settings.MEDIA_URL}generated_cover_letters/{file_id}.docx",
        "pdf_url": f"{settings.MEDIA_URL}generated_cover_letters/{file_id}.pdf",
    })

# ============================================================
# HOW TO USE THIS FILE
# ------------------------------------------------------------
# 1. The two new view functions (cv_enhancer_view,
#    cover_letter_generator_view) are new — add them anywhere
#    in core/views.py (e.g. near resources_view).
#
# 2. generate_cv / generate_cover_letter, and the two prompt
#    builders _build_cv_prompt / _build_cover_letter_prompt,
#    REPLACE the existing versions of the same name in
#    core/views.py with the versions below — they now accept
#    pasted OR uploaded CV text, a job description, additional
#    notes, a tone (cover letter only), and an optional saved
#    opportunity id, while staying backward compatible with the
#    old `job_requirements` field name.
# ============================================================


# ------------------------------------------------------------
# NEW: dedicated tool pages
# ------------------------------------------------------------

@login_required
def cv_enhancer_view(request):
    profile = get_object_or_404(Profile, user=request.user)

    saved_opportunities = (
        SavedOpportunity.objects
        .filter(user=request.user)
        .select_related("opportunity")
        .order_by("-saved_at")
    )

    skill_names = [s.name for s in profile.skills.all()]
    if profile.custom_skills:
        skill_names += [s.strip() for s in profile.custom_skills.split(",") if s.strip()]

    context = {
        "profile": profile,
        "profile_name": request.user.get_full_name() or request.user.username,
        "skill_names": skill_names,
        "skills_count": len(skill_names),
        "saved_opportunities": saved_opportunities,
        "saved_count": saved_opportunities.count(),
        # NOTE: there's no model tracking generated-CV history yet.
        # Wire this up to a real count (e.g. a GeneratedCV model)
        # once that exists — hardcoded to 0 for now.
        "cv_count": 0,
    }
    return render(request, "core/cv_enhancer.html", context)


@login_required
def cover_letter_generator_view(request):
    profile = get_object_or_404(Profile, user=request.user)

    saved_opportunities = (
        SavedOpportunity.objects
        .filter(user=request.user)
        .select_related("opportunity")
        .order_by("-saved_at")
    )

    skill_names = [s.name for s in profile.skills.all()]
    if profile.custom_skills:
        skill_names += [s.strip() for s in profile.custom_skills.split(",") if s.strip()]

    context = {
        "profile": profile,
        "profile_name": request.user.get_full_name() or request.user.username,
        "skill_names": skill_names,
        "skills_count": len(skill_names),
        "saved_opportunities": saved_opportunities,
        "saved_count": saved_opportunities.count(),
        "cv_count": 0,
    }
    return render(request, "core/cover_letter_generator.html", context)


# ------------------------------------------------------------
# UPDATED: CV prompt builder
# ------------------------------------------------------------

def _build_cv_prompt(profile, job_description, cv_text="", additional_notes=""):
    cv_block = ""
    if cv_text:
        cv_block = f"""
The candidate has provided their existing CV text below. Use it as the
primary source of truth for their experience, projects, and education —
enhance and restructure it rather than starting from scratch:
\"\"\"{cv_text}\"\"\"
"""

    notes_block = ""
    if additional_notes:
        notes_block = f"""
The candidate specifically asked you to highlight or take into account
the following when writing the CV:
\"\"\"{additional_notes}\"\"\"
"""

    return f"""
You are an elite CV writer specialising in repositioning candidates with little or
unclear experience into stronger, employable professionals. Your objective is to
transform raw or average CV content into results driven, comercially valuable cv
aligned with SA hiring standards following these strict rules, the summary must be objective
(no descriptive words like 'motivated', 'hardworking', etc) every bullet point must show atleast
one of the following: Outcomes, Deliverable, responsibility, measurable impact where possible.
Remove all fluff and generic statements. Prioritise clarity, structure, and value over length (1 page cv).
Write like a candidate has already operated in a professional environment. Using the candidate's profile below and the
job requirements provided, write a tailored CV. Respond ONLY with valid JSON,
no markdown fences, no commentary, matching exactly this shape:

{{
  "full_name": "string",
  "summary": "3-4 sentence professional summary tailored to the role",
  "key_projects": [
    {{
      "title": "string",
      "organisation": "string",
      "dates": "string",
      "description": "one sentence describing the project",
      "bullets": ["achievement 1", "achievement 2"]
    }}
  ],
  "work_experience": [
    {{
      "title": "string",
      "organisation": "string",
      "dates": "string",
      "bullets": ["achievement 1", "achievement 2"]
    }}
  ],
  "education": [
    {{"qualification": "string", "institution": "string", "dates": "string"}}
  ],
  "relevant_coursework": [
    {{"category": "string", "items": ["item1", "item2"]}}
  ],
  "certifications": [
    {{"title": "string", "dates": "string"}}
  ],
  "technical_skills": [
    {{"category": "string", "items": ["item1", "item2"]}}
  ],
  "soft_skills": ["skill1", "skill2"]
}}

Only include keys that have real content — omit "key_projects" entirely if the
candidate has no relevant projects, rather than inventing filler entries.

Candidate profile:
Name: {profile.get('full_name', '')}
Qualification: {profile.get('qualification', '')}
Institution: {profile.get('institution', '')}
Skills: {', '.join(profile.get('skills', []))}
Location: {profile.get('location', '')}
{cv_block}{notes_block}
Job requirements pasted by the candidate:
\"\"\"{job_description}\"\"\"
"""


def _generate_cv_content(profile, job_description, cv_text="", additional_notes=""):
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    response = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=_build_cv_prompt(profile, job_description, cv_text, additional_notes),
    )
    text = response.text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    return json.loads(text)


@login_required
@require_POST
def generate_cv(request):
    # Accept either the new `job_description` field or the old
    # `job_requirements` name for backward compatibility.
    job_description = (
        request.POST.get("job_description", "").strip()
        or request.POST.get("job_requirements", "").strip()
    )

    opportunity_id = request.POST.get("opportunity_id", "").strip()
    if not job_description and opportunity_id:
        opportunity = Opportunity.objects.filter(id=opportunity_id).first()
        if opportunity and opportunity.description:
            job_description = opportunity.description

    if not job_description:
        return JsonResponse({"error": "Please paste the job requirements first, or select a saved opportunity."}, status=400)

    profile_obj = getattr(request.user, "profile", None)
    if profile_obj is None:
        return JsonResponse({"error": "Please complete your profile before generating a CV."}, status=400)

    # Pasted CV text takes priority; otherwise fall back to an uploaded file.
    cv_text = request.POST.get("cv_text", "").strip()
    cv_file = request.FILES.get("cv_file")
    if not cv_text and cv_file:
        max_size = 5 * 1024 * 1024  # 5MB
        if cv_file.size > max_size:
            return JsonResponse({"error": "That file is too large. Please upload a file under 5MB."}, status=400)
        try:
            cv_text = _extract_text_from_cv(cv_file)
        except ValueError as e:
            return JsonResponse({"error": str(e)}, status=400)

    additional_notes = request.POST.get("additional_notes", "").strip()

    skill_names = [s.name for s in profile_obj.skills.all()]
    if profile_obj.custom_skills:
        extra_skills = [s.strip() for s in profile_obj.custom_skills.split(",") if s.strip()]
        skill_names += extra_skills

    profile = {
        "full_name": request.user.get_full_name() or request.user.username,
        "qualification": profile_obj.qualification,
        "institution": profile_obj.institution,
        "skills": skill_names,
        "location": profile_obj.location,
    }

    try:
        cv_data = _generate_cv_content(profile, job_description, cv_text, additional_notes)
    except Exception as e:
        return JsonResponse({"error": f"Generation failed: {e}"}, status=500)

    file_id = uuid.uuid4().hex
    out_dir = os.path.join(settings.MEDIA_ROOT, "generated_cvs")
    os.makedirs(out_dir, exist_ok=True)
    docx_path = os.path.join(out_dir, f"{file_id}.docx")
    pdf_path = os.path.join(out_dir, f"{file_id}.pdf")

    _build_docx(cv_data, docx_path)
    _build_pdf(cv_data, pdf_path)

    return JsonResponse({
        "docx_url": f"{settings.MEDIA_URL}generated_cvs/{file_id}.docx",
        "pdf_url": f"{settings.MEDIA_URL}generated_cvs/{file_id}.pdf",
    })


# ------------------------------------------------------------
# UPDATED: Cover letter prompt builder
# ------------------------------------------------------------

TONE_INSTRUCTIONS = {
    "professional": "Keep the tone polished, measured, and businesslike.",
    "enthusiastic": "Let genuine enthusiasm for the role and organisation come through, without sounding over-the-top or generic.",
    "confident": "Write with a confident, assured tone that leads with the candidate's strengths.",
    "humble": "Keep the tone modest and grounded, letting the evidence speak for itself rather than overselling.",
    "creative": "Allow a little more personality and creative phrasing, while staying professional and credible.",
    "direct": "Keep sentences short and to the point — no filler, no throat-clearing.",
}


def _build_cover_letter_prompt(cv_text, job_description, additional_notes="", tone="professional"):
    tone_instruction = TONE_INSTRUCTIONS.get(tone, TONE_INSTRUCTIONS["professional"])

    notes_block = ""
    if additional_notes:
        notes_block = f"""
The candidate specifically asked you to highlight or take into account
the following:
\"\"\"{additional_notes}\"\"\"
"""

    return f"""
You are an expert professional cover letter writing assistant.

Your task is to create a highly tailored cover letter using ONLY:
1. The candidate's CV text.
2. The job/opportunity requirements provided below.

TONE: {tone_instruction}

The cover letter must feel like it was written specifically for this
candidate and this employer. It must NOT simply repeat the candidate's CV.

IMPORTANT RULES:
- Base every factual claim strictly on the CV and job information provided.
- NEVER invent employers, responsibilities, achievements, qualifications,
  skills, projects, statistics, company facts, or experiences.
- Do not assume information that is not explicitly provided.
- If the job information does not provide a company name, contact name,
  company-specific detail, or specific business problem, do not invent one.
- Use natural, confident, professional language.
- Avoid generic, robotic, overly enthusiastic, or AI-sounding language.
- Do not use phrases such as "I am writing to apply for..."
  or "I am excited to apply for this position..."
- Do not turn the cover letter into another CV.
- Do not simply list qualifications, skills, or work experience.
- Every paragraph should have a clear purpose.

COVER LETTER STRUCTURE:

1. OPENING / HOOK
Start with a strong hook that demonstrates why the candidate is relevant
to the opportunity.

The opening should lead with one of the following, where supported by the
CV:
- A relevant result or achievement.
- A meaningful experience.
- A relevant technical/business insight.
- A problem the candidate has experience solving.
- A strength that directly relates to what the employer needs.

Do NOT begin with:
"I am writing to apply for..."
"I would like to apply for..."
"I am excited to apply for..."

The opening should naturally establish the role/opportunity without making
the fact that they are applying the main point of the paragraph.

2. THE STORY BEHIND THE CV
Do not repeat the CV as a list.

Instead, explain the connection between the candidate's experiences and
the opportunity. Show how their education, projects, work experience,
internships, certifications, or other relevant experiences have prepared
them to contribute to this particular role.

Focus on the meaning and relevance of their experience rather than merely
stating that they have it.

3. EMPLOYER-FOCUSED VALUE
The cover letter must focus on what the candidate can contribute to the
employer.

Identify the employer's needs from the job requirements and connect them
directly to evidence from the candidate's CV.

Use the structure:

Employer need → Candidate evidence → Potential contribution

Do not focus heavily on statements such as:
"I want to grow my skills."
"This role will help me achieve my goals."
"I want to gain experience."

The employer should clearly understand what the candidate can bring to
the organisation.

4. WHY THIS COMPANY / OPPORTUNITY
Where the job information provides enough evidence, explain why this
specific company, organisation, role, or opportunity is relevant to the
candidate.

Do not use generic statements that could apply to any company.

If the job description contains something specific about:
- the company's work,
- its products or services,
- the role,
- its technology,
- its business challenges,
- its values,
- its industry,
- or the responsibilities,

connect that detail to something genuinely supported by the candidate's CV.

If no company-specific information is provided, do NOT invent company
details. Instead, focus on the specific requirements and responsibilities
of the opportunity.

5. PERSONALISATION
Personalise the letter using at least three relevant elements whenever
the provided information allows it:

A. The company/organisation name.
B. One specific detail about the company's work, role, responsibilities,
   requirements, or opportunity.
C. One direct connection between the candidate's actual experience and
   that specific employer need.

The personalisation must feel natural rather than forced.

6. UNIQUE VALUE
Identify what makes this candidate particularly relevant to the role based
on the evidence available in the CV.

This could be a combination of:
- technical skills,
- business knowledge,
- academic background,
- projects,
- problem-solving experience,
- communication skills,
- leadership,
- customer experience,
- certifications,
- or other demonstrated strengths.

Do not claim that the candidate is "unique" unless the evidence supports
the statement. Instead, demonstrate their value through the connection
between their experience and the employer's needs.

7. CLOSING
End confidently and professionally.

Do NOT use:
"I hope to hear from you soon."
"Thank you for considering my application and I hope to hear from you."

Instead, use a closing that communicates the candidate's interest in
contributing to the organisation and invites a conversation or next step.

For example, the closing should communicate the idea:
"I'd welcome the opportunity to discuss how my experience could contribute
to [Company]."

Do not copy this example word-for-word every time.

OUTPUT REQUIREMENTS:

Respond ONLY with valid JSON.
Do NOT include markdown fences.
Do NOT include commentary outside the JSON.
Return exactly this structure:

{{
  "recipient_line": " Dear Recruiter Team",
  "opening": "A strong 1-2 sentence hook that leads with relevance, insight, achievement, experience, or value rather than the intention to apply.",
  "body_paragraphs": [
    "Paragraph explaining the story behind the candidate's CV and how their relevant experience connects to the opportunity.",
    "Paragraph connecting the employer's specific needs to evidence from the candidate's CV and explaining the value the candidate can contribute.",
    "Optional paragraph explaining why this specific company, organisation, role, or opportunity is relevant, using specific information from the job requirements."
  ],
  "closing": "A confident 1-2 sentence closing that reinforces the candidate's potential contribution and invites a conversation or next step.",
  "signature_name": "The candidate's full name exactly as found in the CV."
}}

ADDITIONAL QUALITY CHECK BEFORE RESPONDING:

Before generating the final JSON, silently check that:

1. The opening does NOT begin with "I am writing to apply".
2. The opening contains a genuine hook based on available evidence.
3. The letter does not simply repeat the CV.
4. The letter explains the story or connection behind the candidate's
   experience.
5. The employer's needs are prioritised over the candidate's personal goals.
6. The letter clearly explains what the candidate can contribute.
7. The company/opportunity is personalised where information is available.
8. At least three personalisation elements are used when the provided
   information makes this possible.
9. Every claim can be traced to the CV or job requirements.
10. No information has been invented.
11. The closing invites a conversation or next step rather than saying
    "I hope to hear from you soon."
12. The language sounds like a real professional candidate, not a generic
    AI-generated template.

Candidate's CV text:
\"\"\"{cv_text}\"\"\"
{notes_block}
Job requirements pasted by the candidate:
\"\"\"{job_description}\"\"\"

"""


def _generate_cover_letter_content(cv_text, job_description, additional_notes="", tone="professional"):
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    response = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=_build_cover_letter_prompt(cv_text, job_description, additional_notes, tone),
    )
    text = response.text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    return json.loads(text)


@login_required
@require_POST
def generate_cover_letter(request):
    job_description = (
        request.POST.get("job_description", "").strip()
        or request.POST.get("job_requirements", "").strip()
    )

    opportunity_id = request.POST.get("opportunity_id", "").strip()
    if not job_description and opportunity_id:
        opportunity = Opportunity.objects.filter(id=opportunity_id).first()
        if opportunity and opportunity.description:
            job_description = opportunity.description

    if not job_description:
        return JsonResponse({"error": "Please paste the job requirements first, or select a saved opportunity."}, status=400)

    additional_notes = request.POST.get("additional_notes", "").strip()
    tone = request.POST.get("tone", "professional").strip() or "professional"

    # Pasted CV text takes priority; otherwise fall back to an uploaded file;
    # otherwise fall back to the candidate's saved profile.
    cv_text = request.POST.get("cv_text", "").strip()
    cv_file = request.FILES.get("cv_file")

    if not cv_text and cv_file:
        max_size = 5 * 1024 * 1024  # 5MB
        if cv_file.size > max_size:
            return JsonResponse({"error": "That file is too large. Please upload a file under 5MB."}, status=400)
        try:
            cv_text = _extract_text_from_cv(cv_file)
        except ValueError as e:
            return JsonResponse({"error": str(e)}, status=400)

    if not cv_text:
        profile_obj = getattr(request.user, "profile", None)
        if profile_obj is None:
            return JsonResponse({"error": "Please paste your CV, upload a file, or complete your profile first."}, status=400)
        skill_names = [s.name for s in profile_obj.skills.all()]
        if profile_obj.custom_skills:
            skill_names += [s.strip() for s in profile_obj.custom_skills.split(",") if s.strip()]
        cv_text = (
            f"Name: {request.user.get_full_name() or request.user.username}\n"
            f"Qualification: {profile_obj.qualification}\n"
            f"Institution: {profile_obj.institution}\n"
            f"Location: {profile_obj.location}\n"
            f"Skills: {', '.join(skill_names)}"
        )

    if not cv_text.strip():
        return JsonResponse({"error": "Couldn't read any CV content. Please try a different file or paste your CV text."}, status=400)

    try:
        letter_data = _generate_cover_letter_content(cv_text, job_description, additional_notes, tone)
    except Exception as e:
        return JsonResponse({"error": f"Generation failed: {e}"}, status=500)

    file_id = uuid.uuid4().hex
    out_dir = os.path.join(settings.MEDIA_ROOT, "generated_cover_letters")
    os.makedirs(out_dir, exist_ok=True)
    docx_path = os.path.join(out_dir, f"{file_id}.docx")
    pdf_path = os.path.join(out_dir, f"{file_id}.pdf")

    _build_cover_letter_docx(letter_data, docx_path)
    _build_cover_letter_pdf(letter_data, pdf_path)

    return JsonResponse({
        "docx_url": f"{settings.MEDIA_URL}generated_cover_letters/{file_id}.docx",
        "pdf_url": f"{settings.MEDIA_URL}generated_cover_letters/{file_id}.pdf",
    })