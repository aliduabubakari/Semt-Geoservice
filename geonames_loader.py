# geonames_loader.py
import pandas as pd
import requests
import os
from pymongo import MongoClient, GEOSPHERE
import logging
import zipfile
import io
import time

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class GeoNamesLoader:
    def __init__(self):
        self.mongo_host = os.environ.get('MONGO_HOST', 'mongo')
        self.mongo_port = int(os.environ.get('MONGO_PORT', 27017))
        self.mongo_user = os.environ.get('MONGO_USER')
        self.mongo_password = os.environ.get('MONGO_PASSWORD')
        self.mongo_dbname = os.environ.get('MONGO_DBNAME', 'intertwino')
        self.client = None
        self.db = None
        self.data_dir = '/tmp/geonames_data'

    def connect_to_mongodb(self):
        """Establish connection to MongoDB with retry logic"""
        max_retries = 5
        retry_delay = 5

        for attempt in range(max_retries):
            try:
                connection_string = (
                    f"mongodb://{self.mongo_user}:{self.mongo_password}@"
                    f"{self.mongo_host}:{self.mongo_port}/?authSource=admin"
                )
                self.client = MongoClient(connection_string, serverSelectionTimeoutMS=5000)
                self.client.admin.command('ping')
                self.db = self.client[self.mongo_dbname]
                logger.info("Successfully connected to MongoDB")
                return True
            except Exception as e:
                logger.warning(f"MongoDB connection attempt {attempt + 1}/{max_retries} failed: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    raise

    def download_and_extract_data(self):
        """Download and extract GeoNames data"""
        try:
            # Create data directory
            os.makedirs(self.data_dir, exist_ok=True)
            
            # Download cities
            logger.info("Downloading cities data...")
            cities_url = "https://download.geonames.org/export/dump/cities1000.zip"
            cities_response = requests.get(cities_url)
            cities_response.raise_for_status()
            
            cities_zip_path = os.path.join(self.data_dir, 'cities1000.zip')
            with open(cities_zip_path, 'wb') as f:
                f.write(cities_response.content)
            
            # Extract cities data
            with zipfile.ZipFile(cities_zip_path, 'r') as zip_ref:
                zip_ref.extractall(self.data_dir)
            logger.info("Cities data downloaded and extracted")
            
            # Download countries
            logger.info("Downloading countries data...")
            countries_url = "https://download.geonames.org/export/dump/countryInfo.txt"
            countries_response = requests.get(countries_url)
            countries_response.raise_for_status()
            
            with open(os.path.join(self.data_dir, 'countryInfo.txt'), 'wb') as f:
                f.write(countries_response.content)
            logger.info("Countries data downloaded")
            
            return True
        except Exception as e:
            logger.error(f"Error downloading data: {str(e)}")
            raise

    def process_and_load_data(self):
        """Process and load the data into MongoDB"""
        try:
            # Process cities first
            logger.info("Processing cities data...")
            cities_columns = [
                'geonameid', 'name', 'asciiname', 'alternatenames',
                'latitude', 'longitude', 'feature_class', 'feature_code',
                'country_code', 'cc2', 'admin1_code', 'admin2_code',
                'admin3_code', 'admin4_code', 'population', 'elevation',
                'dem', 'timezone', 'modification_date'
            ]
            
            cities_df = pd.read_csv(
                os.path.join(self.data_dir, 'cities1000.txt'),
                sep='\t',
                names=cities_columns,
                encoding='utf-8',
                low_memory=False
            )
            
            # Create GeoJSON location field
            cities_df['location'] = cities_df.apply(
                lambda row: {
                    'type': 'Point',
                    'coordinates': [float(row['longitude']), float(row['latitude'])]
                },
                axis=1
            )
            
            # Process countries - with manual parsing to handle inconsistent columns
            logger.info("Processing countries data...")
            countries_data = []
            with open(os.path.join(self.data_dir, 'countryInfo.txt'), 'r', encoding='utf-8') as f:
                # Skip comment lines
                lines = [line for line in f if not line.startswith('#')]
                
                # Process each line manually
                for line in lines:
                    try:
                        # Split the line by tabs and take only the fields we need
                        fields = line.strip().split('\t')
                        if len(fields) >= 19:  # Ensure we have enough fields
                            country_data = {
                                'ISO': fields[0],
                                'ISO3': fields[1],
                                'ISO_Numeric': fields[2],
                                'fips': fields[3],
                                'Country': fields[4],
                                'Capital': fields[5],
                                'Area': fields[6],
                                'Population': fields[7],
                                'Continent': fields[8],
                                'tld': fields[9],
                                'CurrencyCode': fields[10],
                                'CurrencyName': fields[11],
                                'Phone': fields[12],
                                'Languages': fields[15]
                            }
                            countries_data.append(country_data)
                    except Exception as e:
                        logger.warning(f"Error processing country line: {str(e)}")
                        continue
            
            # Convert countries data to DataFrame
            countries_df = pd.DataFrame(countries_data)
            
            # Create collections and indexes
            logger.info("Creating collections and indexes...")
            self.db.geonames_cities.drop()
            self.db.geonames_countries.drop()
            
            # Insert cities data
            logger.info("Inserting cities data...")
            cities_records = cities_df.to_dict('records')
            if cities_records:
                self.db.geonames_cities.insert_many(cities_records)
                self.db.geonames_cities.create_index([("name", "text")])
                self.db.geonames_cities.create_index([("location", GEOSPHERE)])
                logger.info(f"Inserted {len(cities_records)} cities")
            
            # Insert countries data
            logger.info("Inserting countries data...")
            countries_records = countries_df.to_dict('records')
            if countries_records:
                self.db.geonames_countries.insert_many(countries_records)
                self.db.geonames_countries.create_index([("Country", "text")])
                logger.info(f"Inserted {len(countries_records)} countries")
            
            return True
            
        except Exception as e:
            logger.error(f"Error processing and loading data: {str(e)}")
            raise

    def verify_data(self):
        """Verify that data was loaded correctly"""
        try:
            cities_count = self.db.geonames_cities.count_documents({})
            countries_count = self.db.geonames_countries.count_documents({})
            
            cities_indexes = list(self.db.geonames_cities.list_indexes())
            countries_indexes = list(self.db.geonames_countries.list_indexes())
            
            logger.info(f"Data verification results:")
            logger.info(f"Cities count: {cities_count}")
            logger.info(f"Countries count: {countries_count}")
            logger.info(f"Cities indexes: {[idx['name'] for idx in cities_indexes]}")
            logger.info(f"Countries indexes: {[idx['name'] for idx in countries_indexes]}")
            
            return {
                "cities_count": cities_count,
                "countries_count": countries_count,
                "cities_indexes": [idx['name'] for idx in cities_indexes],
                "countries_indexes": [idx['name'] for idx in countries_indexes]
            }
            
        except Exception as e:
            logger.error(f"Error verifying data: {str(e)}")
            raise

    def cleanup(self):
        """Clean up downloaded files"""
        try:
            import shutil
            if os.path.exists(self.data_dir):
                shutil.rmtree(self.data_dir)
            logger.info("Cleanup completed")
        except Exception as e:
            logger.warning(f"Cleanup error: {str(e)}")

def main():
    loader = GeoNamesLoader()
    try:
        logger.info("Starting GeoNames data loading process...")
        loader.connect_to_mongodb()
        loader.download_and_extract_data()
        loader.process_and_load_data()
        stats = loader.verify_data()
        logger.info(f"GeoNames data loading completed successfully")
        logger.info(f"Final statistics: {stats}")
    except Exception as e:
        logger.error(f"Error in main execution: {str(e)}")
        raise
    finally:
        loader.cleanup()

if __name__ == "__main__":
    main()