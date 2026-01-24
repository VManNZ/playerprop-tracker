import streamlit as st
import requests
import json
import io
import time
from datetime import datetime, timedelta
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

# Cache snapshot load to prevent slow Drive reads
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

# --- API FUNCTIONS (OPTIMISED) ---

# 1. Cache the Game List for 1 Hour (Games don't appear/disappear often)
@st.cache_data(ttl=3600)
def get_active_games():
    """Fetches currently active games."""
    url = f
