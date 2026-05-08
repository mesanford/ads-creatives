import os
import time
import pandas as pd

from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.exceptions import FacebookRequestError

from google.cloud import bigquery
from google.cloud import secretmanager

# ==========================================
# 1. Configuration 
# ==========================================
GCP_PROJECT_ID = 'looker-studio-pro-msanford'
BQ_DATASET = 'nueske_retail_meta_v2'
BQ_TABLE = 'meta_ad_creatives' # A dedicated lookup table


# ==========================================

# ==========================================
# 2. Helper Functions
# ==========================================
def get_secret(secret_id, version_id="latest"):
    """Fetches a secret payload from Google Cloud Secret Manager."""
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{GCP_PROJECT_ID}/secrets/{secret_id}/versions/{version_id}"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8").strip()

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
def get_ad_creatives(app_id, app_secret, access_token, ad_account_id):
    print("[INFO] Authenticating with Meta API...")
    FacebookAdsApi.init(app_id, app_secret, access_token)
    account = AdAccount(ad_account_id)

    # STEP 1: Download the actual Creatives (Images, URLs, Text)
    print("[INFO] Fetching Ad Creatives from Meta...")
    # Added 'object_url' and 'object_story_spec' to hunt down the destination link
    creative_fields = [
        'id', 'name', 'title', 'body', 'image_url', 'thumbnail_url', 
        'object_url', 'object_story_spec'
    ]
    
    creatives_cursor = make_api_call_with_retries(
        lambda: account.get_ad_creatives(fields=creative_fields, params={'limit': 50})
    )
    
    # Build a lookup dictionary of Creative ID -> Creative Details
    creative_lookup = {}
    for c in creatives_cursor:
        creative_lookup[c.get('id')] = c

    # STEP 2: Download the Ads to map the Ad ID to the Creative ID
    print("[INFO] Fetching Ads to map IDs...")
    ad_fields = ['id', 'name', 'creative']
    
    ads_cursor = make_api_call_with_retries(
        lambda: account.get_ads(fields=ad_fields, params={'limit': 50})
    )
    
    data = []
    for ad in ads_cursor:
        # Find which creative this Ad uses
        creative_info = ad.get('creative', {})
        creative_id = creative_info.get('id')
        
        # Look up the creative details from Step 1
        c_details = creative_lookup.get(creative_id, {})
        
        # Prefer the high-res image URL, fallback to the video thumbnail
        image_url = c_details.get('image_url')
        thumbnail_url = c_details.get('thumbnail_url')
        asset_url = image_url if image_url else thumbnail_url

        # --- NEW: Extract the Final URL ---
        # Meta hides this in different places based on the ad format
        final_url = c_details.get('object_url')
        
        if not final_url:
            story_spec = c_details.get('object_story_spec', {})
            # Try standard link/image ads
            if 'link_data' in story_spec:
                final_url = story_spec['link_data'].get('link')
            # Try video ads
            elif 'video_data' in story_spec:
                final_url = story_spec['video_data'].get('call_to_action', {}).get('value', {}).get('link')
        # ----------------------------------
        
        data.append({
            'ad_id': ad.get('id'),
            'ad_name': ad.get('name'),
            'headline': c_details.get('title', 'N/A'),
            'ad_text': c_details.get('body', 'N/A'),
            'asset_url': asset_url if asset_url else 'N/A',
            'final_url': final_url if final_url else 'N/A' # <-- Added to the final output
        })
        
    df = pd.DataFrame(data)
    
    # Fill any missing/null values so BigQuery doesn't complain
    df = df.fillna('N/A')
    
    return df

# ==========================================
# 4. Load into BigQuery (Overwrite)
# ==========================================
def load_creatives_bigquery(df):
    if df.empty:
        print("[WARNING] No creative data found. Skipping BQ load.")
        return

    print("[INFO] Initializing BigQuery Client...")
    client = bigquery.Client(project=GCP_PROJECT_ID)
    
    target_table_id = f"{GCP_PROJECT_ID}.{BQ_DATASET}.{BQ_TABLE}"

    # Because this is a lookup table, we completely OVERWRITE it every time (WRITE_TRUNCATE)
    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_TRUNCATE", 
        autodetect=True
    )

    print(f"[INFO] Loading {len(df)} Ad Creatives into {target_table_id}...")
    load_job = client.load_table_from_dataframe(df, target_table_id, job_config=job_config)
    load_job.result() 

    print("[INFO] SUCCESS! Meta Ad Creatives lookup table updated.")

# ==========================================
# 5. Cloud Function Entry Point
# ==========================================
def run_meta_creatives_pipeline(request):
    """HTTP Cloud Function Entry Point."""
    print("[INFO] --- Starting Meta Ads CREATIVE Sync ---")
    try:
        app_id = get_secret("FB_APP_ID")
        app_secret = get_secret("FB_APP_SECRET")
        access_token = get_secret("FB_ACCESS_TOKEN")
        ad_account_id = get_secret("FB_AD_ACCOUNT_ID")

        df_creatives = get_ad_creatives(app_id, app_secret, access_token, ad_account_id)
        load_creatives_bigquery(df_creatives)
        
        return "Pipeline executed successfully.", 200
        
    except Exception as e:
        print(f"[ERROR] Pipeline failed: {e}")
        return f"Pipeline failed: {e}", 500

if __name__ == "__main__":
    run_meta_creatives_pipeline(None)