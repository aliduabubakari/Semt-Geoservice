from flask import Flask, request, jsonify
from flask_restx import Api, Resource, reqparse, fields
import requests
from pyproj import Transformer
from enum import Enum
import os
from functools import wraps
from herepy import GeocoderApi
from pymongo import MongoClient
import folium
import flexpolyline as fp
import pandas as pd
import json
from typing import Dict, List, Optional, Tuple, Union
from datetime import datetime
import logging
from herepy import GeocoderApi

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Config:
    API_TOKEN = os.environ["API_TOKEN"]
    HERE_API_KEY = os.environ["HERE_API_KEY"]
    MONGO_HOST = os.environ["MONGO_HOST"]
    MONGO_USER = os.environ["MONGO_USER"]
    MONGO_PASSWORD = os.environ["MONGO_PASSWORD"]
    MONGO_DBNAME = os.environ["MONGO_DBNAME"]

class TransportMode(Enum):
    PEDESTRIAN = "pedestrian"
    CAR = "car"
    TRUCK = "truck"
    BICYCLE = "bicycle"
    SCOOTER = "scooter"
    TAXI = "taxi"
    BUS = "bus"
    PUBLIC_TRANSIT = "publicTransport"

class MongoDB:
    def __init__(self):
        self.client = MongoClient(
            host=Config.MONGO_HOST,
            username=Config.MONGO_USER,
            password=Config.MONGO_PASSWORD,
            authSource='admin',
            authMechanism='SCRAM-SHA-256'
        )
        self.db = self.client[Config.MONGO_DBNAME]
        self.address_cache = self.db.address
        self.route_cache = self.db.route
        self.poi_cache = self.db.poi

class Utils:
    @staticmethod
    def load_geojson(filename: str) -> Dict:
        with open(filename) as f:
            return json.loads(f.read())

    @staticmethod
    def transform_coordinates(x: float, y: float) -> Tuple[float, float]:
        transformer = Transformer.from_crs("EPSG:7800", "EPSG:4326", always_xy=True)
        lng, lat = transformer.transform(x, y)
        return lat, lng

    @staticmethod
    def get_coordinates_from_name(name: str) -> Optional[Tuple[float, float]]:
        try:
            geocode_url = "https://geocode.search.hereapi.com/v1/geocode"
            params = {
                "q": name,
                "apiKey": Config.HERE_API_KEY,
                "limit": 1
            }
            
            response = requests.get(geocode_url, params=params)
            if response.status_code == 200:
                items = response.json().get('items', [])
                if items:
                    position = items[0]['position']
                    return position['lat'], position['lng']
            return None
        except Exception as e:
            logger.error(f"Error getting coordinates for {name}: {str(e)}")
            return None

def token_required(f):
    @wraps(f)
    @api.doc(security='apikey')  # This shows the token field in Swagger UI
    def decorated(*args, **kwargs):
        parser = reqparse.RequestParser()
        parser.add_argument('token', type=str, required=True, location='args')
        token_args = parser.parse_args()
        
        if token_args["token"] != Config.API_TOKEN:
            return {"error": "Invalid Token"}, 403
        return f(*args, **kwargs)
    return decorated

# At the top of your file, after creating the Api instance:
authorizations = {
    'apikey': {
        'type': 'apiKey',
        'in': 'query',
        'name': 'token'
    }
}

# Flask app and API setup
app = Flask(__name__)
api = Api(
    app,
    authorizations=authorizations,
    security='apikey',  # This makes token required for all endpoints
    title='SEMTUI GeoService',
    description='Your API Description'
)

