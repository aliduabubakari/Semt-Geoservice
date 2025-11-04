#!/bin/bash

set -e  # Exit on any error

# Function to test MongoDB connection
test_mongodb_connection() {
    mongosh \
        --host mongo \
        --port 27017 \
        --username "$MONGO_USER" \
        --password "$MONGO_PASSWORD" \
        --authenticationDatabase admin \
        --eval "db.adminCommand('ping')" >/dev/null 2>&1
}

# Wait for MongoDB to be ready
echo "Waiting for MongoDB to be ready..."
RETRIES=30
DELAY=2

for i in $(seq 1 $RETRIES); do
    if test_mongodb_connection; then
        echo "MongoDB is ready!"
        break
    fi
    
    if [ $i -eq $RETRIES ]; then
        echo "Error: Could not connect to MongoDB after $RETRIES attempts"
        exit 1
    fi
    
    echo "Attempt $i of $RETRIES. Waiting $DELAY seconds..."
    sleep $DELAY
done

# Check if required collections exist, create them if they don't
echo "Checking required collections..."
python3 << END
from pymongo import MongoClient
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    client = MongoClient(
        f"mongodb://{os.environ['MONGO_USER']}:{os.environ['MONGO_PASSWORD']}@"
        f"{os.environ['MONGO_HOST']}:27017/?authSource=admin"
    )
    db = client[os.environ['MONGO_DBNAME']]
    
    # Ensure required collections exist
    collections = db.list_collection_names()
    required_collections = ['address', 'route', 'poi']
    
    for collection in required_collections:
        if collection not in collections:
            db.create_collection(collection)
            logger.info(f"Created collection: {collection}")
        else:
            logger.info(f"Collection exists: {collection}")
    
    logger.info("Database is ready for use")
        
except Exception as e:
    logger.error(f"Error checking database: {str(e)}")
    exit(1)
END

# Start the Flask application
echo "Starting Flask application..."
exec python app.py