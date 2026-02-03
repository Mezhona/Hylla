import os
import time
import requests
from flask import Flask, render_template, session, redirect, url_for, request, abort
from authlib.integrations.flask_client import OAuth
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
import mysql.connector

app = Flask(__name__)
# Middleware for Nginx Proxy Manager to ensure correct URLs/HTTPS
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Secure Session Configuration
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    MAX_CONTENT_LENGTH=2 * 1024 * 1024  # Limit uploads to 2MB
)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev_key_change_this")

# --- DATABASE CONNECTION & SMART INIT ---

def get_db_connection(select_db=True, retries=3, delay=2):
    """
    Connects to the database. 
    select_db=False allows connecting to the SERVER to create the DB if it doesn't exist.
    """
    db_name = os.getenv("DB_NAME", "hylla_db")
    while retries > 0:
        try:
            conn = mysql.connector.connect(
                host=os.getenv("DB_HOST"),
                user=os.getenv("DB_USER"),
                password=os.getenv("DB_PASSWORD"),
                port=int(os.getenv("DB_PORT", 3306)),
                database=db_name if select_db else None 
            )
            return conn
        except mysql.connector.Error as err:
            # Error 1049 = Unknown Database. If we want to select it, fail fast.
            if err.errno == 1049 and select_db:
                raise err
            print(f"DB Connection failed ({err}). Retrying in {delay}s...")
            time.sleep(delay)
            retries -= 1
    raise Exception("Could not connect to database server.")

def init_db():
    """Auto-Magic: Smart check for DB existence, then ensures Tables exist."""
    print("--- 🛠️ STARTING SYSTEM INITIALIZATION 🛠️ ---")
    db_name = os.getenv("DB_NAME", "hylla_db")
    
    # PHASE 1: CHECK IF DATABASE EXISTS
    # We try to connect to the specific DB first. If successful, we skip creation.
    try:
        conn = get_db_connection(select_db=True, retries=1, delay=1)
        conn.close()
        print(f"✅ Database '{db_name}' exists and is accessible. Skipping creation step.")
    except Exception:
        # If connection failed, it likely doesn't exist. Try to create it.
        print(f"⚠️ Database '{db_name}' not accessible. Attempting to create it...")
        try:
            conn = get_db_connection(select_db=False)
            cursor = conn.cursor()
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {db_name}")
            conn.commit()
            cursor.close(); conn.close()
            print(f"✅ Database '{db_name}' created successfully.")
        except Exception as e:
            # If we fail here, it might be permissions. But we continue, 
            # in case the DB exists but we just failed to 'CREATE' it.
            print(f"❌ Warning: Could not create database (Permission Error?). Error: {e}")

    # PHASE 2: CREATE TABLES & LOG REPAIRS
    try:
        conn = get_db_connection(select_db=True)
        cursor = conn.cursor() # Standard cursor returns Tuples
        
        # 1. Detect what is currently there
        cursor.execute("SHOW TABLES")
        existing_tables = [x[0] for x in cursor.fetchall()]
        
        tables = {
            "user_preferences": """
                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id VARCHAR(255) PRIMARY KEY,
                    theme VARCHAR(50) DEFAULT 'default',
                    role VARCHAR(50) DEFAULT 'member',
                    username VARCHAR(255),
                    email VARCHAR(255)
                )""",
            "movies_v2": """
                CREATE TABLE IF NOT EXISTS movies_v2 (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    title VARCHAR(255),
                    year INT,
                    genre VARCHAR(255),
                    director VARCHAR(255),
                    cast TEXT,
                    runtime INT,
                    plot TEXT,
                    poster TEXT,
                    rating FLOAT,
                    media_format VARCHAR(50),
                    is_ripped TINYINT(1) DEFAULT 0,
                    is_locked TINYINT(1) DEFAULT 0,
                    placement VARCHAR(100)
                )""",
            "wishlist": """
                CREATE TABLE IF NOT EXISTS wishlist (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    title VARCHAR(255),
                    year INT,
                    genre VARCHAR(255),
                    poster TEXT,
                    priority VARCHAR(50) DEFAULT 'Medium'
                )""",
            "app_settings": """
                CREATE TABLE IF NOT EXISTS app_settings (
                    setting_key VARCHAR(50) PRIMARY KEY,
                    setting_value TEXT
                )""",
            "audit_log": """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    user_name VARCHAR(255),
                    action VARCHAR(50),
                    movie_title VARCHAR(255),
                    details TEXT
                )"""
        }

        recreated_tables = []

        for name, sql in tables.items():
            cursor.execute(sql)
            if name not in existing_tables:
                print(f"   - 🔧 Table '{name}' was missing. Created.")
                recreated_tables.append(name)

        conn.commit()

        # 3. Log the Repair (If needed)
        if recreated_tables:
            details = f"System recovered missing tables: {', '.join(recreated_tables)}"
            cursor.execute("INSERT INTO audit_log (user_name, action, movie_title, details) VALUES (%s, %s, %s, %s)", 
                          ('System', 'REPAIR', 'Database Structure', details))
            conn.commit()
            print(f"   - 📝 Logged repair action: {details}")

        cursor.close(); conn.close()
        print("✅ Tables validated.")
        
    except Exception as e:
        print(f"❌ CRITICAL: Table creation failed. Error: {e}")

