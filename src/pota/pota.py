import json
import os
from pathlib import Path
import requests
import logging as L
import urllib.parse
from cachetools.func import ttl_cache
from utils.callsigns import get_basecall
from version import __version__

logging = L.getLogger(__name__)


SPOT_URL = "https://api.pota.app/spot/activator"
SPOT_COMMENTS_URL = "https://api.pota.app/spot/comments/{act}/{park}"
ACTIVATOR_URL = "https://api.pota.app/stats/user/{call}"
PARK_URL = "https://api.pota.app/park/{park}"
LOCATIONS_URL = "https://api.pota.app/programs/locations/"
POST_SPOT_URL = "https://api.pota.app/spot/"
LOCATION_PARKS_URL = "https://api.pota.app/location/parks/{loc}"


class Api():
    '''Class that calls the POTA endpoints and returns their results'''

    data_dir = "data"
    '''Directory for stored files'''

    def get_spots(self):
        '''Return all current spots from POTA API'''
        response = requests.get(SPOT_URL)
        if response.status_code == 200:
            json = response.json()
            return json

    def get_spot_comments(self, activator, park):
        '''
        Return all spot + comments from a given activation

        :param str activator: Full call of activator including stroke pre and
            suffixes. Will be URL encoded for the request.
        :param str park: the park reference.
        '''
        quoted = urllib.parse.quote_plus(activator)
        url = SPOT_COMMENTS_URL.format(act=quoted, park=park)
        response = requests.get(url)
        if response.status_code == 200:
            json = response.json()
            return json

    @ttl_cache(ttl=6*60*60)  # 6 hours of cache
    def get_activator_stats(self, activator: str):
        '''
        Return the pota stats for an activator's callsign. Func results are
        cached for 6 hours.

        :param str activator: callsign of activator
        :returns: json activator stats.
        '''
        s = get_basecall(activator)

        url = ACTIVATOR_URL.format(call=s)
        response = requests.get(url)
        if response.status_code == 200:
            json = response.json()
            return json
        else:
            return None

    @ttl_cache(ttl=24*60*60)  # 24 hours of cache
    def get_park(self, park_ref: str):
        '''
        Return the pota stats for an activator's callsign. Func results are
        cached for 24 hours.

        :param str activator: callsign of activator
        :returns: json activator stats.
        '''
        url = PARK_URL.format(park=park_ref)
        response = requests.get(url)
        if response.status_code == 200:
            json = response.json()
            return json

    @ttl_cache(ttl=24*60*60)  # 24 hours of cache
    def check_and_download_parks(self,
                                 location: str,
                                 force: bool = False) -> int:
        '''
        Checks if the data file is present for the given location, if not, it
        downloads the park data for the location.

        Parameters
        ------------
        location : string
            the POTA location string
        force : bool
            true to force downloading, even if file already exists

        Returns
        ------------
        response code from endpoint. -1 if file exists
        '''
        loc = location

        url = LOCATION_PARKS_URL.format(loc=loc)
        json_file = f"parks-{loc}.json"
        file = Path(self.data_dir, json_file)

        if not Path(self.data_dir).exists():
            Path.mkdir(Path(self.data_dir))

        if force:
            return Api.save_json(url, file)

        if os.path.exists(file):
            return -1

        return Api.save_json(url, file)

    @staticmethod
    def save_json(url: str, file_name: str) -> int:
        '''Request json data from an endpoint and save it to the given file.'''

        r = requests.get(url)
        if r.status_code == 200:
            data = r.json()
            with open(file_name, 'w') as out_file:
                out_file.write(json.dumps(data, indent=4))

        return r.status_code

    @staticmethod
    def get_locations():
        '''
        This file is quite large
        '''
        url = LOCATIONS_URL
        response = requests.get(url)
        if response.status_code == 200:
            obj = response.json()
            with open('locations.json', 'w', encoding='utf8') as w:
                w.write(json.dumps(obj))
            return obj

    @staticmethod
    def post_spot(activator_call: str, park_ref: str,
                  freq: str, mode: str,
                  spotter_call: str, spotter_comments: str):
        '''
        Posts a spot to the POTA spot endpoint. Adding or re-spotting a
        activation.
        '''
        url = POST_SPOT_URL
        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-encoding": "gzip, deflate, br, zstd",
            "Content-Type": "application/json",
            "origin": "https://pota.app",
            "referer": "https://pota.app/",
            'user-agent': f"hunterlog/{__version__}"
        }

        json_data = {
            'activator': activator_call,
            'spotter': spotter_call,
            'frequency': freq,
            'reference': park_ref,
            'mode': mode,
            'source': 'hunterlog',
            'comments': spotter_comments
        }

        r = requests.post(url=url, json=json_data, headers=headers)
        logging.debug(f"code: {r.status_code} : {r.reason}")
