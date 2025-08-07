from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import json
import os
from datetime import datetime
import logging
import requests
from dotenv import load_dotenv
import random
import google.generativeai as genai

# Load environment variables from the current directory
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Log environment variables (excluding sensitive data)
logger.info("=== Environment Variables ===")
logger.info(f"FLASK_ENV: {os.getenv('FLASK_ENV')}")
logger.info(f"GEMINI_API_KEY exists: {bool(os.getenv('GEMINI_API_KEY'))}")

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'your_secret_key_here')

# Add custom template filters
@app.template_filter('format_datetime')
def format_datetime(value):
    if not value:
        return "N/A"
    try:
        dt = datetime.fromisoformat(value)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return "Invalid date"

@app.template_filter('format_duration')
def format_duration(value):
    if not value:
        return "N/A"
    try:
        # Convert ISO format duration to seconds
        if isinstance(value, str):
            start = datetime.fromisoformat(value)
            end = datetime.now()
            duration = (end - start).total_seconds()
        else:
            duration = value
        
        # Format duration as HH:MM:SS
        hours = int(duration // 3600)
        minutes = int((duration % 3600) // 60)
        seconds = int(duration % 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    except (ValueError, TypeError):
        return "Invalid duration"

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'info'

# Initialize Flask-Limiter
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

class Config:
    SECRET_KEY = os.getenv('SECRET_KEY', 'your-secret-key')
    USERS_FILE = 'data/users.json'
    QUESTIONS_FILE = 'data/questions.json'
    USER_ANSWERS_FILE = 'data/user_answers.json'
    USER_EXAM_STATE_FILE = 'data/user_exam_state.json'
    TEST_HISTORY_FILE = 'data/test_history.json'  # New file for test history

class User(UserMixin):
    def __init__(self, user_id, username):
        self.id = user_id
        self.username = username

    def get_id(self):
        return str(self.id)

    @staticmethod
    def get(user_id):
        users_data = load_data(Config.USERS_FILE)
        user_data = users_data.get(str(user_id))
        if user_data:
            return User(str(user_id), user_data['username'])
        return None

class QuestionGenerator:
    def __init__(self):
        # Load environment variables
        load_dotenv()
        
        # Get API key from environment
        self.api_key = os.getenv('GEMINI_API_KEY')
        logger.info(f"API Key loaded: {bool(self.api_key)}")
        
        if not self.api_key:
            logger.error("GEMINI_API_KEY not found in environment variables")
            raise ValueError("GEMINI_API_KEY environment variable is not set")
            
        try:
            # Initialize the Google Generative AI client
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel('gemini-2.0-flash')
            logger.info("QuestionGenerator initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing Gemini client: {str(e)}")
            raise

    def generate_question(self, competency_domain, difficulty_level, bloom_level):
        if not self.api_key:
            logger.error("API key is not set")
            return None

        prompt = f"""
        You are an expert question generator for an adaptive testing system. Generate a multiple-choice question with the following specifications:

        Competency Domain: {competency_domain}
        Difficulty Level: {difficulty_level}
        Bloom's Taxonomy Level: {bloom_level}

        Requirements:
        1. The question should be clear, concise, and test the specified competency domain
        2. Provide exactly 4 options (A, B, C, D)
        3. Include one correct answer
        4. The question should be challenging but fair for the specified difficulty level
        5. The question should align with the specified Bloom's Taxonomy level
        6. The question should be unique and not easily searchable
        7. The options should be plausible and well-distributed
        8. The explanation should be clear and educational

        Format your response as a valid JSON object with the following structure:
        {{
            "content": "The question text",
            "options": ["Option A", "Option B", "Option C", "Option D"],
            "correct_answer": "A",  // Must be A, B, C, or D
            "explanation": "Brief explanation of why the answer is correct",
            "competency_domain": "{competency_domain}",
            "difficulty": "{difficulty_level}",
            "bloom_level": "{bloom_level}"
        }}

        Important: Return ONLY the JSON object, no additional text or explanation.
        """

        try:
            logger.info("=== Generating Question ===")
            logger.info(f"Parameters: {competency_domain}, {difficulty_level}, {bloom_level}")
            
            # Generate content using the Gemini model
            response = self.model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.7,
                    "max_output_tokens": 1000,
                    "top_p": 0.8,
                    "top_k": 40
                },
                safety_settings=[
                    {
                        "category": "HARM_CATEGORY_HARASSMENT",
                        "threshold": "BLOCK_MEDIUM_AND_ABOVE"
                    },
                    {
                        "category": "HARM_CATEGORY_HATE_SPEECH",
                        "threshold": "BLOCK_MEDIUM_AND_ABOVE"
                    },
                    {
                        "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                        "threshold": "BLOCK_MEDIUM_AND_ABOVE"
                    },
                    {
                        "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                        "threshold": "BLOCK_MEDIUM_AND_ABOVE"
                    }
                ]
            )
            
            logger.info("=== API Response ===")
            logger.info(f"Response: {response.text}")
            
            if not response.text:
                logger.error("Empty response from API")
                return None
            
            # Clean the content to ensure it's valid JSON
            content = response.text.strip()
            if content.startswith('```json'):
                content = content[7:]
            if content.endswith('```'):
                content = content[:-3]
            content = content.strip()
            
            try:
                question_data = json.loads(content)
                logger.info("=== Parsed Question Data ===")
                logger.info(f"Question Data: {json.dumps(question_data, indent=2)}")
                
                # Validate required fields
                required_fields = ['content', 'options', 'correct_answer', 'explanation']
                if not all(field in question_data for field in required_fields):
                    logger.error(f"Missing required fields in question data: {question_data}")
                    return None
                
                # Validate correct_answer format
                if question_data['correct_answer'] not in ['A', 'B', 'C', 'D']:
                    logger.error(f"Invalid correct_answer format: {question_data['correct_answer']}")
                    return None
                    
                return question_data
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse question JSON: {e}")
                logger.error(f"Raw content: {content}")
                return None
                
        except Exception as e:
            logger.error(f"Error generating question: {str(e)}")
            return None

    def analyze_user_performance(self, user_answers, questions):
        # Convert answers to numerical format for analysis
        answer_mapping = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
        user_scores = []
        
        for answer in user_answers:
            if answer in answer_mapping:
                user_scores.append(answer_mapping[answer])
            else:
                user_scores.append(-1)  # Invalid answer

        # Calculate performance metrics
        total_questions = len(questions)
        correct_answers = sum(1 for i, answer in enumerate(user_answers) 
                            if answer == questions[i]['correct_answer'])
        accuracy = correct_answers / total_questions if total_questions > 0 else 0

        # Initialize domain times tracking
        domain_times = {}
        for domain in set(q['competency_domain'] for q in questions):
            domain_times[domain] = 0

        # Calculate time spent on each domain
        for i, question in enumerate(questions):
            domain = question['competency_domain']
            if 'time_spent' in question:
                domain_times[domain] = domain_times.get(domain, 0) + question['time_spent']

        # CO-PO Mapping Analysis
        co_po_mapping = {
            'CO1': {'po': 'PO1', 'mapping_level': 0.8, 'performance': 0.0},
            'CO2': {'po': 'PO2', 'mapping_level': 0.7, 'performance': 0.0},
            'CO3': {'po': 'PO3', 'mapping_level': 0.9, 'performance': 0.0},
            'CO4': {'po': 'PO4', 'mapping_level': 0.6, 'performance': 0.0},
            'CO5': {'po': 'PO5', 'mapping_level': 0.8, 'performance': 0.0}
        }

        # Calculate performance for each CO
        for i, question in enumerate(questions):
            co = question.get('co', 'CO1')  # Default to CO1 if not specified
            if user_answers[i] == question['correct_answer']:
                co_po_mapping[co]['performance'] += 1
        for co in co_po_mapping:
            co_po_mapping[co]['performance'] /= total_questions

        # Bloom's Taxonomy Analysis
        blooms_analysis = {
            'Remember': {'count': 0, 'performance': 0.0},
            'Understand': {'count': 0, 'performance': 0.0},
            'Apply': {'count': 0, 'performance': 0.0},
            'Analyze': {'count': 0, 'performance': 0.0},
            'Evaluate': {'count': 0, 'performance': 0.0},
            'Create': {'count': 0, 'performance': 0.0}
        }

        # Calculate performance for each Bloom's level
        for i, question in enumerate(questions):
            bloom_level = question.get('bloom_level', 'Remember')  # Default to Remember if not specified
            blooms_analysis[bloom_level]['count'] += 1
            if user_answers[i] == question['correct_answer']:
                blooms_analysis[bloom_level]['performance'] += 1
        for level in blooms_analysis:
            if blooms_analysis[level]['count'] > 0:
                blooms_analysis[level]['performance'] /= blooms_analysis[level]['count']

        # Map difficulty levels to numerical values
        difficulty_mapping = {
            'Easy': 0.3,
            'Medium': 0.6,
            'Hard': 0.9
        }
        
        # Analyze difficulty distribution
        difficulties = [difficulty_mapping.get(q['difficulty'], 0.3) for q in questions 
                      if 'difficulty' in q and q['difficulty'] in difficulty_mapping]
        avg_difficulty = sum(difficulties) / len(difficulties) if difficulties else 0

        # Analyze competency domains
        domains = {}
        for q in questions:
            domain = q['competency_domain']
            domains[domain] = domains.get(domain, 0) + 1

        # Calculate competency scores
        competency_analysis = {}
        for domain, count in domains.items():
            domain_questions = [q for q in questions if q['competency_domain'] == domain]
            domain_answers = [answer for i, answer in enumerate(user_answers) 
                            if questions[i]['competency_domain'] == domain]
            correct_domain_answers = sum(1 for i, answer in enumerate(domain_answers) 
                                      if answer == domain_questions[i]['correct_answer'])
            competency_analysis[domain] = (correct_domain_answers / len(domain_questions) * 100) if domain_questions else 0

        return {
            'total_questions': total_questions,
            'questions_answered': len(user_answers),
            'correct_answers': correct_answers,
            'accuracy': accuracy,
            'performance_percentage': accuracy * 100,
            'average_difficulty': avg_difficulty,
            'domain_distribution': domains,
            'competency_analysis': competency_analysis,
            'co_po_mapping': co_po_mapping,
            'blooms_analysis': blooms_analysis,
            'domain_times': domain_times  # Add domain times to the return value
        }

    def generate_recommendations(self, performance_analysis):
        try:
            prompt = f"""
            Based on the following performance analysis, provide detailed learning recommendations:

            Overall Performance: {performance_analysis['accuracy'] * 100:.2f}%
            Average Difficulty: {performance_analysis['average_difficulty'] * 100:.2f}%
            
            Competency Analysis:
            {performance_analysis['competency_analysis']}
            
            Bloom's Analysis:
            {performance_analysis['blooms_analysis']}

            Format your response as a list of recommendations, each with a category and message. 
            Categories should include: Overall Performance, Learning Strategy, Skill Development, and Next Steps.
            Provide specific, actionable advice for improvement.
            """

            response = self.model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.7,
                    "max_output_tokens": 1000
                }
            )

            # Process the response into structured recommendations
            recommendations = []
            
            # Add generated recommendations
            if response.text:
                lines = response.text.strip().split('\n')
                current_category = None
                current_message = []
                
                for line in lines:
                    line = line.strip()
                    if line:
                        if line.endswith(':'):  # This is a category
                            if current_category and current_message:
                                recommendations.append({
                                    'category': current_category,
                                    'message': ' '.join(current_message)
                                })
                            current_category = line[:-1]
                            current_message = []
                        else:
                            current_message.append(line)
                
                # Add the last recommendation
                if current_category and current_message:
                    recommendations.append({
                        'category': current_category,
                        'message': ' '.join(current_message)
                    })

            # Add default recommendations if none were generated
            if not recommendations:
                recommendations = [
                    {
                        'category': 'Overall Performance',
                        'message': f"Your overall performance is {performance_analysis['accuracy'] * 100:.2f}%. Focus on areas where your score is below 70%."
                    },
                    {
                        'category': 'Learning Strategy',
                        'message': "Review the topics where you scored lowest and practice with additional questions."
                    }
                ]

            return recommendations
            
        except Exception as e:
            logger.error(f"Error generating recommendations: {str(e)}")
            return [
                {
                    'category': 'General Advice',
                    'message': "Continue practicing and focus on areas where you feel less confident."
                }
            ]