# --- HELPER: LOGGING & DIFFS ---
def log_change(action, movie_title, details=""):
    try:
        user_name = session['user'].get('preferred_username', 'Unknown')
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO audit_log (user_name, action, movie_title, details) VALUES (%s, %s, %s, %s)", 
                      (user_name, action, movie_title, details))
        conn.commit()
        cursor.close(); conn.close()
    except Exception as e:
        print(f"Logging failed: {e}")

def generate_diff(old_data, new_form):
    changes = []
    fields = ['title', 'year', 'genre', 'director', 'placement', 'rating', 'media_format']
    
    for field in fields:
        old_val = str(old_data.get(field) or '').strip()
        new_val = str(new_form.get(field) or '').strip()
        if old_val != new_val:
            changes.append(f"{field.capitalize()}: '{old_val}' → '{new_val}'")
            
    # Booleans
    old_ripped = old_data.get('is_ripped') == 1
    new_ripped = new_form.get('is_ripped') == 'on'
    if old_ripped != new_ripped: changes.append(f"Plex: {old_ripped} → {new_ripped}")
    
    old_locked = old_data.get('is_locked') == 1
    new_locked = new_form.get('is_locked') == 'on'
    if old_locked != new_locked: changes.append(f"Locked: {old_locked} → {new_locked}")

    return " | ".join(changes)

# --- OAUTH ---
oauth = OAuth(app)
authentik = oauth.register(
    name='authentik',
    client_id=os.getenv("OIDC_CLIENT_ID"),
    client_secret=os.getenv("OIDC_CLIENT_SECRET"),
    server_metadata_url=os.getenv("OIDC_METADATA_URL"),
    client_kwargs={'scope': 'openid profile email groups'}
)

@app.context_processor
def inject_user_data():
    """Injects user theme, role, AND custom site logo into every template."""
    context = {'user_theme': 'default', 'user_role': 'member'}
    
    # 1. User Preferences
    if session.get('user'):
        user_id = session['user'].get('sub')
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT theme, role FROM user_preferences WHERE user_id = %s", (user_id,))
            row = cursor.fetchone()
            if row:
                context['user_theme'] = row['theme']
                context['user_role'] = row['role']
            cursor.close(); conn.close()
        except Exception: pass
        
    # 2. Site Branding (Logo)
    logo_filename = 'custom_logo.png'
    # Look in static/uploads which is mapped to host
    logo_path = os.path.join(app.root_path, 'static', 'uploads', logo_filename)
    
    if os.path.exists(logo_path):
        context['site_logo'] = url_for('static', filename=f'uploads/{logo_filename}') + f"?v={int(time.time())}"
    else:
        context['site_logo'] = url_for('static', filename='hylla_logo.png')
        
    return context

