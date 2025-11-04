# InterTwino GeoNames API Setup Guide

## Overview
This API provides geocoding and location search functionality using GeoNames data. It includes endpoints for searching cities and countries, with support for text search and geospatial queries.

## Prerequisites
- Docker and Docker Compose
- Git
- Basic understanding of terminal/command line operations

## Project Structure
```
InterTwino-project/
├── app.py                 # Main Flask application
├── geonames_loader.py     # GeoNames data loader
├── init_geonames.py       # Database initialization script
├── requirements.txt       # Python dependencies
├── Dockerfile            # Docker configuration
├── docker-compose.yml    # Docker Compose configuration
├── entrypoint.sh         # Container entrypoint script
└── .env                  # Environment variables
```

## Installation Steps

1. **Clone the Repository**
   ```bash
   git clone <repository-url>
   cd InterTwino-project
   ```

2. **Create Environment File**
   Create a `.env` file in the project root with the following content:
   ```env
   PYTHON_VERSION=3.9.12
   MONGO_VERSION=5.0.8
   JUPYTER_VERSION=python-3.10.4
   MONGO_HOST=mongo
   MONGO_USER=admin
   MONGO_PASSWORD=InterTwino2023!
   MONGO_DBNAME=intertwino
   API_TOKEN=intertwino-gate-2023
   HERE_API_KEY=your_here_api_key
   API_PORT=5005
   MONGO_PORT=27029
   MY_JUPYTER_PORT=8821
   JUPYTER_TOKEN=intertwino2023
   ```

3. **Create Required Files**

   a. **requirements.txt**:
   ```
   flask
   flask-restx
   flask-cors
   pymongo
   pandas
   requests
   python-dotenv
   herepy
   folium
   flexpolyline
   ```

   b. **Dockerfile**:
   ```dockerfile
   ARG PYTHON_VERSION
   FROM python:$PYTHON_VERSION-slim-buster

   # Install MongoDB client tools
   RUN apt-get update && apt-get install -y wget gnupg && \
       wget -qO - https://www.mongodb.org/static/pgp/server-5.0.asc | apt-key add - && \
       echo "deb http://repo.mongodb.org/apt/debian buster/mongodb-org/5.0 main" | tee /etc/apt/sources.list.d/mongodb-org-5.0.list && \
       apt-get update && \
       apt-get install -y mongodb-mongosh && \
       rm -rf /var/lib/apt/lists/*

   WORKDIR /code
   ENV FLASK_APP=app.py
   ENV FLASK_RUN_HOST=0.0.0.0

   COPY requirements.txt requirements.txt
   RUN pip install -r requirements.txt

   COPY . .
   RUN chmod +x entrypoint.sh

   EXPOSE 5000

   ENTRYPOINT ["./entrypoint.sh"]
   ```

   c. **docker-compose.yml**:
   ```yaml
   version: "3.9"
   services:
     web:
       build:
         context: .
         args: 
           PYTHON_VERSION: ${PYTHON_VERSION}
       container_name: "intertwino_api"
       ports:
         - "${API_PORT}:5000"
       restart: no
       environment:
         FLASK_DEBUG: 1
         MONGO_HOST: mongo
         MONGO_PORT: 27017
       env_file:
         - ./.env  
       volumes:
         - .:/code
       depends_on:
         - mongo
       networks:
         - app-network
         
     mongo:
       image: "mongo:${MONGO_VERSION}"
       container_name: "intertwino_mongo"
       ports:
         - "${MONGO_PORT}:27017"
       restart: no
       environment:
         MONGO_INITDB_ROOT_USERNAME: ${MONGO_USER}
         MONGO_INITDB_ROOT_PASSWORD: ${MONGO_PASSWORD}
       volumes:
         - ./mongo-data:/data/db
         - ./mongo-init:/docker-entrypoint-initdb.d
       networks:
         - app-network

   networks:
     app-network:
       driver: bridge
   ```

   d. **entrypoint.sh**:
   ```bash
   #!/bin/sh

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

   # Initialize the database
   echo "Starting database initialization..."
   python init_geonames.py

   # Start the Flask application
   echo "Starting Flask application..."
   python app.py
   ```

4. **Build and Start the Services**
   ```bash
   # Create necessary directories
   mkdir -p mongo-data mongo-init

   # Set execute permission for entrypoint script
   chmod +x entrypoint.sh

   # Build and start containers
   docker-compose up --build
   ```

5. **Initialize GeoNames Data**
   The data initialization should happen automatically during startup. If it doesn't:
   ```bash
   docker-compose exec web python init_geonames.py
   ```

6. **Verify Installation**
   ```bash
   # Check if the API is running
   curl http://localhost:5005/health

   # Verify GeoNames data
   curl http://localhost:5005/api/geonames/verify

   # Test search functionality
   curl "http://localhost:5005/api/geonames/search?q=London&type=cities&limit=10&token=intertwino-gate-2023"
   ```

## API Endpoints

1. **Health Check**
   ```
   GET /health
   ```

2. **GeoNames Search**
   ```
   GET /api/geonames/search
   Parameters:
   - q: Search query string
   - type: Type of search (cities/countries/all)
   - limit: Maximum number of results to return
   - token: API token
   ```

3. **GeoNames Verification**
   ```
   GET /api/geonames/verify
   ```

## Troubleshooting

1. **MongoDB Connection Issues**
   - Check if MongoDB container is running:
     ```bash
     docker-compose ps
     ```
   - Check MongoDB logs:
     ```bash
     docker-compose logs mongo
     ```

2. **Data Loading Issues**
   - Check the web service logs:
     ```bash
     docker-compose logs web
     ```
   - Manually trigger data loading:
     ```bash
     docker-compose exec web python init_geonames.py
     ```

3. **Permission Issues**
   - Ensure proper permissions for mounted volumes:
     ```bash
     sudo chown -R $USER:$USER mongo-data
     ```

## Maintenance

1. **Stopping the Services**
   ```bash
   docker-compose down
   ```

2. **Cleaning Up**
   ```bash
   docker-compose down -v  # Removes volumes
   rm -rf mongo-data/*    # Clears MongoDB data
   ```

3. **Updating GeoNames Data**
   ```bash
   docker-compose exec web python init_geonames.py
   ```

## Security Notes
- Change default passwords in production
- Secure the API token
- Configure proper firewall rules
- Use HTTPS in production

## Additional Resources
- [GeoNames Documentation](http://www.geonames.org/export/)
- [MongoDB Documentation](https://docs.mongodb.com/)
- [Flask Documentation](https://flask.palletsprojects.com/)