# API Models
class ApiModels:
    def __init__(self, api):
        self.address_fields = api.model('Address', {
            'address': fields.String,
        })
        
        self.address_list_fields = api.model('AddressList', {
            'json': fields.List(fields.Nested(self.address_fields))
        })

        self.coordinates_model = api.model('Coordinates', {
            'lat': fields.Float(required=True),
            'lng': fields.Float(required=True)
        })

        self.route_request_model = api.model('RouteRequest', {
            'origin': fields.List(fields.Float, required=True),
            'destination': fields.Raw(required=True),
            'modes': fields.List(fields.String, required=False)
        })

        self.routes_list_fields = api.model('RoutesList', {
            'json': fields.List(fields.Nested(self.route_request_model))
        })

@api.doc(security='apikey')
class GeolocateAddress(Resource):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.geocoder_api = GeocoderApi(api_key=Config.HERE_API_KEY)
        self.db = MongoDB()
        self.sofia_geoshape = [t for t in Utils.load_geojson("municipalities.geojson.json")["features"] 
                              if "SOF" in t["properties"]["nuts4"]]

    def lookup_address(self, address: str) -> Dict:
        try:
            response = self.geocoder_api.free_form(address)
            results = response.as_dict()
            self.db.address_cache.insert_one({
                "address": address.lower(),
                "items": results["items"]
            })
            return results
        except Exception as e:
            raise Exception(f"Error looking up address: {str(e)}")

    def init_geo_obj_debug(self) -> Dict:
        return {
            "type": "FeatureCollection",
            "features": [self.sofia_geoshape[0]]
        }

    def populate_debug(self, results: Dict, geo_obj_debug: Dict) -> None:
        for result in results["items"]:
            lat, lng = result["position"]["lat"], result["position"]["lng"]
            geo_obj_debug["features"].append({
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Point",
                    "coordinates": [lng, lat]
                }
            })

    @api.doc(params={
        "address": "Address to look up",
        "token": "API token for authentication"
    })
    def get(self):
        parser = reqparse.RequestParser()
        parser.add_argument('address', type=str, required=True, location='args')
        parser.add_argument('token', type=str, required=True, location='args')
        args = parser.parse_args()

        if args["token"] != Config.API_TOKEN:
            return {"error": "Invalid Token"}, 403

        try:
            result = self.db.address_cache.find_one({"address": args["address"].lower()})
            if result is None:
                result = self.lookup_address(args["address"])

            out = {"items": result["items"]}
            geo_obj_debug = self.init_geo_obj_debug()
            self.populate_debug(out, geo_obj_debug)
            out["debug"] = geo_obj_debug
            return out

        except Exception as e:
            logger.error(f"Error in GeolocateAddress: {str(e)}")
            return {"error": str(e)}, 400

    @api.expect(ApiModels(api).address_list_fields)
    @api.doc(
        body=ApiModels(api).address_list_fields,
        description="""With this API endpoint you can search for addresses by entering a JSON array of objects 
                    that contains strings corresponding to the address  
                    (e.g., [{"address":"гр. София, УЛ.ВЛАДИМИР МИНКОВ-ЛОТКОВ бл./№ 023"}, {"address":"..."}])
                    """
    )
    def post(self):
        parser = reqparse.RequestParser()
        parser.add_argument('token', type=str, required=True, location='args')
        args = parser.parse_args()

        if args["token"] != Config.API_TOKEN:
            return {"error": "Invalid Token"}, 403

        try:
            addresses = request.get_json()['json']
        except (KeyError, TypeError):
            return {"error": "Invalid JSON"}, 400

        geo_obj_debug = self.init_geo_obj_debug()
        results = []

        for address in addresses:
            try:
                result = self.db.address_cache.find_one({"address": address["address"].lower()})
                if result is None:
                    result = self.lookup_address(address["address"])
                address["items"] = result["items"]
                self.populate_debug(result, geo_obj_debug)
                results.append(address)
            except Exception as e:
                logger.error(f"Error processing address {address}: {str(e)}")
                return {"error": f"Error processing address: {str(e)}"}, 400

        return {"result": results, "debug": geo_obj_debug}
