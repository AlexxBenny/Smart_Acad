from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, Response
import sqlite3
import os
from datetime import date, datetime, timedelta
from ktu_question_generator import question_paper_bp, init_question_paper_db
from ktu_summary_generator import ktu_summary_bp
from chatbot import chatbot_bp
from employability_analyzer import employability_bp
import re

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# Configure database path
db_path = os.path.join(os.path.dirname(__file__), 'db.sqlite3')
app.config['DATABASE'] = db_path

def regex_search(value, pattern):
    """Custom filter for regex search in templates"""
    match = re.search(pattern, value)
    return match.groups() if match else None

app.jinja_env.filters['regex_search'] = regex_search

# Register blueprints
app.register_blueprint(question_paper_bp)
app.register_blueprint(ktu_summary_bp)
app.register_blueprint(chatbot_bp)
app.register_blueprint(employability_bp)

# Initialize database tables
with app.app_context():
    init_question_paper_db(app)

def get_db_connection():
    """Create a connection to the SQLite database"""
    conn = sqlite3.connect(app.config['DATABASE'])
    conn.row_factory = sqlite3.Row
    return conn

# LOGIN ROUTES
@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login route to handle user authentication"""
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM info_user WHERE username = ?', (username,)).fetchone()
        
        if user:
            # Check if the user is a student
            student = conn.execute('SELECT * FROM info_student WHERE user_id = ?', (user['id'],)).fetchone()
            
            # Check if the user is a teacher
            teacher = conn.execute('SELECT * FROM info_teacher WHERE user_id = ?', (user['id'],)).fetchone()
            
            # Simplified password check - replace with proper hashing in production
            if user['password'] == password:
                # Store user info in session
                session['user_id'] = user['id']
                session['username'] = user['username']
                
                # Redirect based on user type
                if student:
                    session['user_type'] = 'student'
                    session['student_usn'] = student['USN']
                    conn.close()
                    return redirect(url_for('student_dashboard'))
                elif teacher:
                    session['user_type'] = 'teacher'
                    session['teacher_id'] = teacher['id']
                    conn.close()
                    return redirect(url_for('teacher_dashboard'))
                else:
                    conn.close()
                    flash('Account not properly configured. Please contact administrator.', 'error')
                    return redirect(url_for('login'))
            else:
                conn.close()
                flash('Invalid username or password. Please try again.', 'error')
                return redirect(url_for('login'))
        
        conn.close()
        flash('Invalid username or password. Please try again.', 'error')
        return redirect(url_for('login'))
    
    return render_template('login.html')

@app.route('/student/dashboard')
def student_dashboard():
    """Student dashboard route"""
    if 'user_id' not in session or session.get('user_type') != 'student':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    # Fetch student details
    student = conn.execute('''
        SELECT s.*, c.section, c.sem, d.name as dept_name 
        FROM info_student s
        JOIN info_class c ON s.class_id_id = c.id
        JOIN info_dept d ON c.dept_id = d.id
        WHERE s.USN = ?
    ''', (session['student_usn'],)).fetchone()
    
    # Fetch student's courses
    courses = conn.execute('''
        SELECT c.name, c.shortname 
        FROM info_studentcourse sc
        JOIN info_course c ON sc.course_id = c.id
        WHERE sc.student_id = ?
    ''', (session['student_usn'],)).fetchall()
    
    conn.close()
    
    return render_template('student_dashboard.html', 
                           student=student, 
                           courses=courses,
                           show_employability=True)

@app.route('/teacher/dashboard')
def teacher_dashboard():
    """Teacher dashboard route"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    # Fetch teacher details
    teacher = conn.execute('''
        SELECT t.*, d.name as dept_name 
        FROM info_teacher t
        JOIN info_dept d ON t.dept_id = d.id
        WHERE t.id = ?
    ''', (session['teacher_id'],)).fetchone()
    
    # Fetch teacher's assigned courses
    assigned_courses = conn.execute('''
        SELECT c.name, c.shortname, cl.section, cl.sem 
        FROM info_assign a
        JOIN info_course c ON a.course_id = c.id
        JOIN info_class cl ON a.class_id_id = cl.id
        WHERE a.teacher_id = ?
    ''', (session['teacher_id'],)).fetchall()
    
    conn.close()
    
    return render_template('teacher_dashboard.html', 
                           teacher=teacher, 
                           assigned_courses=assigned_courses)

@app.route('/logout')
def logout():
    """Logout route to clear session"""
    session.clear()
    return redirect(url_for('index'))

