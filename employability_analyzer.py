from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
import plotly.graph_objects as go
import plotly.utils
import json
import pandas as pd
import numpy as np
import requests
from employability_db import init_db, save_assessment_result, get_user_assessments, get_assessment_stats

# Create blueprint
employability_bp = Blueprint('employability', __name__)

# Ollama configuration
OLLAMA_CONFIG = {
    "model": "mistral",  # Using mistral model for better technical content generation
    "temperature": 0.7,  # Controls randomness (0.0 to 1.0)
    "top_p": 0.9,       # Controls diversity of responses
    "max_tokens": 2000  # Maximum length of generated response
}

# Initialize database
init_db()

# Database functions
def get_db():
    conn = sqlite3.connect('employability.db')
    conn.row_factory = sqlite3.Row
    return conn

def generate_questions_with_ollama(category, num_questions=5):
    """Generate questions for a category using Ollama."""
    prompt = f"""You are an expert technical interviewer. Generate {num_questions} multiple-choice questions about {category} for a technical skills assessment.
    Each question should test practical knowledge and real-world scenarios.
    Format the response as a JSON array with the following structure:
    [
        {{
            "question": "Question text",
            "options": ["Option 1", "Option 2", "Option 3", "Option 4"],
            "correct": 0  // Index of correct answer (0-3)
        }}
    ]
    Make the questions challenging but fair, and ensure they test practical knowledge.
    Category description: {CATEGORIES[category]}
    
    Important guidelines:
    1. Questions should be specific and technical
    2. Options should be clear and distinct
    3. Include at least one option that tests common misconceptions
    4. Focus on practical scenarios rather than theoretical concepts
    5. Ensure the correct answer is unambiguous
    """
    
    try:
        response = requests.post('http://localhost:11434/api/generate',
                               json={
                                   "model": OLLAMA_CONFIG["model"],
                                   "prompt": prompt,
                                   "stream": False,
                                   "options": {
                                       "temperature": OLLAMA_CONFIG["temperature"],
                                       "top_p": OLLAMA_CONFIG["top_p"],
                                       "max_tokens": OLLAMA_CONFIG["max_tokens"]
                                   }
                               })
        
        if response.status_code == 200:
            # Extract JSON from the response
            response_text = response.json()['response']
            # Find JSON array in the response
            start_idx = response_text.find('[')
            end_idx = response_text.rfind(']') + 1
            if start_idx != -1 and end_idx != -1:
                json_str = response_text[start_idx:end_idx]
                questions = json.loads(json_str)
                return questions
    except Exception as e:
        print(f"Error generating questions for {category}: {str(e)}")
    
    # Fallback to default questions if Ollama fails
    return get_default_questions(category)

# Define categories and their descriptions
CATEGORIES = {
    'Web Development': 'Questions about frontend and backend web technologies, frameworks, and best practices',
    'Database': 'Questions about database design, SQL, NoSQL, and data management',
    'System Design': 'Questions about system architecture, scalability, and design patterns',
    'Security': 'Questions about cybersecurity, authentication, and data protection',
    'DevOps': 'Questions about deployment, CI/CD, and infrastructure management',
    'Communication': 'Questions about technical communication, documentation, and team collaboration',
    'Leadership': 'Questions about technical leadership, project management, and team building',
    'Teamwork': 'Questions about collaborative development, code reviews, and pair programming',
    'Job Readiness': 'Questions about interview preparation, portfolio building, and career development',
    'Problem Solving': 'Questions about debugging, optimization, and analytical thinking'
}

# Define employability levels
EMPLOYABILITY_LEVELS = {
    'Level 1': {
        'min_score': 0,
        'max_score': 2.5,
        'description': 'Needs significant improvement in technical and professional skills',
        'title': 'Entry Level'
    },
    'Level 2': {
        'min_score': 2.5,
        'max_score': 3.5,
        'description': 'Suitable for internship positions with potential for growth',
        'title': 'Internship Ready'
    },
    'Level 3': {
        'min_score': 3.5,
        'max_score': 5.0,
        'description': 'Ready for professional roles with strong technical and soft skills',
        'title': 'Job Ready'
    }
}

def get_default_questions(category):
    """Get default questions for a category if Ollama fails."""
    # Your existing hardcoded questions here
    return QUESTIONS.get(category, [])

# Initialize questions dictionary
QUESTIONS = {}

def calculate_category_score(responses):
    """Calculate the score for each category based on correct answers."""
    scores = {}
    for category, questions in QUESTIONS.items():
        correct_answers = 0
        for i, question in enumerate(questions):
            if int(responses.get(f"{category}_{i}", -1)) == question['correct']:
                correct_answers += 1
        scores[category] = (correct_answers / len(questions)) * 5  # Convert to 5-point scale
    return scores