@api.doc(security='apikey')    
class Routing(Resource):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.db = MongoDB()
        self.kindergarten_data = self.load_kindergarten_data()

    def load_kindergarten_data(self) -> Dict[str, Tuple[float, float]]:
        """Load and transform kindergarten data from JSON."""
        with open("12_kindergartens_test_table.json", "r", encoding='utf-8') as f:
            data = json.load(f)
        
        # Transform coordinates and create a mapping
        name_to_coordinates = {}
        for entry in data:
            lat, lng = Utils.transform_coordinates(*entry["coordinates"][0])
            normalized_name = entry["object_nam"].replace('"', '').strip()
            name_to_coordinates[normalized_name] = (lat, lng)
        
        return name_to_coordinates

    def get_route(self, origin: str, destination: str, transport_modes: Optional[List[str]] = None) -> Dict:
        if transport_modes is None:
            transport_modes = [TransportMode.CAR.value, TransportMode.PUBLIC_TRANSIT.value, TransportMode.PEDESTRIAN.value]

        try:
            origin_lat, origin_lng = map(float, origin.split(','))
            dest_lat, dest_lng = map(float, destination.split(','))
            
            all_routes = []
            for mode in transport_modes:
                query = {
                    "transportMode": mode,
                    "origin": f"{origin_lat},{origin_lng}",
                    "destination": f"{dest_lat},{dest_lng}",
                    "return": "summary,polyline",
                    "apiKey": Config.HERE_API_KEY,
                    "alternatives": 3
                }
                
                if mode == TransportMode.PUBLIC_TRANSIT.value:
                    query.update({
                        "return": "summary,polyline,actions,instructions",
                        "departureTime": "any"
                    })
                
                response = requests.get("https://router.hereapi.com/v8/routes", params=query)
                if response.status_code == 200:
                    route_data = response.json()
                    route_data['transportMode'] = mode
                    all_routes.append(route_data)
            
            return {"routes": all_routes}
            
        except Exception as e:
            logger.error(f"Error in get_route: {str(e)}")
            raise Exception(f"Error calculating route: {str(e)}")

    @api.doc(params={
        "pointA": "Origin coordinates (lat,lng)",
        "pointB": "Destination coordinates or name",
        "modes": "Comma-separated transport modes",
        "token": "API token for authentication"
    })
    def get(self):
        parser = reqparse.RequestParser()
        parser.add_argument('pointA', type=str, required=True)
        parser.add_argument('pointB', type=str, required=True)
        parser.add_argument('modes', type=str, required=False)
        parser.add_argument('token', type=str, required=True)
        args = parser.parse_args()

        # Validate token first
        if args['token'] != Config.API_TOKEN:
            return {"error": "Invalid Token"}, 403

        try:
            transport_modes = None
            if args.get('modes'):
                transport_modes = [mode.strip() for mode in args['modes'].split(',')]
                invalid_modes = [mode for mode in transport_modes if mode not in [m.value for m in TransportMode]]
                if invalid_modes:
                    return {"error": f"Invalid transport modes: {invalid_modes}"}, 400

            # Handle pointB
            if ',' in args['pointB']:
                pointB = args['pointB']
            else:
                # Normalize the input name
                normalized_name = args['pointB'].replace('"', '').strip()
                coords = self.kindergarten_data.get(normalized_name)
                if coords is None:
                    logger.error(f"Could not find coordinates for the given name: {normalized_name}")
                    return {"error": "Could not find coordinates for the given name"}, 400
                pointB = f"{coords[0]},{coords[1]}"

            return self.get_route(args['pointA'], pointB, transport_modes)

        except Exception as e:
            logger.error(f"Error in Routing GET: {str(e)}")
            return {"error": str(e)}, 400
    
    @api.expect(ApiModels(api).routes_list_fields)
    @token_required
    def post(self):
        try:
            routes = request.get_json()['json']
            results = []
            
            for route in routes:
                try:
                    origin = f"{route['origin'][0]},{route['origin'][1]}"
                    
                    # Handle destination
                    destination = route.get('destination')
                    if isinstance(destination, str):
                        # Normalize the input name
                        normalized_name = destination.replace('"', '').strip()
                        coords = self.kindergarten_data.get(normalized_name)
                        if coords:
                            destination = f"{coords[0]},{coords[1]}"
                        else:
                            try:
                                lat, lng = map(float, destination.split(","))
                                destination = f"{lat},{lng}"
                            except ValueError:
                                return {"error": f"Invalid destination: {destination}"}, 400
                    elif isinstance(destination, list) and len(destination) == 2:
                        destination = f"{destination[0]},{destination[1]}"
                    else:
                        return {"error": f"Invalid destination format"}, 400

                    transport_modes = route.get('modes')
                    result = self.get_route(origin, destination, transport_modes)
                    route_result = route.copy()
                    route_result.update(result)
                    results.append(route_result)

                except Exception as e:
                    logger.error(f"Error processing route: {str(e)}")
                    return {"error": f"Error processing route: {str(e)}"}, 400

            return {"routes": results}

        except Exception as e:
            logger.error(f"Error in Routing POST: {str(e)}")
            return {"error": str(e)}, 400
        
