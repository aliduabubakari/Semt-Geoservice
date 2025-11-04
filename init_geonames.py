# init_geonames.py
import logging
from geonames_loader import GeoNamesLoader

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def initialize_geonames():
    loader = GeoNamesLoader()
    try:
        logger.info("Starting GeoNames initialization...")
        
        # Connect to MongoDB
        logger.info("Connecting to MongoDB...")
        loader.connect_to_mongodb()
        
        # Download data
        logger.info("Downloading GeoNames data...")
        loader.download_and_extract_data()
        
        # Process and load data
        logger.info("Processing and loading data...")
        loader.process_and_load_data()
        
        # Verify data
        logger.info("Verifying data...")
        stats = loader.verify_data()
        
        logger.info(f"GeoNames initialization completed successfully")
        logger.info(f"Statistics: {stats}")
        
        return True
    except Exception as e:
        logger.error(f"Error during GeoNames initialization: {str(e)}")
        raise
    finally:
        loader.cleanup()

if __name__ == "__main__":
    initialize_geonames()