# ATTENDANCE ROUTES
@app.route('/teacher/mark-attendance', methods=['GET', 'POST'])
def mark_attendance():
    """Route for teachers to mark attendance"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Fetch teacher's assigned classes and courses
    assigned_courses = conn.execute('''
        SELECT a.id as assign_id, c.name as course_name, cl.section, cl.sem 
        FROM info_assign a
        JOIN info_course c ON a.course_id = c.id
        JOIN info_class cl ON a.class_id_id = cl.id
        WHERE a.teacher_id = ?
    ''', (session['teacher_id'],)).fetchall()
    
    if request.method == 'POST':
        assign_id = request.form['assign_id']
        attendance_date = request.form['attendance_date']
        
        # Get all students for this class
        students = conn.execute('''
            SELECT s.USN 
            FROM info_student s
            JOIN info_class c ON s.class_id_id = c.id
            JOIN info_assign a ON c.id = a.class_id_id
            WHERE a.id = ?
        ''', (assign_id,)).fetchall()
        
        # Get list of present students (those explicitly checked)
        present_students = request.form.getlist('students')
        
        try:
            # Begin transaction
            conn.execute('BEGIN')
            
            # Create an attendance class record
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO info_attendanceclass (date, status, assign_id) 
                VALUES (?, 1, ?)
            ''', (attendance_date, assign_id))
            attendanceclass_id = cursor.lastrowid
            
            # Fetch course details for the selected assignment
            course_details = conn.execute('''
                SELECT c.id as course_id, cl.section 
                FROM info_assign a
                JOIN info_course c ON a.course_id = c.id
                JOIN info_class cl ON a.class_id_id = cl.id
                WHERE a.id = ?
            ''', (assign_id,)).fetchone()
            
            # Mark attendance for ALL students
            for student in students:
                # Check if this student is in the present_students list
                status = 1 if student['USN'] in present_students else 0
                
                conn.execute('''
                    INSERT INTO info_attendance 
                    (date, status, attendanceclass_id, course_id, student_id) 
                    VALUES (?, ?, ?, ?, ?)
                ''', (attendance_date, status, attendanceclass_id, course_details['course_id'], student['USN']))
            
            # Commit transaction
            conn.commit()
            flash('Attendance marked successfully!', 'success')
            return redirect(url_for('mark_attendance'))
        
        except Exception as e:
            conn.rollback()
            flash(f'Error marking attendance: {str(e)}', 'error')
    
    # Fetch students for the first assigned course/class
    students = []
    if assigned_courses:
        first_assign = assigned_courses[0]
        students = conn.execute('''
            SELECT s.USN, s.name 
            FROM info_student s
            JOIN info_class c ON s.class_id_id = c.id
            WHERE c.section = ? AND c.sem = ?
            ORDER BY s.name
        ''', (first_assign['section'], first_assign['sem'])).fetchall()
    
    conn.close()
    
    return render_template('mark_attendance.html', 
                           assigned_courses=assigned_courses, 
                           students=students)

@app.route('/teacher/view-attendance', methods=['GET', 'POST'])
def view_attendance():
    """Route for teachers to view attendance records"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Fetch teacher's assigned courses
    assigned_courses = conn.execute('''
        SELECT a.id as assign_id, c.name as course_name, cl.section, cl.sem 
        FROM info_assign a
        JOIN info_course c ON a.course_id = c.id
        JOIN info_class cl ON a.class_id_id = cl.id
        WHERE a.teacher_id = ?
    ''', (session['teacher_id'],)).fetchall()
    
    attendance_records = []
    selected_course = None
    
    if request.method == 'POST':
        assign_id = request.form['assign_id']
        
        # Fetch course details
        course_details = conn.execute('''
            SELECT c.id as course_id, c.name as course_name, 
                   cl.section, cl.sem 
            FROM info_assign a
            JOIN info_course c ON a.course_id = c.id
            JOIN info_class cl ON a.class_id_id = cl.id
            WHERE a.id = ?
        ''', (assign_id,)).fetchone()
        
        # Fetch attendance records
        attendance_records = conn.execute('''
            SELECT s.USN, s.name, 
                   COUNT(CASE WHEN a.status = 1 THEN 1 END) as present_days,
                   COUNT(*) as total_days,
                   ROUND(COUNT(CASE WHEN a.status = 1 THEN 1 END) * 100.0 / COUNT(*), 2) as attendance_percentage
            FROM info_student s
            JOIN info_class c ON s.class_id_id = c.id
            LEFT JOIN info_attendance a ON s.USN = a.student_id
            LEFT JOIN info_attendanceclass ac ON a.attendanceclass_id = ac.id
            LEFT JOIN info_assign assign ON ac.assign_id = assign.id
            WHERE c.section = ? AND c.sem = ? AND assign.id = ?
            GROUP BY s.USN, s.name
            ORDER BY s.name
        ''', (course_details['section'], course_details['sem'], assign_id)).fetchall()
        
        selected_course = course_details
    
    conn.close()
    
    return render_template('view_attendance.html', 
                           assigned_courses=assigned_courses, 
                           attendance_records=attendance_records,
                           selected_course=selected_course)

