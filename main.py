from __future__ import annotations

import csv
import os
import subprocess
import sys
import datetime as dt
from typing import List, Dict, Set
import logging


def start_log() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('gva.log', encoding='utf-8')
        ]
    )
    return logging.getLogger(__name__)


def geocode_incidents(incidents: List[Dict], logger: logging.Logger) -> List[Dict]:
    """Geocode a list of incident dictionaries and return them with coordinates."""
    if not incidents:
        return []
    
    logger.info(f"Geocoding {len(incidents)} new incidents...")
    
    try:
        from geopy.geocoders import ArcGIS
        import time
        
        geolocator = ArcGIS(timeout=10)
        geocoded_incidents = []
        
        for i, incident in enumerate(incidents):
            try:
                city = incident.get('City Or County', '').strip()
                state = incident.get('State', '').strip()
                
                if city and state:
                    address = f"{city}, {state}, USA"
                    location = geolocator.geocode(address)
                    
                    if location:
                        incident['latitude'] = str(location.latitude)
                        incident['longitude'] = str(location.longitude)
                    else:
                        incident['latitude'] = ''
                        incident['longitude'] = ''
                        logger.warning(f"Could not geocode {address}")
                else:
                    incident['latitude'] = ''
                    incident['longitude'] = ''
                    logger.warning(f"Missing city/state data for incident")
                
                geocoded_incidents.append(incident)
                
                if i < len(incidents) - 1:
                    time.sleep(1)
                    
            except Exception as e:
                logger.warning(f"Geocoding error: {e}")
                incident['latitude'] = ''
                incident['longitude'] = ''
                geocoded_incidents.append(incident)
        
        logger.info(f"Geocoding complete: {len(geocoded_incidents)} incidents")
        return geocoded_incidents
        
    except ImportError:
        logger.error("geopy library not found. Install with: pip install geopy")
        logger.info("Continuing without geocoding...")
        return incidents
    except Exception as e:
        logger.error(f"Geocoding error: {e}")
        return incidents


def download_latest_data(logger: logging.Logger, exporter_script: str = 'pull_72_hours.py') -> str | None:
    """Download latest data using the exporter."""
    logger.info("Downloading latest 72-hour data...")
    
    try:
        result = subprocess.run([
            sys.executable, exporter_script,
            '--out-dir', 'temp',
            '--prefix', 'gva_72hr',
            '--overwrite',
        ], capture_output=True, text=True, timeout=300)
        
        if result.returncode == 0:
            target = os.path.join('temp', 'gva_72hr.csv')
            if os.path.exists(target):
                logger.info(f"Found downloaded file: {target}")
                return target
            else:
                logger.error("Expected file temp/gva_72hr.csv not found")
                return None
        else:
            logger.error(f"Download failed: {result.stderr}")
            return None
            
    except subprocess.TimeoutExpired:
        logger.error("Download timed out after 5 minutes")
        return None
    except Exception as e:
        logger.error(f"Download error: {e}")
        return None


def read_ids(filepath: str, logger: logging.Logger) -> Set[str]:
    """Read all incident IDs from CSV file."""
    incident_ids = set()
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if 'Incident ID' in row and row['Incident ID']:
                    incident_ids.add(row['Incident ID'].strip())
    except Exception as e:
        logger.error(f"Error reading {filepath}: {e}")
    return incident_ids

def is_in_nyc(temp_csv):
    records_in_nyc = []
    # sometimes things are spelled differently e.g.s Queens vs Corona (Queens)
    boroughs = ["Brooklyn", "Queens", "Staten Island", "Manhattan", "Bronx"]
    with open(temp_csv, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            row_state = row.get("State", "")
            row_county = row.get("City or County", "")  # default to "" to avoid None crash
            contains_borough = any(borough in row_county for borough in boroughs)
            if row_state == "New York" and contains_borough:
                records_in_nyc.append(row)
    return records_in_nyc 



def find_new_incidents(temp_csv: str, master_file: str, logger: logging.Logger) -> List[Dict]:
    """Compare temp download with master file and return new incidents."""
    logger.info("Comparing data with master file...")
    
    if not os.path.exists(master_file):
        logger.error(f"Master file not found: {master_file}")
        return []
    
    existing_ids = read_ids(master_file, logger)
    new_incidents = []
    
    try:
            nyc_rows = is_in_nyc(temp_csv)
            for row in nyc_rows:
                incident_id = row.get('Incident ID', '').strip()
                if incident_id and incident_id not in existing_ids:
                    row.setdefault('latitude', '')
                    row.setdefault('longitude', '')
                    new_incidents.append(row)
    except Exception as e:
        logger.error(f"Error reading download: {e}")
        return []
    
    logger.info(f"Found {len(new_incidents)} new incidents")
    
    if new_incidents:
        new_incidents = geocode_incidents(new_incidents, logger)
    
    return new_incidents


def update_master_file(new_incidents: List[Dict], master_file: str, logger: logging.Logger) -> bool:
    """Add new incidents to the top of the master file."""
    if not new_incidents:
        logger.info("No new incidents to add - master file is up to date")
        return True
    
    logger.info(f"Adding {len(new_incidents)} new incidents to master file...")
    
    try:
        existing_data = []
        header = None
        
        with open(master_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames
            existing_data = list(reader)
        
        if not header:
            logger.error("Could not read header from master file")
            return False
        
        with open(master_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            
            for incident in new_incidents:
                writer.writerow(incident)
            
            for incident in existing_data:
                writer.writerow(incident)
        
        logger.info(f"Updated master file: {len(new_incidents)} new incidents added")
        return True
        
    except Exception as e:
        logger.error(f"Error updating master file: {e}")
        return False


def cleanup_temp_files(logger: logging.Logger):
    """Clean up temporary downloaded files."""
    logger.info("Cleaning up temporary files...")
    
    try:
        if os.path.exists('temp'):
            for filename in os.listdir('temp'):
                if filename.startswith('gva_72hr') and filename.endswith('.csv'):
                    os.remove(os.path.join('temp', filename))
        logger.info("Cleanup completed")
    except Exception as e:
        logger.error(f"Cleanup error: {e}")


def run_automation(logger: logging.Logger, exporter_script: str = 'pull_72_hours.py') -> bool:
    """Run the complete automation process."""
    logger.info("Starting data update process")
    
    master_file = 'data/gva_master_72.csv'
    
    temp_csv = download_latest_data(logger, exporter_script)
    if not temp_csv:
        return False
    
    new_incidents = find_new_incidents(temp_csv, master_file, logger)
    
    if not update_master_file(new_incidents, master_file, logger):
        logger.error("Failed to update master file")
        return False
    
    cleanup_temp_files(logger)
    
    total_incidents = len(read_ids(master_file, logger))
    logger.info(f"Update completed - {len(new_incidents)} new incidents added, {total_incidents} total")
    
    return True


def main():
    logger = start_log()
    try:
        success = run_automation(logger)
        if success:
            logger.info("Update completed")
            sys.exit(0)
        else:
            logger.error("Update failed")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()