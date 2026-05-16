import json
import base64
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.conf import settings
from .models import Interview, InterviewQuestion

# Try to import Gemini
try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = bool(settings.GEMINI_API_KEY)
    if GEMINI_AVAILABLE:
        genai.configure(api_key=settings.GEMINI_API_KEY)
except ImportError:
    GEMINI_AVAILABLE = False

MAX_SAVED_INTERVIEWS = 5

SA_OCCUPATIONS = [
    "Software Engineer", "Data Scientist", "Accountant", "Financial Analyst",
    "Marketing Manager", "Human Resources Manager", "Project Manager", 
    "Civil Engineer", "Mechanical Engineer", "Electrical Engineer", 
    "Nurse", "Doctor", "Teacher", "Lawyer", "Architect", "Business Analyst",
    "Product Manager", "UX Designer", "Graphic Designer", "Sales Representative",
]

# Fallback questions
FALLBACK_QUESTIONS = {
    'entry': [
        "Tell me about yourself and why you're interested in this position.",
        "What do you know about our company and why do you want to work here?",
        "Describe a time when you had to learn something new quickly.",
        "How do you handle constructive criticism?",
        "Where do you see yourself in 5 years?",
    ],
    'mid': [
        "Describe a challenging project you led and the outcome.",
        "How do you prioritize tasks when managing multiple deadlines?",
        "Tell me about a time you had to convince a team member.",
        "How do you stay current with industry trends?",
        "Describe a difficult problem you solved.",
    ],
    'senior': [
        "Describe your leadership philosophy and how you've applied it.",
        "Tell me about a time you managed a difficult team situation.",
        "How do you align team goals with organisational strategy?",
        "Describe a major change you implemented.",
        "How do you develop and mentor junior team members?",
    ],
    'executive': [
        "What is your vision for this role and how would you create value?",
        "Describe a difficult strategic decision you made.",
        "How do you build relationships with key stakeholders?",
        "Tell me about a time you turned around an underperforming team.",
        "What is your approach to risk management?",
    ],
}

QUESTION_TYPES = ['behavioural', 'technical', 'situational', 'competency']


def generate_interview_questions(occupation, experience_level, question_count, cv_text=None):
    """Generate interview questions (with fallback)"""
    if GEMINI_AVAILABLE:
        try:
            # Try Gemini first
            prompt = f"""Generate {question_count} interview questions for a {experience_level}-level {occupation} position.
Return JSON array with "questionText" and "questionType" (behavioural/technical/situational/competency)."""
            model = genai.GenerativeModel('gemini-1.5-flash')
            response = model.generate_content(prompt)
            # Parse response...
            pass
        except:
            pass
    
    # Use fallback questions
    fallback_list = FALLBACK_QUESTIONS.get(experience_level, FALLBACK_QUESTIONS['mid'])
    questions = []
    for i in range(min(question_count, len(fallback_list))):
        questions.append({
            'questionText': fallback_list[i],
            'questionType': QUESTION_TYPES[i % len(QUESTION_TYPES)]
        })
    while len(questions) < question_count:
        idx = len(questions) % len(fallback_list)
        questions.append({
            'questionText': f"Tell me about your experience with {['leadership', 'problem-solving', 'teamwork', 'initiative', 'communication'][idx]}.",
            'questionType': QUESTION_TYPES[idx % len(QUESTION_TYPES)]
        })
    return questions[:question_count]


def generate_interview_feedback(occupation, experience_level, questions_data, body_scores):
    """Generate feedback (with fallback)"""
    # Simple fallback feedback
    avg_body = (body_scores['eye_contact'] + body_scores['posture'] + body_scores['gesture']) / 3
    overall_score = int(avg_body * 0.3 + 70)
    
    return {
        'strengths': [
            "Good foundational knowledge of the role requirements",
            "Showed willingness to learn and develop",
            "Provided relevant examples from past experience",
        ],
        'improvements': [
            "Use the STAR method to structure answers",
            "Maintain more consistent eye contact",
            "Provide more specific metrics in examples",
        ],
        'feedbackSummary': f"Overall, you demonstrated solid potential for the {occupation} role. Your answers showed relevant knowledge but could benefit from more structured responses.",
        'communicationScore': 75,
        'confidenceScore': 70,
        'structureScore': 65,
        'relevanceScore': 80,
        'questionFeedback': [
            {'feedback': 'Good answer, add more detail.', 'strengths': ['Relevant'], 'improvements': ['Add metrics']}
            for _ in questions_data
        ]
    }


# ============================================================
# PAGE VIEWS
# ============================================================

@login_required
def interview_home(request):
    count = Interview.objects.filter(user=request.user).count()
    at_limit = count >= MAX_SAVED_INTERVIEWS
    return render(request, 'interview/home.html', {
        'count': count,
        'at_limit': at_limit,
        'max_interviews': MAX_SAVED_INTERVIEWS,
    })


@login_required
def interview_setup(request):
    count = Interview.objects.filter(user=request.user).count()
    if count >= MAX_SAVED_INTERVIEWS:
        return redirect('interview_saved')
    return render(request, 'interview/setup.html', {
        'occupations': SA_OCCUPATIONS,
        'count': count,
        'max_interviews': MAX_SAVED_INTERVIEWS,
    })


@login_required
def interview_live(request, interview_id):
    interview = get_object_or_404(Interview, id=interview_id, user=request.user)
    questions = interview.questions.all()
    return render(request, 'interview/live.html', {
        'interview': interview,
        'questions': list(questions.values('id', 'question_index', 'question_text', 'question_type')),
        'questions_json': json.dumps(list(questions.values('id', 'question_index', 'question_text', 'question_type'))),
    })


