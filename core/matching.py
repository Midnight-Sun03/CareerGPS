from .models import Opportunity, CVtip, Skill

def calculate_match_score(profile, opportunity):
    """
    Calculates a match score between a user profile 
    and an opportunity out of 100.
    """
    score = 0
    matched_reasons = []


    if profile.location and opportunity.location:
          opp_location = opportunity.location.lower()
    
          if opp_location == 'remote':
              score += 25
              matched_reasons.append("available remotely")
        
          elif opp_location == 'nationwide':
              score += 25
              matched_reasons.append("open to applicants nationwide")
        
          elif profile.location.lower() in opp_location or \
             opp_location in profile.location.lower():
             score += 25
             matched_reasons.append( f"based in {opportunity.location}, matching your location")


    if profile.qualification and opportunity.required_qualification:
        profile_qualification = profile.qualification.lower()
        required_qualification = opportunity.required_qualification.lower()
        
        degree_words = profile_qualification.split()
        for word in degree_words:
            if len(word) > 3 and word in required_qualification:
                score += 25
                matched_reasons.append(
                    f"aligns with your {profile.qualification}"
                )
                break

    # --- SKILLS MATCH (25 points) ---
    if profile.skills.exists() and opportunity.required_skills.exists():
        profile_skill_ids = set(
            profile.skills.values_list('id', flat=True)
        )
        opportunity_skill_ids = set(
            opportunity.required_skills.values_list('id', flat=True)
        )
        
        
        matched_skill_ids = profile_skill_ids & opportunity_skill_ids
        
        if matched_skill_ids:
            # Score proportionally - more overlap = more points
            overlap_ratio = len(matched_skill_ids) / len(opportunity_skill_ids)
            skill_score = round(overlap_ratio * 25)
            score += skill_score
            
            
            from .models import Skill
            matched_skill_names = Skill.objects.filter(
                id__in=matched_skill_ids
            ).values_list('name', flat=True)
            
            matched_reasons.append(
                f"matches your skills in {', '.join(matched_skill_names)}"
            )

    # --- INTERESTS MATCH (15 points) ---
    if profile.interests.exists():
        profile_interest_names = set(
            profile.interests.values_list('name', flat=True)
        )
        opportunity_title_and_sector = (
            f"{opportunity.title} {opportunity.sector}"
        ).lower()
        
        for interest in profile_interest_names:
            if interest.lower() in opportunity_title_and_sector:
                score += 15
                matched_reasons.append(
                    f"aligns with your interest in {interest}"
                )
                break

    # --- CUSTOM SKILLS MATCH (10 points) ---
    if profile.custom_skills:
        custom_skills = [
            s.strip().lower() 
            for s in profile.custom_skills.split(',')
        ]
        opportunity_text = (
            f"{opportunity.title} {opportunity.description} "
            f"{opportunity.required_qualification}"
        ).lower()
        
        for skill in custom_skills:
            if skill and skill in opportunity_text:
                score += 10
                matched_reasons.append(
                    f"your experience in {skill} is relevant"
                )
                break

    return score, matched_reasons


def get_match_strength(score):
    """
    Converts a numeric score into a 
    human readable match strength label.
    """
    if score >= 80:
        return 'Strong Match'
    elif score >= 60:
        return 'Good Match'
    elif score >= 40:
        return 'Partial Match'
    else:
        return None


def build_why_text(matched_reasons, match_strength):
    """
    Builds the 'Why this matches you' 
    sentence from matched reasons.
    """
    if not matched_reasons:
        return ""
    
    if match_strength == 'Strong Match':
        prefix = "Strong match – "
    elif match_strength == 'Good Match':
        prefix = "Good match – "
    else:
        prefix = "Partial match – "
    
    if len(matched_reasons) == 1:
        reasons_text = matched_reasons[0]
    elif len(matched_reasons) == 2:
        reasons_text = f"{matched_reasons[0]} and {matched_reasons[1]}"
    else:
        reasons_text = (
            ", ".join(matched_reasons[:-1]) + 
            f", and {matched_reasons[-1]}"
        )
    
    return f"{prefix}This opportunity {reasons_text}."


def get_matched_opportunities(profile):
    """
    Main function. Takes a user profile,
    scores all active opportunities,
    and returns a ranked list with
    match data attached.
    """
    # Get all active opportunities
    opportunities = Opportunity.objects.filter(is_active=True)
    
    results = []
    
    for opportunity in opportunities:
        # Calculate score and reasons
        score, matched_reasons = calculate_match_score(
            profile, opportunity
        )
        
        # Get match strength label
        match_strength = get_match_strength(score)
        
        # Skip if below threshold
        if match_strength is None:
            continue
        
        # Build why text
        why_text = build_why_text(matched_reasons, match_strength)
        
        # Get relevant CV tips for this opportunity
        cv_tips = CVtip.objects.filter(
            opportunity_type=opportunity.opportunity_type,
            is_active=True
        ).filter(
            related_skills__in=profile.skills.all()
        ).distinct()[:3]
        
        # If no skill-specific tips, get general ones
        if not cv_tips:
            cv_tips = CVtip.objects.filter(
                opportunity_type=opportunity.opportunity_type,
                is_active=True
            )[:3]
        
        results.append({
            'opportunity': opportunity,
            'score': score,
            'match_strength': match_strength,
            'why_text': why_text,
            'cv_tips': cv_tips,
        })
    
    # Sort by score highest first
    results.sort(key=lambda x: x['score'], reverse=True)
    
    return results

def calculate_completion(profile):
    try:
        has_skills = profile.skills.exists()
    except Exception:
        has_skills = False
    
    try:
        has_interests = profile.interests.exists()
    except Exception:
        has_interests = False

    fields = [
        bool(profile.qualification),
        bool(profile.institution),
        bool(profile.location),
        has_skills,
        has_interests,
    ]
    filled = sum(1 for f in fields if f)
    return int((filled / len(fields)) * 100)

def get_progress_items(profile):
    return [
        {
            "label": "Qualification",
            "complete": bool(profile.qualification),
        },
        {
            "label": "Institution",
            "complete": bool(profile.institution),
        },
        {
            "label": "Location",
            "complete": bool(profile.location),
        },
        {
            "label": "Skills",
            "complete": profile.skills.exists(),
        },
        {
            "label": "Interests",
            "complete": profile.interests.exists(),
        },
    ]
