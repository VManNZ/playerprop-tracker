import streamlit as st
import requests
import json
import io
import time
import pandas as pd
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

# --- 1. CONFIG MUST BE FIRST ---
st.set_page_config(page_title="NBA Tracker", page_icon="🏀", layout="wide")

# --- 2. SETUP & AUTH ---
try:
    if "API_KEY" not in st.secrets:
        st.error("❌ Missing 'API_KEY' in secrets.toml")
        st.stop()
        
    API_KEY = st.secrets["API_KEY"]
    DRIVE_FOLDER_ID = st.secrets["DRIVE_FOLDER_ID"]
    gcp_info = json.loads(st.secrets["GCP_JSON"])
    
    SCOPES = ['https://www.googleapis.com/auth/drive']
    GCP_CREDS = service_account.Credentials.from_service_account_info(
        gcp_info, scopes=SCOPES
    )
except Exception as e:
    st.error(f"⚠️ Configuration Error: {e}")
    st.stop()

# Constants
SPORT = 'basketball_nba'
SNAPSHOT_FILENAME = 'nba_odds_snapshot.json'
TARGET_BOOKMAKER_KEY = 'draftkings'
MARKET_ORDER = [
    'player_points', 'player_rebounds', 'player_assists',
    'player_points_rebounds_assists', 'player_points_rebounds',
    'player_points_assists', 'player_rebounds_assists'
]
TOTALS_MARKET = 'totals'

# --- 3. GOOGLE DRIVE FUNCTIONS ---
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
            service.files().update(
                fileId=file_id, 
                media_body=media
            ).execute()
            return f"Snapshot Updated ({timestamp})"
        else:
            file_metadata = {'name': SNAPSHOT_FILENAME, 'parents': [DRIVE_FOLDER_ID]}
            service.files().create(
                body=file_metadata, 
                media_body=media
            ).execute()
            return f"Snapshot Created ({timestamp})"
    except Exception as e:
        st.error(f"Drive Error: {e}")
        return None

@st.cache_data(ttl=300) 
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

# --- 4. API FUNCTIONS (Optimized) ---

@st.cache_data(ttl=3600)
def get_active_games():
    """Fetches currently active games. Cached for 1 hour."""
    url = f'https://api.the-odds-api.com/v4/sports/{SPORT}/events'
    params = {'apiKey': API_KEY}
    try:
        response = requests.get(url, params=params)
        if 'x-requests-remaining' in response.headers:
            st.session_state['api_remaining'] = response.headers['x-requests-remaining']
        return response.json() if response.status_code == 200 else []
    except:
        return []

# OPTIMIZATION: Cache individual game fetches for 120s
# This ensures that if the list of games changes slightly, we don't re-fetch unchanged games.
@st.cache_data(ttl=120)
def get_odds_for_game(game_id, markets):
    """Fetches odds for a specific game. Cached for 120 seconds."""
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
        if 'x-requests-remaining' in response.headers:
            st.session_state['api_remaining'] = response.headers['x-requests-remaining']
        return response.json() if response.status_code == 200 else None
    except:
        return None

# OPTIMIZATION: Increased TTL to 120s
@st.cache_data(ttl=120, show_spinner=False)
def fetch_all_odds_cached(game_ids, mode="props"):
    """Batch fetcher. Uses cached individual calls internally."""
    all_data = []
    market_string = ','.join(MARKET_ORDER) if mode == "props" else TOTALS_MARKET
    
    progress_bar = st.progress(0)
    for i, game_id in enumerate(game_ids):
        data = get_odds_for_game(game_id, market_string)
        if data: all_data.append(data)
        progress_bar.progress((i + 1) / len(game_ids))
    progress_bar.empty()
    return all_data

