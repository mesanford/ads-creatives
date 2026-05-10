import os
import time
import requests
import pandas as pd
import re
from google.cloud import secretmanager
from bingads.v13.bulk import BulkServiceManager, DownloadParameters
from bingads import AuthorizationData, OAuthWebAuthCodeGrant
import firebase_admin
from firebase_admin import credentials, firestore, storage

# ==========================================
# CONFIGURATION
# ==========================================
GCP_PROJECT_ID = 'looker-studio-pro-msanford'
STORAGE_BUCKET = f"{GCP_PROJECT_ID}.firebasestorage.app"
FIRESTORE_COLLECTION = 'ad_creatives'

# Initialize Firebase Admin
if not firebase_admin._apps:
    firebase_admin.initialize_app(options={'storageBucket': STORAGE_BUCKET})

db = firestore.client()
bucket = storage.bucket()

secret_client = secretmanager.SecretManagerServiceClient()

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def get_secret(secret_id):
    try:
        name = f"projects/{GCP_PROJECT_ID}/secrets/{secret_id}/versions/latest"
        response = secret_client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8").strip()
    except Exception as e:
        print(f"[ERROR] Failed to fetch secret '{secret_id}': {e}")
        raise

def update_secret(secret_id, new_value):
    try:
        parent = f"projects/{GCP_PROJECT_ID}/secrets/{secret_id}"
        secret_client.add_secret_version(
            parent=parent, payload={"data": new_value.encode("UTF-8")}
        )
    except Exception as e:
        pass

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
            
            blob_path = f"creatives/bing/{ad_id}.{ext}"
            blob = bucket.blob(blob_path)
            blob.upload_from_string(response.content, content_type=content_type)
            blob.make_public()
            return blob.public_url
    except Exception as e:
        print(f"[ERROR] Failed to download/upload media for {ad_id}: {e}")
    
    return 'N/A'

def _set_status(status, message, synced=0):
    try:
        db.collection('pipeline_status').document('bing').set({
            'status': status, 'message': message, 'synced': synced,
            'updated_at': firestore.SERVER_TIMESTAMP
        })
    except Exception:
        pass

