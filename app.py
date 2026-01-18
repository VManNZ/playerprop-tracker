import streamlit as st
import requests
import json
import os
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
import io

# --- CONFIGURATION ---
try:
    API_KEY = st.secrets["API_KEY"]
    DRIVE_FOLDER_ID = st.secrets["DRIVE_FOLDER_ID"]
    # Load the Google Credentials from the secrets string
    gcp_info = json.loads(st.secrets["GCP_JSON"])
    GCP_CREDS = service_account.Credentials.from_service_account_info(gcp_info)
except:
    st.error("⚠️ Secrets not found! Make sure API_KEY, DRIVE_FOLDER_ID, and GCP_JSON are in secrets.toml")
    st.stop()

SPORT = 'basketball_nba'
SNAPSHOT_FILENAME = 'nba_odds_snapshot.json' # File name in Drive
TARGET_BOOKMAKER_KEY = 'betonlineag' 

# --- GOOGLE DRIVE FUNCTIONS ---
def get_drive_service():
    return build('drive', 'v3', credentials=GCP_CREDS)

def save_snapshot_to_drive(data):
    """Uploads the JSON data to the specific Google Drive Folder."""
    service = get_drive_service()
    
    # 1. Check if file already exists in the folder
    query = f"'{DRIVE_FOLDER_ID}' in parents and name = '{SNAPSHOT_FILENAME}' and trashed = false"
    results = service.files().list(q=query, fields="files(id)").execute()
    files = results.get('files', [])
    
    # Convert data to a file-like object
    file_content = json.dumps(data)
    media = MediaIoBaseUpload(io.BytesIO(file_content.encode('utf-8')), mimetype='application/json')
    
    if files:
        # Update existing file
        file_id = files[0]['id']
        service.files().update(fileId=file_id, media_body=media).execute()
        return "Updated existing snapshot in Drive."
    else:
        # Create new file
        file_metadata = {'name': SNAPSHOT_FILENAME, 'parents': [DRIVE_FOLDER_ID]}
        service.files().create(body=file_metadata, media_body=media).execute()
        return "Created new snapshot in Drive."

def load_snapshot_from_drive():
    """Downloads the JSON data from Drive."""
    service = get_drive_service()
    
    # Find the file
    query = f"'{DRIVE_FOLDER_ID}' in parents and name = '{SNAPSHOT_FILENAME}' and trashed = false"
    results = service.files().list(q=query, fields="files(id)").execute()
    files = results.get('files', [])
    
    if not files:
        return None

    # Download it
    file_id = files[0]['id']
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while done is False:
        status, done = downloader.next_chunk()
        
    fh.seek(0)
    return json.load(fh)

# --- ODDS API FUNCTIONS ---
def get_active_games():
    url = f'https://api.the-odds-api.com/v4/sports/{SPORT}/events'
    params = {'apiKey': API_KEY}
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200: return response.json()
    except: pass
    return []

def get_props_for_game(game_id):
    market_list = 'player_points,player_rebounds,player_assists,player_points_assists,player_points_rebounds,player_points_rebounds_assists,player_rebounds_assists'
    url = f'https://api.the-odds-api.com/v4/sports/{SPORT}/events/{game_id}/odds'
    params = {
        'apiKey': API_KEY,
        'regions': 'us,eu', 
        'markets': market_list,
        'oddsFormat': 'decimal',
        'bookmakers': TARGET_BOOKMAKER_KEY
    }
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200: return response.json()
    except: return None
    return None

def fetch_all_nba_data():
    all_data = []
    games = get_active_games()
    if games:
        status_text = st.empty()
        progress_bar = st.progress(0)
        for i, game in enumerate(games):
            status_text.text(f"Scanning {game['home_team']} vs {game['away_team']}...")
            game_props = get_props_for_game(game['id'])
            if game_props: all_data.append(game_props)
            progress_bar.progress((i + 1) / len(games))
        progress_bar.empty()
        status_text.empty()
    return all_data

# --- APP LAYOUT ---
st.set_page_config(page_title="NBA Tracker + Drive", page_icon="☁️", layout="wide")
st.title("☁️ NBA Tracker (Synced to Google Drive)")

# Sidebar
st.sidebar.header("⚙️ Controls")

if st.sidebar.button("📸 1. Take Pre-Game Snapshot"):
    with st.spinner("Fetching odds & Syncing to Drive..."):
        data = fetch_all_nba_data()
        if data:
            msg = save_snapshot_to_drive(data)
            st.sidebar.success(f"Success: {msg}")

st.sidebar.write("---")
mode = st.sidebar.radio("View Mode", ["🔥 Market Scanner", "🔎 Player Search"])
threshold = 0
search_query = ""

if mode == "🔥 Market Scanner":
    threshold = st.sidebar.slider("Show moves greater than (+/-)", 1.0, 15.0, 4.0, 0.5)
elif mode == "🔎 Player Search":
    search_query = st.text_input("Enter Player Name", "")

if st.button("🚀 2. Compare Live Data"):
    # LOAD FROM DRIVE INSTEAD OF LOCAL DISK
    with st.spinner("Loading Snapshot from Google Drive..."):
        pre_game_data = load_snapshot_from_drive()
    
    if not pre_game_data:
        st.error("⚠️ No snapshot found in your Google Drive! Take a snapshot first.")
        st.stop()

    with st.spinner("Fetching Live Odds..."):
        live_data = fetch_all_nba_data()

    if not live_data:
        st.error("No live games found.")
        st.stop()

    # --- COMPARISON LOGIC (Same as before) ---
    st.subheader(f"Results")
    found_movers = False
    
    pre_map = {}
    for game in pre_game_data:
        for book in game.get('bookmakers', []):
            if book['key'] != TARGET_BOOKMAKER_KEY: continue
            for market in book.get('markets', []):
                for outcome in market.get('outcomes', []):
                    unique_key = f"{outcome['description']}|{market['key']}|{outcome['name']}"
                    pre_map[unique_key] = outcome.get('point')

    for game in live_data:
        for book in game.get('bookmakers', []):
            if book['key'] != TARGET_BOOKMAKER_KEY: continue
            for market in book.get('markets', []):
                for outcome in market.get('outcomes', []):
                    if mode == "🔎 Player Search" and search_query.lower() not in outcome['description'].lower(): continue
                    unique_key = f"{outcome['description']}|{market['key']}|{outcome['name']}"
                    
                    if unique_key in pre_map:
                        pre_line = pre_map[unique_key]
                        live_line = outcome.get('point')
                        if pre_line is not None and live_line is not None:
                            line_diff = live_line - pre_line
                            if mode == "🔥 Market Scanner" and abs(line_diff) < threshold: continue
                            found_movers = True
                            
                            with st.container():
                                col1, col2, col3, col4 = st.columns([2, 1.5, 1, 1])
                                clean_market = market['key'].replace('player_', '').replace('_', ' ').title()
                                col1.markdown(f"**{outcome['description']}**")
                                col1.caption(f"{clean_market} ({outcome['name']})")
                                col2.write(f"🏦 {book['title']}")
                                col3.metric("Live Line", f"{live_line}", delta=f"{line_diff:.1f}")
                                col4.metric("Pre Line", f"{pre_line}")
                                st.caption(f"Odds: {outcome['price']}")
                                st.divider()

    if not found_movers:
        st.info("No matching records found.")
