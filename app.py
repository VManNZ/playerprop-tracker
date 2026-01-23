import streamlit as st
import requests
import json
import os
from datetime import datetime, timedelta
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
TARGET_BOOKMAKER_KEY = 'draftkings' 

MARKET_ORDER = [
    'player_points', 'player_rebounds', 'player_assists',
    'player_points_rebounds_assists', 'player_points_rebounds',
    'player_points_assists', 'player_rebounds_assists'
]

# Separate market for game totals
TOTALS_MARKET = 'totals'

# --- GOOGLE DRIVE FUNCTIONS ---
def get_drive_service():
    return build('drive', 'v3', credentials=GCP_CREDS)

def get_snapshot_file_id(service):
    query = f"'{DRIVE_FOLDER_ID}' in parents and name = '{SNAPSHOT_FILENAME}' and trashed = false"
    results = service.files().list(q=query, orderBy='modifiedTime desc', fields="files(id, name, modifiedTime)").execute()
    files = results.get('files', [])
    if not files: return None, 0
    if len(files) > 1: st.toast(f"⚠️ Found duplicates. Using newest.", icon="⚠️")
    return files[0]['id'], len(files)

def save_snapshot_to_drive(data):
    try:
        service = get_drive_service()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        
        # New format: data is already a dict with 'props' and 'totals'
        payload = {"last_updated": timestamp, "data": data}
        
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

# ✨ NEW: Cache snapshot loading to avoid repeated Drive reads
@st.cache_data(ttl=120, show_spinner=False)
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
        
        # Handle different snapshot formats
        if isinstance(content, list):
            # Very old format: just a list of games
            return "No Data", {"props": content, "totals": []}
        elif "games" in content:
            # Old format: {"last_updated": ..., "games": [...]}
            return content.get("last_updated"), {"props": content["games"], "totals": []}
        elif "data" in content:
            # New format: {"last_updated": ..., "data": {"props": [...], "totals": [...]}}
            return content.get("last_updated"), content.get("data")
        else:
            return None, None
    except Exception as e:
        st.error(f"Error loading from Drive: {e}")
        return None, None

# --- OPTIMISED API FUNCTIONS ---

# ✨ Cache games list longer (5 minutes) - games don't appear/disappear that fast
@st.cache_data(ttl=300, show_spinner=False)
def get_active_games_cached():
    """Fetches games list with extended cache - costs 1 credit per 5 minutes max."""
    url = f'https://api.the-odds-api.com/v4/sports/{SPORT}/events'
    params = {'apiKey': API_KEY}
    try:
        response = requests.get(url, params=params)
        
        if 'x-requests-remaining' in response.headers:
            st.session_state['api_remaining'] = response.headers['x-requests-remaining']
            st.session_state['api_used'] = response.headers.get('x-requests-used', '?')
        
        if response.status_code == 200: 
            return response.json()
    except: 
        pass
    return []

# ✨ Filter games to only those happening soon (within next 24 hours)
def filter_upcoming_games(games, hours_ahead=24):
    """Only return games starting within the specified time window."""
    if not games:
        return []
    
    now = datetime.utcnow()
    upcoming = []
    
    for game in games:
        try:
            game_time = datetime.strptime(game['commence_time'], '%Y-%m-%dT%H:%M:%SZ')
            time_until = (game_time - now).total_seconds() / 3600  # hours
            
            # Only include games starting within the next X hours
            if 0 <= time_until <= hours_ahead:
                upcoming.append(game)
        except:
            # If we can't parse time, include it to be safe
            upcoming.append(game)
    
    return upcoming

def get_props_for_game(game_id):
    """Fetches props for a single game - costs 1 credit per call."""
    market_list = ','.join(MARKET_ORDER)
    url = f'https://api.the-odds-api.com/v4/sports/{SPORT}/events/{game_id}/odds'
    params = {
        'apiKey': API_KEY, 'regions': 'us,eu', 
        'markets': market_list, 'oddsFormat': 'decimal',
        'bookmakers': TARGET_BOOKMAKER_KEY
    }
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200: 
            return response.json()
    except: 
        return None
    return None

