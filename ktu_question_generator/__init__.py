import os
import fitz
import re
import ollama
import json
import sqlite3
from io import BytesIO
from flask import Blueprint, request, render_template, redirect, current_app, flash, session, url_for, jsonify, make_response
from werkzeug.utils import secure_filename
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

def init_question_paper_db(app):
    """Initialize database tables for question paper generator"""
    with app.app_context():
        conn = sqlite3.connect(app.config['DATABASE'])
        try:
            conn.executescript('''
                -- Question Paper Templates
                CREATE TABLE IF NOT EXISTS question_paper_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    institution TEXT NOT NULL,
                    course TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    total_marks INTEGER NOT NULL,
                    duration_minutes INTEGER NOT NULL,
                    template_structure TEXT NOT NULL,  -- JSON string containing section details
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_by INTEGER,
                    FOREIGN KEY (created_by) REFERENCES info_teacher (id)
                );

                -- Generated Question Papers
                CREATE TABLE IF NOT EXISTS generated_question_papers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    template_id INTEGER,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,  -- JSON string containing questions
                    difficulty_distribution TEXT NOT NULL,  -- JSON string with easy/medium/hard percentages
                    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    generated_by INTEGER,
                    is_edited BOOLEAN DEFAULT FALSE,
                    last_edited_at TIMESTAMP,
                    FOREIGN KEY (template_id) REFERENCES question_paper_templates (id),
                    FOREIGN KEY (generated_by) REFERENCES info_teacher (id)
                );

                -- Question Paper Sections
                CREATE TABLE IF NOT EXISTS question_paper_sections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    template_id INTEGER,
                    section_name TEXT NOT NULL,
                    section_type TEXT NOT NULL,  -- e.g., 'short_answer', 'long_answer', 'mcq'
                    total_questions INTEGER NOT NULL,
                    marks_per_question INTEGER NOT NULL,
                    total_marks INTEGER NOT NULL,
                    difficulty_distribution TEXT NOT NULL,  -- JSON string with easy/medium/hard percentages
                    FOREIGN KEY (template_id) REFERENCES question_paper_templates (id)
                );
            ''')
            conn.commit()
        except Exception as e:
            app.logger.error(f"Error initializing question paper database: {str(e)}")
            conn.rollback()
        finally:
            conn.close()

def get_db_connection():
    """Get a database connection from the current app context"""
    try:
        conn = sqlite3.connect(current_app.config['DATABASE'])
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as e:
        current_app.logger.error(f"Database connection error: {str(e)}")
        flash('Database connection error. Please try again.', 'error')
        return None

question_paper_bp = Blueprint('question_paper', __name__,
                          template_folder='templates',
                          static_folder='static',
                          url_prefix='/question-paper')

# Initialize database tables when blueprint is registered
@question_paper_bp.record_once
def on_register(state):
    init_question_paper_db(state.app)

