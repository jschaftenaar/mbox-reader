"""Flask application for reading and browsing mbox files with SQLite backend."""
import mailbox
import os
import time
import threading
import sqlite3
import hashlib
import json
import base64
from email.utils import parsedate_to_datetime
from flask import Flask, render_template, jsonify, request, send_file
from io import BytesIO

app = Flask(__name__)

MBOX_PATH = os.environ.get('MBOX_PATH', './data/emails.mbox')
DATA_DIR = os.environ.get('DATA_DIR', './data')

loading_thread = None
db_path_cache = None


def get_progress_file_path():
    """Get path to progress file."""
    mbox_hash = get_mbox_md5()
    if mbox_hash is None:
        return None
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, f'progress_{mbox_hash}.json')


def read_progress():
    """Read progress from file."""
    progress_file = get_progress_file_path()
    if progress_file is None or not os.path.exists(progress_file):
        return {'status': 'idle', 'current': 0, 'total': 0, 'message': ''}
    
    try:
        with open(progress_file, 'r') as file:
            return json.load(file)
    except Exception:
        return {'status': 'idle', 'current': 0, 'total': 0, 'message': ''}


def write_progress(status, current, total, message):
    """Write progress to file."""
    progress_file = get_progress_file_path()
    if progress_file is None:
        return
    
    progress = {
        'status': status,
        'current': current,
        'total': total,
        'message': message,
        'timestamp': time.time()
    }
    
    try:
        with open(progress_file, 'w') as file:
            json.dump(progress, file)
    except Exception as error:
        print(f"Error writing progress: {error}")


def get_mbox_md5():
    """Calculate MD5 hash of first and last 1MB of mbox file for quick identification."""
    if not os.path.exists(MBOX_PATH):
        return None
    
    md5_hash = hashlib.md5()
    file_size = os.path.getsize(MBOX_PATH)
    
    # Hash file size
    md5_hash.update(str(file_size).encode())
    
    # Hash first 1MB
    with open(MBOX_PATH, 'rb') as file:
        md5_hash.update(file.read(1024 * 1024))
    
    # Hash last 1MB if file is large enough
    if file_size > 2 * 1024 * 1024:
        with open(MBOX_PATH, 'rb') as file:
            file.seek(-1024 * 1024, 2)
            md5_hash.update(file.read(1024 * 1024))
    
    return md5_hash.hexdigest()


def get_db_path():
    """Get database path based on mbox file hash."""
    global db_path_cache
    
    if db_path_cache is not None:
        return db_path_cache
    
    mbox_hash = get_mbox_md5()
    if mbox_hash is None:
        return None
    
    # Ensure data directory exists
    os.makedirs(DATA_DIR, exist_ok=True)
    
    db_path_cache = os.path.join(DATA_DIR, f'emails_{mbox_hash}.db')
    return db_path_cache


def get_db_connection():
    """Get database connection with proper settings."""
    db_path = get_db_path()
    if db_path is None:
        raise Exception("Mbox file not found")
    
    connection = sqlite3.connect(db_path, timeout=30.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():
    """Create database schema if it doesn't exist."""
    connection = get_db_connection()
    cursor = connection.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mbox_index INTEGER,
            from_addr TEXT,
            to_addr TEXT,
            subject TEXT,
            date TEXT,
            parsed_date TEXT,
            message_id TEXT,
            body TEXT,
            labels TEXT,
            has_attachments INTEGER DEFAULT 0
        )
    ''')
    
    # Create attachments table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id INTEGER,
            filename TEXT,
            content_type TEXT,
            size INTEGER,
            data BLOB,
            FOREIGN KEY (email_id) REFERENCES emails(id)
        )
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_email_id ON attachments(email_id)
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_parsed_date ON emails(parsed_date DESC)
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_from ON emails(from_addr)
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_subject ON emails(subject)
    ''')
    
    # Create FTS5 virtual table for full-text search
    cursor.execute('''
        CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(
            from_addr,
            to_addr,
            subject,
            body,
            content=emails,
            content_rowid=id
        )
    ''')
    
    # Create triggers to keep FTS table in sync
    cursor.execute('''
        CREATE TRIGGER IF NOT EXISTS emails_ai AFTER INSERT ON emails BEGIN
            INSERT INTO emails_fts(rowid, from_addr, to_addr, subject, body)
            VALUES (new.id, new.from_addr, new.to_addr, new.subject, new.body);
        END
    ''')
    
    cursor.execute('''
        CREATE TRIGGER IF NOT EXISTS emails_ad AFTER DELETE ON emails BEGIN
            INSERT INTO emails_fts(emails_fts, rowid, from_addr, to_addr, subject, body)
            VALUES('delete', old.id, old.from_addr, old.to_addr, old.subject, old.body);
        END
    ''')
    
    cursor.execute('''
        CREATE TRIGGER IF NOT EXISTS emails_au AFTER UPDATE ON emails BEGIN
            INSERT INTO emails_fts(emails_fts, rowid, from_addr, to_addr, subject, body)
            VALUES('delete', old.id, old.from_addr, old.to_addr, old.subject, old.body);
            INSERT INTO emails_fts(rowid, from_addr, to_addr, subject, body)
            VALUES (new.id, new.from_addr, new.to_addr, new.subject, new.body);
        END
    ''')
    
    # Store metadata about the mbox file
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    
    connection.commit()
    connection.close()


