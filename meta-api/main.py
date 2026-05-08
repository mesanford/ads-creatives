import os
import time
import pandas as pd
from datetime import datetime, timedelta

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
BQ_TABLE = 'meta_api_data'
BQ_TEMP_TABLE = f"{BQ_TABLE}_temp"

# ==========================================
# 2. Helper Functions
# ==========================================
def get_secret(secret_id, version_id="latest"):
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{GCP_PROJECT_ID}/secrets/{secret_id}/versions/{version_id}"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")

def extract_action_value(actions_list, target_action_type):
    if not isinstance(actions_list, list):
        return 0.0
    for action in actions_list:
        if action.get('action_type') == target_action_type:
            return float(action.get('value', 0.0))
    return 0.0

def make_api_call_with_retries(api_call_func, max_retries=5):
    retries = 0
    backoff_factor = 2  
    wait_time = 60      

    while retries < max_retries:
        try:
            return api_call_func()
        except FacebookRequestError as e:
            error_code = e.api_error_code()
            if error_code in [17, 613, 80000, 80003, 80004, 80014]:
                print(f"Rate limit hit (Error {error_code}). Waiting {wait_time} seconds before retrying...")
                time.sleep(wait_time)
                retries += 1
                wait_time *= backoff_factor 
            else:
                print(f"Meta API Error: {e.api_error_message()}")
                raise e
        except Exception as e:
            raise e
            
    raise Exception(f"API call failed after {max_retries} retries due to rate limits.")

# ==========================================
# 3. Extract Data from Meta Marketing API
# ==========================================
def get_ecommerce_insights(app_id, app_secret, access_token, ad_account_id):
    print("Authenticating with Meta API...")
    FacebookAdsApi.init(app_id, app_secret, access_token)
    account = AdAccount(ad_account_id)

    # Dynamically calculate an 8-day rolling window to account for delayed attribution
    start_date = (datetime.today() - timedelta(days=8)).strftime('%Y-%m-%d')
    end_date = (datetime.today() - timedelta(days=1)).strftime('%Y-%m-%d')

    fields = [
        'date_start', 'campaign_id', 'campaign_name', 'adset_id',
        'adset_name', 'ad_id', 'ad_name', 'spend', 'impressions', 
        'inline_link_clicks', 'actions', 'action_values', 'purchase_roas'      
    ]
    
    params = {
        'time_range': {'since': start_date, 'until': end_date},
        'time_increment': 1, 
        'level': 'ad'  
    }

    print(f"Fetching insights from {start_date} to {end_date}...")
    
    insights = make_api_call_with_retries(
        lambda: account.get_insights(fields=fields, params=params)
    )
    
    data = []
    for item in insights:
        spend = float(item.get('spend', 0.0))
        impressions = int(item.get('impressions', 0))
        link_clicks = int(item.get('inline_link_clicks', 0))
        
        purchases = extract_action_value(item.get('actions', []), 'offsite_conversion.fb_pixel_purchase')
        revenue = extract_action_value(item.get('action_values', []), 'offsite_conversion.fb_pixel_purchase')
        roas = extract_action_value(item.get('purchase_roas', []), 'offsite_conversion.fb_pixel_purchase')

        data.append({
            'date': item.get('date_start'),
            'campaign_id': item.get('campaign_id'),
            'campaign_name': item.get('campaign_name'),
            'adset_id': item.get('adset_id'),      
            'adset_name': item.get('adset_name'),  
            'ad_id': item.get('ad_id'),            
            'ad_name': item.get('ad_name'),        
            'spend': spend,
            'impressions': impressions,
            'link_clicks': link_clicks,
            'purchases': int(purchases),
            'revenue': revenue,
            'roas': roas
        })
        
    return pd.DataFrame(data)

# ==========================================
# 4. Load & Deduplicate in BigQuery
# ==========================================
def load_and_merge_bigquery(df):
    if df.empty:
        print("No data found. Skipping BQ load.")
        return

    print("Initializing BigQuery Client...")
    client = bigquery.Client(project=GCP_PROJECT_ID)
    
    temp_table_id = f"{GCP_PROJECT_ID}.{BQ_DATASET}.{BQ_TEMP_TABLE}"
    target_table_id = f"{GCP_PROJECT_ID}.{BQ_DATASET}.{BQ_TABLE}"

    job_config = bigquery.LoadJobConfig(
        write_disposition="WRITE_TRUNCATE", 
        schema=[
            bigquery.SchemaField("date", "DATE"),
            bigquery.SchemaField("campaign_id", "STRING"),
            bigquery.SchemaField("campaign_name", "STRING"),
            bigquery.SchemaField("adset_id", "STRING"),
            bigquery.SchemaField("adset_name", "STRING"),
            bigquery.SchemaField("ad_id", "STRING"),
            bigquery.SchemaField("ad_name", "STRING"),
            bigquery.SchemaField("spend", "FLOAT"),
            bigquery.SchemaField("impressions", "INTEGER"),
            bigquery.SchemaField("link_clicks", "INTEGER"),
            bigquery.SchemaField("purchases", "INTEGER"),
            bigquery.SchemaField("revenue", "FLOAT"),
            bigquery.SchemaField("roas", "FLOAT"),
        ],
    )

    print(f"Loading {len(df)} rows into staging table {temp_table_id}...")
    load_job = client.load_table_from_dataframe(df, temp_table_id, job_config=job_config)
    load_job.result() 

    merge_query = f"""
        MERGE `{target_table_id}` T
        USING `{temp_table_id}` S
        ON T.date = S.date AND T.ad_id = S.ad_id
        WHEN MATCHED THEN
          UPDATE SET 
            campaign_id = S.campaign_id,
            campaign_name = S.campaign_name,
            adset_id = S.adset_id,
            adset_name = S.adset_name,
            ad_name = S.ad_name,
            spend = S.spend,
            impressions = S.impressions,
            link_clicks = S.link_clicks,
            purchases = S.purchases,
            revenue = S.revenue,
            roas = S.roas
        WHEN NOT MATCHED THEN
          INSERT (date, campaign_id, campaign_name, adset_id, adset_name, ad_id, ad_name, spend, impressions, link_clicks, purchases, revenue, roas)
          VALUES (S.date, S.campaign_id, S.campaign_name, S.adset_id, S.adset_name, S.ad_id, S.ad_name, S.spend, S.impressions, S.link_clicks, S.purchases, S.revenue, S.roas)
    """
    
    print(f"Merging data from staging to target table {target_table_id}...")
    merge_job = client.query(merge_query)
    merge_job.result()
    print("Success! Ad-level data merged into BigQuery.")

from meta_creatives_gallery import run_meta_creatives_gallery_pipeline

# ==========================================
# 5. Cloud Function Entry Points
# ==========================================
def run_meta_pipeline(request):
    """HTTP Cloud Function Entry Point."""
    try:
        app_id = get_secret("FB_APP_ID")
        app_secret = get_secret("FB_APP_SECRET")
        access_token = get_secret("FB_ACCESS_TOKEN")
        ad_account_id = get_secret("FB_AD_ACCOUNT_ID")

        df_insights = get_ecommerce_insights(app_id, app_secret, access_token, ad_account_id)
        
        if not df_insights.empty:
            df_insights['date'] = pd.to_datetime(df_insights['date']).dt.date
            
        load_and_merge_bigquery(df_insights)
        
        return "Pipeline executed successfully.", 200
        
    except Exception as e:
        print(f"An error occurred: {e}")
        return f"Pipeline failed: {e}", 500