class QuestionPaperGenerator:
    def __init__(self):
        pass  # No hardcoded templates or institutions

    def extract_text_from_pdf(self, pdf_path):
        try:
            with fitz.open(pdf_path) as doc:
                full_text = []
                for page in doc:
                    text = page.get_text("text")
                    text = re.sub(r'\s+', ' ', text).strip()
                    full_text.append(text)
                return ' '.join(full_text)
        except Exception as e:
            print(f"PDF extraction error: {e}")
            return ""

    def extract_questions_from_past_papers(self, pdf_paths):
        past_questions = []
        for path in pdf_paths:
            text = self.extract_text_from_pdf(path)
            questions = re.findall(
                r'(?:Q\s*\d+\.?|Question\s*\d*:?|Part\s*[A-Z]\s*\(.*?\):?)(.*?)(?=(?:Q\s*\d+|Question\s*\d+|Part\s*[A-Z]\s*\()|$)',
                text,
                re.DOTALL | re.IGNORECASE
            )
            past_questions.extend([self._clean_question(q) for q in questions if q.strip()])
        return past_questions

    def _clean_question(self, question):
        question = re.sub(r'\[.*?\]', '', question)
        question = re.sub(r'\s+', ' ', question).strip()
        return question[:500]

    def generate_questions(self, combined_text, template, difficulty_distribution, past_questions=None):
        if not combined_text:
            return {"error": "No meaningful content found in the PDFs"}

        # Prepare difficulty distribution for the prompt
        difficulty_prompt = f"""
**Difficulty Distribution:**
- Easy Questions: {difficulty_distribution['easy']}%
- Medium Questions: {difficulty_distribution['medium']}%
- Hard Questions: {difficulty_distribution['hard']}%
"""

        past_questions_section = ""
        if past_questions:
            sample_questions = '\n'.join(f'- {q}' for q in past_questions[:15])
            past_questions_section = f"""
**Avoidance Requirements:**
Do not create questions similar to these past questions:
{sample_questions}

**Additional Guidelines:**
1. Ensure new questions are distinct in both content and phrasing
2. Cover similar concepts but with different approaches
3. Vary question types and formats
"""

        # Build section-specific prompts
        section_prompts = []
        for section in template['sections']:
            section_prompt = f"""
**{section['name']} ({section['type'].replace('_', ' ').title()}):**
- Number of questions: {section['questions']}
- Marks per question: {section['marks_per_question']}
- Total marks: {section['questions'] * section['marks_per_question']}
"""
            section_prompts.append(section_prompt)

        system_prompt = f"""
You are an AI specialized in generating academic question papers.
Follow these guidelines strictly:

**Paper Structure:**
{chr(10).join(section_prompts)}

**Formatting Rules:**
1. Start each section with the section name
2. List questions with bullet points (-)
3. For sub-questions use a) and b)
4. Explicitly include marks allocation in brackets [X Marks]
5. Keep questions concise but unambiguous
6. Label each question with its difficulty level [Easy/Medium/Hard]

**Quality Requirements:**
1. Questions must be unambiguous and academically rigorous
2. Cover all major topics from the source material
3. Include appropriate technical terms
4. Follow the specified difficulty distribution
{difficulty_prompt}
{past_questions_section}
"""

        user_prompt = f"Generate a comprehensive question paper from: {combined_text[:4000]}"

        try:
            response = ollama.chat(
                model="mistral",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            )
            return self._format_response(response["message"]["content"], template)
        except Exception as e:
            return {"error": f"Error generating questions: {str(e)}"}

    def _format_response(self, text, template):
        """Convert raw AI response into structured question paper format"""
        sections = {}
        current_section = None
        current_questions = []
        section_types = {section['name']: section.get('type', '') for section in template['sections']}
        for line in text.split('\n'):
            line = line.strip()
            # Check if this is a section header
            section_match = re.match(r'\*\*(.*?)\*\*', line)
            if section_match:
                if current_section:
                    sections[current_section] = current_questions
                current_section = section_match.group(1)
                current_questions = []
                continue
            # Process questions
            if line and current_section:
                if line.startswith('- ') or line.startswith('a)') or line.startswith('b)') or line[0].isdigit():
                    # Extract difficulty level if present
                    difficulty = None
                    if '[Easy]' in line:
                        difficulty = 'easy'
                        line = line.replace('[Easy]', '').strip()
                    elif '[Medium]' in line:
                        difficulty = 'medium'
                        line = line.replace('[Medium]', '').strip()
                    elif '[Hard]' in line:
                        difficulty = 'hard'
                        line = line.replace('[Hard]', '').strip()
                    # Extract marks if present
                    marks = None
                    marks_match = re.search(r'\[(\d+)\s*Marks\]', line)
                    if marks_match:
                        try:
                            marks = int(marks_match.group(1))
                            line = re.sub(r'\[\d+\s*Marks\]', '', line).strip()
                        except (ValueError, TypeError):
                            marks = None
                    # If no marks specified, use the template's default marks
                    if marks is None and current_section in template['sections']:
                        for section in template['sections']:
                            if section['name'] == current_section:
                                marks = section['marks_per_question']
                                break
                    # MCQ parsing
                    q_type = section_types.get(current_section, '')
                    # Remove leading number and tags like [Medium, 2 Marks] from question text
                    clean_line = re.sub(r'^\d+\.\s*', '', line)  # Remove leading number and dot
                    clean_line = re.sub(r'^\[.*?\]\s*', '', clean_line)  # Remove leading [tags]
                    if q_type == 'multiple_choice':
                        main_q, *opts = re.split(r'(?=a\))', clean_line, maxsplit=1)
                        options = []
                        if opts:
                            # Extract all options (a), b), c), ...) and their text, even if on the same line or new lines
                            option_matches = re.findall(r'([a-z]\))\s*([^a-z\)](?:.*?))(?:\s*(?=[a-z]\))|$)', opts[0], re.DOTALL)
                            options = [o[1].strip().replace('\n', ' ') for o in option_matches if o[1].strip()]
                        current_questions.append({
                            'text': main_q.strip(),
                            'difficulty': difficulty or 'medium',
                            'marks': marks or 0,
                            'options': options
                        })
                    else:
                        # For non-MCQ, just clean up leading number/tags, keep rest of text
                        current_questions.append({
                            'text': clean_line.strip(),
                            'difficulty': difficulty or 'medium',
                            'marks': marks or 0
                        })
                elif current_questions:
                    # Continue previous question if not a new item
                    current_questions[-1]['text'] += " " + line
        # Add the last section
        if current_section:
            sections[current_section] = current_questions
        return {'sections': sections}

