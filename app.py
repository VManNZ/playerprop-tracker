import streamlit as st
import requests
import json
import io
import time
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError

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
TARGET_BOOKMAKER_KEY = 'draftkings' 

MARKET_ORDER = [
    'player_points', 'player_rebounds', 'player_assists',
    'player_points_rebounds_assists', 'player_points_rebounds',
    'player_points_assists', 'player_rebounds_assists'
]
TOTALS_MARKET = 'totals'

# --- GOOGLE DRIVE FUNCTIONS ---
def get_drive_service():
    return build('drive', 'v3', credentials=GCP_CREDS)

def get_snapshot_file_id(service):
    query = f"'{DRIVE_FOLDER_ID}' in parents and name = '{SNAPSHOT_FILENAME}' and trashed = false"
    results = service.files().list(q=query, orderBy='modifiedTime desc', fields="files(id, name, modifiedTime)").execute()
    files = results.get('files', [])
    if not files: return None
    return files[0]['id']

def save_snapshot_to_drive(data):
    try:
        service = get_drive_service()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        payload = {"last_updated": timestamp, "data": data}
        
        file_id = get_snapshot_file_id(service)
        file_content = json.dumps(payload)
        media = MediaIoBaseUpload(io.BytesIO(file_content.encode('utf-8')), mimetype='application/json')
        
        if file_id:
            service.files().update(fileId=file_id, media_body=media).execute()
            return f"Snapshot Updated ({timestamp})"
        else:
            file_metadata = {'name': SNAPSHOT_FILENAME, 'parents': [DRIVE_FOLDER_ID]}
            service.files().create(body=file_metadata, media_body=media).execute()
            return f"Snapshot Created ({timestamp})"
    except Exception as e:
        st.error(f"Drive Error: {e}")
        return None

def load_snapshot_from_drive():
    try:
        service = get_drive_service()
        file_id = get_snapshot_file_id(service)
        if not file_id: return None, None
        
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while done is False: status, done = downloader.next_chunk()
        fh.seek(0)
        content = json.load(fh)
        
        if "data" in content:
            return content.get("last_updated"), content.get("data")
        elif "games" in content:
            return content.get("last_updated"), {"props": content["games"], "totals": []}
        else:
            return "Unknown", {"props": content, "totals": []}
            
    except Exception as e:
        st.error(f"Error loading Snapshot: {e}")
        return None, None

# --- API FUNCTIONS (DIRECT) ---

def get_active_games():
    """Fetches currently active games."""
    url = f'https://api.the-odds-api.com/v4/sports/{SPORT}/events'
    params = {'apiKey': API_KEY}
    try:
        response = requests.get(url, params=params)
        if 'x-requests-remaining' in response.headers:
            st.session_state['api_remaining'] = response.headers['x-requests-remaining']
        return response.json() if response.status_code == 200 else []
    except:
        return []

def get_odds_for_game(game_id, markets):
    """Fetches odds for a specific game and market."""
    url = f'https://api.the-odds-api.com/v4/sports/{SPORT}/events/{game_id}/odds'
    params = {
        'apiKey': API_KEY,
        'regions': 'us,eu',
        'markets': markets,
        'oddsFormat': 'decimal',
        'bookmakers': TARGET_BOOKMAKER_KEY
    }
    try:
        response = requests.get(url, params=params)
        return response.json() if response.status_code == 200 else None
    except:
        return None

def fetch_all_odds(game_ids, mode="props"):
    """Fetches odds for a list of games."""
    all_data = []
    market_string = ','.join(MARKET_ORDER) if mode == "props" else TOTALS_MARKET
    
    progress_bar = st.progress(0)
    for i, game_id in enumerate(game_ids):
        data = get_odds_for_game(game_id, market_string)
        if data: all_data.append(data)
        progress_bar.progress((i + 1) / len(game_ids))
    progress_bar.empty()
    return all_data

# --- DATA PROCESSING ---

def flatten_data(game_data_list, is_totals=False):
    flat_list = []
    if not game_data_list: return flat_list
    
    for game in game_data_list:
        home = game.get('home_team', 'Unknown')
        away = game.get('away_team', 'Unknown')
        matchup = f"{away} @ {home}"
        
        for book in game.get('bookmakers', []):
            if book['key'] != TARGET_BOOKMAKER_KEY: continue
            
            for market in book.get('markets', []):
                # Filter strictly based on mode
                if is_totals and market['key'] != 'totals': continue
                if not is_totals and market['key'] == 'totals': continue

                for outcome in market.get('outcomes', []):
                    # For Totals
                    if is_totals:
                        if outcome['name'] == 'Over':
                            over_price = outcome['price']
                            under_outcome = next((o for o in market['outcomes'] if o['name'] == 'Under'), None)
                            under_price = under_outcome['price'] if under_outcome else '-'
                            
                            flat_list.append({
                                "unique_key": matchup, # Key for totals is just matchup
                                "matchup": matchup,
                                "market_key": "totals",
                                "line": outcome['point'],
                                "over": over_price,
                                "under": under_price
                            })
                    # For Player Props
                    else:
                        if outcome['name'] == 'Over':
                            over_price = outcome['price']
                            under_outcome = next((o for o in market['outcomes'] if o['name'] == 'Under'), None)
                            under_price = under_outcome['price'] if under_outcome else '-'
                            
                            # Key for props is Player + Market
                            key = f"{outcome['description']}|{market['key']}"
                            
                            flat_list.append({
                                "unique_key": key,
                                "player": outcome['description'],
                                "market_key": market['key'],
                                "line": outcome['point'],
                                "over": over_price,
                                "under": under_price,
                                "matchup": matchup
                            })
    return flat_list

