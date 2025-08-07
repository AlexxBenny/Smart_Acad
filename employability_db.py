import sqlite3
import json
from datetime import datetime

def get_db():
    conn = sqlite3.connect('employability.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS assessment_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            scores TEXT NOT NULL,
            overall_score REAL NOT NULL,
            employability_level TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def save_assessment_result(user_id, scores, overall_score, employability_level):
    conn = get_db()
    conn.execute('''
        INSERT INTO assessment_results (user_id, scores, overall_score, employability_level)
        VALUES (?, ?, ?, ?)
    ''', (user_id, json.dumps(scores), overall_score, employability_level))
    conn.commit()
    conn.close()

def get_user_assessments(user_id):
    conn = get_db()
    results = conn.execute('''
        SELECT * FROM assessment_results 
        WHERE user_id = ? 
        ORDER BY timestamp DESC
    ''', (user_id,)).fetchall()
    conn.close()
    return results

def get_assessment_stats(user_id):
    conn = get_db()
    results = conn.execute('''
        SELECT 
            COUNT(*) as total_assessments,
            AVG(overall_score) as avg_score,
            MAX(overall_score) as highest_score,
            MIN(overall_score) as lowest_score,
            MAX(timestamp) as last_assessment
        FROM assessment_results 
        WHERE user_id = ?
    ''', (user_id,)).fetchone()
    conn.close()
    return results 