# --- ADMIN ROUTES ---

@app.route('/admin/health')
def admin_health():
    if not session.get('user'): return redirect(url_for('welcome'))
    
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT role FROM user_preferences WHERE user_id = %s", (session['user'].get('sub'),))
    if cursor.fetchone()['role'] != 'admin': cursor.close(); conn.close(); return abort(403)
    
    health_report = []

    # 1. Database Connection Check
    try:
        conn.ping()
        health_report.append({"name": "Database Connection", "status": "OK", "detail": "Connected to MySQL"})
    except:
        health_report.append({"name": "Database Connection", "status": "ERROR", "detail": "Connection Lost"})

    # 2. Table Checks
    required_tables = ['movies_v2', 'user_preferences', 'wishlist', 'audit_log', 'app_settings']
    cursor.execute("SHOW TABLES")
    existing_tables = [list(x.values())[0] for x in cursor.fetchall()]
    
    for table in required_tables:
        if table in existing_tables:
            health_report.append({"name": f"Table: {table}", "status": "OK", "detail": "Exists"})
        else:
            health_report.append({"name": f"Table: {table}", "status": "MISSING", "detail": "Table not found (Repair needed)"})

    # 3. Environment Variables (API Keys)
    tmdb = os.getenv("TMDB_API_KEY")
    omdb = os.getenv("OMDB_API_KEY")
    
    health_report.append({
        "name": "TMDB Configuration", 
        "status": "OK" if tmdb else "WARNING", 
        "detail": "Key Configured" if tmdb else "Missing TMDB_API_KEY"
    })
    
    health_report.append({
        "name": "OMDB Configuration", 
        "status": "OK" if omdb else "WARNING", 
        "detail": "Key Configured" if omdb else "Missing OMDB_API_KEY"
    })

    # 4. OIDC Check
    oidc = os.getenv("OIDC_CLIENT_ID") and os.getenv("OIDC_METADATA_URL")
    health_report.append({
        "name": "OIDC Identity Provider",
        "status": "OK" if oidc else "ERROR",
        "detail": "Authentik Configured" if oidc else "Missing OIDC Envs"
    })

    cursor.close(); conn.close()
    return render_template('admin_health.html', report=health_report)

@app.route('/admin/health/repair', methods=['POST'])
def admin_repair_db():
    if not session.get('user'): abort(403)
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT role FROM user_preferences WHERE user_id = %s", (session['user'].get('sub'),))
    if cursor.fetchone()['role'] != 'admin': cursor.close(); conn.close(); return abort(403)
    cursor.close(); conn.close()

    init_db()
    return redirect(url_for('admin_health'))

@app.route('/admin/users')
def admin_users():
    if not session.get('user'): return redirect(url_for('welcome'))
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT role FROM user_preferences WHERE user_id = %s", (session['user'].get('sub'),))
    row = cursor.fetchone()
    if not row or row['role'] != 'admin': cursor.close(); conn.close(); return abort(403)
    cursor.execute("SELECT * FROM user_preferences ORDER BY role ASC, username ASC")
    users = cursor.fetchall(); cursor.close(); conn.close()
    return render_template('admin_users.html', users=users, current_user_id=session['user'].get('sub'))

