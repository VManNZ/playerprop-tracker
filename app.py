import streamlit as st
import requests
import json
import io
import time
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

# --- 1. CONFIG MUST BE FIRST ---
st.set_page_config(page_title="NBA Tracker", page_icon="🏀", layout="wide")

# --- 2. SETUP & AUTH ---
try:
    # Load secrets safely
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
            service.files().update(fileId=file_id, media_body=media).execute()
            return f"Snapshot Updated ({timestamp})"
        else:
            file_metadata = {'name': SNAPSHOT_FILENAME, 'parents': [DRIVE_FOLDER_ID]}
            service.files().create(body=file_metadata,
