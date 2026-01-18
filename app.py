import streamlit as st
import requests
import json
import os
from datetime import datetime
import time
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError
import io

# --- CONFIGURATION ---
try:
    API_KEY = st.secrets["API_KEY"]
    DRIVE_FOLDER_ID = st.secrets["DRIVE_FOLDER_ID"]
    gcp_info = json.loads(st.secrets["GCP_JSON"])
    
    SCOPES = ['https://www.googleapis.com/auth/drive']
    GCP_CREDS = service_account.Credentials.from_service_account_info(
        gcp_info, scopes=SCOPES
    )
except Exception as e:
    st.error(f"⚠️ Secret Config Error: {e}")
    st.stop()

SPORT = 'basketball_nba'
SNAPSHOT_FILENAME = 'nba_odds_snapshot.json'
TARGET_BOOKMAKER_KEY = 'betonlineag' 

# 👇 DEFINING YOUR CUSTOM SORT ORDER
MARKET_ORDER = [
    'player_points',
    'player_rebounds',
    'player_assists',
    'player_points_rebounds_assists',
    'player_points_rebounds',
    'player_points_assists',
    'player_rebounds_assists'
]

# --- GOOGLE DRIVE FUNCTIONS ---
def get_drive_service():
    return build('drive', 'v3', credentials=GCP_CREDS)

def save_snapshot_to_drive(data):
    try:
        service = get_drive_service()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        payload = {"last_updated": timestamp, "games": data}
        
        query = f"'{DRIVE_FOLDER_ID}' in parents and name = '{SNAPSHOT_FILENAME}' and trashed = false"
        results = service.files().list(q=query, fields="files(id)").execute()
        files = results.get('files', [])
        
        file_content = json.dumps(payload)
        media = MediaIoBaseUpload(io.BytesIO(file_content.encode('utf-8')), mimetype='application/json')
        
        if files:
            file_id = files[0]['id']
            service.files().update(fileId=file_id, media_body=media).execute()
            return f"Updated snapshot ({timestamp})"
        else:
            file_metadata = {'name': SNAPSHOT_FILENAME, 'parents': [DRIVE_FOLDER_ID]}
            service.files().create(body=file_metadata, media_body=media).execute()
            return f"Created new snapshot ({timestamp})"
            
    except HttpError as error:
        st.error(f"🛑 Google Drive Error: {error.content.decode('utf-8')}")
        return None
    except Exception as e:
        st.error(f"🛑 Unexpected Error: {e}")
        return None

def load_snapshot_from_drive():
    try:
        service = get_drive_service()
        query = f"'{DRIVE_FOLDER_ID}' in parents and name = '{SNAPSHOT_FILENAME}' and trashed = false"
        results = service.files().list(q=query, fields="files(id)").execute()
        files = results.get('files', [])
        
        if not files: return None, None

        file_id = files[0]['id']
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while done is False:
            status, done = downloader.next_chunk()
        fh.seek(0)
        
        content = json.load(fh)
        if isinstance(content, list): return "Old Format", content
        return content.get("last_updated"), content.get("games")
        
    except Exception as e:
        st.error(f"Error loading from Drive: {e}")
        return None, None

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
    # Requesting all 7 markets
    market_list = ','.join(MARKET_ORDER)
    
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
st.title("☁️ NBA Tracker (Synced to Drive)")

# Sidebar
st.sidebar.header("⚙️ Controls")

if st.sidebar.button("📸 1. Take Pre-Game Snapshot"):
    with st.spinner("Fetching odds & Syncing to Drive..."):
        data = fetch_all_nba_data()
        if data:
            msg = save_snapshot_to_drive(data)
            if msg: st.sidebar.success(f"{msg}")
            time.sleep(1) 
            st.rerun() 

try:
    last_ts, _ = load_snapshot_from_drive()
    if last_ts: st.sidebar.info(f"🕒 Snapshot: {last_ts}")
    else: st.sidebar.warning("⚠️ No Snapshot found")
except: pass

st.sidebar.write("---")
mode = st.sidebar.radio("View Mode", ["🔥 Market Scanner", "🔎 Player Search"])
threshold = 0
search_query = ""

if mode == "🔥 Market Scanner":
    threshold = st.sidebar.slider("Show moves greater than (+/-)", 1.0, 15.0, 4.0, 0.5)