def is_database_current():
    """Check if database exists and has data."""
    db_path = get_db_path()
    if db_path is None or not os.path.exists(db_path):
        return False
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute('SELECT COUNT(*) as count FROM emails')
        row = cursor.fetchone()
        connection.close()
        
        return row is not None and row['count'] > 0
    except Exception:
        return False


def parse_email_safely(message):
    """Parse email message and extract relevant information safely."""
    try:
        # Extract Gmail labels if present
        gmail_labels = message.get('X-Gmail-Labels', '')
        if gmail_labels:
            labels = str(gmail_labels)
        else:
            labels = ''
        
        email_data = {
            'from': str(message.get('From', 'Unknown')),
            'to': str(message.get('To', 'Unknown')),
            'subject': str(message.get('Subject', 'No Subject')),
            'date': str(message.get('Date', 'Unknown')),
            'message_id': str(message.get('Message-ID', '')),
            'labels': labels,
            'attachments': [],
            'is_html': False
        }
        
        # Parse date for sorting
        try:
            if email_data['date'] != 'Unknown':
                parsed_date = parsedate_to_datetime(email_data['date'])
                email_data['parsed_date'] = parsed_date.isoformat()
            else:
                email_data['parsed_date'] = ''
        except Exception:
            email_data['parsed_date'] = ''
        
        # Extract body and attachments
        body = ''
        if message.is_multipart():
            for part in message.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get('Content-Disposition', ''))
                
                # Check if it's an attachment
                if 'attachment' in content_disposition or part.get_filename():
                    filename = part.get_filename()
                    if filename:
                        try:
                            payload = part.get_payload(decode=True)
                            if payload:
                                email_data['attachments'].append({
                                    'filename': filename,
                                    'content_type': content_type,
                                    'size': len(payload),
                                    'data': payload
                                })
                        except Exception as e:
                            print(f"Error extracting attachment {filename}: {e}")
                            continue
                
                # Extract body text - prefer plain text, but use HTML if that's all we have
                elif content_type == 'text/plain' and not body:
                    try:
                        body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                        email_data['is_html'] = False
                    except Exception:
                        continue
                elif content_type == 'text/html' and not body:
                    try:
                        body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                        email_data['is_html'] = True
                    except Exception:
                        continue
        else:
            try:
                body = message.get_payload(decode=True).decode('utf-8', errors='ignore')
            except Exception:
                body = str(message.get_payload())
        
        email_data['body'] = body
        email_data['has_attachments'] = 1 if email_data['attachments'] else 0
        return email_data
    except Exception as error:
        print(f"Error parsing email: {error}")
        return None