def get_totals_for_game(game_id):
    """Fetches game totals - costs 1 credit per call."""
    url = f'https://api.the-odds-api.com/v4/sports/{SPORT}/events/{game_id}/odds'
    params = {
        'apiKey': API_KEY, 'regions': 'us,eu', 
        'markets': TOTALS_MARKET, 'oddsFormat': 'decimal',
        'bookmakers': TARGET_BOOKMAKER_KEY
    }
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200: 
            return response.json()
    except: 
        return None
    return None

# ✨ MAIN OPTIMISER: Cache props data for 60 seconds
@st.cache_data(ttl=60, show_spinner=False)
def fetch_props_for_games_cached(game_ids_tuple):
    """Fetches props only for specified game IDs. Uses tuple for hashability."""
    all_data = []
    game_ids = list(game_ids_tuple)  # Convert back to list
    
    if not game_ids:
        return []
    
    progress_bar = st.progress(0)
    for i, game_id in enumerate(game_ids):
        game_props = get_props_for_game(game_id)
        if game_props: 
            all_data.append(game_props)
        progress_bar.progress((i + 1) / len(game_ids))
    progress_bar.empty()
    
    return all_data

@st.cache_data(ttl=60, show_spinner=False)
def fetch_totals_for_games_cached(game_ids_tuple):
    """Fetches game totals only for specified game IDs. Uses tuple for hashability."""
    all_data = []
    game_ids = list(game_ids_tuple)  # Convert back to list
    
    if not game_ids:
        return []
    
    progress_bar = st.progress(0)
    for i, game_id in enumerate(game_ids):
        game_totals = get_totals_for_game(game_id)
        if game_totals: 
            all_data.append(game_totals)
        progress_bar.progress((i + 1) / len(game_ids))
    progress_bar.empty()
    
    return all_data

def flatten_data(game_data_list):
    flat_list = []
    found_bookies = set()
    if not game_data_list: return flat_list, found_bookies
    
    for game in game_data_list:
        home_team = game.get('home_team', 'Unknown')
        away_team = game.get('away_team', 'Unknown')
        
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
                            "player": outcome['description'], 
                            "market_key": market['key'],
                            "line": outcome['point'], 
                            "over": over_price, 
                            "under": under_price,
                            "book": book['title'],
                            "home_team": home_team,
                            "away_team": away_team,
                            "matchup": f"{away_team} @ {home_team}"
                        })
    return flat_list, found_bookies

def flatten_totals_data(game_data_list):
    """Flatten game totals data."""
    flat_list = []
    found_bookies = set()
    if not game_data_list: return flat_list, found_bookies
    
    for game in game_data_list:
        home_team = game.get('home_team', 'Unknown')
        away_team = game.get('away_team', 'Unknown')
        matchup = f"{away_team} @ {home_team}"
        
        for book in game.get('bookmakers', []):
            found_bookies.add(book['key'])
            if book['key'] != TARGET_BOOKMAKER_KEY: continue
            for market in book.get('markets', []):
                if market['key'] != 'totals': continue
                for outcome in market.get('outcomes', []):
                    if outcome['name'] == 'Over':
                        over_price = outcome['price']
                        under_outcome = next((o for o in market['outcomes'] if o['name'] == 'Under'), None)
                        under_price = under_outcome['price'] if under_outcome else '-'
                        
                        flat_list.append({
                            "matchup": matchup,
                            "market_key": "totals",
                            "line": outcome['point'],
                            "over": over_price,
                            "under": under_price,
                            "book": book['title'],
                            "home_team": home_team,
                            "away_team": away_team
                        })
    return flat_list, found_bookies

# --- APP LAYOUT ---
st.set_page_config(page_title="NBA Tracker", page_icon="☁️", layout="wide")
st.title("☁️ NBA Tracker (Optimised)")

# Sidebar
st.sidebar.header("⚙️ Controls")

# ✨ Game filtering option
hours_filter = st.sidebar.slider("Only track games starting within (hours)", 1, 48, 24, 1)