# Initialize Question Generator
question_generator = QuestionGenerator()

def load_data(filename):
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_data(data, filename):
    with open(filename, 'w') as f:
        json.dump(data, f, indent=4)

@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        users_data = load_data(Config.USERS_FILE)
        
        # Find user by username
        user_id = None
        for uid, user in users_data.items():
            if user['username'] == username:
                user_id = uid
                break
        
        if user_id:
            user = User(user_id, username)
            login_user(user)
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid username')
            return redirect(url_for('login'))
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        users_data = load_data(Config.USERS_FILE)
        
        # Check if username already exists
        if any(u['username'] == username for u in users_data.values()):
            flash('Username already exists')
            return redirect(url_for('register'))
        
        # Create new user
        new_user_id = str(len(users_data) + 1)
        users_data[new_user_id] = {
            'username': username,
            'test_results': []
        }
        
        # Save the updated users data
        save_data(users_data, Config.USERS_FILE)
        
        # Initialize user data files
        user_answers = load_data(Config.USER_ANSWERS_FILE)
        user_answers[new_user_id] = []
        save_data(user_answers, Config.USER_ANSWERS_FILE)
        
        user_exam_state = load_data(Config.USER_EXAM_STATE_FILE)
        user_exam_state[new_user_id] = {
            'current_question_index': 0,
            'questions_answered': 0,
            'total_questions': 10,
            'start_time': None,
            'completed': False
        }
        save_data(user_exam_state, Config.USER_EXAM_STATE_FILE)
        
        flash('Registration successful! Please login.')
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    user_id = str(current_user.id)
    user_exam_state = load_data(Config.USER_EXAM_STATE_FILE)
    test_history = load_data(Config.TEST_HISTORY_FILE)
    
    # Get current results
    results = None
    recommendations = None
    if user_id in test_history and test_history[user_id]:
        current_test = test_history[user_id][-1]
        results = current_test['performance']
        recommendations = current_test['recommendations']
    
    # Get previous results for comparison
    previous_results = None
    if user_id in test_history and len(test_history[user_id]) > 1:
        previous_test = test_history[user_id][-2]
        previous_results = previous_test['performance']
    
    return render_template('dashboard.html',
                         results=results,
                         previous_results=previous_results,
                         recommendations=recommendations,
                         exam_state=user_exam_state.get(user_id))