@app.route('/admin/stats')
def admin_stats():
    if not session.get('user'): return redirect(url_for('welcome'))
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT role FROM user_preferences WHERE user_id = %s", (session['user'].get('sub'),))
    row = cursor.fetchone()
    if not row or row['role'] != 'admin': cursor.close(); conn.close(); return abort(403)

    stats = {}
    cursor.execute("SELECT COUNT(*) as count FROM movies_v2")
    stats['total'] = cursor.fetchone()['count']
    cursor.execute("SELECT COUNT(*) as count FROM movies_v2 WHERE is_ripped = 1")
    stats['ripped'] = cursor.fetchone()['count']
    cursor.execute("SELECT COUNT(*) as count FROM movies_v2 WHERE is_locked = 1")
    stats['locked'] = cursor.fetchone()['count']
    cursor.execute("SELECT IFNULL(media_format, 'Unset') as format, COUNT(*) as count FROM movies_v2 GROUP BY media_format ORDER BY count DESC")
    stats['formats'] = cursor.fetchall()
    cursor.execute("SELECT * FROM movies_v2 WHERE (year IS NULL OR year = 0) OR (genre IS NULL OR genre = '') OR (poster IS NULL OR poster = '') ORDER BY title ASC")
    incomplete_movies = cursor.fetchall()

    cursor.close(); conn.close()
    return render_template('admin_stats.html', stats=stats, incomplete_movies=incomplete_movies)

@app.route('/admin/audit')
def admin_audit():
    if not session.get('user'): return redirect(url_for('welcome'))
    search_query = request.args.get('q', '')
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT role FROM user_preferences WHERE user_id = %s", (session['user'].get('sub'),))
    if cursor.fetchone()['role'] != 'admin': cursor.close(); conn.close(); return abort(403)
    
    query = "SELECT * FROM audit_log WHERE 1=1"
    params = []
    if search_query:
        query += " AND (movie_title LIKE %s OR user_name LIKE %s OR action LIKE %s)"
        like_q = f"%{search_query}%"
        params.extend([like_q, like_q, like_q])
    query += " ORDER BY timestamp DESC LIMIT 100"
    
    cursor.execute(query, params)
    logs = cursor.fetchall()
    cursor.close(); conn.close()
    return render_template('admin_audit.html', logs=logs, search_query=search_query)

# --- SETTINGS & LOGO UPLOAD ---
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/admin/settings/logo', methods=['POST'])
def upload_logo():
    if not session.get('user'): abort(403)
    
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT role FROM user_preferences WHERE user_id = %s", (session['user'].get('sub'),))
    if cursor.fetchone()['role'] != 'admin': cursor.close(); conn.close(); return abort(403)
    cursor.close(); conn.close()

    if 'logo_file' not in request.files: return redirect(url_for('settings'))
    file = request.files['logo_file']
    if file.filename == '': return redirect(url_for('settings'))

    if file and allowed_file(file.filename):
        filename = 'custom_logo.png' 
        save_path = os.path.join(app.root_path, 'static', 'uploads', filename)
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        file.save(save_path)
        return redirect(url_for('settings'))
        
    return redirect(url_for('settings'))

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if not session.get('user'): return redirect(url_for('welcome'))
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    user_id = session['user'].get('sub')
    cursor.execute("SELECT role FROM user_preferences WHERE user_id = %s", (user_id,))
    user_role = cursor.fetchone()['role']
    
    if request.method == 'POST':
        if 'theme_selection' in request.form:
            cursor.execute("UPDATE user_preferences SET theme=%s WHERE user_id=%s", (request.form.get('theme_selection'), user_id))
            conn.commit()
        if user_role == 'admin':
            if 'edit_mode' in request.form:
                cursor.execute("REPLACE INTO app_settings (setting_key, setting_value) VALUES ('edit_mode', %s)", ('1' if request.form.get('edit_mode') == 'on' else '0',))
                conn.commit()
        cursor.close(); conn.close()
        return redirect(url_for('settings'))
    
    cursor.execute("SELECT * FROM app_settings")
    res = {row['setting_key']: row['setting_value'] for row in cursor.fetchall()}
    cursor.close(); conn.close()
    return render_template('settings.html', settings=res)