# --- APP LAYOUT ---
st.set_page_config(page_title="NBA Tracker", page_icon="🏀", layout="wide")
st.title("🏀 NBA Tracker (Direct Mode)")

# Sidebar
st.sidebar.header("Controls")

if st.sidebar.button("📸 Take Snapshot"):
    with st.spinner("Fetching Fresh Data..."):
        games = get_active_games()
        if games:
            game_ids = [g['id'] for g in games]
            props = fetch_all_odds(game_ids, mode="props")
            totals = fetch_all_odds(game_ids, mode="totals")
            
            payload = {"props": props, "totals": totals}
            msg = save_snapshot_to_drive(payload)
            st.sidebar.success(msg)
            time.sleep(1)
            st.rerun()
        else:
            st.error("No active games found.")

if 'api_remaining' in st.session_state:
    st.sidebar.write(f"Credits: **{st.session_state['api_remaining']}**")

# Load Snapshot Context
try:
    snap_ts, snap_data = load_snapshot_from_drive()
    if snap_ts:
        st.sidebar.info(f"Snapshot: {snap_ts}")
        props_snap = flatten_data(snap_data.get('props', []), is_totals=False)
        totals_snap = flatten_data(snap_data.get('totals', []), is_totals=True)
        
        # Create Maps
        props_map = {x['unique_key']: x for x in props_snap}
        totals_map = {x['unique_key']: x for x in totals_snap}
    else:
        st.sidebar.warning("No Snapshot Found")
        props_map, totals_map = {}, {}
except:
    props_map, totals_map = {}, {}

# View Controls
mode = st.sidebar.radio("Mode", ["Player Props", "Game Totals"])
threshold = 0

if mode == "Player Props":
    threshold = st.sidebar.slider("Min Diff (+/-)", 1.0, 10.0, 3.0, 0.5)
else:
    threshold = st.sidebar.slider("Min Diff (+/-)", 1.0, 15.0, 4.0, 0.5)

# Main Action
if st.button("🚀 Compare Live Data"):
    with st.spinner("Fetching Live Odds..."):
        games = get_active_games()
        if not games:
            st.error("No active games found.")
            st.stop()
            
        game_ids = [g['id'] for g in games]
        
        if mode == "Player Props":
            live_raw = fetch_all_odds(game_ids, mode="props")
            live_flat = flatten_data(live_raw, is_totals=False)
            compare_map = props_map
        else:
            live_raw = fetch_all_odds(game_ids, mode="totals")
            live_flat = flatten_data(live_raw, is_totals=True)
            compare_map = totals_map
            
        # Comparison Logic
        results = []
        for live_item in live_flat:
            key = live_item['unique_key']
            
            if key in compare_map:
                pre_item = compare_map[key]
                
                # Check line validity
                if live_item['line'] is not None and pre_item['line'] is not None:
                    diff = live_item['line'] - pre_item['line']
                    
                    if abs(diff) >= threshold:
                        results.append({
                            **live_item,
                            "live_val": live_item['line'],
                            "pre_val": pre_item['line'],
                            "diff": diff
                        })
        
        # Display Results
        if results:
            results.sort(key=lambda x: abs(x['diff']), reverse=True)
            st.subheader(f"Found {len(results)} Movers")
            
            for res in results:
                with st.container():
                    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
                    
                    # Label
                    if mode == "Player Props":
                        pretty_market = res['market_key'].replace('player_', '').replace('_', ' ').title()
                        c1.markdown(f"**{res['player']}**")
                        c1.caption(f"{pretty_market} | {res['matchup']}")
                    else:
                        c1.markdown(f"**{res['matchup']}**")
                        c1.caption("Total Points")
                        
                    # Metrics
                    c2.metric("Live", res['live_val'], delta=f"{res['diff']:.1f}")
                    c3.metric("Pre", res['pre_val'])
                    c4.write(f"O: {res['over']} | U: {res['under']}")
                    st.divider()
        else:
            st.info(f"No moves found greater than {threshold}")