@app.route('/student/attendance')
def student_attendance():
    """Route for students to view their attendance"""
    if 'user_id' not in session or session.get('user_type') != 'student':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Fetch all courses for the student
    all_courses = conn.execute('''
        SELECT c.id as course_id, c.name as course_name
        FROM info_studentcourse sc
        JOIN info_course c ON sc.course_id = c.id
        WHERE sc.student_id = ?
    ''', (session['student_usn'],)).fetchall()
    
    # Fetch student's course-wise attendance
    attendance_records = conn.execute('''
        SELECT c.id as course_id, 
               c.name as course_name, 
               COALESCE(COUNT(CASE WHEN a.status = 1 THEN 1 END), 0) as present_days,
               COALESCE(COUNT(a.id), 0) as total_days,
               COALESCE(ROUND(COUNT(CASE WHEN a.status = 1 THEN 1 END) * 100.0 / COUNT(a.id), 2), 0) as attendance_percentage
        FROM info_course c
        LEFT JOIN info_attendance a ON c.id = a.course_id AND a.student_id = ?
        WHERE c.id IN (
            SELECT course_id 
            FROM info_studentcourse 
            WHERE student_id = ?
        )
        GROUP BY c.id, c.name
        ORDER BY c.name
    ''', (session['student_usn'], session['student_usn'])).fetchall()
    
    # Fetch detailed attendance record
    detailed_attendance = conn.execute('''
        SELECT c.name as course_name, 
               a.date, 
               a.status
        FROM info_attendance a
        JOIN info_course c ON a.course_id = c.id
        WHERE a.student_id = ?
        ORDER BY a.date DESC
    ''', (session['student_usn'],)).fetchall()
    
    conn.close()
    
    return render_template('student_attendance.html', 
                           attendance_records=attendance_records,
                           detailed_attendance=detailed_attendance)

@app.route('/teacher/mark-attendance/get-students', methods=['GET'])
def get_students_for_course():
    """Route to fetch students for a specific course and class"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return jsonify([]), 403
    
    assign_id = request.args.get('assign_id')
    
    if not assign_id:
        return jsonify([]), 400
    
    conn = get_db_connection()
    
    try:
        # Fetch course and class details for the assignment
        course_details = conn.execute('''
            SELECT c.section, c.sem
            FROM info_assign a
            JOIN info_class c ON a.class_id_id = c.id
            WHERE a.id = ?
        ''', (assign_id,)).fetchone()
        
        if not course_details:
            return jsonify([]), 404
        
        # Fetch students for the specific section and semester
        students = conn.execute('''
            SELECT USN, name 
            FROM info_student s
            JOIN info_class c ON s.class_id_id = c.id
            WHERE c.section = ? AND c.sem = ?
            ORDER BY s.name
        ''', (course_details['section'], course_details['sem'])).fetchall()
        
        # Convert to list of dictionaries
        student_list = [dict(student) for student in students]
        
        return jsonify(student_list)
    
    except Exception as e:
        print(f"Error fetching students: {e}")
        return jsonify([]), 500
    finally:
        conn.close()

@app.route('/student/timetable')
def student_timetable():
    """Route for students to view their timetable"""
    if 'user_id' not in session or session.get('user_type') != 'student':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Fetch student's class details
    student_class = conn.execute('''
        SELECT s.class_id_id, c.section, c.sem 
        FROM info_student s
        JOIN info_class c ON s.class_id_id = c.id
        WHERE s.USN = ?
    ''', (session['student_usn'],)).fetchone()
    
    # Fetch timetable for the student's class
    timetable = conn.execute('''
        SELECT 
            at.day, 
            at.period, 
            c.name as course_name, 
            t.name as teacher_name
        FROM info_assigntime at
        JOIN info_assign a ON at.assign_id = a.id
        JOIN info_course c ON a.course_id = c.id
        JOIN info_teacher t ON a.teacher_id = t.id
        JOIN info_class cl ON a.class_id_id = cl.id
        WHERE cl.id = ?
        ORDER BY 
            CASE at.day 
                WHEN 'Monday' THEN 1 
                WHEN 'Tuesday' THEN 2 
                WHEN 'Wednesday' THEN 3 
                WHEN 'Thursday' THEN 4 
                WHEN 'Friday' THEN 5 
                WHEN 'Saturday' THEN 6 
                ELSE 7 
            END,
            at.period
    ''', (student_class['class_id_id'],)).fetchall()
    
    conn.close()
    
    # Organize timetable by day
    organized_timetable = {}
    for row in timetable:
        if row['day'] not in organized_timetable:
            organized_timetable[row['day']] = []
        organized_timetable[row['day']].append(row)
    
    return render_template('student_timetable.html', 
                           timetable=organized_timetable,
                           student_class=student_class)

@app.route('/teacher/timetable')
def teacher_timetable():
    """Route for teachers to view their timetable"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Fetch teacher's timetable
    timetable = conn.execute('''
        SELECT 
            at.day, 
            at.period, 
            c.name as course_name, 
            cl.section,
            cl.sem
        FROM info_assigntime at
        JOIN info_assign a ON at.assign_id = a.id
        JOIN info_course c ON a.course_id = c.id
        JOIN info_class cl ON a.class_id_id = cl.id
        WHERE a.teacher_id = ?
        ORDER BY 
            CASE at.day 
                WHEN 'Monday' THEN 1 
                WHEN 'Tuesday' THEN 2 
                WHEN 'Wednesday' THEN 3 
                WHEN 'Thursday' THEN 4 
                WHEN 'Friday' THEN 5 
                WHEN 'Saturday' THEN 6 
                ELSE 7 
            END,
            at.period
    ''', (session['teacher_id'],)).fetchall()
    
    conn.close()
    
    # Organize timetable by day
    organized_timetable = {}
    for row in timetable:
        if row['day'] not in organized_timetable:
            organized_timetable[row['day']] = []
        organized_timetable[row['day']].append(row)
    
    return render_template('teacher_timetable.html', 
                           timetable=organized_timetable)

