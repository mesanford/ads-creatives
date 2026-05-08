import json
from google.cloud import secretmanager
from google.cloud import bigquery
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

# ==========================================
# 1. Helper Functions
# ==========================================
def get_secret(project_id, secret_id, version_id="latest"):
    """Fetches the OAuth2 credentials payload from Secret Manager."""
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/{version_id}"
    response = client.access_secret_version(request={"name": name})
    return json.loads(response.payload.data.decode("UTF-8"))

def ensure_bq_table_exists(bq_client, bq_table_id):
    """Creates the BigQuery dimension table for creative mapping."""
    schema = [
        bigquery.SchemaField("account_id", "STRING"),
        bigquery.SchemaField("campaign_id", "STRING"),
        bigquery.SchemaField("campaign_name", "STRING"),
        bigquery.SchemaField("group_type", "STRING"),       
        bigquery.SchemaField("group_id", "STRING"),         
        bigquery.SchemaField("group_name", "STRING"),
        bigquery.SchemaField("ad_or_asset_id", "STRING"),
        bigquery.SchemaField("ad_or_asset_name", "STRING"),
        bigquery.SchemaField("creative_type", "STRING"),    
        bigquery.SchemaField("headlines", "STRING"),        # <-- ADDED
        bigquery.SchemaField("descriptions", "STRING"),     # <-- ADDED
        bigquery.SchemaField("final_url", "STRING"),        
        bigquery.SchemaField("media_url", "STRING"),        
    ]
    
    table = bigquery.Table(bq_table_id, schema=schema)
    table = bq_client.create_table(table, exists_ok=True)
    print(f"BigQuery Table Verified: {table.project}.{table.dataset_id}.{table.table_id}")

def pull_creatives(ga_client, bq_client, target_customer_ids, bq_table_id):
    """Pulls enabled ads and asset links and overwrites the BQ table."""
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

    rows_to_insert = []
    
    # Using WRITE_TRUNCATE to act as a daily snapshot (overwrites table)
    job_config = bigquery.LoadJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE)

    for child_id in target_customer_ids:
        for query_name, query_string in queries.items():
            try:
                stream = ga_service.search_stream(customer_id=child_id, query=query_string)
                for batch in stream:
                    for row in batch.results:
                        
                        final_url = None
                        media_url = None
                        headlines = None
                        descriptions = None
                        
                        if query_name == "Ad_Level":
                            g_type = "Ad Group"
                            g_id, g_name = str(row.ad_group.id), row.ad_group.name
                            item_id = str(row.ad_group_ad.ad.id)
                            item_name = getattr(row.ad_group_ad.ad, "name", "Unnamed Ad")
                            item_type = row.ad_group_ad.ad.type_.name
                            
                            # Final URLs are lists, grab the first one if it exists
                            if row.ad_group_ad.ad.final_urls:
                                final_url = row.ad_group_ad.ad.final_urls[0]
                                
                            # Parse Responsive Search Ad text into piped strings
                            if item_type == "RESPONSIVE_SEARCH_AD":
                                hl_list = [hl.text for hl in row.ad_group_ad.ad.responsive_search_ad.headlines]
                                desc_list = [desc.text for desc in row.ad_group_ad.ad.responsive_search_ad.descriptions]
                                if hl_list:
                                    headlines = " | ".join(hl_list)
                                if desc_list:
                                    descriptions = " | ".join(desc_list)
                                
                        elif query_name == "Asset_Group":
                            g_type = "Asset Group"
                            g_id, g_name = str(row.asset_group.id), row.asset_group.name
                            item_id = str(row.asset.id)
                            item_name = getattr(row.asset, "name", "Unnamed Asset")
                            item_type = row.asset.type_.name
                            
                            # Extract media/text based on asset type
                            if item_type == 'IMAGE':
                                media_url = row.asset.image_asset.full_size.url
                            elif item_type == 'YOUTUBE_VIDEO':
                                video_id = row.asset.youtube_video_asset.youtube_video_id
                                media_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else None
                            elif item_type == 'TEXT':
                                field_type = row.asset_group_asset.field_type.name
                                if 'HEADLINE' in field_type:
                                    headlines = row.asset.text_asset.text
                                else:
                                    descriptions = row.asset.text_asset.text

                        rows_to_insert.append({
                            "account_id": str(row.customer.id),
                            "campaign_id": str(row.campaign.id),
                            "campaign_name": row.campaign.name,
                            "group_type": g_type,
                            "group_id": g_id,
                            "group_name": g_name,
                            "ad_or_asset_id": item_id,
                            "ad_or_asset_name": item_name,
                            "creative_type": item_type,
                            "headlines": headlines,
                            "descriptions": descriptions,
                            "final_url": final_url,
                            "media_url": media_url
                        })
            except GoogleAdsException as ex:
                print(f"API Error for account {child_id} ({query_name}): {ex.error.code().name}")

    if rows_to_insert:
        job = bq_client.load_table_from_json(rows_to_insert, bq_table_id, job_config=job_config)
        job.result()
        return len(rows_to_insert)
    return 0

# ==========================================
# 2. Cloud Function Entry Point
# ==========================================
def run_creative_pipeline(request):
    """HTTP Cloud Function Entry Point."""
    GCP_PROJECT_ID = "looker-studio-pro-msanford" 
    SECRET_ID = "GOOGLE_ADS_CREDENTIALS"
    BQ_TABLE_ID = "looker-studio-pro-msanford.nueske_retail_gads_v2.gads_ad_creatives" 
    TARGET_CUSTOMER_IDS = ["8005256502"]

    try:
        credentials_dict = get_secret(GCP_PROJECT_ID, SECRET_ID)
        googleads_client = GoogleAdsClient.load_from_dict(credentials_dict, version="v24")
        bigquery_client = bigquery.Client(project=GCP_PROJECT_ID)
        
        ensure_bq_table_exists(bigquery_client, BQ_TABLE_ID)
        
        total_records = pull_creatives(googleads_client, bigquery_client, TARGET_CUSTOMER_IDS, BQ_TABLE_ID)
            
        return f"Creative pipeline success: Wrote {total_records} active creatives to BQ.", 200
        
    except Exception as e:
        print(f"Pipeline failed: {e}")
        return f"Error: {e}", 500

# For local testing (optional)
if __name__ == "__main__":
    run_creative_pipeline(None)