# --- USER MANAGEMENT ACTIONS ---
@app.route('/admin/promote/<target_id>', methods=['POST'])
def promote_user(target_id):
    if not session.get('user'): abort(403)
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT role FROM user_preferences WHERE user_id = %s", (session['user'].get('sub'),))
    if cursor.fetchone()['role'] != 'admin': abort(403)
    cursor.execute("UPDATE user_preferences SET role = 'admin' WHERE user_id = %s", (target_id,))
    conn.commit(); cursor.close(); conn.close()
    return redirect(url_for('admin_users'))

@app.route('/admin/demote/<target_id>', methods=['POST'])
def demote_user(target_id):
    if not session.get('user'): abort(403)
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT role FROM user_preferences WHERE user_id = %s", (session['user'].get('sub'),))
    if cursor.fetchone()['role'] != 'admin': abort(403)
    if target_id != session['user'].get('sub'):
        cursor.execute("UPDATE user_preferences SET role = 'member' WHERE user_id = %s", (target_id,))
        conn.commit()
    cursor.close(); conn.close()
    return redirect(url_for('admin_users'))

@app.route('/admin/delete/<target_id>', methods=['POST'])
def delete_user(target_id):
    if not session.get('user'): abort(403)
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT role FROM user_preferences WHERE user_id = %s", (session['user'].get('sub'),))
    if cursor.fetchone()['role'] != 'admin': abort(403)
    if target_id != session['user'].get('sub'):
        cursor.execute("DELETE FROM user_preferences WHERE user_id = %s", (target_id,))
        conn.commit()
    cursor.close(); conn.close()
    return redirect(url_for('admin_users'))

# --- AUTH ROUTES ---
@app.route('/login')
def login(): return authentik.authorize_redirect(url_for('auth_callback', _external=True))

@app.route('/callback')
def auth_callback():
    token = authentik.authorize_access_token()
    user_info = token['userinfo']
    session['user'] = user_info
    
    user_id = user_info.get('sub')
    username = user_info.get('preferred_username', 'Unknown')
    email = user_info.get('email', 'No Email')
    user_groups = user_info.get('groups', [])
    
    admin_group_env = os.getenv("OIDC_ADMIN_GROUP")
    determined_role = None
    if admin_group_env:
        determined_role = 'admin' if admin_group_env in user_groups else 'member'
            
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM user_preferences WHERE user_id = %s", (user_id,))
    existing_user = cursor.fetchone()
    
    if not existing_user:
        if not determined_role: determined_role = 'member'
        cursor.execute("SELECT COUNT(*) as count FROM user_preferences")
        if cursor.fetchone()['count'] == 0: determined_role = 'admin'
        cursor.execute("INSERT INTO user_preferences (user_id, theme, role, username, email) VALUES (%s, 'default', %s, %s, %s)", (user_id, determined_role, username, email))
    else:
        if admin_group_env:
            cursor.execute("UPDATE user_preferences SET username=%s, email=%s, role=%s WHERE user_id=%s", (username, email, determined_role, user_id))
        else:
            cursor.execute("UPDATE user_preferences SET username=%s, email=%s WHERE user_id=%s", (username, email, user_id))
            cursor.execute("SELECT COUNT(*) as count FROM user_preferences WHERE role = 'admin'")
            if cursor.fetchone()['count'] == 0: cursor.execute("UPDATE user_preferences SET role = 'admin' WHERE user_id = %s", (user_id,))
    conn.commit(); cursor.close(); conn.close()
    return redirect(url_for('index'))

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('welcome'))

# --- API ROUTES ---
@app.route('/api/search_local')
def search_local():
    if not session.get('user'): abort(403)
    query = request.args.get('q', '')
    if len(query) < 2: return {"results": []}
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    sql = "SELECT id, title, year, poster, media_format FROM movies_v2 WHERE title LIKE %s OR director LIKE %s OR cast LIKE %s ORDER BY title ASC LIMIT 5"
    like_q = f"%{query}%"
    cursor.execute(sql, (like_q, like_q, like_q))
    results = cursor.fetchall(); cursor.close(); conn.close()
    return {"results": results}