def main(event, context):
    print("[INFO] --- Starting Bing Ads GALLERY Sync ---")
    _set_status('running', 'Authenticating with Microsoft Ads...')

    try:
        client_id = get_secret("MS_CLIENT_ID")
        dev_token = get_secret("MS_DEV_TOKEN")
        customer_id = int(get_secret("MS_CUSTOMER_ID")) 
        account_id = int(get_secret("MS_ACCOUNT_ID")) 
        client_secret = get_secret("MS_CLIENT_SECRET")
        refresh_token = get_secret("MS_REFRESH_TOKEN")
        
        authentication = OAuthWebAuthCodeGrant(
            client_id=client_id, client_secret=client_secret, redirection_uri="http://localhost:8080"
        )
        authentication.request_oauth_tokens_by_refresh_token(refresh_token)
        
        new_refresh_token = authentication.oauth_tokens.refresh_token
        if new_refresh_token and new_refresh_token != refresh_token:
            update_secret("MS_REFRESH_TOKEN", new_refresh_token)

        auth_data = AuthorizationData(
            account_id=account_id, customer_id=customer_id,
            developer_token=dev_token, authentication=authentication
        )

        print("[INFO] Setting up Bulk API Download...")
        bulk_service_manager = BulkServiceManager(
            authorization_data=auth_data, 
            poll_interval_in_milliseconds=5000, 
            environment='production'
        )

        download_parameters = DownloadParameters(
            campaign_ids=None, 
            data_scope=['EntityData'],
            download_entities=['Ads', 'AssetGroups', 'Images', 'Videos'],
            result_file_directory='/tmp',
            result_file_name='bing_creatives_raw.csv',
            overwrite_result_file=True,
            last_sync_time_in_utc=None
        )

        _set_status('running', 'Waiting for Microsoft to package bulk export...')
        print("[INFO] Waiting for Microsoft to package the account blueprint...")
        bulk_file_path = bulk_service_manager.download_file(download_parameters)

        if not bulk_file_path:
            _set_status('error', 'Failed to download bulk file from Microsoft.')
            print("[ERROR] Failed to download Bulk file.")
            return

        _set_status('running', 'Parsing bulk export...')
        print("[INFO] Parsing massive Bulk CSV...")
        df_raw = pd.read_csv(bulk_file_path, encoding='utf-8-sig', dtype=str)
        
        if df_raw.empty:
            print("[WARNING] The Bulk file contains no data.")
            return

        # Build media lookup
        media_lookup = {}
        media_rows = df_raw[df_raw['Type'].isin(['Image', 'Video'])]
        for _, row in media_rows.iterrows():
            media_id = str(row['Id']).strip()
            url = str(row['Url']).strip()
            if pd.notna(url) and url != 'nan':
                media_lookup[media_id] = url

        # Filter to Ads and Asset Groups
        # Include RSA columns (Title 1-3, Description 1-2) alongside legacy ETA columns (Title, Text)
        columns_to_keep = [
            'Type', 'Status', 'Id', 'Parent Id', 'Campaign', 'Ad Group', 'Name',
            'Title', 'Title Part 2', 'Title Part 3',
            'Title 1', 'Title 2', 'Title 3',
            'Text', 'Text Part 2',
            'Description 1', 'Description 2',
            'Final Url', 'Images', 'Videos',
        ]
        available_cols = [col for col in columns_to_keep if col in df_raw.columns]
        df = df_raw[available_cols].copy()
        df = df[df['Type'].str.contains('Ad|Asset Group', na=False, case=False)]

        def get_media_url(row):
            image_str = str(row.get('Images', ''))
            video_str = str(row.get('Videos', ''))
            match_img = re.search(r'"id":\s*"?(\d+)"?', image_str)
            if match_img:
                media_id = match_img.group(1)
                if media_id in media_lookup:
                    return media_lookup[media_id]
            match_vid = re.search(r'"id":\s*"?(\d+)"?', video_str)
            if match_vid:
                media_id = match_vid.group(1)
                if media_id in media_lookup:
                    return media_lookup[media_id]
            return 'N/A'

        df['source_asset_url'] = df.apply(get_media_url, axis=1)

        def cell(row, col):
            """Return cell value as string, converting NaN/empty to 'N/A'."""
            val = row.get(col)
            if val is None or (isinstance(val, float) and pd.isna(val)) or str(val).strip() in ('', 'nan'):
                return 'N/A'
            return str(val).strip()

        def first_valid(row, *cols):
            for col in cols:
                v = cell(row, col)
                if v != 'N/A':
                    return v
            return 'N/A'

        total = len(df)
        _set_status('running', f'Syncing {total} creatives to Firestore...')
        print(f"[INFO] Syncing {total} Bing Ad Creatives to Firestore...")

        batch = db.batch()
        count = 0

        for _, row in df.iterrows():
            ad_id = str(row['Id'])
            source_asset_url = row['source_asset_url']

            # Download and Upload to Firebase Storage
            firebase_storage_url = download_and_upload_media(source_asset_url, ad_id)

            headline = first_valid(row, 'Title', 'Title 1', 'Title Part 2', 'Title 2', 'Name')
            ad_text = first_valid(row, 'Text', 'Description 1', 'Text Part 2', 'Description 2')
            ad_name = first_valid(row, 'Name', 'Title', 'Title 1')

            if headline == 'N/A' and ad_text == 'N/A' and firebase_storage_url == 'N/A':
                print(f"[SKIP] Ad {ad_id} has no media, headline, or text — skipping.")
                continue

            doc_data = {
                'ad_id': ad_id,
                'platform': 'Bing',
                'ad_name': ad_name,
                'headline': headline,
                'ad_text': ad_text,
                'source_asset_url': source_asset_url,
                'firebase_storage_url': firebase_storage_url,
                'final_url': cell(row, 'Final Url'),
                'campaign_name': cell(row, 'Campaign'),
                'ad_group_name': cell(row, 'Ad Group'),
                'updated_at': firestore.SERVER_TIMESTAMP
            }

            doc_ref = db.collection(FIRESTORE_COLLECTION).document(f"bing_{ad_id}")
            batch.set(doc_ref, doc_data)
            count += 1

            if count % 25 == 0:
                _set_status('running', f'Syncing creatives...', count)
            if count % 500 == 0:
                batch.commit()
                batch = db.batch()

        batch.commit()
        _set_status('complete', f'Done! Synced {count} creatives.', count)
        print(f"[INFO] SUCCESS! {count} Bing Ad Creatives synced to Firestore.")
        os.remove(bulk_file_path)

    except Exception as e:
        _set_status('error', f'Error: {str(e)[:200]}')
        print(f"[ERROR] Pipeline failed: {str(e)}")
        raise e 

_CORS_HEADERS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
}

def run_bing_creatives_gallery_pipeline(request):
    """HTTP Cloud Function Entry Point."""
    if request.method == 'OPTIONS':
        return ('', 204, _CORS_HEADERS)

    try:
        main(None, None)
        return ('Bing creatives gallery synced successfully.', 200, {'Access-Control-Allow-Origin': '*'})
    except Exception as e:
        print(f"[ERROR] Pipeline failed: {e}")
        return (f"Pipeline failed: {e}", 500, {'Access-Control-Allow-Origin': '*'})

if __name__ == "__main__":
    main(None, None)
