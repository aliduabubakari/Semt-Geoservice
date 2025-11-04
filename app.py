import os
from flask import Flask, request, jsonify
from flask_restx import Api, Resource, reqparse, fields
from flask_cors import CORS
from pymongo import MongoClient
import json
import requests
import folium
import flexpolyline as fp
import pandas as pd
import logging
import time

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_env_variable(var_name):
    try:
        return os.environ[var_name]
    except KeyError:
        error_msg = f"Error: {var_name} environment variable not set."
        logger.error(error_msg)
        raise Exception(error_msg)

API_TOKEN = get_env_variable("API_TOKEN")
GEOAPIFY_API_KEY = get_env_variable("GEOAPIFY_API_KEY")

MONGO_HOST = os.environ.get("MONGO_HOST", "intertwino_mongo")
MONGO_PORT = int(os.environ.get("MONGO_PORT", 27017))
MONGO_USER = os.environ["MONGO_USER"]
MONGO_PASSWORD = os.environ["MONGO_PASSWORD"]
MONGO_DBNAME = os.environ["MONGO_DBNAME"]

client = MongoClient(f"mongodb://{MONGO_HOST}:{MONGO_PORT}/",
                     username=MONGO_USER,
                     password=MONGO_PASSWORD,
                     authSource='admin',
                     authMechanism='SCRAM-SHA-256', 
                     maxPoolSize=100)
address_cache = client[MONGO_DBNAME].address            
route_cache = client[MONGO_DBNAME].route                  
poi_cache = client[MONGO_DBNAME].poi 

current_dir = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(current_dir, "municipalities.geojson.json")) as f:
    geodata = json.loads(f.read())
temp = geodata["features"]
sofia_geoshape = [t for t in temp if "SOF" in t["properties"]["nuts4"]]

app = Flask(__name__)
CORS(app)  # Enable CORS for all domains on all routes
api = Api(app, prefix='/api')

# Global variables for tracking cache hits, misses, and API calls
cache_hits = 0
cache_misses = 0
api_call_count = 0

# Determine whether to use caching based on an environment variable
USE_CACHE = os.getenv('USE_CACHE', 'true').lower() == 'true'

def validate_token(token):
    return token == API_TOKEN

def get_address_data(address):
    global cache_hits, cache_misses
    address = address.lower()
    start_time = time.time()
    result = address_cache.find_one({"address": address}) if USE_CACHE else None
    end_time = time.time()
    if result:
        cache_hits += 1
        logger.info(f"Cache hit for address: {address}")
    else:
        cache_misses += 1
        logger.info(f"Cache miss for address: {address}")
    logger.info(f"Address lookup time: {end_time - start_time:.4f} seconds")
    return result

def get_route_data(origin, destination):
    result = route_cache.find_one({"origin": origin, "destination": destination}) if USE_CACHE else None
    return result

def get_poi(name):
    name = name.lower().replace('"', '')
    name = name.replace('"', '')
    name = " ".join(name.split())
    result = poi_cache.find_one({"name": name}) if USE_CACHE else None
    return result

def transform_geoapify_to_here_format(geoapify_response):
    """
    Transform Geoapify response to match the HERE API response format
    """
    items = []
    for feature in geoapify_response.get("features", []):
        properties = feature.get("properties", {})
        geometry = feature.get("geometry", {})
        
        # Transform to HERE-like format
        item = {
            "title": properties.get("formatted"),
            "id": properties.get("place_id"),
            "resultType": properties.get("result_type", "unknown"),
            "address": {
                "label": properties.get("formatted"),
                "countryCode": properties.get("country_code"),
                "countryName": properties.get("country"),
                "state": properties.get("state"),
                "county": properties.get("county"),
                "city": properties.get("city"),
                "district": properties.get("district"),
                "street": properties.get("street"),
                "postalCode": properties.get("postcode"),
                "houseNumber": properties.get("housenumber")
            },
            "position": {
                "lat": geometry.get("coordinates", [])[1] if geometry.get("coordinates") else properties.get("lat"),
                "lng": geometry.get("coordinates", [])[0] if geometry.get("coordinates") else properties.get("lon")
            },
            "access": [
                {
                    "lat": geometry.get("coordinates", [])[1] if geometry.get("coordinates") else properties.get("lat"),
                    "lng": geometry.get("coordinates", [])[0] if geometry.get("coordinates") else properties.get("lon")
                }
            ] if geometry.get("coordinates") else [],
            "mapView": {
                "west": properties.get("bbox", {}).get("lon1") or (properties.get("lon") - 0.01),
                "south": properties.get("bbox", {}).get("lat1") or (properties.get("lat") - 0.01),
                "east": properties.get("bbox", {}).get("lon2") or (properties.get("lon") + 0.01),
                "north": properties.get("bbox", {}).get("lat2") or (properties.get("lat") + 0.01)
            }
        }
        items.append(item)
    
    return {"items": items}

