import os
import time
import pandas as pd
from datetime import datetime
from google.cloud import secretmanager
from google.cloud import bigquery
from google.cloud.exceptions import NotFound
from bingads.v13.reporting import *
from bingads.v13.reporting import ReportingServiceManager, ReportingDownloadParameters
from bingads import AuthorizationData, OAuthWebAuthCodeGrant
from bingads.service_client import ServiceClient

# ==========================================
# GLOBAL BACKFILL SETTINGS
# Format: "YYYY-MM-DD"
# ==========================================
BACKFILL_START = "2025-01-01"
BACKFILL_END = "2026-05-04"
# ==========================================

# Initialize Clients
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
        print(f"[INFO] Successfully updated secret: {secret_id}")
    except Exception as e:
        print(f"[ERROR] Failed to update secret '{secret_id}': {e}")
        raise

def build_custom_report_time(reporting_service, start_str, end_str):
    """Converts our global string dates into Microsoft's strict SOAP Date objects."""
    start_dt = datetime.strptime(start_str, "%Y-%m-%d")
    end_dt = datetime.strptime(end_str, "%Y-%m-%d")

    report_time = reporting_service.factory.create('ReportTime')
    
    # Start Date
    start_date = reporting_service.factory.create('Date')
    start_date.Day = start_dt.day
    start_date.Month = start_dt.month
    start_date.Year = start_dt.year
    report_time.CustomDateRangeStart = start_date

    # End Date
    end_date = reporting_service.factory.create('Date')
    end_date.Day = end_dt.day
    end_date.Month = end_dt.month
    end_date.Year = end_dt.year
    report_time.CustomDateRangeEnd = end_date
    
    return report_time

def process_and_upload_report(reporting_service_manager, report_request, bq_client, table_id):
    print(f"\n[INFO] === Processing Backfill for {table_id} ===")
    
    download_parameters = ReportingDownloadParameters(
        report_request=report_request,
        result_file_directory='/tmp',
        result_file_name=f'backfill_{table_id.split(".")[-1]}.csv', 
        overwrite_result_file=True
    )

    print(f"[INFO] Requesting data from {BACKFILL_START} to {BACKFILL_END}...")
    report_path = reporting_service_manager.download_file(download_parameters)
    
    if not report_path:
        print(f"[WARNING] Failed to download report. Skipping upload to {table_id}.")
        return

    print("[INFO] Parsing report data with Pandas...")
    df = pd.read_csv(report_path, encoding='utf-8-sig')
    
    if df.empty:
        print(f"[WARNING] Report contains no data. Skipping {table_id}.")
        return

    # Clean column headers and format the date
    df.columns = [c.replace(' ', '_').replace('.', '') for c in df.columns]
    df['TimePeriod'] = pd.to_datetime(df['TimePeriod']).dt.strftime('%Y-%m-%d')
    
    # --- BULLETPROOF FIX: Force Metrics to be Numeric Floats ---
    metric_cols = [
        'AverageCpc', 'Clicks', 'Conversions', 'CostPerConversion', 
        'Impressions', 'ImpressionSharePercent', 'Revenue', 'Spend'
    ]
    for col in metric_cols:
        if col in df.columns:
            if df[col].dtype == 'object':
                df[col] = df[col].astype(str).str.replace(',', '', regex=False).str.replace('%', '', regex=False)
            df[col] = pd.to_numeric(df[col], errors='coerce')
    # -----------------------------------------------------------
    
    print(f"[INFO] Checking if BigQuery table `{table_id}` exists...")
    try:
        bq_client.get_table(table_id)
        unique_dates = df['TimePeriod'].unique().tolist()
        dates_formatted = ", ".join([f"'{d}'" for d in unique_dates])
        
        print(f"[INFO] Deleting existing records for the backfill dates to prevent duplicates...")
        delete_query = f"DELETE FROM `{table_id}` WHERE TimePeriod IN ({dates_formatted})"
        bq_client.query(delete_query).result()
    except NotFound:
        print("[INFO] Table does not exist. It will be created automatically.")

    print(f"[INFO] Loading {len(df)} historical rows into BigQuery...")
    job_config = bigquery.LoadJobConfig(write_disposition="WRITE_APPEND", autodetect=True)
    job = bq_client.load_table_from_dataframe(df, table_id, job_config=job_config)
    job.result() 
    
    print(f"[INFO] SUCCESS! {job.output_rows} rows appended to {table_id}.")
    os.remove(report_path)