@app.route('/api/search_media')
def search_media():
    if not session.get('user'): abort(403)
    query = request.args.get('title'); results = []
    TMDB_KEY = os.getenv("TMDB_API_KEY"); OMDB_KEY = os.getenv("OMDB_API_KEY")
    if TMDB_KEY:
        try:
            res = requests.get(f"https://api.themoviedb.org/3/search/movie?query={query}&language=en-US", headers={"Authorization": f"Bearer {TMDB_KEY}"}, timeout=2).json()
            for m in res.get('results', [])[:4]:
                results.append({"source": "TMDB", "id": m['id'], "title": m.get('title'), "year": m.get('release_date', '')[:4], "poster": f"https://image.tmdb.org/t/p/w200{m.get('poster_path')}" if m.get('poster_path') else None})
        except: pass
    if OMDB_KEY:
        try:
            res = requests.get(f"http://www.omdbapi.com/?apikey={OMDB_KEY}&s={query}&type=movie", timeout=2).json()
            if res.get('Response') == 'True':
                for m in res.get('Search', [])[:4]:
                    results.append({"source": "OMDB", "id": m['imdbID'], "title": m.get('Title'), "year": m.get('Year')[:4], "poster": m.get('Poster') if m.get('Poster') != 'N/A' else None})
        except: pass
    return {"results": results}

@app.route('/api/get_media_details')
def get_media_details():
    if not session.get('user'): abort(403)
    source = request.args.get('source'); media_id = request.args.get('id'); data = {}
    TMDB_KEY = os.getenv("TMDB_API_KEY"); OMDB_KEY = os.getenv("OMDB_API_KEY")
    if source == 'TMDB' and TMDB_KEY:
        m = requests.get(f"https://api.themoviedb.org/3/movie/{media_id}?append_to_response=credits&language=en-US", headers={"Authorization": f"Bearer {TMDB_KEY}"}).json()
        cast = [c['name'] for c in m.get('credits', {}).get('cast', [])[:5]]
        director = next((c['name'] for c in m.get('credits', {}).get('crew', []) if c['job'] == 'Director'), "Unknown")
        rating = round(float(m.get('vote_average')), 1) if m.get('vote_average') else None
        data = {"title": m.get('title'), "year": m.get('release_date', '')[:4], "genre": "/".join([g['name'] for g in m.get('genres', [])]), "director": director, "cast": ", ".join(cast), "runtime": m.get('runtime'), "plot": m.get('overview'), "rating": rating, "poster": f"https://image.tmdb.org/t/p/w500{m.get('poster_path')}" if m.get('poster_path') else None}
    elif source == 'OMDB' and OMDB_KEY:
        m = requests.get(f"http://www.omdbapi.com/?apikey={OMDB_KEY}&i={media_id}&plot=full").json()
        try: rating = float(m.get('imdbRating', 0))
        except: rating = None
        data = {"title": m.get('Title'), "year": m.get('Year')[:4], "genre": m.get('Genre'), "director": m.get('Director'), "cast": m.get('Actors'), "runtime": m.get('Runtime', '').split(' ')[0], "plot": m.get('Plot'), "rating": rating, "poster": m.get('Poster') if m.get('Poster') != 'N/A' else None}
    return data

# --- MAIN APP ROUTES ---