def determine_employability_level(overall_score):
    """Determine the employability level based on the overall score."""
    for level, criteria in EMPLOYABILITY_LEVELS.items():
        if criteria['min_score'] <= overall_score <= criteria['max_score']:
            return {
                'level': level,
                'title': criteria['title'],
                'description': criteria['description']
            }
    return {
        'level': 'Level 1',
        'title': 'Entry Level',
        'description': 'Needs significant improvement in technical and professional skills'
    }

def create_radar_chart(current_scores, past_results=None):
    """Create a radar chart with current and past scores"""
    categories = list(current_scores.keys())
    
    # Create traces for current scores
    current_trace = go.Scatterpolar(
        r=[current_scores[cat] for cat in categories],
        theta=categories,
        fill='toself',
        name='Current Assessment',
        line=dict(color='#4361ee')
    )
    
    data = [current_trace]
    
    # Add traces for past assessments with different colors
    if past_results:
        colors = ['#f72585', '#4cc9f0', '#f8961e', '#7209b7', '#3a0ca3']
        for i, result in enumerate(past_results[:5]):  # Show last 5 assessments
            past_trace = go.Scatterpolar(
                r=[result['scores'][cat] for cat in categories],
                theta=categories,
                fill='toself',
                name=f'Assessment {i+1} ({result["timestamp"][:10]})',
                line=dict(color=colors[i % len(colors)]),
                opacity=0.5
            )
            data.append(past_trace)
    
    layout = go.Layout(
        polar=dict(
            radialaxis=dict(
                visible=True,
                range=[0, 100]
            )
        ),
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="center",
            x=0.5
        )
    )
    
    return go.Figure(data=data, layout=layout)

def generate_improvement_suggestions(scores, level):
    """Generate suggestions for improvement based on scores and level."""
    suggestions = []
    
    # Add level-specific suggestions
    if level['level'] == 'Level 1':
        suggestions.append("Focus on building fundamental technical skills and professional competencies.")
    elif level['level'] == 'Level 2':
        suggestions.append("Work on strengthening your technical skills and gaining practical experience through internships.")
    else:
        suggestions.append("Continue to enhance your skills and maintain professional development.")
    
    # Add category-specific suggestions
    for category, score in scores.items():
        if score < 3:
            suggestions.append(f"Consider improving your knowledge in {category}. Current score: {score:.1f}/5")
    
    return suggestions

@employability_bp.route('/analyzer')
def index():
    """Render the employability analyzer page"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    # Generate questions for each category
    questions_with_indices = {}
    for category in CATEGORIES.keys():
        questions = generate_questions_with_ollama(category)
        questions_with_indices[category] = [
            {
                'index': i,
                'question': q['question'],
                'options': q['options']
            }
            for i, q in enumerate(questions)
        ]
    
    # Get past assessments and stats
    past_assessments = get_user_assessments(session['user_id'])
    stats = get_assessment_stats(session['user_id'])
    
    past_results = []
    for assessment in past_assessments:
        past_results.append({
            'timestamp': assessment['timestamp'],
            'scores': json.loads(assessment['scores']),
            'overall_score': assessment['overall_score'],
            'employability_level': assessment['employability_level']
        })
    
    return render_template('employability.html', 
                         questions=questions_with_indices,
                         past_results=past_results,
                         stats=stats)

@employability_bp.route('/analyzer/assess', methods=['POST'])
def assess():
    """Handle the assessment submission"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    responses = request.form.to_dict()
    scores = calculate_category_score(responses)
    overall_score = calculate_overall_score(scores)
    employability_level = get_employability_level(overall_score)
    suggestions = get_improvement_suggestions(scores)
    
    # Save the assessment result
    save_assessment_result(session['user_id'], scores, overall_score, employability_level)
    
    # Get past assessments and stats
    past_assessments = get_user_assessments(session['user_id'])
    stats = get_assessment_stats(session['user_id'])
    
    past_results = []
    for assessment in past_assessments:
        past_results.append({
            'timestamp': assessment['timestamp'],
            'scores': json.loads(assessment['scores']),
            'overall_score': assessment['overall_score'],
            'employability_level': assessment['employability_level']
        })
    
    # Create radar chart with past performances
    fig = create_radar_chart(scores, past_results)
    chart_json = json.dumps(fig, cls=plotly.utils.PlotlyJSONEncoder)
    
    return jsonify({
        'scores': scores,
        'overall_score': overall_score,
        'employability_level': employability_level,
        'suggestions': suggestions,
        'chart': chart_json,
        'past_results': past_results,
        'stats': dict(stats)
    })

if __name__ == '__main__':
    app.run(debug=True) 