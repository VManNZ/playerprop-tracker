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
TARGET_BOOKMAKER_KEY = 'fanduel' 

MARKET_ORDER = [
    'player_points', 'player_rebounds', 'player_assists',
    'player_points_rebounds_assists', 'player_points_rebounds',
    'player_points_assists', 'player_rebounds_assists'
]

# --- GOOGLE DRIVE FUNCTIONS ---
def get_drive_service():
    return build('drive', 'v3', credentials=GCP_CREDS)

def get_snapshot_file_id(service):
    """Finds the most recent snapshot file."""
    query = f"'{DRIVE_FOLDER_ID}' in parents and name = '{SNAPSHOT_FILENAME}' and trashed = false"
    results = service.files().list(q=query, orderBy='modifiedTime desc', fields="files(id, name, modifiedTime)").execute()
    files = results.get('files', [])
    if not files: return None, 0
    if len(files) > 1: st.toast(f"⚠️ Found {len(files)} duplicate files. Using newest.", icon="⚠️")
    return files[0]['id'], len(files)

def save_snapshot_to_drive(data):
    try:
        service = get_drive_service()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        payload = {"last_updated": timestamp, "games": data}
        
        file_id, count = get_snapshot_file_id(service)
        file_content = json.dumps(payload)
        media = MediaIoBaseUpload(io.BytesIO(file_content.encode('utf-8')), mimetype='application/json')
        
        if file_id:
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
        file_id, count = get_snapshot_file_id(service)
        if not file_id: return None, None

        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while done is False: status, done = downloader.next_chunk()
        fh.seek(0)
        
        content = json.load(fh)
        # Handle "Blank File" list [] gracefully
        if isinstance(content, list): 
            return "No Data (Blank File)", content
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
    market_list = ','.join(MARKET_ORDER)
    url = f'https://api.the-odds-api.com/v4/sports/{SPORT}/events/{game_id}/odds'
    params = {
        'apiKey': API_KEY, 'regions': 'us,eu', 
        'markets': market_list, 'oddsFormat': 'decimal',
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
    
    # 🛑 DEBUG: Check if games exist at all
    if not games:
        st.error("⚠️ API returned 0 active games. (Are there games scheduled today?)")
        return []

    if games:
        status_text = st.empty()
        progress_bar = st.progress(0)
        for i, game in enumerate(games):
            status_text.text(f"Scanning {game['home_team']} vs {game['away_team']}...")
            game_props = get_props_for_game(game['id'])
            if game_props: 
                all_data.append(game_props)
            progress_bar.progress((i + 1) / len(games))
        progress_bar.empty()
        status_text.empty()
        
    return all_data

def flatten_data(game_data_list):
    flat_list = []
    found_bookies = set()
    if not game_data_list: return flat_list, found_bookies
    
    for game in game_data_list:
        for book in game.get('bookmakers', []):
            found_bookies.add(book['key'])
            if book['key'] != TARGET_BOOKMAKER_KEY: continue
            for market in book.get('markets', []):
                for outcome in market.get('outcomes', []):
                    if outcome['name'] == 'Over':
                        over_price = outcome['price']
                        under_outcome = next((o for o in market['outcomes'] if o['name'] == 'Under'), None)
                        under_price = under_outcome['price'] if under_outcome else '-'
                        flat_list.append({
                            "player": outcome['description'], "market_key": market['key'],
                            "line": outcome['point'], "over": over_price, "under": under_price,
                            "book": book['title']
                        })
    return flat_list, found_bookies

# --- APP LAYOUT ---
st.set_page_config(page_title="NBA Tracker + Drive", page_icon="☁️", layout="wide")
st.title("☁️ NBA Tracker (FanDuel)")

# Sidebar
st.sidebar.header("⚙️ Controls")

if st.sidebar.button("📸 1. Take Pre-Game Snapshot"):
    with st.spinner("Fetching odds..."):
        data = fetch_all_nba_data()
        
        if data:
            with st.spinner("Syncing to Drive..."):
                msg = save_snapshot_to_drive(data)
                if msg: st.sidebar.success(f"{msg}")
                time.sleep(2) 
                st.rerun()
        else:
            # 🛑 ERROR ALERT IF NO DATA FOUND
            st.sidebar.error("❌ Scan failed: No odds found for FanDuel.")
            st.sidebar.warning("Possible reasons:\n1. No games today.\n2. Odds not released yet.\n3. API Key limit reached.")

# LOAD TIMESTAMP
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
        st.warning("⚠️ No live games active. Showing Snapshot data only.")

    pre_flat, pre_bookies = flatten_data(pre_game_data)
    live_flat, live_bookies = flatten_data(live_data)
    
    if not pre_flat:
        st.error(f"❌ Your snapshot is empty for '{TARGET_BOOKMAKER_KEY}'!")
        if pre_bookies: st.warning(f"Found data for: {', '.join(pre_bookies)}")
        st.info("💡 FIX: Click 'Take Pre-Game Snapshot' in the sidebar.")
        st.stop()

    pre_map = {f"{x['player']}|{x['market_key']}": x for x in pre_flat}
    live_map = {f"{x['player']}|{x['market_key']}": x for x in live_flat}
    results_list = []

    if mode == "🔥 Market Scanner":
        if not live_flat:
            st.error("Scanner requires live games. None found.")
            st.stop()  
        for key, live_item in live_map.items():
            if key in pre_map:
                pre_item = pre_map[key]
                if live_item['line'] is not None and pre_item['line'] is not None:
                    diff = live_item['line'] - pre_item['line']
                    if abs(diff) >= threshold:
                        results_list.append({**live_item, "live_display": live_item['line'], "pre_display": pre_item['line'], "diff": diff, "status": "active"})

    elif mode == "🔎 Player Search":
        if not search_query:
            st.warning("Please enter a player name.")
            st.stop()
        found_match = False
        for key, pre_item in pre_map.items():
            if search_query.lower() in pre_item['player'].lower():
                found_match = True
                if key in live_map:
                    live_item = live_map[key]
                    diff = live_item['line'] - pre_item['line']
                    results_list.append({**live_item, "live_display": live_item['line'], "pre_display": pre_item['line'], "diff": diff, "status": "active"})
                else:
                    results_list.append({**pre_item, "live_display": "No Live Game", "pre_display": pre_item['line'], "diff": 0, "status": "inactive"})
        if not found_match: st.warning(f"No player found matching '{search_query}'.")

    if not results_list:
        st.info("No records found.")
    else:
        st.subheader(f"Results ({len(results_list)})")
        if ts: st.caption(f"Comparing against snapshot from: {ts}")
        results_list.sort(key=lambda x: (x['player'], MARKET_ORDER.index(x['market_key']) if x['market_key'] in MARKET_ORDER else 99))

        for item in results_list:
            with st.container():
                col1, col2, col3, col4 = st.columns([2, 1.5, 1.5, 1])
                m_key = item['market_key']
                if m_key == 'player_points_assists': pretty = "Points + Assists"
                elif m_key == 'player_points_rebounds': pretty = "Points + Rebounds"
                elif m_key == 'player_rebounds_assists': pretty = "Rebounds + Assists"
                elif m_key == 'player_points_rebounds_assists': pretty = "Pts + Rebs + Asts"
                else: pretty = m_key.replace('player_', '').replace('_', ' ').title()

                col1.markdown(f"**{item['player']}**")
                col1.caption(f"{pretty}")
                if item['status'] == 'inactive':
                    col2.metric("Live Line", "N/A", delta=None)
                    col2.caption("No Live Game")
                else:
                    col2.metric("Live Line", f"{item['live_display']}", delta=f"{item['diff']:.1f}")
                col3.metric("Pre Line", f"{item['pre_display']}")
                col4.write(f"**Over:** {item['over']}")
                col4.write(f"**Under:** {item['under']}")
                st.divider()

    with st.expander("🛠️ Debug Information"):
        st.write(f"**Target Bookmaker:** `{TARGET_BOOKMAKER_KEY}`")
        st.write(f"**Snapshot Data Size:** {len(pre_flat)} props")
