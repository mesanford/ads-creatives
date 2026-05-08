import sys
import json
from datetime import datetime, timedelta
from google.cloud import secretmanager
from google.cloud import bigquery
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

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
        # Unified Group Fields (Handles both Ad Groups and Asset Groups)
        bigquery.SchemaField("group_id", "STRING"),
        bigquery.SchemaField("group_name", "STRING"),
        # Ad Fields (Will be NULL for Performance Max Asset Groups)
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

def main(ga_client, bq_client, target_customer_ids, target_date, bq_table_id):
    ga_service = ga_client.get_service("GoogleAdsService")
    
    # --- 1. IDEMPOTENCY CHECK ---
    # Only run this ONCE per date, before the query loops begin!
    print(f"Clearing any existing records in BigQuery for {target_date}...")
    delete_query = f"DELETE FROM `{bq_table_id}` WHERE date = '{target_date}'"
    try:
        bq_client.query(delete_query).result()
    except Exception as e:
        print(f"Failed to execute delete query. Aborting. Error: {e}")
        sys.exit(1)

    # --- 2. DEFINE QUERIES ---
    # Added metrics.conversions and metrics.conversions_value to both queries
    queries = {
        "Ad_Level": f"""
            SELECT
              segments.date,
              customer.id,
              campaign.id,
              campaign.name,
              campaign.advertising_channel_type,
              ad_group.id,
              ad_group.name,
              ad_group_ad.ad.id,
              ad_group_ad.ad.name,
              segments.ad_network_type,
              metrics.impressions,
              metrics.clicks,
              metrics.cost_micros,
              metrics.conversions,
              metrics.conversions_value
            FROM ad_group_ad
            WHERE segments.date = '{target_date}'
              AND metrics.impressions > 0
        """,
        "Asset_Group": f"""
            SELECT
              segments.date,
              customer.id,
              campaign.id,
              campaign.name,
              campaign.advertising_channel_type,
              asset_group.id,
              asset_group.name,
              segments.ad_network_type,
              metrics.impressions,
              metrics.clicks,
              metrics.cost_micros,
              metrics.conversions,
              metrics.conversions_value
            FROM asset_group
            WHERE segments.date = '{target_date}'
              AND metrics.impressions > 0
        """
    }

    print(f"Starting data pull for {len(target_customer_ids)} selected accounts for {target_date}...")

    rows_to_insert = []
    total_inserted = 0
    CHUNK_SIZE = 5000 

    job_config = bigquery.LoadJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND
    )

    # --- 3. LOOP THROUGH CUSTOMERS & QUERIES ---
    for child_id in target_customer_ids:
        for query_name, query_string in queries.items():
            try:
                stream = ga_service.search_stream(customer_id=child_id, query=query_string)
                for batch in stream:
                    for row in batch.results:
                        
                        # Data Mapping based on the Query Type
                        if query_name == "Ad_Level":
                            group_id = str(row.ad_group.id)
                            group_name = row.ad_group.name
                            ad_id = str(row.ad_group_ad.ad.id)
                            ad_name = getattr(row.ad_group_ad.ad, "name", None) # Safely get ad name if it exists
                        elif query_name == "Asset_Group":
                            group_id = str(row.asset_group.id)
                            group_name = row.asset_group.name
                            ad_id = None
                            ad_name = None

                        rows_to_insert.append({
                            "date": row.segments.date,
                            "account_id": str(row.customer.id),
                            "campaign_id": str(row.campaign.id),
                            "campaign_name": row.campaign.name,
                            "channel_type": row.campaign.advertising_channel_type.name,
                            "group_id": group_id,
                            "group_name": group_name,
                            "ad_id": ad_id,
                            "ad_name": ad_name,
                            "ad_network": row.segments.ad_network_type.name,
                            "impressions": row.metrics.impressions,
                            "clicks": row.metrics.clicks,
                            "cost": row.metrics.cost_micros / 1000000.0,
                            "conversions": row.metrics.conversions,             # <-- ADDED
                            "conversion_value": row.metrics.conversions_value   # <-- ADDED
                        })

                        # --- IN-MEMORY CHUNKING ---
                        if len(rows_to_insert) >= CHUNK_SIZE:
                            job = bq_client.load_table_from_json(rows_to_insert, bq_table_id, job_config=job_config)
                            job.result() 
                            
                            if job.errors:
                                print(f"BigQuery Insert Errors ({query_name}): {job.errors}")
                            else:
                                total_inserted += len(rows_to_insert)
                                rows_to_insert.clear()  
                                
            except GoogleAdsException as ex:
                print(f"Skipping account {child_id} ({query_name}) due to API Error: {ex.error.code().name}")
                continue

    # --- 4. INSERT REMAINING ROWS ---
    if rows_to_insert:
        job = bq_client.load_table_from_json(rows_to_insert, bq_table_id, job_config=job_config)
        job.result() 
        
        if job.errors:
            print(f"BigQuery Final Insert Errors: {job.errors}")
        else:
            total_inserted += len(rows_to_insert)

    if total_inserted > 0:
        print(f"Success: Inserted a total of {total_inserted} rows for {target_date}.")
    else:
        print(f"No ad data found across the selected accounts for {target_date}.")
        
if __name__ == "__main__":
    # --- CONFIGURATION ---
    GCP_PROJECT_ID = "looker-studio-pro-msanford" 
    SECRET_ID = "GOOGLE_ADS_CREDENTIALS"
    BQ_TABLE_ID = "looker-studio-pro-msanford.nueske_retail_gads_v2.gads_api_custom" 
    
    TARGET_CUSTOMER_IDS = [
        "8005256502"
    ]

    # --- BACKFILL DATE RANGE ---
    # Specify your exact start and end dates (YYYY-MM-DD)
    start_date = datetime.strptime("2025-01-01", "%Y-%m-%d")
    end_date = datetime.strptime("2026-05-03", "%Y-%m-%d")

    try:
        credentials_dict = get_secret(GCP_PROJECT_ID, SECRET_ID)
        googleads_client = GoogleAdsClient.load_from_dict(credentials_dict, version="v24")
        bigquery_client = bigquery.Client(project=GCP_PROJECT_ID)
        
        print("\nChecking BigQuery configuration...")
        ensure_bq_table_exists(bigquery_client, BQ_TABLE_ID)
        
        current_date = start_date
        while current_date <= end_date:
            target_date_str = current_date.strftime('%Y-%m-%d')
            
            print(f"\n{'='*50}")
            print(f"Processing Date: {target_date_str}")
            print(f"{'='*50}")
            
            main(googleads_client, bigquery_client, TARGET_CUSTOMER_IDS, target_date_str, BQ_TABLE_ID)
            
            current_date += timedelta(days=1)
            
        print("\nHistorical backfill complete!")
        
    except Exception as e:
        print(f"Pipeline failed. Error: {e}")