address_fields = api.model('Address', {
    'address': fields.String,
})

address_list_fields = api.model('AddressList', {
    'json': fields.List(
        fields.Nested(address_fields), 
        example=[
            {"address": "гр. София, УЛ.ВЛАДИМИР МИНКОВ-ЛОТКОВ бл./№ 023"},
            {"address": "гр. София, Ж.К.ХИПОДРУМА бл./№ 038"}
        ])
})

@app.route('/health')
def health_check():
    return jsonify({"status": "healthy"}), 200

@api.route('/reconciliators/geocodingHere')
@api.doc(
    responses={200: "OK", 404: "Not found",
               400: "Bad request", 403: "Invalid token"},
    params={ "token": "token api key"}
)
class GeolocateAddress(Resource):

    def lookup_address(self, address):
        global api_call_count
        api_call_count += 1
        logger.info(f"API call count: {api_call_count}")
        
        # Use Geoapify Geocoding API
        url = "https://api.geoapify.com/v1/geocode/search"
        params = {
            "text": address,
            "format": "json",
            "apiKey": GEOAPIFY_API_KEY,
            "lang": "bg",  # Bulgarian language
            "limit": 10
        }
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            results = response.json()
            
            # Transform Geoapify response to HERE-like format
            transformed_results = transform_geoapify_to_here_format(results)
            
            if USE_CACHE:
                address_cache.insert_one({
                    "address": address.lower(),
                    "items": transformed_results["items"]
                })
            return transformed_results
        except requests.exceptions.RequestException as e:
            logger.error(f"Geoapify API error: {str(e)}")
            raise Exception(f"Geocoding service error: {str(e)}")
    
    def init_geo_obj_debug(self):
        geo_obj_debug = {}
        geo_obj_debug["type"] = "FeatureCollection"
        geo_obj_debug["features"] = []
        geo_obj_debug["features"].append(sofia_geoshape[0])
        return geo_obj_debug

    def populate_debug(self, results, geo_obj_debug):
        for result in results["items"]:
            (lat, lng) = (result["position"]["lat"], result["position"]["lng"])
            geo_obj_debug["features"].append({
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Point",
                    "coordinates": [
                        lng,
                        lat
                    ]
                }
            })
        
    @api.doc(
        params={
            "address": "Name to look for lookup an address"
        },
        description="""With this API endpoint you can search for address by entering a query string corresponding to the address 
                    (for e.g. гр. София, УЛ.ВЛАДИМИР МИНКОВ-ЛОТКОВ бл./№ 023.)
                    """
    )
    def get(self):
        parser = reqparse.RequestParser()
        parser.add_argument('address', type=str, help='variable 1', location='args')
        parser.add_argument('token', type=str, help='variable 2', location='args')
        args = parser.parse_args()
        name = args["address"]
        token = args["token"]
        if not validate_token(token):
            return {"Error": "Invalid Token"}, 403
        result = get_address_data(name)
        if result is None:  
            try:  
                result = self.lookup_address(name)
            except Exception as e:
                return {"Error": str(e)}, 400
        out = {}    
        out["items"] = result["items"]    
        geo_obj_debug = self.init_geo_obj_debug()
        self.populate_debug(out, geo_obj_debug)
        out["debug"] = geo_obj_debug
        return out    

    @api.doc(
        body = address_list_fields,
        description="""With this API endpoint you can search for addresses by entering a json array of object 
                    that contains strings corresponding  the address  
                    (for e.g. [{"address":"гр. София, УЛ.ВЛАДИМИР МИНКОВ-ЛОТКОВ бл./№ 023.", {"address":"..."}, ...])
                    """
    )
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('token', type=str, help='API token', location='args')
        args = parser.parse_args()
        token = args["token"]
        if not validate_token(token):
            return {"Error": "Invalid Token"}, 403
        try:
            data = request.get_json()
            if 'json' not in data:
                return {"Error": "Missing 'json' key in request body"}, 400
            addresses = data['json']
        except Exception as e:
            logger.error(f"Error parsing JSON: {str(e)}")
            return {"Error": "Invalid JSON"}, 400
        
        geo_obj_debug = self.init_geo_obj_debug()      
        for address in addresses:
            try:
                result = get_address_data(address["address"])
                if result is None:
                    result = self.lookup_address(address["address"])
                address["items"] = result["items"]
                self.populate_debug(result, geo_obj_debug)
            except Exception as e:   
                logger.error(f"Error processing address {address}: {str(e)}")
                return {"Error": f"Error processing address: {str(e)}"}, 400 
        out = {
            "result": addresses,
            "debug": geo_obj_debug
        }    
        return out