if st.sidebar.button("📸 1. Take Pre-Game Snapshot"):
    # Clear ALL caches to get fresh data for snapshot
    get_active_games_cached.clear()
    load_snapshot_from_drive.clear()
    
    with st.spinner("Fetching Fresh Game Data..."):
        all_games = get_active_games_cached()
        upcoming_games = filter_upcoming_games(all_games, hours_ahead=hours_filter)
        
        if upcoming_games:
            st.info(f"Found {len(upcoming_games)} games within next {hours_filter}h (filtered from {len(all_games)} total)")
            game_ids = tuple(g['id'] for g in upcoming_games)  # Convert to tuple for caching
            
            # Fetch both player props AND game totals
            props_data = fetch_props_for_games_cached(game_ids)
            totals_data = fetch_totals_for_games_cached(game_ids)
            
            if props_data or totals_data:
                # Combine both datasets for snapshot
                snapshot_payload = {
                    "props": props_data,
                    "totals": totals_data
                }
                msg = save_snapshot_to_drive(snapshot_payload)
                if msg: 
                    st.sidebar.success(f"{msg}")
                    load_snapshot_from_drive.clear()  # Clear snapshot cache
                time.sleep(1) 
                st.rerun()
        else:
            st.warning(f"No games found within next {hours_filter} hours")

# --- API HEALTH METER ---
if 'api_remaining' in st.session_state:
    rem = int(st.session_state['api_remaining'])
    st.sidebar.markdown("---")
    st.sidebar.subheader("📊 API Usage")
    if rem > 0: 
        st.sidebar.success(f"Credits Left: **{rem}**")
    else: 
        st.sidebar.error(f"Credits Left: **{rem}**")

try:
    last_ts, _ = load_snapshot_from_drive()
    if last_ts: 
        st.sidebar.info(f"🕒 Snapshot: {last_ts}")
    else: 
        st.sidebar.warning("⚠️ No Snapshot found")
except: 
    pass

st.sidebar.write("---")
mode = st.sidebar.radio("View Mode", ["🔥 Market Scanner", "🔎 Player Search", "🏀 Game Totals"])
threshold = 0
search_query = ""

# ✨ FIX: Allowed lower minimums (1.0) so you can verify the scanner works
if mode == "🔥 Market Scanner":
    # Default 8.0, but allows going down to 1.0
    threshold = st.sidebar.slider("Show moves greater than (+/-)", 1.0, 25.0, 8.0, 0.5)
elif mode == "🔎 Player Search":
    search_query = st.text_input("Enter Player Name", "")
elif mode == "🏀 Game Totals":
    # Default 10.0, but allows going down to 1.0
    threshold = st.sidebar.slider("Show total moves greater than (+/-)", 1.0, 30.0, 10.0, 0.5)

# 🚀 LIVE DATA CONTROLS
col1, col2 = st.columns([1, 4])
with col1:
    scan_clicked = st.button("🚀 2. Compare Live Data")
with col2:
    if st.button("🔄 Force Refresh Live Odds"):
        get_active_games_cached.clear()
        fetch_props_for_games_cached.clear()
        fetch_totals_for_games_cached.clear()
        st.toast("Cache cleared! Next scan will fetch fresh data...", icon="🔄")