def save_uploaded_files(files, file_type):
    upload_folder = os.path.join(current_app.instance_path, 'uploads', file_type)
    os.makedirs(upload_folder, exist_ok=True)
    
    saved_paths = []
    for i, file in enumerate(files):
        if file and file.filename:
            if file.filename.lower().endswith('.pdf'):
                filename = secure_filename(f"{file_type}_{i}_{file.filename}")
                pdf_path = os.path.join(upload_folder, filename)
                file.save(pdf_path)
                saved_paths.append(pdf_path)
            else:
                flash('Only PDF files are allowed', 'error')
                return None
    
    return saved_paths if saved_paths else None

@question_paper_bp.route("/", methods=["GET", "POST"])
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    if request.method == "POST":
        if 'step' not in request.form:
            flash('Invalid request', 'error')
            return redirect(url_for('question_paper.index'))
        step = request.form['step']
        if step == 'select_template':
            # Only allow custom template creation or selection
            action = request.form.get('action')
            if action == 'create':
                return render_template("question_paper/create_template.html")
            elif action == 'reuse':
                # Show a list of saved templates for the user to select
                conn = get_db_connection()
                templates = conn.execute('SELECT id, name, template_structure FROM question_paper_templates WHERE created_by = ?', (session['teacher_id'],)).fetchall()
                conn.close()
                return render_template("question_paper/select_saved_template.html", templates=templates)
            else:
                flash('Please choose to create a new template or reuse an existing one.', 'error')
                return redirect(url_for('question_paper.index'))
        elif step == 'choose_saved_template':
            # User selects a saved template
            template_id = request.form.get('template_id')
            if not template_id:
                flash('Please select a template.', 'error')
                return redirect(url_for('question_paper.index'))
            conn = get_db_connection()
            template_row = conn.execute('SELECT template_structure FROM question_paper_templates WHERE id = ? AND created_by = ?', (template_id, session['teacher_id'])).fetchone()
            conn.close()
            if not template_row:
                flash('Template not found.', 'error')
                return redirect(url_for('question_paper.index'))
            session['custom_template'] = json.loads(template_row['template_structure'])
            # Pass template_id to the upload_materials page
            return render_template("question_paper/upload_materials.html", template_id=template_id)
        elif step == 'create_template':
            # Handle custom template creation (same as before)
            try:
                template = {
                    'name': request.form.get('template_name'),
                    'institution': request.form.get('institution'),
                    'course': request.form.get('course'),
                    'subject': request.form.get('subject'),
                    'total_marks': int(request.form.get('total_marks')),
                    'duration_minutes': int(request.form.get('duration_minutes')),
                    'sections': []
                }
                section_names = request.form.getlist('section_name[]')
                section_types = request.form.getlist('section_type[]')
                section_questions = request.form.getlist('section_questions[]')
                section_marks = request.form.getlist('section_marks[]')
                for i in range(len(section_names)):
                    if section_names[i] and section_types[i] and section_questions[i] and section_marks[i]:
                        template['sections'].append({
                            'name': section_names[i],
                            'type': section_types[i],
                            'questions': int(section_questions[i]),
                            'marks_per_question': int(section_marks[i])
                        })
                if not template['sections']:
                    flash('Please define at least one section', 'error')
                    return redirect(url_for('question_paper.index'))
                session['custom_template'] = template
                return render_template("question_paper/upload_materials.html")
            except (ValueError, KeyError) as e:
                flash(f'Error creating template: {str(e)}', 'error')
                return redirect(url_for('question_paper.index'))
        elif step == 'generate':
            if 'custom_template' not in session:
                flash('Please create or select a template first', 'error')
                return redirect(url_for('question_paper.index'))
            template = session['custom_template']
            if not template or 'sections' not in template:
                flash('Invalid template configuration', 'error')
                return redirect(url_for('question_paper.index'))
            files = request.files.getlist('pdfs')
            if not files:
                flash('Please upload at least one study material', 'error')
                return redirect(request.url)
            study_materials = save_uploaded_files(files, "study_materials")
            if not study_materials:
                return redirect(request.url)
            
            # Handle optional past papers upload
            past_papers = None
            past_paper_files = request.files.getlist('past_papers')
            if past_paper_files and any(f.filename for f in past_paper_files):
                # Filter out empty files and limit to 5
                valid_past_papers = [f for f in past_paper_files if f.filename][:5]
                if valid_past_papers:
                    past_papers = save_uploaded_files(valid_past_papers, "past_papers")
                    if not past_papers:
                        # If past papers upload fails, continue without them (it's optional)
                        past_papers = None
            difficulty_distribution = {
                'easy': int(request.form.get('easy_percentage', 30)),
                'medium': int(request.form.get('medium_percentage', 40)),
                'hard': int(request.form.get('hard_percentage', 30))
            }
            generator = QuestionPaperGenerator()
            combined_text = []
            for path in study_materials:
                text = generator.extract_text_from_pdf(path)
                if text:
                    combined_text.append(text)
            if not combined_text:
                flash('No extractable text found in PDFs', 'error')
                return redirect(request.url)
            
            # Extract past questions if past papers were uploaded
            past_questions = None
            if past_papers:
                try:
                    past_questions = generator.extract_questions_from_past_papers(past_papers)
                except Exception as e:
                    # If past papers processing fails, continue without them
                    current_app.logger.warning(f"Failed to process past papers: {str(e)}")
                    past_questions = None
            
            result = generator.generate_questions(
                '\n\n'.join(combined_text),
                template,
                difficulty_distribution,
                past_questions
            )
            if 'error' in result:
                flash(result['error'], 'error')
                return redirect(request.url)
            conn = get_db_connection()
            try:
                cursor = conn.cursor()
                # Only save template if template_id is not provided
                template_id = request.form.get('template_id')
                if not template_id:
                    cursor.execute('''
                        INSERT INTO question_paper_templates 
                        (name, institution, course, subject, total_marks, duration_minutes, template_structure, created_by)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        template['name'],
                        template['institution'],
                        template['course'],
                        template['subject'],
                        template['total_marks'],
                        template['duration_minutes'],
                        json.dumps(template),
                        session['teacher_id']
                    ))
                    template_id = cursor.lastrowid
                cursor.execute('''
                    INSERT INTO generated_question_papers 
                    (template_id, title, content, difficulty_distribution, generated_by)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    int(template_id),
                    f"{template.get('subject', 'Question Paper')} - {datetime.now().strftime('%Y-%m-%d')}",
                    json.dumps(result),
                    json.dumps(difficulty_distribution),
                    session['teacher_id']
                ))
                paper_id = cursor.lastrowid
                conn.commit()
                # Clean up uploaded files
                for path in study_materials:
                    try:
                        if os.path.exists(path):
                            os.remove(path)
                    except:
                        pass
                # Clean up past papers if they were uploaded
                if past_papers:
                    for path in past_papers:
                        try:
                            if os.path.exists(path):
                                os.remove(path)
                        except:
                            pass
                session.pop('custom_template', None)
                return redirect(url_for('question_paper.view_paper', paper_id=paper_id))
            except Exception as e:
                conn.rollback()
                flash(f'Error saving question paper: {str(e)}', 'error')
                return redirect(request.url)
            finally:
                conn.close()
    # GET request - show template selection (create or reuse)
    return render_template("question_paper/select_template.html")

