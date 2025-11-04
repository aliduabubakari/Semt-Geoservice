from pymongo import MongoClient
import os
import time

def test_connection():
    host = os.environ.get('MONGO_HOST', 'mongo')
    port = int(os.environ.get('MONGO_PORT', 27017))
    user = os.environ.get('MONGO_USER')
    password = os.environ.get('MONGO_PASSWORD')
    
    connection_string = f"mongodb://{user}:{password}@{host}:{port}/?authSource=admin"
    
    print(f"Attempting to connect to MongoDB at {host}:{port}")
    
    try:
        client = MongoClient(connection_string, serverSelectionTimeoutMS=5000)
        client.admin.command('ping')
        print("Successfully connected to MongoDB!")
        return True
    except Exception as e:
        print(f"Failed to connect: {str(e)}")
        return False

if __name__ == "__main__":
    max_retries = 5
    for i in range(max_retries):
        if test_connection():
            break
        print(f"Retry {i+1}/{max_retries}")
        time.sleep(5)