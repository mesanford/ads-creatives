import os
import time
import pandas as pd
from google.cloud import secretmanager
from google.cloud import bigquery
from google.cloud.exceptions import NotFound
from bingads.v13.reporting import *
from bingads.v13.reporting import ReportingServiceManager, ReportingDownloadParameters
from bingads import AuthorizationData, OAuthWebAuthCodeGrant
from bingads.service_client import ServiceClient

# Initialize Clients
secret_client = secretmanager.SecretManagerServiceClient()
bq_client = bigquery.Client()

def get_secret(secret_id):
    """Fetches a secret from Google Secret Manager and strips hidden spaces/newlines."""
    try:
        name = f"projects/{os.environ['GCP_PROJECT']}/secrets/{secret_id}/versions/latest"
        response = secret_client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8").strip()
    except Exception as e:
        print(f"[ERROR] Failed to fetch secret '{secret_id}': {e}")
        raise

def update_secret(secret_id, new_value):
    """Updates a secret in Google Secret Manager."""
    try:
        parent = f"projects/{os.environ['GCP_PROJECT']}/secrets/{secret_id}"
        secret_client.add_secret_version(
            parent=parent, payload={"data": new_value.encode("UTF-8")}
        )
        print(f"[INFO] Successfully updated secret: {secret_id}")
    except Exception as e:
        print(f"[ERROR] Failed to update secret '{secret_id}': {e}")
        raise

def process_and_upload_report(reporting_service_manager, report_request, bq_client, table_id):
    """Helper function to download, clean, deduplicate, and upload a report to BigQuery."""
    print(f"\n[INFO] === Processing Report for {table_id} ===")
    
    download_parameters = ReportingDownloadParameters(
        report_request=report_request,
        result_file_directory='/tmp',
        result_file_name=f'temp_{table_id.split(".")[-1]}.csv', 
        overwrite_result_file=True
    )

    print("[INFO] Waiting for Microsoft to generate the report...")
    report_path = reporting_service_manager.download_file(download_parameters)
    
    if not report_path:
        print(f"[WARNING] Failed to download report. Skipping upload to {table_id}.")
        return

    print("[INFO] Parsing report data with Pandas...")
    df = pd.read_csv(report_path, encoding='utf-8-sig')
    
    if df.empty:
        print(f"[WARNING] Report contains no data. Skipping {table_id}.")
        return

    # Clean headers and format dates
    df.columns = [c.replace(' ', '_').replace('.', '') for c in df.columns]
    df['TimePeriod'] = pd.to_datetime(df['TimePeriod']).dt.strftime('%Y-%m-%d')
    
    # --- BULLETPROOF FIX: Force Metrics to be Numeric Floats ---
    metric_cols = [
        'AverageCpc', 'Clicks', 'Conversions', 'CostPerConversion', 
        'Impressions', 'ImpressionSharePercent', 'Revenue', 'Spend'
    ]
    for col in metric_cols:
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace(',', '', regex=False).str.replace('%', '', regex=False)
            df[col] = pd.to_numeric(df[col], errors='coerce')
    # -----------------------------------------------------------
    
    # Handle Idempotency
    print(f"[INFO] Checking if BigQuery table `{table_id}` exists...")
    try:
        bq_client.get_table(table_id)
        unique_dates = df['TimePeriod'].unique().tolist()
        dates_formatted = ", ".join([f"'{d}'" for d in unique_dates])
        
        print(f"[INFO] Deleting existing records for dates: {dates_formatted}")
        delete_query = f"DELETE FROM `{table_id}` WHERE TimePeriod IN ({dates_formatted})"
        bq_client.query(delete_query).result()
    except NotFound:
        print("[INFO] Table does not exist. It will be created automatically.")

    # Load Data
    print(f"[INFO] Loading {len(df)} rows into BigQuery...")
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND", autodetect=True)
    job = bq_client.load_table_from_dataframe(df, table_id, job_config=job_config)
    job.result() 
    
    print(f"[INFO] SUCCESS! {job.output_rows} rows appended to {table_id}.")
    os.remove(report_path)

from bing_creatives_gallery import main as run_bing_creatives_gallery_pipeline