@question_paper_bp.route("/paper/<int:paper_id>")
def view_paper(paper_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    try:
        # Get paper details
        paper = conn.execute('''
            SELECT qp.*, t.name as template_name, t.course, t.subject
            FROM generated_question_papers qp
            LEFT JOIN question_paper_templates t ON qp.template_id = t.id
            WHERE qp.id = ?
        ''', (paper_id,)).fetchone()
        
        if not paper:
            flash('Question paper not found', 'error')
            return redirect(url_for('question_paper.index'))
        
        # Check if user has permission to view
        if session.get('user_type') == 'student':
            # Add student-specific permission check here
            pass
        
        # Parse paper data
        paper_data = json.loads(paper['content'])
        difficulty_distribution = json.loads(paper['difficulty_distribution'])
        
        # Convert generated_at string to datetime object
        generated_at = datetime.strptime(paper['generated_at'], '%Y-%m-%d %H:%M:%S') if paper['generated_at'] else None
        
        # Get template if it exists
        template = None
        if paper['template_id']:
            template = conn.execute('''
                SELECT * FROM question_paper_templates WHERE id = ?
            ''', (paper['template_id'],)).fetchone()
        
        # Convert paper dict to include datetime object
        paper_dict = dict(paper)
        paper_dict['generated_at'] = generated_at
        
        return render_template("question_paper/view_paper.html",
                             paper=paper_dict,
                             template=template,
                             sections=paper_data.get('sections', {}),
                             difficulty_distribution=difficulty_distribution,
                             now=datetime.now())
    except Exception as e:
        flash(f'Error loading question paper: {str(e)}', 'error')
        return redirect(url_for('question_paper.index'))
    finally:
        conn.close()

@question_paper_bp.route("/paper/<int:paper_id>/edit", methods=["POST"])
def edit_paper(paper_id):
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return jsonify({'error': 'Unauthorized'}), 403
    
    try:
        edits = request.json
        conn = get_db_connection()
        
        # Get current paper
        paper = conn.execute('SELECT * FROM generated_question_papers WHERE id = ?', (paper_id,)).fetchone()
        if not paper:
            return jsonify({'error': 'Paper not found'}), 404
        
        # Update paper content
        content = json.loads(paper['content'])
        for section, questions in edits.items():
            if section in content['sections']:
                for i, question in enumerate(questions):
                    if i < len(content['sections'][section]):
                        content['sections'][section][i].update(question)
        
        # Save changes
        conn.execute('''
            UPDATE generated_question_papers 
            SET content = ?, is_edited = TRUE, last_edited_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (json.dumps(content), paper_id))
        
        conn.commit()
        return jsonify({'success': True})
        
    except Exception as e:
        if 'conn' in locals():
            conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        if 'conn' in locals():
            conn.close()

@question_paper_bp.route("/paper/<int:paper_id>/download")
def download_paper(paper_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    paper = conn.execute('''
        SELECT qp.*, t.name as template_name, t.course, t.subject
        FROM generated_question_papers qp
        LEFT JOIN question_paper_templates t ON qp.template_id = t.id
        WHERE qp.id = ?
    ''', (paper_id,)).fetchone()
    
    if not paper:
        flash('Question paper not found', 'error')
        return redirect(url_for('question_paper.index'))
    
    paper_data = json.loads(paper['content'])
    
    # Generate PDF using reportlab or similar
    # This is a placeholder - implement actual PDF generation
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    
    response = make_response()
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=question_paper_{paper_id}.pdf'
    
    # Create PDF
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    story = []
    
    # Add header
    header_style = ParagraphStyle(
        'CustomHeader',
        parent=styles['Heading1'],
        fontSize=16,
        spaceAfter=30
    )
    story.append(Paragraph(f"{paper['subject']}", header_style))
    story.append(Paragraph(f"Course: {paper['course']}", styles['Normal']))
    story.append(Paragraph(f"Date: {paper['generated_at']}", styles['Normal']))
    story.append(Spacer(1, 20))
    
    # Add sections
    for section, questions in paper_data['sections'].items():
        story.append(Paragraph(section, styles['Heading2']))
        for q in questions:
            story.append(Paragraph(f"• {q['text']} [{q['marks']} Marks]", styles['Normal']))
        story.append(Spacer(1, 10))
    
    # Build PDF
    doc.build(story)
    pdf = buffer.getvalue()
    buffer.close()
    
    response.data = pdf
    return response