@app.route('/')
def index():
    if not session.get('user'): return redirect(url_for('welcome'))
    search_query = request.args.get('q', '')
    selected_genres = request.args.getlist('genre') 
    decade_filter = request.args.get('decade', '')
    sort_by = request.args.get('sort_by', 'title_asc')
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    query = "SELECT id, title, year, genre, rating, poster, media_format, is_ripped, is_locked, placement FROM movies_v2 WHERE 1=1"
    params = []
    if search_query:
        query += " AND (title LIKE %s OR cast LIKE %s OR director LIKE %s)"; params.extend([f"%{search_query}%"]*3)
    if selected_genres:
        for g in selected_genres: query += " AND genre LIKE %s"; params.append(f"%{g}%")
    if decade_filter:
        query += " AND year BETWEEN %s AND %s"; params.extend([int(decade_filter), int(decade_filter) + 9])
    if sort_by == 'title_desc': query += " ORDER BY title DESC"
    elif sort_by == 'genre_asc': query += " ORDER BY genre ASC"
    elif sort_by == 'year_desc': query += " ORDER BY year DESC"
    elif sort_by == 'rating_desc': query += " ORDER BY rating DESC"
    else: query += " ORDER BY title ASC"
    cursor.execute(query, params); movies = cursor.fetchall()
    cursor.execute("SELECT DISTINCT genre FROM movies_v2 WHERE genre IS NOT NULL")
    raw_genres = cursor.fetchall(); genres_set = set()
    for row in raw_genres:
        for g in row['genre'].split('/'): genres_set.add(g.strip())
    cursor.close(); conn.close()
    return render_template('index.html', movies=movies, search_query=search_query, genres=sorted(list(genres_set)), selected_genres=selected_genres, current_decade=decade_filter, current_sort=sort_by)

@app.route('/welcome')
def welcome():
    if session.get('user'): return redirect(url_for('index'))
    return render_template('login_screen.html')

@app.route('/wishlist')
def wishlist():
    if not session.get('user'): return redirect(url_for('welcome'))
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM wishlist ORDER BY FIELD(priority, 'High', 'Medium', 'Low'), title ASC")
    items = cursor.fetchall(); cursor.close(); conn.close()
    return render_template('wishlist.html', items=items)

@app.route('/wishlist/add', methods=['POST'])
def add_to_wishlist():
    if not session.get('user'): abort(403)
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("INSERT INTO wishlist (title, year, genre, poster, priority) VALUES (%s, %s, %s, %s, %s)", 
                  (request.form.get('title'), request.form.get('year'), request.form.get('genre'), request.form.get('poster'), request.form.get('priority', 'Medium')))
    conn.commit(); cursor.close(); conn.close()
    return redirect(url_for('wishlist'))

@app.route('/wishlist/delete/<int:item_id>', methods=['POST'])
def delete_wishlist_item(item_id):
    if not session.get('user'): abort(403)
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("DELETE FROM wishlist WHERE id = %s", (item_id,)); conn.commit(); cursor.close(); conn.close()
    return redirect(url_for('wishlist'))

@app.route('/wishlist/move/<int:item_id>', methods=['POST'])
def move_to_collection(item_id):
    if not session.get('user'): abort(403)
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM wishlist WHERE id = %s", (item_id,))
    item = cursor.fetchone()
    if item:
        cursor.execute("INSERT INTO movies_v2 (title, year, genre, poster, rating, media_format, is_ripped, is_locked, placement) VALUES (%s, %s, %s, %s, %s, NULL, 0, 0, NULL)", 
                      (item['title'], item['year'], item['genre'], item['poster'], 0.0))
        log_change("ADDED_FROM_WISHLIST", item['title'], "Moved from Wishlist")
        cursor.execute("DELETE FROM wishlist WHERE id = %s", (item_id,)); conn.commit()
    cursor.close(); conn.close()
    return redirect(url_for('index'))

