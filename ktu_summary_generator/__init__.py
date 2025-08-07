# ktu_summary_generator/__init__.py

import os
import fitz  # PyMuPDF for PDF processing
import re
import ollama
import hashlib
from flask import Blueprint, request, render_template, redirect, current_app, flash, session, url_for
from werkzeug.utils import secure_filename
from datetime import datetime
import concurrent.futures
import nltk
from nltk.tokenize import sent_tokenize
from functools import lru_cache
import time

# Download required NLTK data
try:
    nltk.download('punkt', quiet=True)
except:
    pass

# Create the blueprint
ktu_summary_bp = Blueprint('ktu_summary', __name__, 
                         template_folder='templates',
                         static_folder='static',
                         url_prefix='/ktu-summary')

class KTUSummaryGenerator:
    def __init__(self):
        """Initialize the summary generator with optimized parameters for speed"""
        self.max_chunk_size = 800      # Reduced from 1000
        self.min_chunk_size = 300      # Increased from 200
        self.max_workers = 4           # Reduced from 6
        self.request_timeout = 15      # Reduced from 20
        self.cache = {}               # Simple in-memory cache
        self.max_chunks = 8           # Reduced from 12
        self.max_summary_length = 400  # Reduced from 500
        self.max_pages = 20           # Reduced from 30

    def _clean_text(self, text):
        """Enhanced text cleaning to remove noise"""
        # Remove headers and footers
        text = re.sub(r'^\s*Page \d+.*$', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*\d+\s*$', '', text, flags=re.MULTILINE)
        
        # Remove references and citations
        text = re.sub(r'\[\d+\]', '', text)
        text = re.sub(r'\(\d{4}\)', '', text)
        
        # Remove repetitive formatting artifacts
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'([.!?])\s*\1+', r'\1', text)
        
        # Remove common PDF artifacts
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\xff]', '', text)
        
        return text.strip()

    def _is_reference_page(self, text):
        """Check if the page appears to be a reference page"""
        reference_indicators = [
            'references',
            'bibliography',
            'works cited',
            r'^\s*\[\d+\]',
            r'^\s*\d+\.\s+[A-Z]'
        ]
        
        text_lower = text.lower()
        return any(re.search(pattern, text_lower) for pattern in reference_indicators)

    def extract_text_from_pdf(self, pdf_path):
        """Extracts text from PDF with optimized and simplified processing"""
        try:
            with fitz.open(pdf_path) as doc:
                max_pages = min(self.max_pages, len(doc))
                
                full_text = []
                for page_num in range(max_pages):
                    text = doc[page_num].get_text("text")
                    text = self._clean_text(text)
                    
                    if text and not self._is_reference_page(text):
                        # Limit individual page text length
                        if len(text) > 5000:  # Limit per page
                            text = text[:5000]
                        full_text.append(text)
                
                return ' '.join(full_text)
        except Exception as e:
            print(f"PDF extraction error: {e}")
            return ""

    def _preprocess_text(self, text):
        """Simplified text preprocessing optimized for speed"""
        # Quick text cleaning
        text = re.sub(r'\s+', ' ', text)
        
        # Simple sentence-based chunking
        sentences = sent_tokenize(text)
        chunks = []
        current_chunk = []
        current_length = 0
        
        for sentence in sentences:
            sent_len = len(sentence)
            
            if current_length + sent_len > self.max_chunk_size and current_length >= self.min_chunk_size:
                chunks.append(' '.join(current_chunk))
                current_chunk = [sentence]
                current_length = sent_len
            else:
                current_chunk.append(sentence)
                current_length += sent_len
        
        if current_chunk:
            chunks.append(' '.join(current_chunk))
        
        # Limit chunks and ensure even distribution
        if len(chunks) > self.max_chunks:
            selected_chunks = [chunks[0]]  # First chunk
            step = (len(chunks) - 2) // (self.max_chunks - 2)
            for i in range(1, self.max_chunks - 1):
                idx = min(1 + i * step, len(chunks) - 2)
                selected_chunks.append(chunks[idx])
            selected_chunks.append(chunks[-1])  # Last chunk
            chunks = selected_chunks
            
        return chunks

    @lru_cache(maxsize=32)
    def _get_chunk_hash(self, chunk):
        """Generate a hash for the chunk to enable caching"""
        return hashlib.md5(chunk.encode()).hexdigest()

    def _process_chunk(self, chunk, chunk_index, total_chunks):
        """Process a chunk with optimized prompt for brevity and speed"""
        chunk_hash = self._get_chunk_hash(chunk)
        if chunk_hash in self.cache:
            return self.cache[chunk_hash]
        
        system_prompt = """You are a fast academic summarizer. Create extremely concise summaries.
Use minimal words. Format with markdown: # for topics, - for points.
Focus ONLY on key concepts, definitions, and formulas."""

        user_prompt = f"""Summarize this content (chunk {chunk_index+1}/{total_chunks}):

{chunk}

Requirements:
1. Max {self.max_summary_length} words
2. Use markdown headings and bullets
3. Include only key points
4. Skip examples
5. Focus on core concepts"""

        try:
            start_time = time.time()
            response = ollama.chat(
                model="mistral",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                options={
                    "temperature": 0.1,
                    "num_predict": 512,  # Reduced from 1024
                }
            )
            
            if time.time() - start_time > self.request_timeout:
                print(f"Warning: Chunk {chunk_index+1} timeout")
                return response["message"]["content"] if "message" in response else ""
            
            result = response["message"]["content"]
            
            # Limit length
            words = result.split()
            if len(words) > self.max_summary_length:
                result = ' '.join(words[:self.max_summary_length])
            
            self.cache[chunk_hash] = result
            return result

        except Exception as e:
            print(f"Error processing chunk {chunk_index + 1}: {str(e)}")
            return ""

    def _merge_summaries(self, summaries):
        """Simplified merger focused on speed and conciseness"""
        if not summaries:
            return ["No summary generated"]

        sections = {}
        section_order = []
        current_section = "Overview"
        
        for summary in summaries:
            lines = summary.split('\n')
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                if line.startswith('# '):
                    current_section = line[2:].strip()
                    if current_section not in sections:
                        sections[current_section] = []
                        section_order.append(current_section)
                elif line.startswith('## '):
                    sections[current_section].append(f"- **{line[3:].strip()}**")
                else:
                    if not line.startswith('#'):
                        sections[current_section].append(line)

        merged_lines = []
        seen_content = set()
        
        for section_title in section_order:
            content = sections[section_title]
            merged_lines.append(f"# {section_title}")
            merged_lines.append("")
            
            for line in content:
                norm_line = re.sub(r'\s+', ' ', line.lower()).strip()
                if norm_line and norm_line not in seen_content:
                    seen_content.add(norm_line)
                    merged_lines.append(line)
            
            merged_lines.append("")
        
        # Add key takeaways if not present
        if not any(line.lower().startswith('# key takeaways') for line in merged_lines):
            merged_lines.append("# Key Takeaways")
            merged_lines.append("")
            bullet_lines = [line for line in merged_lines if line.startswith('- ')]
            for line in bullet_lines[:3]:  # Reduced from 5
                merged_lines.append(line)
                
        return merged_lines

    def generate_summary(self, combined_text):
        """Generates a summary with focus on speed"""
        if not combined_text:
            return ["No meaningful content found in the PDFs"]

        self.cache = {}
        chunks = self._preprocess_text(combined_text)
        
        if not chunks:
            return ["No processable content found"]

        print(f"Processing {len(chunks)} chunks")
        
        summaries = []
        processed_count = 0
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_chunk = {
                executor.submit(self._process_chunk, chunk, i, len(chunks)): i 
                for i, chunk in enumerate(chunks)
            }
            
            for future in concurrent.futures.as_completed(future_to_chunk):
                try:
                    summary = future.result()
                    if summary:
                        summaries.append(summary)
                    processed_count += 1
                    print(f"Processed {processed_count}/{len(chunks)} chunks")
                except Exception as e:
                    print(f"Error in chunk processing: {e}")
                    processed_count += 1

        if not summaries:
            return ["Error: Could not generate summary"]

        return self._merge_summaries(summaries)