routes_fields = api.model('Route', {
    'origin': fields.List(fields.Float),
    'destination': fields.List(fields.Float)
})

routes_list_fields = api.model('RouteList', {
    'json': fields.List(
        fields.Nested(routes_fields), 
        example=[
            {"origin": [42.68843, 23.37989], "destination": [42.70211, 23.33198]},
            {"origin": [42.68840, 23.37990], "destination": [42.70212, 23.33190]},
            {"origin": [42.68840, 23.37990], "destination": 'дг №7 "детелина"'}
        ])
})

@api.route('/route')
@api.doc(
    responses={200: "OK", 404: "Not found",
               400: "Bad request", 403: "Invalid token"},
    params={
        "token": "token api key"
    }
)

class Routing(Resource):

    def get_route(self, origin, destination):
        # Use Geoapify Routing API
        url = "https://api.geoapify.com/v1/routing"
        params = {
            "waypoints": f"{origin}|{destination}",
            "mode": "walk",
            "apiKey": GEOAPIFY_API_KEY,
            "details": "instruction_details"
        }
        
        try:
            response = requests.get(url, params=params)
            response.raise_for_status()
            result = response.json()
            
            # Transform Geoapify routing response to match expected format
            transformed_result = self.transform_routing_response(result)
            
            if USE_CACHE:
                route_cache.insert_one({
                    "origin": origin,
                    "destination": destination,
                    "routes": transformed_result["routes"]
                })
            
            return transformed_result
        except requests.exceptions.RequestException as e:
            logger.error(f"Geoapify Routing API error: {str(e)}")
            raise Exception(f"Routing service error: {str(e)}")
    
    def transform_routing_response(self, geoapify_response):
        """
        Transform Geoapify routing response to match HERE routing response format
        """
        routes = []
        
        for feature in geoapify_response.get("features", []):
            properties = feature.get("properties", {})
            
            # Extract summary information
            summary = {
                "duration": properties.get("time", 0),
                "length": properties.get("distance", 0)
            }
            
            # Extract polyline (Geoapify uses encoded polyline)
            polyline = properties.get("legs", [{}])[0].get("points") if properties.get("legs") else ""
            
            route = {
                "sections": [
                    {
                        "id": "section-0",
                        "type": "pedestrian",
                        "actions": [],
                        "arrival": {"time": ""},
                        "departure": {"time": ""},
                        "summary": summary,
                        "polyline": polyline
                    }
                ]
            }
            routes.append(route)
        
        return {"routes": routes}

    @api.doc(
        params={
            "pointA": "Geocoords of point A (use order lat,lng separated by comma for e.g. 42.69357,23.36488)",
            "pointB": """Geocoords of point B or Point of Interest 
            (use order lat,lng separated by comma for e.g. 42.70214,23.37594 or ДГ №20 "Жасминов парк")"""
        },
        description='Compute path from point A to point B in pedestrian mode'
    )
    def get(self):
        parser = reqparse.RequestParser()
        parser.add_argument('pointA', type=str, help='variable 1', location='args')
        parser.add_argument('pointB', type=str, help='variable 2', location='args')
        parser.add_argument('token', type=str, help='variable 3', location='args')
        args = parser.parse_args()
        pointA = args["pointA"]
        pointB = args["pointB"]
        try:
            [float(i) for i in pointB.split(",")]
        except:
            school = get_poi(pointB)
            if school is None:
                return {"Error": "Invalid Point of Interest name"}, 400   
            else:
                pointB = school["coords"]    
        token = args["token"]
        if not validate_token(token):
            return {"Error": "Invalid Token"}, 403
        result = get_route_data(pointA, pointB) 
        if result is None:   
            try:
                result = self.get_route(pointA, pointB)
            except Exception as e:   
                return {"Error": str(e)}, 400 
        out = {}
        out["routes"] = result["routes"]    
        return out

    @api.doc(
        body=routes_list_fields,
        description='Compute path for each object in the list the route from source to destination in pedestrian mode'
    )
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('token', type=str, help='API token', location='args')
        args = parser.parse_args()
        token = args["token"]
        if not validate_token(token):
            return {"Error": "Invalid Token"}, 403
        try:
            data = request.get_json()
            if 'json' not in data:
                return {"Error": "Missing 'json' key in request body"}, 400
            routes = data['json']
        except Exception as e:
            logger.error(f"Error parsing JSON: {str(e)}")
            return {"Error": "Invalid JSON"}, 400

        for route in routes:
            try:
                if isinstance(route["destination"], str):
                    school = get_poi(route["destination"])
                    if school is None:
                        return {"Error": f"Invalid Point of Interest name: {route['destination']}"}, 400   
                    route["destination"] = school["coords"].split(",")
                origin = ",".join(str(p) for p in route["origin"])
                destination = ",".join(str(p) for p in route["destination"])
                result = get_route_data(origin, destination)
                if result is None:
                    result = self.get_route(origin, destination)
                route["routes"] = result["routes"]
            except KeyError as e:
                logger.error(f"KeyError in route data: {str(e)}")
                return {"Error": f"Missing key in route data: {str(e)}"}, 400
            except Exception as e:   
                logger.error(f"Error processing route {route}: {str(e)}")
                return {"Error": f"Error processing route: {str(e)}"}, 400 
       
        return routes