@app.route('/test')
@login_required
def test():
    return render_template('test.html')

@app.route('/api/questions/next')
@login_required
def get_next_question():
    try:
        user_id = str(current_user.id)
        logger.info(f"=== Getting Next Question for User {user_id} ===")
        
        # Load all required data
        try:
            user_exam_state = load_data(Config.USER_EXAM_STATE_FILE)
            questions = load_data(Config.QUESTIONS_FILE)
            logger.info(f"Loaded data: exam_state={bool(user_exam_state)}, questions={len(questions)}")
        except Exception as e:
            logger.error(f"Error loading data files: {str(e)}")
            return jsonify({
                'error': 'Failed to load data',
                'message': 'Please try again later'
            }), 500
        
        # Get exam state
        exam_state = user_exam_state.get(user_id, {})
        if not exam_state:
            logger.error("No exam state found")
            return jsonify({
                'error': 'Exam not initialized',
                'message': 'Please start the test first'
            }), 400
        
        if exam_state['completed']:
            logger.info("Exam already completed")
            return jsonify({'message': 'Exam completed'}), 200
        
        current_index = exam_state['current_question_index']
        if current_index >= exam_state['total_questions']:
            logger.info("Reached maximum questions, marking exam as completed")
            exam_state['completed'] = True
            try:
                save_data(user_exam_state, Config.USER_EXAM_STATE_FILE)
            except Exception as e:
                logger.error(f"Error saving completed exam state: {str(e)}")
            return jsonify({'message': 'Exam completed'}), 200
        
        try:
            question = questions[current_index]
            if not question:
                raise IndexError("Question not found")
            logger.info(f"Returning question {current_index}: {question.get('content', '')[:50]}...")
            return jsonify(question)
        except (IndexError, KeyError) as e:
            logger.error(f"Error accessing question: {str(e)}")
            return jsonify({
                'error': 'Question not found',
                'message': 'Please try again later'
            }), 500
            
    except Exception as e:
        logger.error(f"Unexpected error in get_next_question: {str(e)}")
        return jsonify({
            'error': 'Unexpected error',
            'message': 'Please try again later'
        }), 500

