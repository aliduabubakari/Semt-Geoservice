from flask import Flask, request, jsonify
from flask_restx import Api, Resource, reqparse, fields
import requests
from pyproj import Transformer
from enum import Enum
import os
from functools import wraps
from herepy import GeocoderApi

app = Flask(__name__)
api = Api(app)

API_TOKEN = os.environ["API_TOKEN"]
HERE_API_KEY = os.environ["HERE_API_KEY"]
geocoder_api = GeocoderApi(api_key=HERE_API_KEY)


# Define the transformer for UTM to WGS 84 (latitude, longitude)
transformer = Transformer.from_crs("EPSG:7800", "EPSG:4326", always_xy=True)

def transform_coordinates(x, y):
    """Transform coordinates from UTM to lat/lng."""
    lng, lat = transformer.transform(x, y)  # Note: transformer returns (lng, lat)
    return lat, lng  # Return in (lat, lng) format for consistency


# Sample data with UTM coordinates
data = [
    { "fid": 1, "id": 8, "object_nam": "ДГ №7 \"Детелина\"", "object_nom": 7, "adres": "ул. \"Деян Белишки\" №44", "coordinates": [[319578.847508467035368, 4727494.587673143483698]] },
    # Additional entries...
]

# Create a mapping from object_nam to converted coordinates
name_to_coordinates = {}
for entry in data:
    lat, lng = transform_coordinates(*entry["coordinates"][0])
    normalized_name = entry["object_nam"].replace('"', '')
    name_to_coordinates[normalized_name] = (lat, lng)

HERE_API_KEY = os.getenv("HERE_API_KEY")

class TransportMode(Enum):
    PEDESTRIAN = "pedestrian"
    CAR = "car"
    TRUCK = "truck"
    BICYCLE = "bicycle"
    SCOOTER = "scooter"
    TAXI = "taxi"
    BUS = "bus"
    PUBLIC_TRANSIT = "publicTransport"

class Region:
    def __init__(self, name, bounds):
        self.name = name
        self.min_lat = bounds['min_lat']
        self.max_lat = bounds['max_lat']
        self.min_lng = bounds['min_lng']
        self.max_lng = bounds['max_lng']

    def is_within_bounds(self, lat, lng):
        return (self.min_lat <= lat <= self.max_lat) and (self.min_lng <= lng <= self.max_lng)

# Define regions with their boundaries
REGIONS = {
    'BGR': Region('Bulgaria', {
        'min_lat': 41.2,
        'max_lat': 44.2,
        'min_lng': 22.3,
        'max_lng': 28.6
    }),
    'ROU': Region('Romania', {
        'min_lat': 43.6,
        'max_lat': 48.2,
        'min_lng': 20.2,
        'max_lng': 29.7
    }),
    # Add more countries as needed
}

def transform_coordinates(x, y):
    """Transform coordinates from UTM to lat/lng."""
    lng, lat = transformer.transform(x, y)
    return lat, lng

def is_within_region(lat, lng, region_code=None):
    """
    Validate if coordinates are within specified region's bounds
    If no region specified, check if coordinates are within any known region
    """
    if region_code:
        region = REGIONS.get(region_code)
        return region and region.is_within_bounds(lat, lng)
    return any(region.is_within_bounds(lat, lng) for region in REGIONS.values())

def get_coordinates_from_name(name):
    """Get coordinates from predefined data or HERE API."""
    normalized_name = name.replace('"', '')
    
    # Check predefined data first
    coords = name_to_coordinates.get(normalized_name)
    if coords:
        return coords

    # If not found, use HERE Geocoding API
    geocode_url = "https://geocode.search.hereapi.com/v1/geocode"
    params = {
        "q": name,
        "apiKey": HERE_API_KEY,
        "limit": 1
    }
    
    response = requests.get(geocode_url, params=params)
    if response.status_code == 200:
        items = response.json().get('items', [])
        if items:
            position = items[0]['position']
            return position['lat'], position['lng']
    return None

# Define API models for request validation
coordinates_model = api.model('Coordinates', {
    'lat': fields.Float(required=True, description='Latitude'),
    'lng': fields.Float(required=True, description='Longitude')
})

route_request_model = api.model('RouteRequest', {
    'origin': fields.List(fields.Float, required=True, description='Origin coordinates [lat, lng]'),
    'destination': fields.Raw(required=True, description='Destination (coordinates array or string)'),
    'modes': fields.List(fields.String, required=False, description='List of transport modes')
})

routes_list_fields = api.model('RoutesList', {
    'json': fields.List(fields.Nested(route_request_model), required=True, description='List of route requests')
})

def validate_token(token):
    """Validate API token"""
    return token == API_TOKEN


def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        parser = reqparse.RequestParser()
        parser.add_argument('token', type=str, required=True, location='args')
        token_args = parser.parse_args()
        
        if not validate_token(token_args["token"]):
            return {"error": "Invalid Token"}, 403
        return f(*args, **kwargs)
    return decorated