@app.route('/teacher/enter-marks', methods=['GET', 'POST'])
def enter_marks():
    """Route for teachers to enter marks"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Fetch teacher's assigned courses
    assigned_courses = conn.execute('''
        SELECT a.id as assign_id, c.name as course_name, 
               cl.section, cl.sem 
        FROM info_assign a
        JOIN info_course c ON a.course_id = c.id
        JOIN info_class cl ON a.class_id_id = cl.id
        WHERE a.teacher_id = ?
    ''', (session['teacher_id'],)).fetchall()
    
    students = []
    selected_course = None
    
    if request.method == 'POST':
        assign_id = request.form['assign_id']
        marks_name = request.form['marks_name']
        
        # Fetch students for this course/class
        students = conn.execute('''
            SELECT s.USN, s.name 
            FROM info_student s
            JOIN info_class c ON s.class_id_id = c.id
            JOIN info_assign a ON c.id = a.class_id_id
            WHERE a.id = ?
            ORDER BY s.name
        ''', (assign_id,)).fetchall()
        
        # Get course details
        selected_course = conn.execute('''
            SELECT c.id as course_id, c.name as course_name, 
                   cl.section, cl.sem 
            FROM info_assign a
            JOIN info_course c ON a.course_id = c.id
            JOIN info_class cl ON a.class_id_id = cl.id
            WHERE a.id = ?
        ''', (assign_id,)).fetchone()
        
    conn.close()
    
    return render_template('enter_marks.html', 
                           assigned_courses=assigned_courses, 
                           students=students,
                           selected_course=selected_course)

@app.route('/teacher/get-existing-marks', methods=['GET'])
def get_existing_marks():
    """Route to fetch existing marks for a specific course and assessment type"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return jsonify([]), 403
    
    assign_id = request.args.get('assign_id')
    marks_name = request.args.get('marks_name')
    
    if not assign_id or not marks_name:
        return jsonify([]), 400
    
    conn = get_db_connection()
    
    try:
        # Get the course_id for this assignment
        course_details = conn.execute('''
            SELECT c.id as course_id
            FROM info_assign a
            JOIN info_course c ON a.course_id = c.id
            WHERE a.id = ?
        ''', (assign_id,)).fetchone()
        
        if not course_details:
            return jsonify([]), 404
        
        # Fetch existing marks for this course and assessment type
        existing_marks = conn.execute('''
            SELECT s.USN as student_usn, 
                   m.marks1 as marks
            FROM info_student s
            JOIN info_class c ON s.class_id_id = c.id
            JOIN info_assign a ON c.id = a.class_id_id
            LEFT JOIN info_studentcourse sc ON (sc.student_id = s.USN AND sc.course_id = ?)
            LEFT JOIN info_marks m ON (m.studentcourse_id = sc.id AND m.name = ?)
            WHERE a.id = ?
            ORDER BY s.name
        ''', (course_details['course_id'], marks_name, assign_id)).fetchall()
        
        # Convert to list of dictionaries
        marks_list = [dict(mark) for mark in existing_marks]
        
        return jsonify(marks_list)
    
    except Exception as e:
        print(f"Error fetching existing marks: {e}")
        return jsonify([]), 500
    finally:
        conn.close()

