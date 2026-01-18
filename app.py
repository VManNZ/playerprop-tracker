import streamlit as st
import requests
import json
import os

# --- CONFIGURATION ---
# 1. Try to load from Streamlit Secrets (Cloud)
# 2. If not found, look for an Environment Variable
# 3. If that fails, warn the user (or use a hardcoded fallback for local testing ONLY)
try:
    API_KEY = st.secrets["API_KEY"]
except:
    # ⚠️ REPLACEME: For local testing, you can paste your key here temporarily.
    # But strictly speaking, you should use .streamlit/secrets.toml (see instructions below).
    API_KEY = 'f0d04a66f3975d480558b76a0f101551'

SPORT = 'basketball_nba'
SNAPSHOT_FILE = 'nba_odds_snapshot.json'
TARGET_BOOKMAKER_KEY = 'betonlineag' 
TARGET_BOOKMAKER_TITLE = 'BetOnline.ag'

# --- HELPER FUNCTIONS ---
def get_active_games():
    """Step 1: Get a list of all active/upcoming games."""
    url = f'https://api.the-odds-api.com/v4/sports/{SPORT}/events'
    params = {'apiKey': API_KEY}
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        st.error(f"Connection Error: {e}")
    return []

def get_props_for_game(game_id):
    """Step 2: Get player props for a SPECIFIC game ID (BetOnline ONLY)."""
    # Combo markets included
    market_list = (
        'player_points,player_rebounds,player_assists,'
        'player_points_assists,player_points_rebounds,'
        'player_points_rebounds_assists,player_rebounds_assists'
    )
    
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
        if response.status_code == 200:
            return response.json()
    except:
        return None
    return None

def fetch_all_nba_data():
    """Combines Step 1 and Step 2 to get ALL data."""
    all_data = []
    games = get_active_games()
    
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

# --- APP LAYOUT ---
st.set_page_config(page_title="NBA Tracker", page_icon="🏀", layout="wide")
st.title("🏀 NBA Live vs Pre-Game Tracker")

# Sidebar Controls
st.sidebar.header("⚙️ Controls")

# SNAPSHOT
if st.sidebar.button("📸 1. Take Pre-Game Snapshot"):
    data = fetch_all_nba_data()
    if data:
        with open(SNAPSHOT_FILE, 'w') as f:
            json.dump(data, f)
        st.sidebar.success(f"Saved snapshot of {len(data)} games.")

st.sidebar.write("---")

# SEARCH MODE SELECTION
mode = st.sidebar.radio("View Mode", ["🔥 Market Scanner", "🔎 Player Search"])

# SETTINGS BASED ON MODE
threshold = 0
search_query = ""

if mode == "🔥 Market Scanner":
    st.header("🔥 High Variance Scanner")
    st.write("Showing players where the line moved significantly.")
    threshold = st.sidebar.slider("Show moves greater than (+/-)", 1.0, 15.0, 4.0, 0.5)

elif mode == "🔎 Player Search":
    st.header("🔎 Player Lookup")
    st.write("Compare all stats for a specific player.")
    search_query = st.text_input("Enter Player Name (e.g. LeBron)", "")

# SCAN BUTTON
if st.button("🚀 2. Compare Live Data"):
    
    # Validation
    if not os.path.exists(SNAPSHOT_FILE):
        st.error("⚠️ No snapshot found! Please take a Pre-Game Snapshot first.")
        st.stop()
        
    if mode == "🔎 Player Search" and not search_query:
        st.warning("Please enter a player name.")
        st.stop()

    # Load Snapshot
    with open(SNAPSHOT_FILE, 'r') as f:
        pre_game_data = json.load(f)

    # Fetch Live
    with st.spinner("Fetching live market data..."):
        live_data = fetch_all_nba_data()

    if not live_data:
        st.error("No live games found.")
        st.stop()

    # --- COMPARISON LOGIC ---
    st.subheader(f"Results")
    
    found_movers = False
    
    # 1. Build Pre-Game Lookup
    pre_map = {}
    for game in pre_game_data:
        for book in game.get('bookmakers', []):
            if book['key'] != TARGET_BOOKMAKER_KEY: continue
            for market in book.get('markets', []):
                for outcome in market.get('outcomes', []):
                    unique_key = f"{outcome['description']}|{market['key']}|{outcome['name']}"
                    pre_map[unique_key] = outcome.get('point')

    # 2. Compare with Live
    for game in live_data:
        for book in game.get('bookmakers', []):
            if book['key'] != TARGET_BOOKMAKER_KEY: continue
            for market in book.get('markets', []):
                for outcome in market.get('outcomes', []):
                    
                    # FILTER: Player Name Search
                    if mode == "🔎 Player Search":
                        if search_query.lower() not in outcome['description'].lower():
                            continue

                    unique_key = f"{outcome['description']}|{market['key']}|{outcome['name']}"
                    
                    if unique_key in pre_map:
                        pre_line = pre_map[unique_key]
                        live_line = outcome.get('point')
                        
                        if pre_line is not None and live_line is not None:
                            line_diff = live_line - pre_line
                            
                            # FILTER: Threshold Scanner
                            # If in scanner mode, skip small moves. 
                            # If in search mode, show EVERYTHING (threshold effectively 0).
                            if mode == "🔥 Market Scanner" and abs(line_diff) < threshold:
                                continue
                            
                            found_movers = True
                            
                            # --- DISPLAY CARD ---
                            with st.container():
                                col1, col2, col3, col4 = st.columns([2, 1.5, 1, 1])
                                
                                clean_market = market['key'].replace('player_', '').replace('_', ' ').title()
                                
                                # Highlight Name if searching
                                col1.markdown(f"**{outcome['description']}**")
                                col1.caption(f"{clean_market} ({outcome['name']})")
                                
                                col2.write(f"🏦 {book['title']}")
                                
                                # Color code the delta
                                col3.metric("Live Line", f"{live_line}", delta=f"{line_diff:.1f}")
                                col4.metric("Pre Line", f"{pre_line}")
                                st.caption(f"Odds: {outcome['price']}")
                                st.divider()

    if not found_movers:
        if mode == "🔥 Market Scanner":
            st.info(f"✅ No lines have moved by more than {threshold} yet.")
        else:
            st.info(f"❌ No props found for '{search_query}'. (Check spelling or if they are playing today)")