@api.route('/route')
@api.doc(
    responses={200: "OK", 404: "Not found", 400: "Bad request", 403: "Invalid token"},
    params={"token": "API token for authentication"}
)

class Routing(Resource):
    def get_route(self, origin, destination, transport_modes=None):
        """
        Get routes using specified transport modes.
        If no transport modes specified, try all available modes.
        """
        if transport_modes is None:
            transport_modes = [TransportMode.CAR.value, TransportMode.PUBLIC_TRANSIT.value, 
                             TransportMode.PEDESTRIAN.value]

        origin_coords = origin.split(',')
        dest_coords = destination.split(',')
        
        try:
            origin_lat, origin_lng = float(origin_coords[0]), float(origin_coords[1])
            dest_lat, dest_lng = float(dest_coords[0]), float(dest_coords[1])
            
            # Validate coordinates are within any known region
            if not is_within_region(origin_lat, origin_lng):
                return {"error": "Origin coordinates outside supported regions"}, 400
            if not is_within_region(dest_lat, dest_lng):
                return {"error": "Destination coordinates outside supported regions"}, 400
            
            all_routes = []
            for mode in transport_modes:
                query = {
                    "transportMode": mode,
                    "origin": f"{origin_lat},{origin_lng}",
                    "destination": f"{dest_lat},{dest_lng}",
                    "return": "summary,polyline",
                    "apiKey": HERE_API_KEY,
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
            
        except (ValueError, IndexError) as e:
            return {"error": f"Invalid coordinates format: {str(e)}"}, 400

    @api.doc(params={
        "pointA": "Geocoords of point A (lat,lng, e.g. 42.69357,23.36488)",
        "pointB": "Either coordinates (lat,lng) or name",
        "modes": "Comma-separated list of transport modes (optional)"
    })
    @token_required
    def get(self):
        parser = reqparse.RequestParser()
        parser.add_argument('pointA', type=str, required=True, location='args')
        parser.add_argument('pointB', type=str, required=True, location='args')
        parser.add_argument('modes', type=str, required=False, location='args')
        args = parser.parse_args()

        transport_modes = None
        if args.get('modes'):
            transport_modes = [mode.strip() for mode in args['modes'].split(',')]
            invalid_modes = [mode for mode in transport_modes 
                           if mode not in [m.value for m in TransportMode]]
            if invalid_modes:
                return {"error": f"Invalid transport modes: {invalid_modes}"}, 400

        try:
            # Handle pointB
            if ',' in args['pointB']:
                lat, lng = args['pointB'].split(",")
                coords = (float(lat), float(lng))
            else:
                coords = get_coordinates_from_name(args['pointB'])
                if coords is None:
                    return {"error": "Could not find coordinates for the given name"}, 400
            
            pointB = f"{coords[0]},{coords[1]}"
            return self.get_route(args['pointA'], pointB, transport_modes)
            
        except Exception as e:
            return {"error": str(e)}, 400

    @api.expect(routes_list_fields)
    @api.doc(description='Calculate routes for multiple origin-destination pairs')
    @token_required
    def post(self):
        try:
            routes = request.get_json()['json']
        except (KeyError, TypeError):
            return {"error": "Invalid JSON structure"}, 400

        results = []
        for route in routes:
            try:
                # Handle origin
                if not isinstance(route.get('origin'), list) or len(route['origin']) != 2:
                    return {"error": f"Invalid origin format for route: {route}"}, 400
                
                origin = f"{route['origin'][0]},{route['origin'][1]}"
                
                # Handle destination
                destination = route.get('destination')
                if destination is None:
                    return {"error": "Missing destination"}, 400

                if isinstance(destination, str):
                    coords = get_coordinates_from_name(destination)
                    if coords:
                        destination = f"{coords[0]},{coords[1]}"
                    else:
                        try:
                            # Try parsing as coordinate string
                            lat, lng = map(float, destination.split(","))
                            destination = f"{lat},{lng}"
                        except ValueError:
                            return {
                                "error": f"Invalid destination: {destination}",
                                "available_locations": sorted(name_to_coordinates.keys())
                            }, 400
                elif isinstance(destination, list) and len(destination) == 2:
                    destination = f"{destination[0]},{destination[1]}"
                else:
                    return {"error": f"Invalid destination format: {destination}"}, 400

                # Handle transport modes
                transport_modes = route.get('modes', None)
                if transport_modes:
                    invalid_modes = [mode for mode in transport_modes 
                                if mode not in [m.value for m in TransportMode]]
                    if invalid_modes:
                        return {"error": f"Invalid transport modes: {invalid_modes}"}, 400

                # Get route
                result = self.get_route(origin, destination, transport_modes)
                
                # Ensure result is a dictionary before updating
                if isinstance(result, dict):
                    route_result = route.copy()
                    route_result.update(result)
                    results.append(route_result)
                else:
                    return {"error": "Unexpected response format from route calculation"}, 500

            except Exception as e:
                return {"error": f"Error processing route: {str(e)}"}, 400

        return {"routes": results}

api.add_resource(Routing, '/route')