@app.route('/api/questions/submit', methods=['POST'])
@login_required
def submit_answer():
    try:
        data = request.get_json()
        answer = data.get('answer')
        question_id = data.get('question_id')
        time_spent = data.get('time_spent', 0)  # Get time spent in seconds

        # Load current test state
        test_state = load_data(Config.USER_EXAM_STATE_FILE)
        user_state = test_state.get(str(current_user.id), {})
        
        if not user_state:
            return jsonify({'error': 'No active test found'}), 400

        # Update the question with the answer and time spent
        for question in user_state.get('questions', []):
            if question.get('id') == question_id:
                question['user_answer'] = answer
                question['time_spent'] = time_spent
                break

        # Save updated test state
        test_state[str(current_user.id)] = user_state
        save_data(test_state, Config.USER_EXAM_STATE_FILE)

        return jsonify({'success': True})

    except Exception as e:
        logger.error(f"Error submitting answer: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/test/state')
@login_required
def get_test_state():
    user_id = str(current_user.id)
    user_exam_state = load_data(Config.USER_EXAM_STATE_FILE)
    
    logger.info("=== Getting Test State ===")
    logger.info(f"User ID: {user_id}")
    logger.info(f"Exam State: {user_exam_state.get(user_id)}")
    
    exam_state = user_exam_state.get(user_id, {})
    if not exam_state:
        logger.info("No exam state found, initializing new exam state")
        exam_state = {
            'current_question_index': 0,
            'questions_answered': 0,
            'total_questions': 5,
            'start_time': datetime.now().isoformat(),
            'completed': False
        }
        user_exam_state[user_id] = exam_state
        save_data(user_exam_state, Config.USER_EXAM_STATE_FILE)
    
    return jsonify(exam_state)

@app.route('/api/test/generate-questions', methods=['POST'])
@login_required
def generate_questions():
    try:
        user_id = str(current_user.id)
        logger.info(f"=== Generating Questions for User {user_id} ===")
        
        # Load questions data
        questions = load_data(Config.QUESTIONS_FILE)
        
        # Clear existing questions
        questions = []
        logger.info("Generating new questions")
        
        # Define broader competency domains
        competency_domains = [
            'Problem Solving', 
            'Critical Thinking', 
            'Analytical Skills',
            'Technical Knowledge',
            'Communication',
            'Time Management',
            'Data Analysis',
            'Logical Reasoning',
            'Creative Thinking',
            'Decision Making'
        ]
        
        difficulty_levels = ['Easy', 'Medium', 'Hard']
        bloom_levels = ['Remember', 'Understand', 'Apply', 'Analyze', 'Evaluate', 'Create']
        
        # Test API connection first
        try:
            test_response = question_generator.model.generate_content("Test connection")
            logger.info("API connection test successful")
        except Exception as e:
            error_msg = f"API connection failed: {str(e)}"
            logger.error(error_msg)
            flash(error_msg, 'error')
            return jsonify({
                'error': 'API connection failed',
                'message': error_msg
            }), 500
        
        # Generate questions one at a time
        for i in range(10):  # Generate exactly 10 questions
            # Ensure each competency domain is covered at least once
            if i < len(competency_domains):
                competency_domain = competency_domains[i]
            else:
                competency_domain = random.choice(competency_domains)
            
            difficulty_level = random.choice(difficulty_levels)
            bloom_level = random.choice(bloom_levels)
            
            logger.info(f"Generating question {i+1} with parameters: {competency_domain}, {difficulty_level}, {bloom_level}")
            try:
                new_question = question_generator.generate_question(
                    competency_domain=competency_domain,
                    difficulty_level=difficulty_level,
                    bloom_level=bloom_level
                )
                
                if new_question:
                    # Save each question immediately after generation
                    questions.append(new_question)
                    try:
                        save_data(questions, Config.QUESTIONS_FILE)
                        logger.info(f"Successfully generated and saved question {i+1}")
                    except Exception as e:
                        error_msg = f"Error saving question {i+1}: {str(e)}"
                        logger.error(error_msg)
                        flash(error_msg, 'error')
                        return jsonify({
                            'error': 'Failed to save question',
                            'message': error_msg
                        }), 500
                else:
                    error_msg = f"Failed to generate question {i+1}. Please check your API key and try again."
                    logger.error(error_msg)
                    flash(error_msg, 'error')
                    return jsonify({
                        'error': 'Failed to generate questions',
                        'message': error_msg
                    }), 500
            except Exception as e:
                error_msg = f"Error generating question {i+1}: {str(e)}"
                logger.error(error_msg)
                flash(error_msg, 'error')
                return jsonify({
                    'error': 'Failed to generate questions',
                    'message': error_msg
                }), 500
        
        flash('Questions generated successfully!', 'success')
        return jsonify({
            'success': True,
            'message': 'Questions generated successfully',
            'count': len(questions)
        })
        
    except Exception as e:
        error_msg = f"Error generating questions: {str(e)}"
        logger.error(error_msg)
        flash(error_msg, 'error')
        return jsonify({
            'error': 'Failed to generate questions',
            'message': error_msg
        }), 500

@app.route('/api/test/initialize', methods=['POST'])
@login_required
def initialize_test():
    try:
        user_id = str(current_user.id)
        logger.info(f"=== Initializing Test for User {user_id} ===")
        
        # Load all required data
        user_exam_state = load_data(Config.USER_EXAM_STATE_FILE)
        user_answers = load_data(Config.USER_ANSWERS_FILE)
        questions = load_data(Config.QUESTIONS_FILE)
        
        # Check if questions exist
        if not questions or len(questions) < 10:  # Updated to check for 10 questions
            return jsonify({
                'error': 'Questions not generated',
                'message': 'Please generate questions first'
            }), 400
        
        # Reset exam state
        exam_state = {
            'current_question_index': 0,
            'questions_answered': 0,
            'total_questions': 10,  # Updated to 10 questions
            'start_time': datetime.now().isoformat(),
            'completed': False
        }
        user_exam_state[user_id] = exam_state
        
        # Clear previous answers
        if user_id in user_answers:
            user_answers[user_id] = []
        
        # Save the reset state
        save_data(user_exam_state, Config.USER_EXAM_STATE_FILE)
        save_data(user_answers, Config.USER_ANSWERS_FILE)
        
        return jsonify({
            'success': True,
            'message': 'Test initialized successfully'
        })
        
    except Exception as e:
        logger.error(f"Error initializing test: {str(e)}")
        return jsonify({
            'error': 'Failed to initialize test',
            'message': 'Please try again later'
        }), 500

if __name__ == '__main__':
    app.run(debug=True) 