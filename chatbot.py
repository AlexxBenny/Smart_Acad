from flask import Blueprint, request, jsonify, session, Response, render_template, redirect, url_for
import sqlite3
from datetime import date, datetime, timedelta
import json
import ollama

# Create blueprint
chatbot_bp = Blueprint('chatbot', __name__)

def get_db_connection():
    """Create a connection to the SQLite database"""
    conn = sqlite3.connect('db.sqlite3')
    conn.row_factory = sqlite3.Row
    return conn

@chatbot_bp.route('/chat')
def chat_interface():
    """Chat interface route"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('chat.html')

@chatbot_bp.route('/chat/send', methods=['POST'])
def chat_send():
    """Handle chat messages with AI integration and database access"""
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    user_message = request.json.get('message', '').strip()
    if not user_message:
        return jsonify({'error': 'No message provided'}), 400
    
    try:
        conn = get_db_connection()
        
        # Get user context from session and database
        user_context = {
            'id': session.get('user_id'),
            'username': session.get('username'),
            'type': session.get('user_type'),
            'name': None,
            'usn': None,
            'teacher_id': None,
            'dept': None,
            'semester': None,
            'section': None
        }
        
        if user_context['type'] == 'student':
            # Get student details
            student = conn.execute('''
                SELECT s.*, c.section, c.sem, d.name as dept_name 
                FROM info_student s
                JOIN info_class c ON s.class_id_id = c.id
                JOIN info_dept d ON c.dept_id = d.id
                WHERE s.USN = ?
            ''', (session['student_usn'],)).fetchone()
            
            if student:
                user_context.update({
                    'usn': student['USN'],
                    'name': student['name'],
                    'dept': student['dept_name'],
                    'semester': student['sem'],
                    'section': student['section']
                })
                
        elif user_context['type'] == 'teacher':
            # Get teacher details
            teacher = conn.execute('''
                SELECT t.*, d.name as dept_name 
                FROM info_teacher t
                JOIN info_dept d ON t.dept_id = d.id
                WHERE t.id = ?
            ''', (session['teacher_id'],)).fetchone()
            
            if teacher:
                user_context.update({
                    'teacher_id': teacher['id'],
                    'name': teacher['name'],
                    'dept': teacher['dept_name']
                })

        if not user_context['name']:
            conn.close()
            return jsonify({'error': 'Could not retrieve user information'}), 400

        # Initialize conversation history if not exists
        if 'chat_history' not in session:
            session['chat_history'] = []
        
        # Add user message to history
        session['chat_history'].append({'role': 'user', 'content': user_message})
        
        # Keep only last 10 messages for context
        if len(session['chat_history']) > 10:
            session['chat_history'] = session['chat_history'][-10:]

        # Analysis prompt without conversation history for academic queries
        analysis_prompt = f"""
            Analyze the following user query in detail:

            Query: {user_message}
            User Type: {user_context['type']}

            Determine:
            1. Query type (timetable/attendance/marks/general)
            2. Request type (query/modify)
            3. For marks query:
            - Is a specific course/subject mentioned? Extract exact course name
            - Assessment type if mentioned
            4. For marks modification:
            - Student USN (format: CS## or similar)
            - Marks value (numeric)
            - Course/Subject name
            - Assessment type (test/quiz/assignment/internal/etc)
            5. For attendance modification:
                - Student USN
                - Status (present/absent)
                - Course name
                - Date information:
                    * For "today" → date_type: "today"
                    * For "yesterday" → date_type: "yesterday"
                    * For specific dates → specific_date in YYYY-MM-DD format
                    * If no date mentioned → date_type: "today"
            6. For timetable queries:
            - Is a specific day mentioned? Extract exact day name
            - Check for phrases like "timetable for [day]" or "[day] timetable"
            - Normalize day names (e.g., "monday", "Monday", "mon" → "Monday")

            Consider these examples for timetable queries:
            - "monday timetable" → day: "Monday"
            - "timetable for monday" → day: "Monday"
            - "show me monday's timetable" → day: "Monday"
            - "what classes do I have on monday" → day: "Monday"
            - "show my timetable" → day: null (show full week)

            Respond in JSON format with keys:
            - query_type: string
            - request_type: string
            - course_name: string or null (exact course name if mentioned)
            - assessment_type: string or null
            - student_usn: string or null
            - marks_value: number or null
            - attendance_status: string or null
            - date_type: string or null
            - specific_date: string or null
            - day: string or null (capitalized day name if mentioned)
            """

        # Get query analysis
        analysis_response = ollama.generate(
            model='llama3',
            prompt=analysis_prompt,
            format='json'
        )
        analysis = json.loads(analysis_response['response'])

        # Handle different types of queries
        if analysis.get("query_type") == "marks" and analysis.get("request_type") == "modify":
            if user_context['type'] != 'teacher':
                response = "Only teachers can enter marks."
            else:
                response = handle_marks_query(user_context, user_message, analysis)
        elif analysis.get("query_type") == "attendance" and analysis.get("request_type") == "modify":
            if user_context['type'] != 'teacher':
                response = "Only teachers can mark attendance."
            else:
                response = handle_attendance_query(user_context, user_message, analysis)
        elif analysis.get("query_type") == "timetable":
            response = handle_timetable_query(user_context, user_message, analysis)
        elif analysis.get("query_type") == "attendance" and analysis.get("request_type") == "query":
            response = handle_attendance_query(user_context, user_message, analysis)
        elif analysis.get("query_type") == "marks" and analysis.get("request_type") == "query":
            response = handle_marks_query(user_context, user_message, analysis)
        else:
            # Only include conversation history for general queries
            system_prompt = f"""
            You are an AI Academic Assistant for a university system. 
            Current User: {user_context['name']} ({user_context['type'].title()})
            Department: {user_context.get('dept', 'N/A')}
            {f"Semester: {user_context['semester']}, Section: {user_context['section']}" if user_context['type'] == 'student' else ""}
            
            Previous conversation:
            {format_chat_history(session['chat_history'][:-1])}
            
            Provide responses based only on actual information available.
            Do not generate fictional data.
            Keep responses concise and relevant to academic queries.
            Use emojis appropriately to enhance readability.
            """
            
            messages = [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_message}
            ]
            
            ollama_response = ollama.chat(
                model='llama3',
                messages=messages
            )
            response = ollama_response['message']['content']

        # Add response to history
        session['chat_history'].append({'role': 'assistant', 'content': response})
        session.modified = True

        conn.close()
        return jsonify({'response': response})

    except json.JSONDecodeError:
        if 'conn' in locals():
            conn.close()
        return jsonify({'error': 'Failed to parse AI response'}), 500
    except Exception as e:
        if 'conn' in locals():
            conn.close()
        return jsonify({'error': str(e)}), 500

@chatbot_bp.route('/chat/stream')
def chat_stream():
    """Server-Sent Events route for streaming chat responses"""
    if 'user_id' not in session:
        return Response("Not authenticated", status=401)
    
    def generate():
        conn = get_db_connection()
        
        # Get user context
        user_context = ""
        if session.get('user_type') == 'student':
            student = conn.execute('SELECT * FROM info_student WHERE USN = ?', (session['student_usn'],)).fetchone()
            if student:
                user_context = f"Student {student['name']} (USN: {student['USN']})"
        elif session.get('user_type') == 'teacher':
            teacher = conn.execute('SELECT * FROM info_teacher WHERE id = ?', (session['teacher_id'],)).fetchone()
            if teacher:
                user_context = f"Teacher {teacher['name']}"
        
        conn.close()
        
        user_message = request.args.get('message', '').strip()
        if not user_message:
            yield "data: No message provided\n\n"
            return
        
        system_prompt = f"""
        You are an AI Academic Assistant for {user_context}.
        For academic queries, provide accurate information.
        For general questions, be helpful and informative.
        Format responses clearly with appropriate emojis.
        """
        
        messages = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_message}
        ]
        
        stream = ollama.chat(
            model='llama3',
            messages=messages,
            stream=True
        )
        
        for chunk in stream:
            if 'message' in chunk and 'content' in chunk['message']:
                yield f"data: {chunk['message']['content']}\n\n"
    
    return Response(generate(), mimetype='text/event-stream')

def format_chat_history(history):
    """Format chat history for prompt context"""
    if not history:
        return "No previous conversation."
    
    formatted = []
    for msg in history:
        role = "User" if msg['role'] == 'user' else "Assistant"
        formatted.append(f"{role}: {msg['content']}")
    
    # Only include last 5 messages if history is too long
    if len(formatted) > 5:
        formatted = formatted[-5:]
        formatted.insert(0, "... (earlier conversation omitted)")
    
    return "\n".join(formatted)

# Helper functions for query handling
def handle_attendance_query(user_details, user_message, analysis):
    """Handle attendance-related queries"""
    conn = get_db_connection()
    response = ""
    
    try:
        if analysis.get('request_type') == 'modify':
            if user_details['type'] != 'teacher':
                return "Only teachers can mark attendance."

            student_usn = analysis.get('student_usn')
            status = 1 if analysis.get('attendance_status') == 'present' else 0
            course_name = analysis.get('course_name')
            
            # Fixed date handling
            current_date = date.today()
            if analysis.get('date_type') == 'yesterday':
                date_str = (current_date - timedelta(days=1)).strftime('%Y-%m-%d')
            elif analysis.get('date_type') == 'today':
                date_str = current_date.strftime('%Y-%m-%d')
            elif analysis.get('specific_date'):
                try:
                    # Parse and validate the specific date
                    specific_date = datetime.strptime(analysis.get('specific_date'), '%Y-%m-%d').date()
                    date_str = specific_date.strftime('%Y-%m-%d')
                except ValueError:
                    return "Invalid date format. Please use YYYY-MM-DD format."
            else:
                # If no date specified, use current date
                date_str = current_date.strftime('%Y-%m-%d')

            if not all([student_usn, date_str, course_name]):
                return "Please provide student USN, date, and course information."

            try:
                # Get course ID and verify teacher's assignment
                course_info = conn.execute('''
                    SELECT a.id as assign_id, c.id as course_id
                    FROM info_assign a
                    JOIN info_course c ON a.course_id = c.id
                    WHERE a.teacher_id = ? AND c.name = ?
                ''', (user_details['teacher_id'], course_name)).fetchone()

                if not course_info:
                    return f"You are not assigned to the course: {course_name}"

                # Verify student exists
                student = conn.execute('''
                    SELECT 1 FROM info_student WHERE USN = ?
                ''', (student_usn,)).fetchone()

                if not student:
                    return f"Student with USN {student_usn} not found."

                # Start transaction
                conn.execute('BEGIN')

                # Check/Create attendance class
                attendance_class = conn.execute('''
                    SELECT id FROM info_attendanceclass
                    WHERE date = ? AND assign_id = ?
                ''', (date_str, course_info['assign_id'])).fetchone()

                if not attendance_class:
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT INTO info_attendanceclass (date, status, assign_id)
                        VALUES (?, 1, ?)
                    ''', (date_str, course_info['assign_id']))
                    attendanceclass_id = cursor.lastrowid
                else:
                    attendanceclass_id = attendance_class['id']

                # Update or insert attendance record
                existing = conn.execute('''
                    SELECT id FROM info_attendance
                    WHERE date = ? AND student_id = ? AND course_id = ?
                ''', (date_str, student_usn, course_info['course_id'])).fetchone()

                if existing:
                    conn.execute('''
                        UPDATE info_attendance
                        SET status = ?, attendanceclass_id = ?
                        WHERE id = ?
                    ''', (status, attendanceclass_id, existing['id']))
                else:
                    conn.execute('''
                        INSERT INTO info_attendance
                        (date, status, attendanceclass_id, course_id, student_id)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (date_str, status, attendanceclass_id, course_info['course_id'], student_usn))

                conn.commit()
                status_word = "present" if status == 1 else "absent"
                formatted_date = datetime.strptime(date_str, '%Y-%m-%d').strftime('%d-%m-%Y')
                response = f"✅ Successfully marked {student_usn} as {status_word} for {course_name} on {formatted_date}"

            except Exception as e:
                conn.rollback()
                response = f"❌ Error marking attendance: {str(e)}"
                
        else:
            if user_details['type'] == "student":
                course_name = analysis.get('course_name')
                
                if course_name:
                    # Get attendance for specific course
                    attendance_records = conn.execute('''
                        SELECT c.name as course_name,
                               COUNT(CASE WHEN a.status = 1 THEN 1 END) as present_days,
                               COUNT(*) as total_days,
                               ROUND(COUNT(CASE WHEN a.status = 1 THEN 1 END) * 100.0 / COUNT(*), 2) as attendance_percentage
                        FROM info_course c
                        LEFT JOIN info_attendance a ON c.id = a.course_id AND a.student_id = ?
                        WHERE c.name = ? AND c.id IN (
                            SELECT course_id 
                            FROM info_studentcourse 
                            WHERE student_id = ?
                        )
                        GROUP BY c.id, c.name
                    ''', (user_details['usn'], course_name, user_details['usn'])).fetchall()

                    if attendance_records:
                        response = f"📊 Your attendance for {course_name}:\n\n"
                        for record in attendance_records:
                            response += f"✅ Present: {record['present_days']} days\n"
                            response += f"📅 Total Classes: {record['total_days']} days\n"
                            response += f"📈 Attendance: {record['attendance_percentage']}%\n"
                            
                            # Get detailed attendance records
                            detailed = conn.execute('''
                                SELECT a.date, a.status
                                FROM info_attendance a
                                JOIN info_course c ON a.course_id = c.id
                                WHERE a.student_id = ? AND c.name = ?
                                ORDER BY a.date DESC
                            ''', (user_details['usn'], course_name)).fetchall()
                            
                            if detailed:
                                response += "\nDetailed attendance:\n"
                                for detail in detailed:
                                    status = "Present ✅" if detail['status'] == 1 else "Absent ❌"
                                    formatted_date = datetime.strptime(detail['date'], '%Y-%m-%d').strftime('%d-%m-%Y')
                                    response += f"📅 {formatted_date}: {status}\n"
                    else:
                        response = f"No attendance records found for {course_name}."
                else:
                    # Show all courses attendance if no specific course mentioned
                    attendance_records = conn.execute('''
                        SELECT c.name as course_name,
                               COUNT(CASE WHEN a.status = 1 THEN 1 END) as present_days,
                               COUNT(*) as total_days,
                               ROUND(COUNT(CASE WHEN a.status = 1 THEN 1 END) * 100.0 / COUNT(*), 2) as attendance_percentage
                        FROM info_course c
                        LEFT JOIN info_attendance a ON c.id = a.course_id AND a.student_id = ?
                        WHERE c.id IN (
                            SELECT course_id 
                            FROM info_studentcourse 
                            WHERE student_id = ?
                        )
                        GROUP BY c.id, c.name
                        ORDER BY c.name
                    ''', (user_details['usn'], user_details['usn'])).fetchall()

                    if attendance_records:
                        response = "📊 Your attendance summary:\n\n"
                        for record in attendance_records:
                            response += f"📚 {record['course_name']}:\n"
                            response += f"  ✅ Present: {record['present_days']} days\n"
                            response += f"  📅 Total Classes: {record['total_days']} days\n"
                            response += f"  📈 Attendance: {record['attendance_percentage']}%\n\n"
                    else:
                        response = "No attendance records found."

            elif user_details['type'] == "teacher":
                student_usn = analysis.get('student_usn')
                course_name = analysis.get('course_name')

                if student_usn and course_name:
                    attendance = conn.execute('''
                        SELECT a.date, a.status,
                               s.name as student_name
                        FROM info_attendance a
                        JOIN info_student s ON a.student_id = s.USN
                        JOIN info_course c ON a.course_id = c.id
                        WHERE c.name = ? AND s.USN = ?
                        ORDER BY a.date DESC
                    ''', (course_name, student_usn)).fetchall()

                    if attendance:
                        response = f"📊 Attendance record for {student_usn} in {course_name}:\n\n"
                        for record in attendance:
                            status = "Present ✅" if record['status'] == 1 else "Absent ❌"
                            formatted_date = datetime.strptime(record['date'], '%Y-%m-%d').strftime('%d-%m-%Y')
                            response += f"📅 {formatted_date}: {status}\n"
                    else:
                        response = f"No attendance records found for {student_usn} in {course_name}."
                else:
                    response = "Please specify both student USN and course name to view attendance."

    finally:
        conn.close()
    
    return response

def handle_marks_query(user_details, user_message, analysis):
    """Handle marks-related queries"""
    conn = get_db_connection()
    response = ""
    
    try:
        if analysis.get('request_type') == 'modify':
            if user_details['type'] != 'teacher':
                return "Only teachers can enter marks."

            student_usn = analysis.get('student_usn')
            marks_value = analysis.get('marks_value')
            course_name = analysis.get('course_name')
            assessment_type = analysis.get('assessment_type')

            if not all([student_usn, marks_value is not None, course_name, assessment_type]):
                return "Please provide student USN, marks value, course name, and assessment type."

            try:
                # Verify teacher's assignment to the course
                course_info = conn.execute('''
                    SELECT a.id as assign_id, c.id as course_id
                    FROM info_assign a
                    JOIN info_course c ON a.course_id = c.id
                    WHERE a.teacher_id = ? AND c.name = ?
                ''', (user_details['teacher_id'], course_name)).fetchone()

                if not course_info:
                    return f"You are not assigned to teach {course_name}."

                # Verify student exists
                student = conn.execute('''
                    SELECT 1 FROM info_student WHERE USN = ?
                ''', (student_usn,)).fetchone()

                if not student:
                    return f"Student with USN {student_usn} not found."

                # Start transaction
                conn.execute('BEGIN')

                # Get or create studentcourse record
                studentcourse = conn.execute('''
                    SELECT id FROM info_studentcourse 
                    WHERE student_id = ? AND course_id = ?
                ''', (student_usn, course_info['course_id'])).fetchone()

                if not studentcourse:
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT INTO info_studentcourse (student_id, course_id)
                        VALUES (?, ?)
                    ''', (student_usn, course_info['course_id']))
                    studentcourse_id = cursor.lastrowid
                else:
                    studentcourse_id = studentcourse['id']

                # Update or insert marks
                existing_marks = conn.execute('''
                    SELECT id FROM info_marks 
                    WHERE studentcourse_id = ? AND name = ?
                ''', (studentcourse_id, assessment_type)).fetchone()

                if existing_marks:
                    conn.execute('''
                        UPDATE info_marks 
                        SET marks1 = ? 
                        WHERE id = ?
                    ''', (marks_value, existing_marks['id']))
                else:
                    conn.execute('''
                        INSERT INTO info_marks (name, marks1, studentcourse_id)
                        VALUES (?, ?, ?)
                    ''', (assessment_type, marks_value, studentcourse_id))

                conn.commit()
                response = f"✅ Successfully recorded {marks_value} marks for {student_usn} in {course_name} ({assessment_type})"

            except Exception as e:
                conn.rollback()
                response = f"❌ Error recording marks: {str(e)}"
                
        else:
            if user_details['type'] == "student":
                course_name = analysis.get('course_name')
                
                if course_name:
                    # Query for specific course marks
                    marks_records = conn.execute('''
                        SELECT c.name as course_name, 
                               m.name as assessment_type,
                               m.marks1 as marks
                        FROM info_marks m
                        JOIN info_studentcourse sc ON m.studentcourse_id = sc.id
                        JOIN info_course c ON sc.course_id = c.id
                        WHERE sc.student_id = ? AND c.name = ?
                        ORDER BY m.name
                    ''', (user_details['usn'], course_name)).fetchall()

                    if marks_records:
                        response = f"📊 Your marks for {course_name}:\n\n"
                        for record in marks_records:
                            response += f"📝 {record['assessment_type']}: {record['marks']}\n"
                    else:
                        response = f"No marks found for {course_name}."
                else:
                    # Show all marks if no specific course mentioned
                    marks_records = conn.execute('''
                        SELECT c.name as course_name, 
                               m.name as assessment_type,
                               m.marks1 as marks
                        FROM info_marks m
                        JOIN info_studentcourse sc ON m.studentcourse_id = sc.id
                        JOIN info_course c ON sc.course_id = c.id
                        WHERE sc.student_id = ?
                        ORDER BY c.name, m.name
                    ''', (user_details['usn'],)).fetchall()

                    if marks_records:
                        response = "📊 Your marks:\n\n"
                        current_course = None
                        for record in marks_records:
                            if record['course_name'] != current_course:
                                response += f"📚 {record['course_name']}:\n"
                                current_course = record['course_name']
                            response += f"  📝 {record['assessment_type']}: {record['marks']}\n"
                    else:
                        response = "No marks records found."

            elif user_details['type'] == "teacher":
                student_usn = analysis.get('student_usn')
                course_name = analysis.get('course_name')

                if student_usn and course_name:
                    marks = conn.execute('''
                        SELECT m.name as assessment_type, 
                               m.marks1 as marks
                        FROM info_marks m
                        JOIN info_studentcourse sc ON m.studentcourse_id = sc.id
                        JOIN info_course c ON sc.course_id = c.id
                        WHERE sc.student_id = ? AND c.name = ?
                        ORDER BY m.name
                    ''', (student_usn, course_name)).fetchall()

                    if marks:
                        response = f"📊 Marks for {student_usn} in {course_name}:\n\n"
                        for record in marks:
                            response += f"📝 {record['assessment_type']}: {record['marks']}\n"
                    else:
                        response = f"No marks found for {student_usn} in {course_name}."
                else:
                    response = "Please specify both student USN and course name to view marks."

    finally:
        conn.close()
    
    return response

def handle_timetable_query(user_details, user_message, analysis):
    """Handle timetable-related queries"""
    conn = get_db_connection()
    response = ""
    
    try:
        # Get day from AI analysis
        day = analysis.get('day')
        if day:
            day = day.capitalize()
        
        if user_details['type'] == "student":
            # First get student's class details
            student_class = conn.execute('''
                SELECT s.class_id_id, c.section, c.sem 
                FROM info_student s
                JOIN info_class c ON s.class_id_id = c.id
                WHERE s.USN = ?
            ''', (user_details['usn'],)).fetchone()
            
            if not student_class:
                return "Could not find your class information."
            
            timetable = conn.execute('''
                SELECT at.day, at.period, c.name as course_name, t.name as teacher_name
                FROM info_assigntime at
                JOIN info_assign a ON at.assign_id = a.id
                JOIN info_course c ON a.course_id = c.id
                JOIN info_teacher t ON a.teacher_id = t.id
                JOIN info_class cl ON a.class_id_id = cl.id
                JOIN info_studentcourse sc ON (sc.course_id = c.id AND sc.student_id = ?)
                WHERE cl.id = ?
                AND cl.section = ?
                AND cl.sem = ?
                AND (at.day = ? OR ? IS NULL)
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
            ''', (user_details['usn'], student_class['class_id_id'], 
                  student_class['section'], student_class['sem'], 
                  day, day)).fetchall()
            
            if timetable:
                if day:
                    response = f"📅 Your timetable for {day}:\n\n"
                else:
                    response = "📅 Your weekly timetable:\n\n"
                
                current_day = None
                for row in timetable:
                    if row['day'] != current_day:
                        response += f"📌 {row['day']}:\n"
                        current_day = row['day']
                    response += f"⏰ Period {row['period']}: {row['course_name']} (Prof. {row['teacher_name']})\n"
            else:
                response = "No timetable found for the specified day." if day else "No timetable data found."
            
        elif user_details['type'] == "teacher":
            query = '''
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
            '''
            params = [user_details['teacher_id']]
            
            if day:
                query += ' AND at.day = ?'
                params.append(day)
            
            query += '''
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
            '''
            
            timetable = conn.execute(query, params).fetchall()
            
            if timetable:
                if day:
                    response = f"📅 Your timetable for {day}:\n\n"
                else:
                    response = "📅 Your weekly timetable:\n\n"
                
                current_day = None
                for row in timetable:
                    if row['day'] != current_day:
                        response += f"📌 {row['day']}:\n"
                        current_day = row['day']
                    response += f"⏰ Period {row['period']}: {row['course_name']} for Section {row['section']} (Sem {row['sem']})\n"
            else:
                if day:
                    response = f"No classes scheduled for {day}."
                else:
                    assignments = conn.execute('''
                        SELECT COUNT(*) as count 
                        FROM info_assign 
                        WHERE teacher_id = ?
                    ''', (user_details['teacher_id'],)).fetchone()
                    
                    if assignments['count'] == 0:
                        response = "No courses are currently assigned to you."
                    else:
                        response = "No timetable entries found for your assigned courses."
    finally:
        conn.close()
    
    return response 