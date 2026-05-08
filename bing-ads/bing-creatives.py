import os
import time
import pandas as pd
import re
from google.cloud import secretmanager
from google.cloud import bigquery
from bingads.v13.bulk import BulkServiceManager, DownloadParameters
from bingads import AuthorizationData, OAuthWebAuthCodeGrant

# ==========================================
# CONFIGURATION
# ==========================================
BQ_TABLE_ID = "looker-studio-pro-msanford.nueske_msads_data.bing_ad_creatives"
# ==========================================

secret_client = secretmanager.SecretManagerServiceClient()
bq_client = bigquery.Client()

def get_secret(secret_id):
    try:
        name = f"projects/{os.environ['GCP_PROJECT']}/secrets/{secret_id}/versions/latest"
        response = secret_client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8").strip()
    except Exception as e:
        print(f"[ERROR] Failed to fetch secret '{secret_id}': {e}")
        raise

def update_secret(secret_id, new_value):
    try:
        parent = f"projects/{os.environ['GCP_PROJECT']}/secrets/{secret_id}"
        secret_client.add_secret_version(
            parent=parent, payload={"data": new_value.encode("UTF-8")}
        )
    except Exception as e:
        pass

def main(event, context):
    print("[INFO] --- Starting Bing Ads CREATIVE Sync ---")
    
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

        # Added 'Images' and 'Videos' so we can extract their URLs!
        download_parameters = DownloadParameters(
            campaign_ids=None, 
            data_scope=['EntityData'],
            download_entities=['Ads', 'AssetGroups', 'Images', 'Videos'],
            result_file_directory='/tmp',
            result_file_name='bing_creatives_raw.csv',
            overwrite_result_file=True,
            last_sync_time_in_utc=None
        )

        print("[INFO] Waiting for Microsoft to package the account blueprint...")
        bulk_file_path = bulk_service_manager.download_file(download_parameters)
        
        if not bulk_file_path:
            print("[ERROR] Failed to download Bulk file.")
            return

        print("[INFO] Parsing massive Bulk CSV...")
        df_raw = pd.read_csv(bulk_file_path, encoding='utf-8-sig', dtype=str)
        
        if df_raw.empty:
            print("[WARNING] The Bulk file contains no data.")
            return

        # ==========================================
        # THE URL LOOKUP MAGIC
        # ==========================================
        print("[INFO] Mapping Image/Video URLs to Ad IDs...")
        
        # 1. Build a dictionary of Media IDs -> Actual URLs
        media_lookup = {}
        media_rows = df_raw[df_raw['Type'].isin(['Image', 'Video'])]
        for _, row in media_rows.iterrows():
            media_id = str(row['Id']).strip()
            url = str(row['Url']).strip()
            if pd.notna(url) and url != 'nan':
                media_lookup[media_id] = url

        # 2. Filter the dataframe down to just our Ads and Asset Groups
        columns_to_keep = ['Type', 'Status', 'Id', 'Parent Id', 'Campaign', 'Ad Group', 'Name', 'Title', 'Text', 'Final Url', 'Images', 'Videos']
        available_cols = [col for col in columns_to_keep if col in df_raw.columns]
        df = df_raw[available_cols].copy()
        df = df[df['Type'].str.contains('Ad|Asset Group', na=False, case=False)]

        # 3. Create a function to extract the hidden ID and grab the URL
        def get_media_url(row):
            image_str = str(row.get('Images', ''))
            video_str = str(row.get('Videos', ''))
            
            # Use Regex to hunt for the JSON ID pattern inside the string
            match_img = re.search(r'"id":\s*"?(\d+)"?', image_str)
            if match_img:
                media_id = match_img.group(1)
                if media_id in media_lookup:
                    return media_lookup[media_id]
            
            # If no image is found, check for a video ID
            match_vid = re.search(r'"id":\s*"?(\d+)"?', video_str)
            if match_vid:
                media_id = match_vid.group(1)
                if media_id in media_lookup:
                    return media_lookup[media_id]

            return 'N/A'

        # Apply the magic function to create our new column
        df['Asset_Url'] = df.apply(get_media_url, axis=1)

        # Drop the messy JSON columns so BigQuery stays clean
        if 'Images' in df.columns:
            df = df.drop(columns=['Images'])
        if 'Videos' in df.columns:
            df = df.drop(columns=['Videos'])

        df = df.fillna('N/A')
        # ==========================================

        print(f"[INFO] Uploading {len(df)} Ad Creatives to BigQuery...")
        
        job_config = bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE", autodetect=True)
        job = bq_client.load_table_from_dataframe(df, BQ_TABLE_ID, job_config=job_config)
        job.result() 
        
        print(f"[INFO] SUCCESS! {job.output_rows} Ad Creatives (with URLs!) updated in BigQuery.")
        os.remove(bulk_file_path)

    except Exception as e:
        print(f"[ERROR] Pipeline failed: {str(e)}")
        raise e 

if __name__ == "__main__":
    main(None, None)