elif mode == "🔎 Player Search":
    search_query = st.text_input("Enter Player Name", "")

if st.button("🚀 2. Compare Live Data"):
    
    with st.spinner("Loading Snapshot..."):
        ts, pre_game_data = load_snapshot_from_drive()
    
    if not pre_game_data:
        st.error("⚠️ No snapshot data found.")
        st.stop()

    with st.spinner("Fetching Live Odds..."):
        live_data = fetch_all_nba_data()

    if not live_data:
        st.error("No live games found.")
        st.stop()

    # --- 1. BUILD PRE-GAME MAP ---
    # Key: "Player Name | Market Key" -> Value: Point (Line)
    pre_map = {}
    for game in pre_game_data:
        for book in game.get('bookmakers', []):
            if book['key'] != TARGET_BOOKMAKER_KEY: continue
            for market in book.get('markets', []):
                for outcome in market.get('outcomes', []):
                    # We only need the line from one side (Over) to know the pre-game line
                    unique_key = f"{outcome['description']}|{market['key']}"
                    pre_map[unique_key] = outcome.get('point')

    # --- 2. SCAN LIVE DATA & COLLECT RESULTS ---
    results_list = []

    for game in live_data:
        for book in game.get('bookmakers', []):
            if book['key'] != TARGET_BOOKMAKER_KEY: continue
            
            for market in book.get('markets', []):
                market_key = market['key']
                outcomes = market.get('outcomes', [])
                
                # We need at least one outcome to get the player name & line
                if not outcomes: continue
                
                player_name = outcomes[0]['description']
                live_line = outcomes[0].get('point')
                
                # Check Filter: Player Search
                if mode == "🔎 Player Search" and search_query.lower() not in player_name.lower():
                    continue

                # Check Pre-Game Comparison
                unique_key = f"{player_name}|{market_key}"
                pre_line = pre_map.get(unique_key)
                
                if pre_line is not None and live_line is not None:
                    line_diff = live_line - pre_line
                    
                    # Check Filter: Threshold
                    if mode == "🔥 Market Scanner" and abs(line_diff) < threshold:
                        continue
                    
                    # Get Odds for Over/Under
                    over_price = next((o['price'] for o in outcomes if o['name'] == 'Over'), '-')
                    under_price = next((o['price'] for o in outcomes if o['name'] == 'Under'), '-')
                    
                    # Add to list for sorting
                    results_list.append({
                        "player": player_name,
                        "market_key": market_key,
                        "live_line": live_line,
                        "pre_line": pre_line,
                        "diff": line_diff,
                        "over": over_price,
                        "under": under_price,
                        "book": book['title']
                    })

    # --- 3. SORT & DISPLAY ---
    if not results_list:
        st.info("No records found.")
    else:
        st.subheader(f"Results ({len(results_list)})")
        if ts: st.caption(f"Comparing against snapshot from: {ts}")
        
        # Sort Logic: First by Player Name, THEN by your Custom Market Order
        # We use the index in MARKET_ORDER to determine rank (0 to 6)
        results_list.sort(key=lambda x: (
            x['player'], 
            MARKET_ORDER.index(x['market_key']) if x['market_key'] in MARKET_ORDER else 99
        ))

        for item in results_list:
            with st.container():
                col1, col2, col3, col4 = st.columns([2, 1.5, 1.5, 1])
                
                # Pretty Market Name
                m_key = item['market_key']
                if m_key == 'player_points_assists': pretty_market = "Points + Assists"
                elif m_key == 'player_points_rebounds': pretty_market = "Points + Rebounds"
                elif m_key == 'player_rebounds_assists': pretty_market = "Rebounds + Assists"
                elif m_key == 'player_points_rebounds_assists': pretty_market = "Pts + Rebs + Asts"
                else: pretty_market = m_key.replace('player_', '').replace('_', ' ').title()

                # Column 1: Name & Market
                col1.markdown(f"**{item['player']}**")
                col1.caption(f"{pretty_market}")
                
                # Column 2: The Lines (With Delta)
                col2.metric("Live Line", f"{item['live_line']}", delta=f"{item['diff']:.1f}")
                
                # Column 3: Pre-Game Reference
                col3.metric("Pre Line", f"{item['pre_line']}")
                
                # Column 4: Combined Odds
                col4.write(f"**Over:** {item['over']}")
                col4.write(f"**Under:** {item['under']}")
                
                st.divider()