def index_mbox_to_database():
    """Index mbox file into SQLite database in background thread."""
    print(f"DEBUG: Starting database indexing")
    print(f"DEBUG: MBOX_PATH = {MBOX_PATH}")
    print(f"DEBUG: File exists check: {os.path.exists(MBOX_PATH)}")
    print(f"DEBUG: Current working directory: {os.getcwd()}")
    
    if not os.path.exists(MBOX_PATH):
        write_progress('error', 0, 0, 'Mbox file not found')
        print(f"DEBUG: Mbox file not found at {MBOX_PATH}")
        return
    
    # Get file size for display
    file_size = os.path.getsize(MBOX_PATH)
    file_size_gb = file_size / (1024 * 1024 * 1024)
    file_size_mb = file_size / (1024 * 1024)
    size_display = f'{file_size_gb:.1f} GB' if file_size_gb >= 1 else f'{file_size_mb:.1f} MB'
    
    print(f"DEBUG: File size: {size_display}")
    
    try:
        # Initialize database
        initialize_database()
        
        # Clear existing data
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute('DELETE FROM emails')
        cursor.execute('DELETE FROM metadata')
        connection.commit()
        
        write_progress('loading', 0, 0, f'Indexing {size_display} mbox file to database...')
        
        print(f"DEBUG: Opening mbox file...")
        mbox = mailbox.mbox(MBOX_PATH)
        print(f"DEBUG: Mbox file opened, starting indexing...")
        
        start_time = time.time()
        batch_size = 100
        batch = []
        
        for index, message in enumerate(mbox):
            email_data = parse_email_safely(message)
            if email_data:
                batch.append((
                    index,
                    email_data['from'],
                    email_data['to'],
                    email_data['subject'],
                    email_data['date'],
                    email_data['parsed_date'],
                    email_data['message_id'],
                    email_data['body'],
                    email_data['labels'],
                    email_data['has_attachments'],
                    email_data['attachments']  # Store for later processing
                ))
            
            # Insert in batches for better performance
            if len(batch) >= batch_size:
                # Insert emails
                email_batch = [(b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7], b[8], b[9]) for b in batch]
                cursor.executemany('''
                    INSERT INTO emails (mbox_index, from_addr, to_addr, subject, date, parsed_date, message_id, body, labels, has_attachments)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', email_batch)
                connection.commit()
                
                # Insert attachments
                for email_tuple in batch:
                    if email_tuple[10]:  # has attachments
                        # Get the email_id we just inserted
                        cursor.execute('SELECT id FROM emails WHERE mbox_index = ?', (email_tuple[0],))
                        email_id = cursor.fetchone()['id']
                        
                        # Insert each attachment
                        for att in email_tuple[10]:
                            cursor.execute('''
                                INSERT INTO attachments (email_id, filename, content_type, size, data)
                                VALUES (?, ?, ?, ?, ?)
                            ''', (email_id, att['filename'], att['content_type'], att['size'], att['data']))
                
                connection.commit()
                batch = []
                
                # Update progress every batch
                elapsed = time.time() - start_time
                emails_per_second = (index + 1) / elapsed if elapsed > 0 else 0
                
                if emails_per_second > 1:
                    message = f'Indexing emails... {index + 1} indexed ({emails_per_second:.1f} emails/sec)'
                else:
                    seconds_per_email = 1 / emails_per_second if emails_per_second > 0 else 0
                    message = f'Indexing emails... {index + 1} indexed ({seconds_per_email:.2f} sec/email)'
                
                write_progress('loading', index + 1, 0, message)
                
                if (index + 1) % 1000 == 0:
                    print(f"DEBUG: Indexed {index + 1} emails, speed: {emails_per_second:.2f} emails/sec")
        
        # Insert remaining batch
        if batch:
            # Insert emails
            email_batch = [(b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7], b[8], b[9]) for b in batch]
            cursor.executemany('''
                INSERT INTO emails (mbox_index, from_addr, to_addr, subject, date, parsed_date, message_id, body, labels, has_attachments)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', email_batch)
            connection.commit()
            
            # Insert attachments
            for email_tuple in batch:
                if email_tuple[10]:  # has attachments
                    cursor.execute('SELECT id FROM emails WHERE mbox_index = ?', (email_tuple[0],))
                    email_id = cursor.fetchone()['id']
                    
                    for att in email_tuple[10]:
                        cursor.execute('''
                            INSERT INTO attachments (email_id, filename, content_type, size, data)
                            VALUES (?, ?, ?, ?, ?)
                        ''', (email_id, att['filename'], att['content_type'], att['size'], att['data']))
            
            connection.commit()
        
        # No need to store hash - database filename is based on mbox hash
        connection.commit()
        
        # Get total count
        cursor.execute('SELECT COUNT(*) as count FROM emails')
        total_count = cursor.fetchone()['count']
        
        connection.close()
        
        elapsed_total = time.time() - start_time
        write_progress('complete', total_count, total_count, f'Indexed {total_count} emails in {elapsed_total:.1f} seconds')
        print(f"DEBUG: Indexing complete! Total: {total_count} emails in {elapsed_total:.1f} seconds")
        
    except Exception as error:
        print(f"DEBUG ERROR: {error}")
        import traceback
        traceback.print_exc()
        write_progress('error', 0, 0, f'Error: {str(error)}')


def start_indexing_if_needed():
    """Start indexing mbox file if database is not current."""
    global loading_thread
    
    # Check if database is current
    if is_database_current():
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute('SELECT COUNT(*) as count FROM emails')
        count = cursor.fetchone()['count']
        connection.close()
        write_progress('complete', count, count, f'Database ready with {count} emails')
        return
    
    # Check if already indexing
    if loading_thread is not None and loading_thread.is_alive():
        return
    
    # Start indexing in background
    loading_thread = threading.Thread(target=index_mbox_to_database, daemon=True)
    loading_thread.start()


@app.route('/')
def index():
    """Render the main page."""
    return render_template('index.html')


@app.route('/api/progress')
def get_progress():
    """Get loading/indexing progress."""
    return jsonify(read_progress())


@app.route('/api/emails')
def get_emails():
    """Get paginated list of emails from database."""
    # Start indexing if needed
    start_indexing_if_needed()
    
    # Check progress
    progress = read_progress()
    
    # Check if still indexing
    if progress['status'] in ['loading']:
        return jsonify({
            'loading': True,
            'progress': progress
        })
    
    # Check if database is ready
    if not is_database_current():
        return jsonify({
            'loading': True,
            'progress': progress
        })
    
    try:
        # Get pagination parameters
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        label_filter = request.args.get('label', '').strip()
        
        # Limit per_page to reasonable values
        per_page = min(max(per_page, 10), 200)
        
        # Calculate offset
        offset = (page - 1) * per_page
        
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Build query based on label filter
        if label_filter:
            # Filter by label - check if label is in the comma-separated list
            cursor.execute('''
                SELECT COUNT(*) as count FROM emails
                WHERE labels LIKE ?
            ''', (f'%{label_filter}%',))
            total_count = cursor.fetchone()['count']
            
            cursor.execute('''
                SELECT id, mbox_index, from_addr, to_addr, subject, date, labels
                FROM emails
                WHERE labels LIKE ?
                ORDER BY parsed_date DESC NULLS LAST
                LIMIT ? OFFSET ?
            ''', (f'%{label_filter}%', per_page, offset))
        else:
            # Get total count
            cursor.execute('SELECT COUNT(*) as count FROM emails')
            total_count = cursor.fetchone()['count']
            
            # Get paginated emails ordered by date (newest first)
            cursor.execute('''
                SELECT id, mbox_index, from_addr, to_addr, subject, date, labels
                FROM emails
                ORDER BY parsed_date DESC NULLS LAST
                LIMIT ? OFFSET ?
            ''', (per_page, offset))
        
        emails = []
        for row in cursor.fetchall():
            emails.append({
                'id': row['id'],
                'index': row['mbox_index'],
                'from': row['from_addr'],
                'to': row['to_addr'],
                'subject': row['subject'],
                'date': row['date'],
                'labels': row['labels']
            })
        
        connection.close()
        
        total_pages = (total_count + per_page - 1) // per_page
        
        return jsonify({
            'loading': False,
            'emails': emails,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total_count,
                'total_pages': total_pages,
                'has_prev': page > 1,
                'has_next': page < total_pages
            }
        })
    except Exception as error:
        print(f"Error fetching emails: {error}")
        return jsonify({'error': str(error)}), 500