# --- 5. DATA PROCESSING ---

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
                # Strict Filtering
                if is_totals and market['key'] != 'totals': continue
                if not is_totals and market['key'] == 'totals': continue

                for outcome in market.get('outcomes', []):
                    if is_totals:
                        # Logic for GAME TOTALS
                        if outcome['name'] == 'Over':
                            over_price = outcome['price']
                            under_outcome = next((o for o in market['outcomes'] if o['name'] == 'Under'), None)
                            under_price = under_outcome['price'] if under_outcome else '-'
                            flat_list.append({
                                "unique_key": matchup, 
                                "matchup": matchup,
                                "market_key": "totals",
                                "line": outcome['point'],
                                "over": over_price,
                                "under": under_price
                            })
                    else:
                        # Logic for PLAYER PROPS
                        if outcome['name'] == 'Over':
                            over_price = outcome['price']
                            under_outcome = next((o for o in market['outcomes'] if o['name'] == 'Under'), None)
                            under_price = under_outcome['price'] if under_outcome else '-'
                            
                            clean_player = outcome['description'].strip()
                            key = f"{clean_player}|{market['key']}"
                            
                            flat_list.append({
                                "unique_key": key,
                                "player": clean_player,
                                "market_key": market['key'],
                                "line": outcome['point'],
                                "over": over_price,
                                "under": under_price,
                                "matchup": matchup
                            })
    return flat_list

# --- 6. APP LAYOUT & STATE ---

st.title("🏀 NBA Tracker (Optimized)")

# Initialize Session State
if 'scan_results' not in st.session_state:
    st.session_state['scan_results'] = None
if 'scan_timestamp' not in st.session_state:
    st.session_state['scan_timestamp'] = None
if 'scan_mode' not in st.session_state:
    st.session_state['scan_mode'] = None

# Sidebar
st.sidebar.header("Controls")

# --- API STATUS ---
if 'api_remaining' in st.session_state:
    st.sidebar.markdown("### 📊 API Status")
    credits = int(st.session_state['api_remaining'])
    if credits > 50:
        st.sidebar.success(f"Credits Left: **{credits}**")
    else:
        st.sidebar.warning(f"Credits Left: **{credits}**")
    st.sidebar.markdown("---")

# OPTIMIZATION: Reduced range to 12h, default 8h to focus on immediate games
hours_window = st.sidebar.slider("Scan games within (Hours)", 1, 12, 8)

if st.sidebar.button("📸 Take Snapshot"):
    get_active_games.clear()
    fetch_all_odds_cached.clear()
    get_odds_for_game.clear() # Clear individual cache too
    
    with st.spinner("Fetching Fresh Data..."):
        games = get_active_games()
        valid_games = []
        now = datetime.utcnow()
        for g in games:
            try:
                commence = datetime.strptime(g['commence_time'], "%Y-%m-%dT%H:%M:%SZ")
                # OPTIMIZATION: Reduced lookback to -4h to avoid finished games
                diff = (commence - now).total_seconds() / 3600
                if -4 <= diff <= hours_window:
                    valid_games.append(g)
            except: pass

        if valid_games:
            game_ids = [g['id'] for g in valid_games]
            st.toast(f"Snapshotting {len(game_ids)} games...", icon="📸")
            props = fetch_all_odds_cached(game_ids, mode="props")
            totals = fetch_all_odds_cached(game_ids, mode="totals")
            payload = {"props": props, "totals": totals}
            msg = save_snapshot_to_drive(payload)
            st.sidebar.success(msg)
            load_snapshot_from_drive.clear()
            time.sleep(1)
            st.rerun()
        else:
            st.error("No valid games found for snapshot.")

# Load Snapshot
try:
    snap_ts, snap_data = load_snapshot_from_drive()
    if snap_ts:
        st.sidebar.info(f"Snapshot: {snap_ts}")
        props_snap = flatten_data(snap_data.get('props', []), is_totals=False)
        totals_snap = flatten_data(snap_data.get('totals', []), is_totals=True)
        props_map = {x['unique_key']: x for x in props_snap}
        totals_map = {x['unique_key']: x for x in totals_snap}
    else:
        st.sidebar.warning("No Snapshot Found")
        props_map, totals_map = {}, {}
except:
    props_map, totals_map = {}, {}

# View Mode
st.sidebar.markdown("---")
mode = st.sidebar.radio("Mode", ["Player Props", "Game Totals"])

# Auto-Clear Results on Mode Switch
if st.session_state['scan_mode'] and st.session_state['scan_mode'] != mode:
    st.session_state['scan_results'] = None
    st.session_state['scan_mode'] = None
    st.rerun()

threshold = 0
if mode == "Player Props":
    # UPDATE: Min 9.0, Default 10.0
    threshold = st.sidebar.slider("Min Diff (+/-)", 9.0, 20.0, 10.0, 0.5)
else:
    # No slider needed for Game Totals
    st.sidebar.info("Showing all live games for Totals.")

# Main Buttons
col1, col2 = st.columns([1, 4])
with col1:
    scan_btn = st.button("🚀 Compare Live Data")