@ktu_summary_bp.route("/", methods=["GET", "POST"])
def summary():
    """Handles multi-PDF upload and summary generation for students"""
    if 'user_id' not in session or session.get('user_type') != 'student':
        flash('Only students can access this feature', 'error')
        return redirect(url_for('login'))
    
    if request.method == "POST":
        if 'pdf_count' in request.form:
            session['pdf_count'] = min(int(request.form['pdf_count']), 3)
            return render_template("ktu_summary/upload.html", 
                                pdf_count=session['pdf_count'])
        
        if 'pdfs' in request.files:
            flash('Processing started - this may take a minute...', 'info')
            start_time = time.time()
            pdf_count = session.get('pdf_count', 1)
            files = request.files.getlist('pdfs')
            
            valid_files = []
            for file in files[:pdf_count]:
                if file.filename == '':
                    continue
                if file and file.filename.lower().endswith('.pdf'):
                    valid_files.append(file)
            
            if len(valid_files) != pdf_count:
                flash(f'Please upload exactly {pdf_count} PDF files', 'error')
                return redirect(request.url)
            
            upload_folder = os.path.join(current_app.instance_path, 'uploads')
            os.makedirs(upload_folder, exist_ok=True)
            
            combined_text = []
            temp_files = []
            
            try:
                generator = KTUSummaryGenerator()
                
                with concurrent.futures.ThreadPoolExecutor(max_workers=pdf_count) as executor:
                    future_to_file = {}
                    for i, file in enumerate(valid_files):
                        filename = secure_filename(f"doc_{i}_{file.filename}")
                        pdf_path = os.path.join(upload_folder, filename)
                        file.save(pdf_path)
                        temp_files.append(pdf_path)
                        future_to_file[executor.submit(generator.extract_text_from_pdf, pdf_path)] = (i, file.filename)
                    
                    for future in concurrent.futures.as_completed(future_to_file):
                        i, original_filename = future_to_file[future]
                        try:
                            text = future.result()
                            if text:
                                if len(text) > 50000:
                                    text = text[:50000]
                                combined_text.append(f"[PDF {i+1}]\n{text}")
                        except Exception as e:
                            print(f"Error processing PDF {i+1}: {e}")
                
                if not combined_text:
                    flash('No extractable text found in PDFs', 'error')
                    return redirect(request.url)
                
                summary = generator.generate_summary('\n\n'.join(combined_text))
                current_date = datetime.now().strftime("%B %d, %Y at %H:%M")
                processing_time = time.time() - start_time
                
                return render_template("ktu_summary/results.html", 
                                    summary=summary,
                                    current_date=current_date,
                                    pdf_count=pdf_count,
                                    processing_time=processing_time)
                
            except Exception as e:
                flash(f'Error generating summary: {str(e)}', 'error')
                return render_template("ktu_summary/error.html", 
                                    error=str(e))
            finally:
                for path in temp_files:
                    if os.path.exists(path):
                        os.remove(path)
    
    return render_template("ktu_summary/select_count.html")