@app.route('/teacher/save-marks', methods=['POST'])
def save_marks():
    """Route to save marks for students with enhanced debugging"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    try:
        course_id = request.form.get('course_id')
        marks_name = request.form.get('marks_name')
        
        print(f"Saving marks for course_id: {course_id}, marks_name: {marks_name}")
        print(f"Form data received: {request.form}")
        
        if not course_id or not marks_name:
            flash('Missing required fields', 'error')
            return redirect(url_for('enter_marks'))
        
        # Begin transaction
        conn.execute('BEGIN')
        
        # Process each student's marks
        for key, value in request.form.items():
            if key.startswith('marks_'):
                usn = key.split('_')[1]
                marks = value or '0'
                
                print(f"Processing marks for USN: {usn}, Marks: {marks}")
                
                # Find or create studentcourse record
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR IGNORE INTO info_studentcourse (course_id, student_id)
                    VALUES (?, ?)
                ''', (course_id, usn))
                
                # Get studentcourse_id
                studentcourse = conn.execute('''
                    SELECT id FROM info_studentcourse 
                    WHERE course_id = ? AND student_id = ?
                ''', (course_id, usn)).fetchone()
                
                if not studentcourse:
                    raise ValueError(f"Failed to get studentcourse_id for USN: {usn}")
                
                # Insert or update marks
                cursor.execute('''
                    INSERT OR REPLACE INTO info_marks 
                    (name, marks1, studentcourse_id) 
                    VALUES (?, ?, ?)
                ''', (marks_name, marks, studentcourse['id']))
                
                print(f"Successfully saved marks for USN {usn}")
        
        # Commit transaction
        conn.commit()
        flash('Marks saved successfully!', 'success')
        return redirect(url_for('enter_marks'))
    
    except Exception as e:
        conn.rollback()
        print(f"Error saving marks: {str(e)}")
        flash(f'Error saving marks: {str(e)}', 'error')
        return redirect(url_for('enter_marks'))
    finally:
        conn.close()

@app.route('/student/marks')
def student_marks():
    """Route for students to view their marks"""
    if 'user_id' not in session or session.get('user_type') != 'student':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    
    # Fetch all courses for the student
    all_courses = conn.execute('''
        SELECT DISTINCT c.id as course_id, c.name as course_name
        FROM info_studentcourse sc
        JOIN info_course c ON sc.course_id = c.id
        WHERE sc.student_id = ?
    ''', (session['student_usn'],)).fetchall()
    
    # Fetch student's marks for all courses
    marks_records = conn.execute('''
        SELECT 
            c.id as course_id,
            c.name as course_name, 
            m.name as marks_name,
            m.marks1 as marks
        FROM info_studentcourse sc
        JOIN info_course c ON sc.course_id = c.id
        JOIN info_marks m ON sc.id = m.studentcourse_id
        WHERE sc.student_id = ?
        ORDER BY c.name, m.name
    ''', (session['student_usn'],)).fetchall()
    
    conn.close()
    
    return render_template('student_marks.html', 
                           marks_records=marks_records,
                           all_courses=all_courses)

@app.route('/teacher/get-course-details', methods=['GET'])
def get_course_details():
    """Route to fetch course details for a specific assignment"""
    if 'user_id' not in session or session.get('user_type') != 'teacher':
        return jsonify({}), 403
    
    assign_id = request.args.get('assign_id')
    
    if not assign_id:
        return jsonify({}), 400
    
    conn = get_db_connection()
    
    try:
        # Fetch course details for this assignment
        course_details = conn.execute('''
            SELECT c.id as course_id
            FROM info_assign a
            JOIN info_course c ON a.course_id = c.id
            WHERE a.id = ?
        ''', (assign_id,)).fetchone()
        
        if not course_details:
            return jsonify({}), 404
        
        return jsonify(dict(course_details))
    
    except Exception as e:
        print(f"Error fetching course details: {e}")
        return jsonify({}), 500
    finally:
        conn.close()

def init_db():
    conn = get_db_connection()
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
    conn.close()

@app.context_processor
def inject_now():
    return {'now': datetime.now()}

if __name__ == '__main__':
    # Initialize database tables
    init_db()
    app.run(debug=True)