@app.route('/api/search')
def search_emails():
    """Full-text search emails with pagination."""
    # Check if database is ready
    if not is_database_current():
        progress = read_progress()
        return jsonify({
            'loading': True,
            'progress': progress
        })
    
    try:
        # Get search query
        query = request.args.get('q', '').strip()
        if not query:
            return jsonify({'error': 'Search query required'}), 400
        
        # Get pagination parameters
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        
        # Limit per_page to reasonable values
        per_page = min(max(per_page, 10), 200)
        
        # Calculate offset
        offset = (page - 1) * per_page
        
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Check if FTS table exists
        cursor.execute('''
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='emails_fts'
        ''')
        fts_exists = cursor.fetchone() is not None
        
        if not fts_exists:
            connection.close()
            return jsonify({
                'error': 'Search index not available. Please rebuild the database.',
                'needs_rebuild': True
            }), 400
        
        # FTS5 search query
        # Use MATCH for full-text search across all indexed fields
        cursor.execute('''
            SELECT COUNT(*) as count
            FROM emails_fts
            WHERE emails_fts MATCH ?
        ''', (query,))
        total_count = cursor.fetchone()['count']
        
        # Get paginated search results
        cursor.execute('''
            SELECT e.id, e.mbox_index, e.from_addr, e.to_addr, e.subject, e.date, e.labels,
                   snippet(emails_fts, 3, '<mark>', '</mark>', '...', 32) as snippet
            FROM emails_fts
            JOIN emails e ON emails_fts.rowid = e.id
            WHERE emails_fts MATCH ?
            ORDER BY e.parsed_date DESC NULLS LAST
            LIMIT ? OFFSET ?
        ''', (query, per_page, offset))
        
        emails = []
        for row in cursor.fetchall():
            emails.append({
                'id': row['id'],
                'index': row['mbox_index'],
                'from': row['from_addr'],
                'to': row['to_addr'],
                'subject': row['subject'],
                'date': row['date'],
                'labels': row['labels'],
                'snippet': row['snippet']
            })
        
        connection.close()
        
        total_pages = (total_count + per_page - 1) // per_page
        
        return jsonify({
            'loading': False,
            'emails': emails,
            'query': query,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total_count,
                'total_pages': total_pages,
                'has_prev': page > 1,
                'has_next': page < total_pages
            }
        })
    except Exception as error:
        print(f"Error searching emails: {error}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(error)}), 500


@app.route('/api/email/<int:email_id>')
def get_email(email_id):
    """Get specific email by database ID."""
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        cursor.execute('''
            SELECT id, mbox_index, from_addr, to_addr, subject, date, body, labels, has_attachments
            FROM emails
            WHERE id = ?
        ''', (email_id,))
        
        row = cursor.fetchone()
        
        if row is None:
            connection.close()
            return jsonify({'error': 'Email not found'}), 404
        
        # Get attachments if present
        attachments = []
        if row['has_attachments']:
            cursor.execute('''
                SELECT id, filename, content_type, size
                FROM attachments
                WHERE email_id = ?
            ''', (email_id,))
            
            for att_row in cursor.fetchall():
                attachments.append({
                    'id': att_row['id'],
                    'filename': att_row['filename'],
                    'content_type': att_row['content_type'],
                    'size': att_row['size']
                })
        
        connection.close()
        
        # Detect if body is HTML
        is_html = row['body'].strip().startswith('<') if row['body'] else False
        
        return jsonify({
            'id': row['id'],
            'index': row['mbox_index'],
            'from': row['from_addr'],
            'to': row['to_addr'],
            'subject': row['subject'],
            'date': row['date'],
            'body': row['body'],
            'is_html': is_html,
            'labels': row['labels'],
            'attachments': attachments
        })
    except Exception as error:
        print(f"Error fetching email: {error}")
        return jsonify({'error': str(error)}), 500

@app.route('/api/rebuild')
def rebuild_index():
    """Force rebuild of the database and FTS index."""
    try:
        db_path = get_db_path()
        if db_path and os.path.exists(db_path):
            os.remove(db_path)
            print(f"Deleted database: {db_path}")
        
        progress_file = get_progress_file_path()
        if progress_file and os.path.exists(progress_file):
            os.remove(progress_file)
            print(f"Deleted progress file: {progress_file}")
        
        # Clear cache
        global db_path_cache
        db_path_cache = None
        
        # Start indexing
        start_indexing_if_needed()
        
        return jsonify({
            'success': True,
            'message': 'Database rebuild started. This will take some time for large files.'
        })
    except Exception as error:
        print(f"Error rebuilding index: {error}")
        return jsonify({'error': str(error)}), 500


@app.route('/api/email/<int:email_id>/attachments')
def get_email_attachments(email_id):
    """Get list of attachments for an email."""
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        cursor.execute('''
            SELECT id, filename, content_type, size
            FROM attachments
            WHERE email_id = ?
        ''', (email_id,))
        
        attachments = []
        for row in cursor.fetchall():
            attachments.append({
                'id': row['id'],
                'filename': row['filename'],
                'content_type': row['content_type'],
                'size': row['size']
            })
        
        connection.close()
        
        return jsonify({'attachments': attachments})
    except Exception as error:
        print(f"Error fetching attachments: {error}")
        return jsonify({'error': str(error)}), 500


@app.route('/api/attachment/<int:attachment_id>/download')
def download_attachment(attachment_id):
    """Download a specific attachment."""
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        cursor.execute('''
            SELECT filename, content_type, data
            FROM attachments
            WHERE id = ?
        ''', (attachment_id,))
        
        row = cursor.fetchone()
        connection.close()
        
        if row is None:
            return jsonify({'error': 'Attachment not found'}), 404
        
        # Create a BytesIO object from the blob data
        file_data = BytesIO(row['data'])
        
        return send_file(
            file_data,
            mimetype=row['content_type'],
            as_attachment=True,
            download_name=row['filename']
        )
    except Exception as error:
        print(f"Error downloading attachment: {error}")
        return jsonify({'error': str(error)}), 500


@app.route('/api/labels')
def get_labels():
    """Get all unique labels from emails."""
    if not is_database_current():
        progress = read_progress()
        return jsonify({
            'loading': True,
            'progress': progress
        })
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Get all labels
        cursor.execute('SELECT DISTINCT labels FROM emails WHERE labels IS NOT NULL AND labels != ""')
        rows = cursor.fetchall()
        connection.close()
        
        # Parse and count labels
        label_counts = {}
        for row in rows:
            if row['labels']:
                labels = [l.strip() for l in row['labels'].split(',') if l.strip()]
                for label in labels:
                    label_counts[label] = label_counts.get(label, 0) + 1
        
        # Sort by count descending
        sorted_labels = sorted(label_counts.items(), key=lambda x: x[1], reverse=True)
        
        return jsonify({
            'labels': [{'name': name, 'count': count} for name, count in sorted_labels]
        })
    except Exception as error:
        print(f"Error fetching labels: {error}")
        return jsonify({'error': str(error)}), 500


@app.route('/api/stats')
def get_stats():
    """Get database statistics."""
    if not is_database_current():
        progress = read_progress()
        return jsonify({
            'loading': True,
            'progress': progress
        })
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Get total count
        cursor.execute('SELECT COUNT(*) as count FROM emails')
        total_count = cursor.fetchone()['count']
        
        # Get date range
        cursor.execute('''
            SELECT 
                MIN(parsed_date) as earliest,
                MAX(parsed_date) as latest
            FROM emails
            WHERE parsed_date IS NOT NULL AND parsed_date != ''
        ''')
        date_range = cursor.fetchone()
        
        connection.close()
        
        return jsonify({
            'total_emails': total_count,
            'earliest_date': date_range['earliest'],
            'latest_date': date_range['latest']
        })
    except Exception as error:
        print(f"Error fetching stats: {error}")
        return jsonify({'error': str(error)}), 500



@app.route('/health')
def health():
    """Health check endpoint."""
    mbox_exists = os.path.exists(MBOX_PATH)
    db_path = get_db_path()
    db_exists = db_path is not None and os.path.exists(db_path)
    db_current = is_database_current()
    
    return jsonify({
        'status': 'healthy',
        'mbox_path': MBOX_PATH,
        'mbox_exists': mbox_exists,
        'db_path': db_path,
        'db_exists': db_exists,
        'db_current': db_current
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

# Made with Bob