@app.route('/add', methods=['GET', 'POST'])
def add_movie():
    if not session.get('user'): return redirect(url_for('welcome'))
    if request.method == 'POST':
        if request.form.get('save_target') == 'wishlist': return add_to_wishlist()
        conn = get_db_connection(); cursor = conn.cursor()
        sql = """INSERT INTO movies_v2 (title, year, genre, director, cast, runtime, plot, poster, rating, media_format, is_ripped, is_locked, placement) 
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""
        data = (request.form.get('title'), request.form.get('year') or None, request.form.get('genre') or None, 
                request.form.get('director') or None, request.form.get('cast') or None, request.form.get('runtime') or None, 
                request.form.get('plot') or None, request.form.get('poster') or None, request.form.get('rating') or None, 
                request.form.get('media_format') or None, 1 if request.form.get('is_ripped') == 'on' else 0, 
                1 if request.form.get('is_locked') == 'on' else 0, request.form.get('placement') or None)
        cursor.execute(sql, data)
        method = request.form.get('update_method', 'Manual Entry')
        log_change("ADDED", request.form.get('title'), f"Method: {method}")
        conn.commit(); cursor.close(); conn.close()
        return redirect(url_for('index'))
    return render_template('add_movie.html')

@app.route('/movie/edit/<int:movie_id>', methods=['GET', 'POST'])
def edit_movie(movie_id):
    if not session.get('user'): return redirect(url_for('welcome'))
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM movies_v2 WHERE id = %s", (movie_id,)); old_movie = cursor.fetchone()
    if not old_movie: cursor.close(); conn.close(); abort(404)
    
    if request.method == 'POST':
        changes = generate_diff(old_movie, request.form)
        method = request.form.get('update_method', 'Manual Edit')
        if changes: log_change("UPDATED", old_movie['title'], f"Method: {method} | Changes: [{changes}]")
        
        sql = """UPDATE movies_v2 SET title=%s, year=%s, genre=%s, director=%s, cast=%s, 
                 runtime=%s, plot=%s, poster=%s, rating=%s, media_format=%s, is_ripped=%s, is_locked=%s, placement=%s 
                 WHERE id=%s"""
        data = (request.form.get('title'), request.form.get('year') or None, request.form.get('genre') or None, 
                request.form.get('director') or None, request.form.get('cast') or None, request.form.get('runtime') or None, 
                request.form.get('plot') or None, request.form.get('poster') or None, request.form.get('rating') or None, 
                request.form.get('media_format') or None, 1 if request.form.get('is_ripped') == 'on' else 0, 
                1 if request.form.get('is_locked') == 'on' else 0, request.form.get('placement') or None, movie_id)
        cursor.execute(sql, data); conn.commit(); cursor.close(); conn.close()
        return redirect(url_for('movie_detail', movie_id=movie_id))
    
    cursor.close(); conn.close()
    return render_template('edit_movie.html', movie=old_movie)

@app.route('/movie/<int:movie_id>')
def movie_detail(movie_id):
    if not session.get('user'): return redirect(url_for('welcome'))
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM movies_v2 WHERE id = %s", (movie_id,)); movie = cursor.fetchone()
    cursor.execute("SELECT setting_value FROM app_settings WHERE setting_key = 'edit_mode'"); mode_row = cursor.fetchone()
    edit_mode_enabled = (mode_row and mode_row['setting_value'] == '1')
    cursor.close(); conn.close()
    if not movie: abort(404)
    return render_template('detail.html', movie=movie, edit_mode_enabled=edit_mode_enabled)

@app.route('/movie/delete/<int:movie_id>', methods=['POST'])
def delete_movie(movie_id):
    if not session.get('user'): abort(403)
    conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT setting_value FROM app_settings WHERE setting_key = 'edit_mode'"); mode_row = cursor.fetchone()
    if mode_row and mode_row['setting_value'] == '1':
        cursor.execute("SELECT title FROM movies_v2 WHERE id = %s", (movie_id,)); m = cursor.fetchone()
        log_change("DELETED", m['title'] if m else "Unknown", "Permanent Delete")
        cursor.execute("DELETE FROM movies_v2 WHERE id = %s", (movie_id,)); conn.commit()
    cursor.close(); conn.close()
    return redirect(url_for('index'))

if __name__ == '__main__':
    if os.environ.get("SKIP_DB_INIT") != "true":
        init_db()
    
    app.run(host='0.0.0.0', port=5000)
