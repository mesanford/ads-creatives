import json
import requests
import os
from google.cloud import secretmanager
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
import firebase_admin
from firebase_admin import credentials, firestore, storage

# ==========================================
# CONFIGURATION
# ==========================================
GCP_PROJECT_ID = "looker-studio-pro-msanford" 
SECRET_ID = "GOOGLE_ADS_CREDENTIALS"
TARGET_CUSTOMER_IDS = ["8005256502"]
STORAGE_BUCKET = f"{GCP_PROJECT_ID}.firebasestorage.app"
FIRESTORE_COLLECTION = 'ad_creatives'

# Initialize Firebase Admin
if not firebase_admin._apps:
    firebase_admin.initialize_app(options={'storageBucket': STORAGE_BUCKET})

db = firestore.client()
bucket = storage.bucket()

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def get_secret(project_id, secret_id, version_id="latest"):
    """Fetches the OAuth2 credentials payload from Secret Manager."""
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"
    response = client.access_secret_version(request={"name": name})
    return json.loads(response.payload.data.decode("UTF-8"))

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
            
            blob_path = f"creatives/gads/{ad_id}.{ext}"
            blob = bucket.blob(blob_path)
            blob.upload_from_string(response.content, content_type=content_type)
            blob.make_public()
            return blob.public_url
    except Exception as e:
        print(f"[ERROR] Failed to download/upload media for {ad_id}: {e}")
    
    return 'N/A'

def pull_creatives_to_firebase(ga_client, target_customer_ids):
    """Pulls enabled ads and asset links and syncs to Firestore."""
    ga_service = ga_client.get_service("GoogleAdsService")
    
    queries = {
        "Ad_Level": """
            SELECT 
                customer.id, 
                campaign.id, 
                campaign.name, 
                ad_group.id, 
                ad_group.name, 
                ad_group_ad.ad.id, 
                ad_group_ad.ad.name, 
                ad_group_ad.ad.type, 
                ad_group_ad.ad.final_urls,
                ad_group_ad.ad.responsive_search_ad.headlines,
                ad_group_ad.ad.responsive_search_ad.descriptions
            FROM ad_group_ad
            WHERE campaign.status = 'ENABLED' 
              AND ad_group.status = 'ENABLED'
              AND ad_group_ad.status = 'ENABLED'
        """,
        "Asset_Group": """
            SELECT 
                customer.id, 
                campaign.id, 
                campaign.name, 
                asset_group.id, 
                asset_group.name, 
                asset_group_asset.field_type,
                asset.id, 
                asset.name, 
                asset.type, 
                asset.image_asset.full_size.url, 
                asset.youtube_video_asset.youtube_video_id,
                asset.text_asset.text
            FROM asset_group_asset
            WHERE campaign.status = 'ENABLED' 
              AND asset_group.status = 'ENABLED'
              AND asset_group_asset.status = 'ENABLED'
              AND asset.type IN ('IMAGE', 'YOUTUBE_VIDEO', 'TEXT')
        """
    }

    count = 0
    batch = db.batch()

    for child_id in target_customer_ids:
        for query_name, query_string in queries.items():
            try:
                stream = ga_service.search_stream(customer_id=child_id, query=query_string)
                for batch_result in stream:
                    for row in batch_result.results:
                        
                        final_url = None
                        source_asset_url = None
                        headlines = None
                        descriptions = None
                        
                        if query_name == "Ad_Level":
                            g_type = "Ad Group"
                            item_id = str(row.ad_group_ad.ad.id)
                            item_name = getattr(row.ad_group_ad.ad, "name", "Unnamed Ad")
                            item_type = row.ad_group_ad.ad.type_.name
                            
                            if row.ad_group_ad.ad.final_urls:
                                final_url = row.ad_group_ad.ad.final_urls[0]
                                
                            if item_type == "RESPONSIVE_SEARCH_AD":
                                hl_list = [hl.text for hl in row.ad_group_ad.ad.responsive_search_ad.headlines]
                                desc_list = [desc.text for desc in row.ad_group_ad.ad.responsive_search_ad.descriptions]
                                if hl_list:
                                    headlines = " | ".join(hl_list)
                                if desc_list:
                                    descriptions = " | ".join(desc_list)
                                
                        elif query_name == "Asset_Group":
                            g_type = "Asset Group"
                            item_id = str(row.asset.id)
                            item_name = getattr(row.asset, "name", "Unnamed Asset")
                            item_type = row.asset.type_.name
                            
                            if item_type == 'IMAGE':
                                source_asset_url = row.asset.image_asset.full_size.url
                            elif item_type == 'YOUTUBE_VIDEO':
                                video_id = row.asset.youtube_video_asset.youtube_video_id
                                source_asset_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else None
                            elif item_type == 'TEXT':
                                field_type = row.asset_group_asset.field_type.name
                                if 'HEADLINE' in field_type:
                                    headlines = row.asset.text_asset.text
                                else:
                                    descriptions = row.asset.text_asset.text

                        # YouTube videos can't be fetched as raw files; store the embed URL directly
                        if item_type == 'YOUTUBE_VIDEO':
                            video_id = row.asset.youtube_video_asset.youtube_video_id
                            firebase_storage_url = f"https://www.youtube.com/embed/{video_id}" if video_id else 'N/A'
                        else:
                            firebase_storage_url = download_and_upload_media(source_asset_url, item_id)

                        doc_data = {
                            "ad_id": item_id,
                            "platform": "Google",
                            "account_id": str(row.customer.id),
                            "campaign_name": row.campaign.name,
                            "group_type": g_type,
                            "ad_name": item_name,
                            "creative_type": item_type,
                            "headline": headlines if headlines else 'N/A',
                            "ad_text": descriptions if descriptions else 'N/A',
                            "final_url": final_url if final_url else 'N/A',
                            "source_asset_url": source_asset_url if source_asset_url else 'N/A',
                            "firebase_storage_url": firebase_storage_url,
                            "updated_at": firestore.SERVER_TIMESTAMP
                        }

                        doc_ref = db.collection(FIRESTORE_COLLECTION).document(f"gads_{item_id}")
                        batch.set(doc_ref, doc_data)
                        count += 1
                        
                        if count % 500 == 0:
                            batch.commit()
                            batch = db.batch()

            except GoogleAdsException as ex:
                print(f"API Error for account {child_id} ({query_name}): {ex.error.code().name}")

    batch.commit()
    return count

# ==========================================
# 2. Cloud Function Entry Point
# ==========================================
_CORS_HEADERS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
}

def run_creative_gallery_pipeline(request):
    """HTTP Cloud Function Entry Point."""
    if request.method == 'OPTIONS':
        return ('', 204, _CORS_HEADERS)

    try:
        credentials_dict = get_secret(GCP_PROJECT_ID, SECRET_ID)
        googleads_client = GoogleAdsClient.load_from_dict(credentials_dict, version="v24")

        total_records = pull_creatives_to_firebase(googleads_client, TARGET_CUSTOMER_IDS)

        return (f"Creative gallery pipeline success: Synced {total_records} creatives to Firestore.", 200, {'Access-Control-Allow-Origin': '*'})

    except Exception as e:
        print(f"Pipeline failed: {e}")
        return (f"Error: {e}", 500, {'Access-Control-Allow-Origin': '*'})

if __name__ == "__main__":
    run_creative_gallery_pipeline(None)