@api.doc(security='apikey')
class MapView(Resource):
    @api.doc(params={"polyline": "Encoded polyline for the route"})
    def get(self):
        try:
            polyline = request.args.get('polyline')
            if not polyline:
                return {"error": "Polyline parameter is required"}, 400

            coordinates = fp.decode(polyline)
            coordinates = [[c[1], c[0]] for c in coordinates]
            points = [(i[1], i[0]) for i in coordinates]

            m = folium.Map()
            
            # Add markers
            folium.Marker(
                points[0], 
                icon=folium.Icon(color="red", icon="map-pin", prefix='fa')
            ).add_to(m)
            folium.Marker(
                points[-1], 
                icon=folium.Icon(color="blue", icon="map-marker", prefix='fa')
            ).add_to(m)
            
            # Add route line
            folium.PolyLine(points, weight=5, opacity=1).add_to(m)

            # Fit bounds
            df = pd.DataFrame(coordinates).rename(columns={0: 'Lon', 1: 'Lat'})[['Lat', 'Lon']]
            sw = df[['Lat', 'Lon']].min().values.tolist()
            ne = df[['Lat', 'Lon']].max().values.tolist()
            m.fit_bounds([sw, ne])

            return m._repr_html_()

        except Exception as e:
            logger.error(f"Error in MapView: {str(e)}")
            return {"error": "Invalid Polyline"}, 400
@api.doc(security='apikey')
class ReverseGeocode(Resource):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.db = MongoDB()

    def lookup_coordinates(self, lat: float, lng: float) -> Dict:
        try:
            # Create cache key from coordinates
            cache_key = f"{lat:.6f},{lng:.6f}"
            
            # Check cache first
            cached_result = self.db.address_cache.find_one({"coordinates": cache_key})
            if cached_result:
                return cached_result["result"]

            # If not in cache, query HERE API
            reverse_geocode_url = "https://revgeocode.search.hereapi.com/v1/revgeocode"
            params = {
                "at": f"{lat},{lng}",
                "apiKey": Config.HERE_API_KEY
            }
            response = requests.get(reverse_geocode_url, params=params)
            response.raise_for_status()  # Raise an error for bad responses
            result = response.json()

            # Cache the result
            self.db.address_cache.insert_one({
                "coordinates": cache_key,
                "result": result,
                "timestamp": datetime.utcnow()
            })

            return result
        except Exception as e:
            raise Exception(f"Error in reverse geocoding: {str(e)}")

    @api.doc(params={
        "lat": "Latitude of the location",
        "lng": "Longitude of the location",
        "token": "API token for authentication"
    })
    @token_required
    def get(self):
        parser = reqparse.RequestParser()
        parser.add_argument('lat', type=float, required=True, location='args')
        parser.add_argument('lng', type=float, required=True, location='args')
        args = parser.parse_args()

        try:
            result = self.lookup_coordinates(args['lat'], args['lng'])
            
            # Format the response similar to your existing endpoints
            return {
                "items": result.get("items", []),
                "debug": {
                    "type": "FeatureCollection",
                    "features": [{
                        "type": "Feature",
                        "properties": {},
                        "geometry": {
                            "type": "Point",
                            "coordinates": [args['lng'], args['lat']]
                        }
                    }]
                }
            }

        except Exception as e:
            logger.error(f"Error in ReverseGeocode: {str(e)}")
            return {"error": str(e)}, 400

