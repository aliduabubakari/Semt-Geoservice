from flask import Flask, request
from flask_restx import Api, Resource, reqparse, fields
from herepy import GeocoderApi
from pymongo import MongoClient
import os
import json
import requests
import folium
import flexpolyline as fp
import pandas as pd

API_TOKEN = os.environ["API_TOKEN"]
HERE_API_KEY = os.environ["HERE_API_KEY"]
geocoder_api = GeocoderApi(api_key=HERE_API_KEY)

MONGO_HOST = os.environ["MONGO_HOST"]
MONGO_USER = os.environ["MONGO_USER"]
MONGO_PASSWORD = os.environ["MONGO_PASSWORD"]
MONGO_DBNAME = os.environ["MONGO_DBNAME"]
client = MongoClient(MONGO_HOST,
                     username=MONGO_USER,
                     password=MONGO_PASSWORD,
                     authSource='admin',
                     authMechanism='SCRAM-SHA-256')
address_cache = client[MONGO_DBNAME].address            
route_cache = client[MONGO_DBNAME].route                  
poi_cache = client[MONGO_DBNAME].poi                  



with open("municipalities.geojson.json") as f:
    geodata = json.loads(f.read())
temp = geodata["features"]
sofia_geoshape = [t for t in temp if "SOF" in t["properties"]["nuts4"]]


app = Flask(__name__)
api = Api(app)


def validate_token(token):
    return token == API_TOKEN
    
def get_address_data(address):
    address = address.lower()
    result = address_cache.find_one({"address": address})
    return result

def get_route_data(origin, destination):
    result = route_cache.find_one({"origin": origin, "destination": destination})
    return result

def get_poi(name):
    name = name.lower().replace('"', '')
    name = name.replace('“', '')
    name = " ".join(name.split())
    result = poi_cache.find_one({"name": name})
    return result


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

@api.route('/geocoords')
@api.doc(
    responses={200: "OK", 404: "Not found",
               400: "Bad request", 403: "Invalid token"},
    params={ "token": "token api key"}
)
class GeolocateAddress(Resource):

    def lookup_address(self, address):
        response = geocoder_api.free_form(address)
        results = response.as_dict()
        address_cache.insert_one({
            "address": address.lower(),
            "items": results["items"]
        })
        return results
    
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
        parser.add_argument('token', type=str, help='variable 1', location='args')
        args = parser.parse_args()
        token = args["token"]
        if not validate_token(token):
            return {"Error": "Invalid Token"}, 403
        try:
            addresses = request.get_json()['json']
        except:
            return {"Error": "Invalid Json"}, 400
        geo_obj_debug = self.init_geo_obj_debug()      
        for address in addresses:
            result = get_address_data(address["address"])
            if result is None:
                try:
                    result = self.lookup_address(address["address"])
                except Exception as e:   
                    return {"Error": str(e)}, 400 
            address["items"] = result["items"]
            self.populate_debug(result, geo_obj_debug)
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
        query = {
            "transportMode": "pedestrian", 
            "origin": origin, 
            "destination": destination, 
            "return": "summary,polyline", 
            "apiKey": HERE_API_KEY
        }
        result = requests.get("https://router.hereapi.com/v8/routes", params=query)
        result = result.json()
        route_cache.insert_one({
            "origin": origin,
            "destination": destination,
            "routes": result["routes"]
        })
        
        return result  

    @api.doc(
        params={
            "pointA": "Geocoords of point A (use oder lat,lng separeted to comma for e.g. 42.69357,23.36488)",
            "pointB": """Geocoords of point B or Point of Interest 
            (use order lat,lng separated to comma for e.g. 42.70214,23.37594 or ДГ №20 "Жасминов парк")"""
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
        parser.add_argument('token', type=str, help='variable 1', location='args')
        args = parser.parse_args()
        token = args["token"]
        if not validate_token(token):
            return {"Error": "Invalid Token"}, 403
        try:
            routes = request.get_json()['json']
        except:
            return {"Error": "Invalid Json"}, 400

        for route in routes:
            if type(route["destination"]) == str:
                school = get_poi(route["destination"])
                if school is None:
                    return {"Error": "Invalid Point of Interest name"}, 400   
                else:
                    route["destination"] = school["coords"].split(",")
            origin, destination = (",".join([str(p) for p in route["origin"]]), ",".join([str(p) for p in route["destination"]]))
            result = get_route_data(origin, destination)
            if result is None:
                try:
                    result = self.get_route(origin, destination)
                except Exception as e:   
                    return {"Error": str(e)}, 400 
            route["routes"] = result["routes"]
       
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


if __name__ == '__main__':
    app.run(debug=True)