@app.route('/map')
def map():
    polyline = request.args.get('polyline')
    try:
        coordinates = fp.decode(polyline)
        coordinates = [[c[1], c[0]] for c in coordinates]
        mls = coordinates
        points = [(i[1], i[0]) for i in mls]
        m = folium.Map()
        
        # add marker for the start and ending points
        folium.Marker(points[0], icon=folium.Icon(color="red",icon="map-pin", prefix='fa')).add_to(m) # start point
        folium.Marker(points[-1], icon=folium.Icon(color="blue",icon="map-marker", prefix='fa')).add_to(m) # end point
        
        # add the lines
        folium.PolyLine(points, weight=5, opacity=1).add_to(m)
        # create optimal zoom
        df = pd.DataFrame(mls).rename(columns={0:'Lon', 1:'Lat'})[['Lat', 'Lon']]
        sw = df[['Lat', 'Lon']].min().values.tolist()
        ne = df[['Lat', 'Lon']].max().values.tolist()
        m.fit_bounds([sw, ne])
    except Exception as e:
        return {"Error": "Invalid Polyline"}
    return m._repr_html_()

@app.route('/metrics')
def metrics():
    cache_hit_rate = cache_hits / (cache_hits + cache_misses) if (cache_hits + cache_misses) > 0 else 0
    return jsonify({
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "cache_hit_rate": cache_hit_rate,
        "api_call_count": api_call_count
    }), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)