// init-mongo.js
db = db.getSiblingDB('intertwino');

// Create collections with indexes
db.createCollection('geonames_cities');
db.geonames_cities.createIndex({ "name": "text" });
db.geonames_cities.createIndex({ "location": "2dsphere" });

db.createCollection('geonames_countries');
db.geonames_countries.createIndex({ "name": "text" });