with col2:
    if st.button("🔄 Force Refresh"):
        fetch_all_odds_cached.clear()
        get_odds_for_game.clear() # Clear granular cache
        st.session_state['scan_results'] = None
        st.toast("Cache cleared!", icon="🔄")

# Logic
if scan_btn:
    st.write(f"🔎 **Scanning ({mode})...**")
    games = get_active_games()
    valid_game_ids = []
    now = datetime.utcnow()
    
    for g in games:
        try:
            commence = datetime.strptime(g['commence_time'], "%Y-%m-%dT%H:%M:%SZ")
            diff = (commence - now).total_seconds() / 3600
            # OPTIMIZATION: Lookback limited to 4 hours for live games
            if -4 <= diff <= hours_window:
                valid_game_ids.append(g['id'])
        except: pass
    
    if not valid_game_ids:
        st.error(f"No active games found.")
        st.session_state['scan_results'] = []
    else:
        results = []
        if mode == "Player Props":
            live_raw = fetch_all_odds_cached(valid_game_ids, mode="props")
            live_flat = flatten_data(live_raw, is_totals=False)
            compare_map = props_map
            
            for live_item in live_flat:
                key = live_item['unique_key']
                if key in compare_map:
                    pre_item = compare_map[key]
                    if live_item['line'] is not None and pre_item['line'] is not None:
                        diff = live_item['line'] - pre_item['line']
                        results.append({
                            **live_item,
                            "live_val": live_item['line'],
                            "pre_val": pre_item['line'],
                            "diff": diff
                        })
        else:
            # GAME TOTALS LOGIC
            live_raw = fetch_all_odds_cached(valid_game_ids, mode="totals")
            live_flat = flatten_data(live_raw, is_totals=True)
            compare_map = totals_map
            
            for live_item in live_flat:
                key = live_item['unique_key']
                if key in compare_map:
                    pre_item = compare_map[key]
                    if live_item['line'] is not None and pre_item['line'] is not None:
                        diff = live_item['line'] - pre_item['line']
                        results.append({
                            "Matchup": live_item['matchup'],
                            "Live Total": live_item['line'],
                            "Pre Total": pre_item['line'],
                            "Diff": diff
                        })

        st.session_state['scan_results'] = results
        st.session_state['scan_timestamp'] = datetime.now().strftime("%H:%M:%S")
        st.session_state['scan_mode'] = mode

# --- DISPLAY LOGIC ---
if st.session_state['scan_results'] is None:
    st.info(f"👋 Select '{mode}' and click 'Compare Live Data'.")
else:
    results_all = st.session_state['scan_results']
    scan_ts = st.session_state['scan_timestamp']
    saved_mode = st.session_state.get('scan_mode', mode)
    
    st.markdown("---")
    st.subheader(f"📊 Results ({len(results_all)})")
    st.caption(f"Last Scanned: {scan_ts} | Mode: {saved_mode}")

    if saved_mode == "Game Totals":
        # TABLE VIEW FOR TOTALS
        if results_all:
            df = pd.DataFrame(results_all)
            if not df.empty:
                df = df.sort_values(by="Diff", ascending=False, key=abs)
                
                st.dataframe(
                    df.style.format({
                        "Live Total": "{:.1f}",
                        "Pre Total": "{:.1f}",
                        "Diff": "{:+.1f}"
                    }),
                    use_container_width=True,
                    hide_index=True
                )
            else:
                st.warning("No valid game totals found in live data.")
        else:
            st.warning("No Game Totals data found.")

    else:
        # PLAYER PROPS VIEW
        filtered_results = [r for r in results_all if abs(r['diff']) >= threshold]
        
        if filtered_results:
            filtered_results.sort(key=lambda x: x['diff'], reverse=True)
            for res in filtered_results:
                with st.container():
                    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
                    pretty_market = res['market_key'].replace('player_', '').replace('_', ' ').title()
                    c1.markdown(f"**{res['player']}**")
                    c1.write(f"🏟️ *{res['matchup']}*")
                    c1.caption(f"{pretty_market}")
                    c2.metric("Live", res['live_val'], delta=f"{res['diff']:.1f}")
                    c3.metric("Pre", res['pre_val'])
                    c4.write(f"O: {res['over']} | U: {res['under']}")
                    st.divider()
        else:
            st.warning(f"No player props moves found >= {threshold}")