class NearbyPlaces(Resource):
    @api.doc(params={
        "lat": "Latitude of the location",
        "lng": "Longitude of the location",
        "radius": "Search radius in meters (default is 1000)",
        "category": "Category of places to search for (e.g., restaurant, park)",
        "token": "API token for authentication"
    })
    @token_required
    def get(self):
        parser = reqparse.RequestParser()
        parser.add_argument('lat', type=float, required=True, location='args')
        parser.add_argument('lng', type=float, required=True, location='args')
        parser.add_argument('radius', type=int, required=False, default=1000, location='args')
        parser.add_argument('category', type=str, required=False, location='args')
        args = parser.parse_args()

        try:
            # Use HERE Places API to find nearby places
            places_url = "https://places.ls.hereapi.com/places/v1/discover/here"
            params = {
                "at": f"{args['lat']},{args['lng']}",
                "apiKey": Config.HERE_API_KEY,
                "pretty": "true"  # Optional: for a more readable response
            }
            if args.get('category'):
                params["cat"] = args['category']

            response = requests.get(places_url, params=params)
            response.raise_for_status()  # Raise an error for bad responses
            places_data = response.json()

            # Format the response
            places = []
            for item in places_data.get('results', {}).get('items', []):
                place_info = {
                    "name": item.get('title'),
                    "category": item.get('category', {}).get('title'),
                    "distance": item.get('distance'),
                    "address": item.get('vicinity'),
                    "position": item.get('position')
                }
                places.append(place_info)

            return {"places": places}

        except Exception as e:
            logger.error(f"Error in NearbyPlaces: {str(e)}")
            return {"error": str(e)}, 400

class OptimizeRoute(Resource):
    @api.expect(ApiModels(api).routes_list_fields)
    @token_required
    def post(self):
        try:
            routes = request.get_json()['json']
            # Example: Optimize route using HERE Routing API
            # Implement optimization logic here
            return {"message": "Route optimization not yet implemented"}
        except Exception as e:
            logger.error(f"Error in OptimizeRoute: {str(e)}")
            return {"error": str(e)}, 400 

class BatchGeocode(Resource):
    @api.expect(ApiModels(api).address_list_fields)
    @token_required
    def post(self):
        try:
            addresses = request.get_json()['json']
            # Prepare batch request
            batch_request = "\n".join([f"searchtext={addr['address']}" for addr in addresses])
            batch_url = "https://batch.geocoder.ls.hereapi.com/6.2/jobs"
            params = {
                "apiKey": Config.HERE_API_KEY,
                "action": "run",
                "outdelim": "json",
                "outcols": "displayLatitude,displayLongitude,locationLabel"
            }
            response = requests.post(batch_url, params=params, data=batch_request)
            return response.json()
        except Exception as e:
            logger.error(f"Error in BatchGeocode: {str(e)}")
            return {"error": str(e)}, 400

# Register resources
api.add_resource(GeolocateAddress, '/geocoords')
api.add_resource(Routing, '/route')
api.add_resource(ReverseGeocode, '/reverse-geocode')
api.add_resource(NearbyPlaces, '/nearby-places')
api.add_resource(OptimizeRoute, '/optimize-route')
api.add_resource(BatchGeocode, '/batch-geocode')
api.add_resource(MapView, '/map')

if __name__ == '__main__':
    app.run(debug=True)