import sys
import json
from datetime import datetime, timedelta
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
    """Creates the BigQuery table with a unified schema and partitioning."""
    schema = [
        bigquery.SchemaField("date", "DATE"),
        bigquery.SchemaField("account_id", "STRING"),
        bigquery.SchemaField("campaign_id", "STRING"),
        bigquery.SchemaField("campaign_name", "STRING"),
        bigquery.SchemaField("channel_type", "STRING"),
        bigquery.SchemaField("group_id", "STRING"),
        bigquery.SchemaField("group_name", "STRING"),
        bigquery.SchemaField("ad_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("ad_name", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("ad_network", "STRING"),
        bigquery.SchemaField("impressions", "INT64"),
        bigquery.SchemaField("clicks", "INT64"),
        bigquery.SchemaField("cost", "FLOAT64"),
        bigquery.SchemaField("conversions", "FLOAT64"),       # <-- ADDED
        bigquery.SchemaField("conversion_value", "FLOAT64"),  # <-- ADDED
    ]
    
    table = bigquery.Table(bq_table_id, schema=schema)
    table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY,
        field="date"
    )
    
    table = bq_client.create_table(table, exists_ok=True)
    print(f"BigQuery Table Verified: {table.project}.{table.dataset_id}.{table.table_id}")

def process_date(ga_client, bq_client, target_customer_ids, target_date, bq_table_id):
    """Core logic to pull and insert data for a specific date."""
    ga_service = ga_client.get_service("GoogleAdsService")
    
    # Idempotency: Clear existing records for this date
    delete_query = f"DELETE FROM `{bq_table_id}` WHERE date = '{target_date}'"
    bq_client.query(delete_query).result()

    # <-- ADDED metrics.conversions and metrics.conversions_value to SELECT queries below
    queries = {
        "Ad_Level": f"""
            SELECT segments.date, customer.id, campaign.id, campaign.name, 
                   campaign.advertising_channel_type, ad_group.id, ad_group.name, 
                   ad_group_ad.ad.id, ad_group_ad.ad.name, segments.ad_network_type, 
                   metrics.impressions, metrics.clicks, metrics.cost_micros,
                   metrics.conversions, metrics.conversions_value
            FROM ad_group_ad
            WHERE segments.date = '{target_date}' AND metrics.impressions > 0
        """,
        "Asset_Group": f"""
            SELECT segments.date, customer.id, campaign.id, campaign.name, 
                   campaign.advertising_channel_type, asset_group.id, asset_group.name, 
                   segments.ad_network_type, metrics.impressions, metrics.clicks, 
                   metrics.cost_micros, metrics.conversions, metrics.conversions_value
            FROM asset_group
            WHERE segments.date = '{target_date}' AND metrics.impressions > 0
        """
    }

    rows_to_insert = []
    job_config = bigquery.LoadJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_APPEND)

    for child_id in target_customer_ids:
        for query_name, query_string in queries.items():
            try:
                stream = ga_service.search_stream(customer_id=child_id, query=query_string)
                for batch in stream:
                    for row in batch.results:
                        if query_name == "Ad_Level":
                            g_id, g_name = str(row.ad_group.id), row.ad_group.name
                            a_id, a_name = str(row.ad_group_ad.ad.id), getattr(row.ad_group_ad.ad, "name", None)
                        else:
                            g_id, g_name = str(row.asset_group.id), row.asset_group.name
                            a_id, a_name = None, None

                        rows_to_insert.append({
                            "date": row.segments.date,
                            "account_id": str(row.customer.id),
                            "campaign_id": str(row.campaign.id),
                            "campaign_name": row.campaign.name,
                            "channel_type": row.campaign.advertising_channel_type.name,
                            "group_id": g_id,
                            "group_name": g_name,
                            "ad_id": a_id,
                            "ad_name": a_name,
                            "ad_network": row.segments.ad_network_type.name,
                            "impressions": row.metrics.impressions,
                            "clicks": row.metrics.clicks,
                            "cost": row.metrics.cost_micros / 1000000.0,
                            "conversions": row.metrics.conversions,             # <-- ADDED
                            "conversion_value": row.metrics.conversions_value   # <-- ADDED
                        })
            except GoogleAdsException as ex:
                print(f"API Error for account {child_id}: {ex.error.code().name}")

    if rows_to_insert:
        job = bq_client.load_table_from_json(rows_to_insert, bq_table_id, job_config=job_config)
        job.result()
        return len(rows_to_insert)
    return 0

from gads_creatives_gallery import run_creative_gallery_pipeline

# ==========================================
# 2. Cloud Function Entry Points
# ==========================================
def run_google_ads_pipeline(request):
    """HTTP Cloud Function Entry Point."""
    GCP_PROJECT_ID = "looker-studio-pro-msanford" 
    SECRET_ID = "GOOGLE_ADS_CREDENTIALS"
    BQ_TABLE_ID = "looker-studio-pro-msanford.nueske_retail_gads_v2.gads_api_custom" 
    TARGET_CUSTOMER_IDS = ["8005256502"]

    try:
        credentials_dict = get_secret(GCP_PROJECT_ID, SECRET_ID)
        googleads_client = GoogleAdsClient.load_from_dict(credentials_dict, version="v24")
        bigquery_client = bigquery.Client(project=GCP_PROJECT_ID)
        
        ensure_bq_table_exists(bigquery_client, BQ_TABLE_ID)
        
        # Dynamic 7-day window
        today = datetime.today()
        current_date = today - timedelta(days=7)
        end_date = today - timedelta(days=1)
        
        total_records = 0
        while current_date <= end_date:
            target_date_str = current_date.strftime('%Y-%m-%d')
            total_records += process_date(googleads_client, bigquery_client, TARGET_CUSTOMER_IDS, target_date_str, BQ_TABLE_ID)
            current_date += timedelta(days=1)
            
        return f"Pipeline success: Inserted {total_records} rows for the last 7 days.", 200
        
    except Exception as e:
        print(f"Pipeline failed: {e}")
        return f"Error: {e}", 500