def main(event, context):
    print("[INFO] --- Starting Bing Ads to BigQuery Pipeline ---")
    
    try:
        # 1. Fetch Secrets & Auth
        print("[INFO] Fetching credentials and authenticating...")
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

        # 2. Setup Reporting Services
        reporting_service = ServiceClient(
            service='ReportingService', version=13, 
            authorization_data=auth_data, environment='production'
        )
        reporting_service_manager = ReportingServiceManager(
            authorization_data=auth_data, poll_interval_in_milliseconds=5000, environment='production'
        )

        # Create a reusable Scope object for standard campaigns
        report_scope = reporting_service.factory.create('AccountThroughAdGroupReportScope')
        report_scope.AccountIds = {'long': [account_id]}

        # ==========================================
        # REPORT 1: AD PERFORMANCE (Standard Search/Display)
        # ==========================================
        ad_req = reporting_service.factory.create('AdPerformanceReportRequest')
        ad_req.Aggregation = 'Daily'
        ad_req.ExcludeColumnHeaders = False
        ad_req.ExcludeReportFooter = True
        ad_req.ExcludeReportHeader = True
        ad_req.Format = 'Csv'
        ad_req.FormatVersion = '2.0'
        ad_req.ReportName = 'AdPerformance'
        ad_req.ReturnOnlyCompleteData = False
        ad_req.Scope = report_scope
        
        ad_time = reporting_service.factory.create('ReportTime')
        ad_time.PredefinedTime = 'Yesterday'
        ad_time.CustomDateRangeStart = None
        ad_time.CustomDateRangeEnd = None
        ad_req.Time = ad_time
        
        ad_cols = reporting_service.factory.create('ArrayOfAdPerformanceReportColumn')
        ad_cols.AdPerformanceReportColumn.append([
            'TimePeriod', 'AccountId', 'AccountName', 'AccountNumber',
            'CampaignType', 'CampaignStatus', 'CampaignId', 'CampaignName', 
            'AdGroupName', 'AdGroupId', 'AdId', 'AdStatus', 'AdDistribution', 
            'DestinationUrl', 'DeviceType', 'FinalUrl', 'AverageCpc', 
            'Clicks', 'Conversions', 'CostPerConversion', 'Impressions', 'Revenue', 'Spend'
        ])
        ad_req.Columns = ad_cols

        process_and_upload_report(
            reporting_service_manager, ad_req, bq_client, 
            "looker-studio-pro-msanford.nueske_msads_data.bing_ads_performance"
        )

        # ==========================================
        # REPORT 2: AD GROUP PERFORMANCE 
        # ==========================================
        ag_req = reporting_service.factory.create('AdGroupPerformanceReportRequest')
        ag_req.Aggregation = 'Daily'
        ag_req.ExcludeColumnHeaders = False
        ag_req.ExcludeReportFooter = True
        ag_req.ExcludeReportHeader = True
        ag_req.Format = 'Csv'
        ag_req.FormatVersion = '2.0'
        ag_req.ReportName = 'AdGroupPerformance_Daily'
        ag_req.ReturnOnlyCompleteData = False
        ag_req.Scope = report_scope
        
        ag_time = reporting_service.factory.create('ReportTime')
        ag_time.PredefinedTime = 'Yesterday'
        ag_time.CustomDateRangeStart = None
        ag_time.CustomDateRangeEnd = None
        ag_req.Time = ag_time 
        
        ag_cols = reporting_service.factory.create('ArrayOfAdGroupPerformanceReportColumn')
        ag_cols.AdGroupPerformanceReportColumn.append([
            'TimePeriod', 'AccountId', 'CampaignId', 'CampaignName', 'CampaignStatus', 
            'AdGroupId', 'AdGroupName', 'Status', 'AdDistribution', # Corrected to 'Status'
            'ImpressionSharePercent', 'AverageCpc', 'Clicks', 'Impressions', 'Spend',
            'Conversions', 'CostPerConversion', 'Revenue' 
        ])
        ag_req.Columns = ag_cols

        process_and_upload_report(
            reporting_service_manager, ag_req, bq_client, 
            "looker-studio-pro-msanford.nueske_msads_data.bing_adgroup_performance"
        )

        # ==========================================
        # REPORT 3: ASSET GROUP PERFORMANCE (PMAX Only!)
        # ==========================================
        pmax_scope = reporting_service.factory.create('AccountThroughAssetGroupReportScope')
        pmax_scope.AccountIds = {'long': [account_id]}

        pmax_req = reporting_service.factory.create('AssetGroupPerformanceReportRequest')
        pmax_req.Aggregation = 'Daily'
        pmax_req.ExcludeColumnHeaders = False
        pmax_req.ExcludeReportFooter = True
        pmax_req.ExcludeReportHeader = True
        pmax_req.Format = 'Csv'
        pmax_req.FormatVersion = '2.0'
        pmax_req.ReportName = 'PmaxPerformance'
        pmax_req.ReturnOnlyCompleteData = False
        pmax_req.Scope = pmax_scope
        
        pmax_time = reporting_service.factory.create('ReportTime')
        pmax_time.PredefinedTime = 'Yesterday'
        pmax_time.CustomDateRangeStart = None
        pmax_time.CustomDateRangeEnd = None
        pmax_req.Time = pmax_time
        
        pmax_cols = reporting_service.factory.create('ArrayOfAssetGroupPerformanceReportColumn')
        pmax_cols.AssetGroupPerformanceReportColumn.append([
            'TimePeriod', 'AccountId', 'CampaignId', 'CampaignName','CampaignType', 
            'AssetGroupId', 'AssetGroupName', 'AssetGroupStatus',
            'AverageCpc', 'Clicks', 'Conversions', 
            'CostPerConversion', 'Impressions', 'Revenue', 'Spend'
        ])
        pmax_req.Columns = pmax_cols

        process_and_upload_report(
            reporting_service_manager, pmax_req, bq_client, 
            "looker-studio-pro-msanford.nueske_msads_data.bing_pmax_performance"
        )
        
        print("\n[INFO] --- All Reports Successfully Processed! ---")

    except Exception as e:
        print(f"[ERROR] Pipeline failed: {str(e)}")
        raise e 

if __name__ == "__main__":
    main(None, None)