if scan_clicked or st.session_state.get('scan_active', False):
    st.session_state['scan_active'] = True
    
    with st.spinner("Loading Snapshot..."):
        ts, pre_game_data = load_snapshot_from_drive()
    
    if not pre_game_data:
        st.error("⚠️ No snapshot data found. Take a snapshot first!")
        st.stop()

    # Extract props and totals from snapshot
    pre_props_data = pre_game_data.get('props', [])
    pre_totals_data = pre_game_data.get('totals', [])

    with st.spinner("Fetching Live Game List..."):
        all_games = get_active_games_cached()
        upcoming_games = filter_upcoming_games(all_games, hours_ahead=hours_filter)
    
    if not upcoming_games:
        st.warning(f"⚠️ No games found within next {hours_filter} hours.")
        st.stop()
    
    with st.spinner(f"Fetching Live Odds for {len(upcoming_games)} games (Cached for 60s)..."):
        game_ids = tuple(g['id'] for g in upcoming_games)
        live_props_data = fetch_props_for_games_cached(game_ids)
        live_totals_data = fetch_totals_for_games_cached(game_ids)

    if not live_props_data and not live_totals_data:
        st.warning("⚠️ No live odds data available.")
        st.stop()
    
    # Process based on selected mode
    if mode == "🏀 Game Totals":
        # Game Totals Mode
        pre_flat, pre_bookies = flatten_totals_data(pre_totals_data)
        live_flat, live_bookies = flatten_totals_data(live_totals_data)
        
        if not pre_flat:
            st.error(f"❌ Your snapshot has no totals data for '{TARGET_BOOKMAKER_KEY}'!")
            if pre_bookies: 
                st.warning(f"Found totals data for: {', '.join(pre_bookies)}")
            st.stop()
        
        if not live_flat:
            st.error("No live game totals available.")
            st.stop()
        
        pre_map = {x['matchup']: x for x in pre_flat}
        live_map = {x['matchup']: x for x in live_flat}
        results_list = []
        
        for matchup, live_item in live_map.items():
            if matchup in pre_map:
                pre_item = pre_map[matchup]
                if live_item['line'] is not None and pre_item['line'] is not None:
                    diff = live_item['line'] - pre_item['line']
                    if abs(diff) >= threshold:
                        results_list.append({
                            **live_item,
                            "live_display": live_item['line'],
                            "pre_display": pre_item['line'],
                            "diff": diff,
                            "status": "active"
                        })
        
        if results_list:
            st.subheader(f"Game Totals with Movement ({len(results_list)})")
            if ts: 
                st.caption(f"Comparing against snapshot from: {ts}")
            results_list.sort(key=lambda x: abs(x['diff']), reverse=True)
            
            for item in results_list:
                with st.container():
                    col1, col2, col3, col4 = st.columns([2, 1.5, 1.5, 1])
                    
                    col1.markdown(f"**{item['matchup']}**")
                    col1.caption("Game Total Points")
                    
                    col2.metric("Live Total", f"{item['live_display']}", delta=f"{item['diff']:.1f}")
                    col3.metric("Pre Total", f"{item['pre_display']}")
                    col4.write(f"**Over:** {item['over']}")
                    col4.write(f"**Under:** {item['under']}")
                    st.divider()
        else:
            st.info("No game totals found with significant movement.")
    
    else:
        # Player Props Mode (existing logic)
        pre_flat, pre_bookies = flatten_data(pre_props_data)
        live_flat, live_bookies = flatten_data(live_props_data)
        
        if not pre_flat:
            st.error(f"❌ Your snapshot is empty for '{TARGET_BOOKMAKER_KEY}'!")
            if pre_bookies: 
                st.warning(f"Found data for: {', '.join(pre_bookies)}")
            st.stop()

        pre_map = {f"{x['player']}|{x['market_key']}": x for x in pre_flat}
        live_map = {f"{x['player']}|{x['market_key']}": x for x in live_flat}
        results_list = []

        if mode == "🔥 Market Scanner":
            if not live_flat:
                st.error("Scanner requires live games.")
                st.stop()  
            for key, live_item in live_map.items():
                if key in pre_map:
                    pre_item = pre_map[key]
                    if live_item['line'] is not None and pre_item['line'] is not None:
                        diff = live_item['line'] - pre_item['line']
                        if abs(diff) >= threshold:
                            results_list.append({
                                **live_item, 
                                "live_display": live_item['line'], 
                                "pre_display": pre_item['line'], 
                                "diff": diff, 
                                "status": "active"
                            })

        elif mode == "🔎 Player Search":
            if search_query:
                found_match = False
                for key, pre_item in pre_map.items():
                    if search_query.lower() in pre_item['player'].lower():
                        found_match = True
                        if key in live_map:
                            live_item = live_map[key]
                            diff = live_item['line'] - pre_item['line']
                            results_list.append({
                                **live_item, 
                                "live_display": live_item['line'], 
                                "pre_display": pre_item['line'], 
                                "diff": diff, 
                                "status": "active"
                            })
                        else:
                            results_list.append({
                                **pre_item, 
                                "live_display": "No Live Game", 
                                "pre_display": pre_item['line'], 
                                "diff": 0, 
                                "status": "inactive"
                            })
                if not found_match: 
                    st.warning(f"No player found matching '{search_query}'.")

        # --- DIAGNOSTIC TOOL ---
        # This block checks if we are filtering out everything, or if data is genuinely identical
        if mode == "🔥 Market Scanner":
            with st.expander("🛠️ Diagnostics (Open this if seeing 0 results)"):
                st.write(f"**Snapshot Records:** {len(pre_map)}")
                st.write(f"**Live Records:** {len(live_map)}")
                
                # Check for Cache Trap (identical data)
                all_diffs = []
                for key, live_item in live_map.items():
                    if key in pre_map:
                        pre_item = pre_map[key]
                        if live_item['line'] is not None and pre_item['line'] is not None:
                            d = live_item['line'] - pre_item['line']
                            all_diffs.append({
                                "player": live_item['player'],
                                "market": live_item['market_key'],
                                "diff": d
                            })
                
                zeros = sum(1 for x in all_diffs if x['diff'] == 0)
                st.write(f"**Exact Matches (Diff = 0):** {zeros}")
                
                if len(all_diffs) > 0 and len(all_diffs) == zeros:
                    st.warning("⚠️ All live lines match the snapshot exactly. You are likely viewing cached data.")
                    st.info("💡 Fix: Click 'Force Refresh Live Odds' at the top right.")
                
                # Show top 5 movers regardless of threshold
                all_diffs.sort(key=lambda x: abs(x['diff']), reverse=True)
                st.write("**Top 5 Biggest Moves Found (Raw Data):**")
                if all_diffs:
                    st.table(all_diffs[:5])
                else:
                    st.write("No matching players found between snapshot and live.")

        if results_list:
            st.subheader(f"Results ({len(results_list)})")
            if ts: 
                st.caption(f"Comparing against snapshot from: {ts}")
            results_list.sort(key=lambda x: (
                x['player'], 
                MARKET_ORDER.index(x['market_key']) if x['market_key'] in MARKET_ORDER else 99
            ))

            for item in results_list:
                with st.container():
                    col1, col2, col3, col4 = st.columns([2, 1.5, 1.5, 1])
                    m_key = item['market_key']
                    
                    market_names = {
                        'player_points_assists': "Points + Assists",
                        'player_points_rebounds': "Points + Rebounds",
                        'player_rebounds_assists': "Rebounds + Assists",
                        'player_points_rebounds_assists': "Pts + Rebs + Asts"
                    }
                    pretty = market_names.get(m_key, m_key.replace('player_', '').replace('_', ' ').title())

                    col1.markdown(f"**{item['player']}**")
                    col1.caption(f"{pretty}")
                    if 'matchup' in item:
                        col1.caption(f"🏀 {item['matchup']}")
                    
                    if item['status'] == 'inactive':
                        col2.metric("Live Line", "N/A", delta=None)
                        col2.caption("No Live Game")
                    else:
                        col2.metric("Live Line", f"{item['live_display']}", delta=f"{item['diff']:.1f}")
                    
                    col3.metric("Pre Line", f"{item['pre_display']}")
                    col4.write(f"**Over:** {item['over']}")
                    col4.write(f"**Under:** {item['under']}")
                    st.divider()
        elif search_query or mode == "🔥 Market Scanner":
            st.info("No records found matching your criteria.")

# ✨ Show optimisation info
with st.expander("💡 Optimisation Info"):
    st.markdown("""
    **How this saves API credits:**
    
    1. **Extended Game List Cache (5 min)**: The list of active games is cached for 5 minutes instead of 60 seconds, reducing calls by 80%
    
    2. **Time-Based Filtering**: Only fetches odds for games starting within your selected time window (default 24h)
    
    3. **Separate Cache Layers**: Games list, player props, and totals are cached independently, so searching/filtering uses zero credits
    
    4. **Snapshot Loading Cache**: Snapshot is cached for 2 minutes to avoid repeated Drive reads
    
    5. **Smart Refresh**: "Force Refresh" only clears necessary caches
    """)
