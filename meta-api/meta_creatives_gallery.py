import os
import time
import requests
import pandas as pd
import uuid
from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.exceptions import FacebookRequestError
from google.cloud import secretmanager
import firebase_admin
from firebase_admin import credentials, firestore, storage

# ==========================================
# 1. Configuration 
# ==========================================
GCP_PROJECT_ID = 'looker-studio-pro-msanford'
STORAGE_BUCKET = f"{GCP_PROJECT_ID}.firebasestorage.app"
FIRESTORE_COLLECTION = 'ad_creatives'

# Initialize Firebase Admin
if not firebase_admin._apps:
    # Use default credentials when running on GCP
    firebase_admin.initialize_app(options={'storageBucket': STORAGE_BUCKET})

db = firestore.client()
bucket = storage.bucket()

# ==========================================
# 2. Helper Functions
# ==========================================
def get_secret(secret_id, version_id="latest"):
    """Fetches a secret payload from Google Cloud Secret Manager."""
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{GCP_PROJECT_ID}/secrets/{secret_id}/versions/{version_id}"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8").strip()

def download_and_upload_media(url, ad_id):
    """Downloads media from URL and uploads to Firebase Storage."""
    if not url or url == 'N/A' or not url.startswith('http'):
        return 'N/A'
    
    try:
        print(f"[INFO] Downloading media for Ad ID: {ad_id}...")
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            content_type = response.headers.get('content-type', '')
            ext = 'jpg'
            if 'video' in content_type:
                ext = 'mp4'
            elif 'png' in content_type:
                ext = 'png'
            elif 'gif' in content_type:
                ext = 'gif'
            
            blob_path = f"creatives/meta/{ad_id}.{ext}"
            blob = bucket.blob(blob_path)
            blob.upload_from_string(response.content, content_type=content_type)
            
            # Make the blob publicly accessible for the web gallery
            blob.make_public()
            return blob.public_url
    except Exception as e:
        print(f"[ERROR] Failed to download/upload media for {ad_id}: {e}")
    
    return 'N/A'

def make_api_call_with_retries(api_call_func, max_retries=5):
    """Executes a Meta API call with exponential backoff for rate limits."""
    retries = 0
    backoff_factor = 2  
    wait_time = 60      

    while retries < max_retries:
        try:
            return api_call_func()
        except FacebookRequestError as e:
            error_code = e.api_error_code()
            if error_code in [17, 613, 80000, 80003, 80004, 80014]:
                print(f"[WARNING] Rate limit hit (Error {error_code}). Waiting {wait_time} seconds before retrying...")
                time.sleep(wait_time)
                retries += 1
                wait_time *= backoff_factor 
            else:
                print(f"[ERROR] Meta API Error: {e.api_error_message()}")
                raise e
        except Exception as e:
            raise e
            
    raise Exception(f"API call failed after {max_retries} retries due to rate limits.")

# ==========================================
# 3. Extract Creatives from Meta
# ==========================================
def sync_creatives_to_firebase(app_id, app_secret, access_token, ad_account_id):
    print("[INFO] Authenticating with Meta API...")
    FacebookAdsApi.init(app_id, app_secret, access_token)
    account = AdAccount(ad_account_id)

    # STEP 1: Download the actual Creatives
    print("[INFO] Fetching Ad Creatives from Meta...")
    creative_fields = [
        'id', 'name', 'title', 'body', 'image_url', 'thumbnail_url', 
        'object_url', 'object_story_spec'
    ]
    
    creatives_cursor = make_api_call_with_retries(
        lambda: account.get_ad_creatives(fields=creative_fields, params={'limit': 50})
    )
    
    creative_lookup = {}
    for c in creatives_cursor:
        creative_lookup[c.get('id')] = c

    # STEP 2: Download the Ads to map the Ad ID to the Creative ID
    print("[INFO] Fetching Ads to map IDs...")
    ad_fields = ['id', 'name', 'creative']
    
    ads_cursor = make_api_call_with_retries(
        lambda: account.get_ads(fields=ad_fields, params={'limit': 50})
    )
    
    batch = db.batch()
    count = 0

    for ad in ads_cursor:
        ad_id = ad.get('id')
        creative_info = ad.get('creative', {})
        creative_id = creative_info.get('id')
        
        c_details = creative_lookup.get(creative_id, {})
        
        image_url = c_details.get('image_url')
        thumbnail_url = c_details.get('thumbnail_url')
        source_asset_url = image_url if image_url else thumbnail_url

        # Extract Final URL
        final_url = c_details.get('object_url')
        if not final_url:
            story_spec = c_details.get('object_story_spec', {})
            if 'link_data' in story_spec:
                final_url = story_spec['link_data'].get('link')
            elif 'video_data' in story_spec:
                final_url = story_spec['video_data'].get('call_to_action', {}).get('value', {}).get('link')
        
        # Download and Upload to Firebase Storage
        firebase_storage_url = download_and_upload_media(source_asset_url, ad_id)
        
        doc_data = {
            'ad_id': ad_id,
            'platform': 'Meta',
            'ad_name': ad.get('name'),
            'headline': c_details.get('title', 'N/A'),
            'ad_text': c_details.get('body', 'N/A'),
            'source_asset_url': source_asset_url if source_asset_url else 'N/A',
            'firebase_storage_url': firebase_storage_url,
            'final_url': final_url if final_url else 'N/A',
            'updated_at': firestore.SERVER_TIMESTAMP
        }
        
        doc_ref = db.collection(FIRESTORE_COLLECTION).document(f"meta_{ad_id}")
        batch.set(doc_ref, doc_data)
        count += 1
        
        if count % 500 == 0:
            batch.commit()
            batch = db.batch()

    batch.commit()
    print(f"[INFO] SUCCESS! {count} Meta Ad Creatives synced to Firestore.")
    return count

# ==========================================
# 4. Cloud Function Entry Point
# ==========================================
_CORS_HEADERS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
}

def run_meta_creatives_gallery_pipeline(request):
    """HTTP Cloud Function Entry Point."""
    if request.method == 'OPTIONS':
        return ('', 204, _CORS_HEADERS)

    print("[INFO] --- Starting Meta Ads GALLERY Sync ---")
    try:
        app_id = get_secret("FB_APP_ID")
        app_secret = get_secret("FB_APP_SECRET")
        access_token = get_secret("FB_ACCESS_TOKEN")
        ad_account_id = get_secret("FB_AD_ACCOUNT_ID")

        total = sync_creatives_to_firebase(app_id, app_secret, access_token, ad_account_id)

        return (f"Gallery pipeline executed successfully. Synced {total} creatives.", 200, {'Access-Control-Allow-Origin': '*'})

    except Exception as e:
        print(f"[ERROR] Pipeline failed: {e}")
        return (f"Pipeline failed: {e}", 500, {'Access-Control-Allow-Origin': '*'})

if __name__ == "__main__":
    run_meta_creatives_gallery_pipeline(None)