@login_required
def interview_feedback(request, interview_id):
    interview = get_object_or_404(Interview, id=interview_id, user=request.user)
    questions = interview.questions.all()
    return render(request, 'interview/feedback.html', {
        'interview': interview,
        'questions': questions,
    })


@login_required
def interview_saved(request):
    interviews = Interview.objects.filter(user=request.user)
    count = interviews.count()
    at_limit = count >= MAX_SAVED_INTERVIEWS
    return render(request, 'interview/saved.html', {
        'interviews': interviews,
        'count': count,
        'at_limit': at_limit,
        'max_interviews': MAX_SAVED_INTERVIEWS,
    })


# ============================================================
# API ENDPOINTS
# ============================================================

@login_required
@require_http_methods(["POST"])
def api_create_interview(request):
    try:
        data = json.loads(request.body)
        occupation = data.get('occupation', '').strip()
        experience_level = data.get('experienceLevel', 'mid')
        question_count = int(data.get('questionCount', 5))
        cv_text = data.get('cvText', '')
        popia_consent = data.get('popiaConsent', False)

        if not occupation:
            return JsonResponse({'error': 'Occupation is required'}, status=400)
        if not popia_consent:
            return JsonResponse({'error': 'POPIA consent is required'}, status=400)

        interview = Interview.objects.create(
            user=request.user,
            occupation=occupation,
            experience_level=experience_level,
            question_count=question_count,
            cv_text=cv_text or None,
            popia_consent=True,
            status='setup',
        )
        return JsonResponse({'id': interview.id})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
def api_generate_questions(request, interview_id):
    try:
        interview = get_object_or_404(Interview, id=interview_id, user=request.user)
        questions = generate_interview_questions(
            interview.occupation, interview.experience_level, 
            interview.question_count, interview.cv_text
        )

        for i, q in enumerate(questions):
            InterviewQuestion.objects.create(
                interview=interview,
                question_index=i,
                question_text=q['questionText'],
                question_type=q.get('questionType', 'behavioural'),
            )

        interview.status = 'active'
        interview.save()
        return JsonResponse({'success': True, 'questionCount': len(questions)})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
def api_submit_answer(request, question_id):
    try:
        question = get_object_or_404(InterviewQuestion, id=question_id, interview__user=request.user)
        data = json.loads(request.body)

        eye = float(data.get('eyeContactScore', 50))
        posture = float(data.get('postureScore', 50))
        gesture = float(data.get('gestureScore', 50))
        presence = (eye * 0.4) + (posture * 0.35) + (gesture * 0.25)

        question.answer_text = data.get('answerText', '')
        question.eye_contact_score = eye
        question.posture_score = posture
        question.gesture_score = gesture
        question.presence_score = presence
        question.save()

        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
def api_upload_recording(request, interview_id):
    try:
        data = json.loads(request.body)
        question_id = data.get('questionId')
        base64_data = data.get('base64Data', '')
        mime_type = data.get('mimeType', 'audio/webm')

        ext = 'webm' if 'webm' in mime_type else 'mp4'
        file_data = base64.b64decode(base64_data)
        filename = f"audio_{interview_id}_{question_id}.{ext}"
        file_content = ContentFile(file_data, name=filename)

        if question_id:
            question = get_object_or_404(InterviewQuestion, id=question_id)
            question.audio_file.save(filename, file_content, save=True)

        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_http_methods(["POST"])
def api_complete_interview(request, interview_id):
    try:
        interview = get_object_or_404(Interview, id=interview_id, user=request.user)
        data = json.loads(request.body)

        eye = float(data.get('eyeContactScore', 50))
        posture = float(data.get('postureScore', 50))
        gesture = float(data.get('gestureScore', 50))
        overall_presence = (eye * 0.4) + (posture * 0.35) + (gesture * 0.25)

        questions = interview.questions.all()
        questions_data = [{'question_text': q.question_text, 'answer_text': q.answer_text} for q in questions]

        feedback = generate_interview_feedback(
            interview.occupation, interview.experience_level, questions_data,
            {'eye_contact': eye, 'posture': posture, 'gesture': gesture}
        )

        for i, q in enumerate(questions):
            qf = feedback.get('questionFeedback', [])[i] if i < len(feedback.get('questionFeedback', [])) else {}
            q.answer_feedback = qf.get('feedback', '')
            q.answer_strengths = qf.get('strengths', [])
            q.answer_improvements = qf.get('improvements', [])
            q.save()

        interview.status = 'completed'
        interview.overall_presence_score = overall_presence
        interview.eye_contact_score = eye
        interview.posture_score = posture
        interview.gesture_score = gesture
        interview.communication_score = feedback.get('communicationScore', 0)
        interview.confidence_score = feedback.get('confidenceScore', 0)
        interview.structure_score = feedback.get('structureScore', 0)
        interview.relevance_score = feedback.get('relevanceScore', 0)
        interview.strengths = feedback.get('strengths', [])
        interview.improvements = feedback.get('improvements', [])
        interview.feedback_summary = feedback.get('feedbackSummary', '')
        interview.save()

        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
@require_http_methods(["DELETE"])
def api_delete_interview(request, interview_id):
    try:
        interview = get_object_or_404(Interview, id=interview_id, user=request.user)
        interview.delete()
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def api_get_interview(request, interview_id):
    try:
        interview = get_object_or_404(Interview, id=interview_id, user=request.user)
        questions = list(interview.questions.values())
        return JsonResponse({'interview': {'id': interview.id, 'status': interview.status}, 'questions': questions})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@login_required
def api_occupations(request):
    return JsonResponse({'occupations': SA_OCCUPATIONS})