def main(event, context):
    print(f"[INFO] --- Starting Bing Ads BACKFILL ({BACKFILL_START} to {BACKFILL_END}) ---")
    
    try:
        # 1. Fetch Secrets & Auth
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

        report_scope = reporting_service.factory.create('AccountThroughAdGroupReportScope')
        report_scope.AccountIds = {'long': [account_id]}
        
        # Build the custom time object once to use for all reports
        custom_report_time = build_custom_report_time(reporting_service, BACKFILL_START, BACKFILL_END)

        # ==========================================
        # REPORT 1: AD PERFORMANCE 
        # ==========================================
        ad_req = reporting_service.factory.create('AdPerformanceReportRequest')
        ad_req.Aggregation = 'Daily'
        ad_req.ExcludeColumnHeaders = False
        ad_req.ExcludeReportFooter = True
        ad_req.ExcludeReportHeader = True
        ad_req.Format = 'Csv'
        ad_req.FormatVersion = '2.0'
        ad_req.ReportName = 'AdPerformance_Backfill'
        ad_req.ReturnOnlyCompleteData = False
        ad_req.Scope = report_scope
        ad_req.Time = custom_report_time 
        
        ad_cols = reporting_service.factory.create('ArrayOfAdPerformanceReportColumn')
        ad_cols.AdPerformanceReportColumn.append([
            'TimePeriod', 'AccountId', 'AccountName', 'AccountNumber',
            'CampaignType', 'CampaignStatus', 'CampaignId', 'CampaignName', 
            'AdGroupName', 'AdGroupId', 'AdId','AdStatus', 'AdDistribution', 
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
        ag_req.ReportName = 'AdGroupPerformance_Backfill'
        ag_req.ReturnOnlyCompleteData = False
        ag_req.Scope = report_scope
        ag_req.Time = custom_report_time 
        
        ag_cols = reporting_service.factory.create('ArrayOfAdGroupPerformanceReportColumn')
        ag_cols.AdGroupPerformanceReportColumn.append([
            'TimePeriod', 'AccountId', 'CampaignId', 'CampaignName', 'CampaignStatus', 
            'AdGroupId', 'AdGroupName', 'Status', 'AdDistribution', 
            'ImpressionSharePercent', 'AverageCpc', 'Clicks', 'Impressions', 'Spend',
            'Conversions', 'CostPerConversion', 'Revenue' 
        ])
        ag_req.Columns = ag_cols

        process_and_upload_report(
            reporting_service_manager, ag_req, bq_client, 
            "looker-studio-pro-msanford.nueske_msads_data.bing_adgroup_performance"
        )

        # ==========================================
        # REPORT 3: ASSET GROUP PERFORMANCE (PMAX)
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
        pmax_req.ReportName = 'PmaxPerformance_Backfill'
        pmax_req.ReturnOnlyCompleteData = False
        pmax_req.Scope = pmax_scope
        pmax_req.Time = custom_report_time 
        
        pmax_cols = reporting_service.factory.create('ArrayOfAssetGroupPerformanceReportColumn')
        pmax_cols.AssetGroupPerformanceReportColumn.append([
            'TimePeriod', 'AccountId', 'CampaignId', 'CampaignName', 'CampaignType', 
            'AssetGroupId', 'AssetGroupName', 'AssetGroupStatus',
            'AverageCpc', 'Clicks', 'Conversions', 
            'CostPerConversion', 'Impressions', 'Revenue', 'Spend'
        ])
        pmax_req.Columns = pmax_cols

        process_and_upload_report(
            reporting_service_manager, pmax_req, bq_client, 
            "looker-studio-pro-msanford.nueske_msads_data.bing_pmax_performance"
        )

        print("\n[INFO] --- Backfill Complete! ---")

    except Exception as e:
        print(f"[ERROR] Pipeline failed: {str(e)}")
        raise e 

if __name__ == "__main__":
    main(None, None)