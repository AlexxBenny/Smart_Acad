# Educational System Project

This is a comprehensive educational system with multiple components:

## Features

- Question paper generation (KTU format)
- Study material summarization
- Student management system
- Attendance tracking
- Marks management
- Chat functionality

## Project Structure

```
.
├── app.py                    # Main Flask application
├── ktu_question_generator/   # Question generation module
├── ktu_summary_generator/    # Study material summarization
├── templates/                # HTML templates
├── static/                   # Static files (CSS, JS)
├── uploads/                  # File uploads directory
├── instance/                 # Instance files and uploads
├── requirements.txt          # Python dependencies
└── multiple .db files        # Database files
```

## Setup Instructions

1. Clone the repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Set up environment variables in `.env`
4. Run the application:
   ```bash
   python app.py
   ```

## Configuration

- Configure database paths in `app.py`
- Set upload folder paths as needed
- Modify templates in `templates/` directory

## License

MIT License