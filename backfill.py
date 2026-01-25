import mysql.connector, os, requests, time

conn = mysql.connector.connect(
    host=os.getenv("DB_HOST"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    database=os.getenv("DB_NAME")
)
cursor = conn.cursor(dictionary=True)

# Get TMDB Token
cursor.execute("SELECT setting_value FROM app_settings WHERE setting_key = 'tmdb_api_key'")
res_token = cursor.fetchone()
if not res_token:
    print("Error: No TMDB token found in app_settings table.")
    exit()

token = res_token['setting_value']
headers = {"Authorization": f"Bearer {token}"}

# Find movies missing data
cursor.execute("SELECT id, title FROM movies_v2 WHERE poster IS NULL")
movies = cursor.fetchall()

for m in movies:
    print(f"Updating: {m['title']}...")
    search_url = f"https://api.themoviedb.org/3/search/movie?query={m['title']}"
    try:
        res = requests.get(search_url, headers=headers).json()
        
        if res.get('results'):
            data = res['results'][0]
            
            # Robust Year Handling
            raw_date = data.get('release_date', '')
            movie_year = raw_date[:4] if (raw_date and len(raw_date) >= 4) else None
            
            # Robust Poster Handling
            poster_path = data.get('poster_path')
            poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None
            
            sql = "UPDATE movies_v2 SET plot=%s, poster=%s, rating=%s, year=%s WHERE id=%s"
            cursor.execute(sql, (data.get('overview'), poster_url, data.get('vote_average'), movie_year, m['id']))
            conn.commit()
            time.sleep(0.2)
    except Exception as e:
        print(f"Failed to update {m['title']}: {e}")

cursor.close(); conn.close()
print("Done!")
