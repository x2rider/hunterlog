import json
import socket
import time
import webview
import logging as L
import datetime
from datetime import timedelta

from db.db import DataBase
from db.models.activators import Activator, ActivatorSchema
from db.models.parks import ParkSchema
from db.models.qsos import QsoSchema
from db.models.spot_comments import SpotCommentSchema
from db.models.spots import SpotSchema
from db.models.user_config import UserConfigSchema
from pota import PotaApi, PotaStats
from utils.adif import AdifLog
from version import __version__

from cat import CAT
from utils.distance import Distance

logging = L.getLogger("api")
# IDTOKENPAT = r"^.*CognitoIdentityServiceProvider\..+\.idToken=([\w\.-]*\;)"


class JsApi:
    def __init__(self, the_db: DataBase, pota_api: PotaApi):
        self.db = the_db
        self.pota = pota_api
        self.adif_log = AdifLog()
        logging.debug("init CAT...")
        cfg = self.db.get_user_config()
        self.cat = CAT("flrig", cfg.flr_host, cfg.flr_port)
        self.pw = None

    def get_spot(self, spot_id: int):
        logging.debug('py get_spot')
        spot = self.db.spots.get_spot(spot_id)
        ss = SpotSchema()
        return ss.dumps(spot)

    def get_spots(self):
        logging.debug('py get_spots')
        spots = self.db.spots.get_spots()
        ss = SpotSchema(many=True)
        return ss.dumps(spots)

    def get_spot_comments(self, spot_id: int):
        spot = self.db.spots.get_spot(spot_id)

        x = self.db.get_spot_comments(spot.activator, spot.reference)
        ss = SpotCommentSchema(many=True)
        return ss.dumps(x)

    def insert_spot_comments(self, spot_id: int):
        '''
        Pulls the spot comments from the POTA api and inserts them into our
        database.

        :param int spot_id: spot id. pk in db
        '''
        spot = self.db.spots.get_spot(spot_id)
        comms = self.pota.get_spot_comments(spot.activator, spot.reference)
        self.db.insert_spot_comments(spot.activator, spot.reference, comms)

    def get_qso_from_spot(self, id: int):
        logging.debug('py getting qso data')
        q = self.db.build_qso_from_spot(id)
        if q is None:
            return {"success": False}

        cfg = self.db.get_user_config()
        d = Distance.distance_miles(cfg.my_grid6, q.gridsquare)
        q.distance = d
        qs = QsoSchema()
        return qs.dumps(q)

    def get_activator_stats(self, callsign):
        logging.debug("getting activator stats...")
        ac = self._get_activator(callsign)
        if ac is None:
            return json.dumps({
                'success': False,
                'message': 'activator does not exists in POTA'
            })
        return ActivatorSchema().dumps(ac)

    def get_activator_hunts(self, callsign):
        logging.debug("getting hunt count stats...")
        return self.db.qsos.get_activator_hunts(callsign)

    def get_park(self, ref: str, pull_from_pota: bool = True) -> str:
        '''
        Returns the JSON for the park if found in the db

        :param str ref: the POTA park reference designator string
        :param bool pull_from_pota: True (default) to try to update when a park
            is not in the db.

        :returns JSON of park object in db or None if not found
        '''
        if ref is None:
            logging.error("get_park: ref param was None")
            return

        logging.debug(f"get_park: getting park {ref}")

        park = self.db.parks.get_park(ref)

        if park is None and pull_from_pota:
            logging.debug(f"get_park: park was None {ref}")
            api_res = self.pota.get_park(ref)
            logging.debug(f"get_park: park from api {api_res}")
            self.db.parks.update_park_data(api_res)
            park = self.db.parks.get_park(ref)
        elif park.name is None:
            logging.debug(f"get_park: park Name was None {ref}")
            api_res = self.pota.get_park(ref)
            logging.debug(f"get_park: park from api {api_res}")
            self.db.parks.update_park_data(api_res)
            park = self.db.parks.get_park(ref)

        ps = ParkSchema()
        return ps.dumps(park)

    def get_park_hunts(self, ref: str) -> str:
        '''
        Returns a JSON object containing the number of QSOs with activators at
        the given park reference.

        :param str ref: the POTA park reference designator string

        :returns JSON of park object in db or None if not found
        '''
        if ref is None:
            logging.error("get_park: ref param was None")
            return json.dumps({"success": False,
                               "msg": 'ref: invalid argument'})

        park = self.db.parks.get_park(ref)

        if park is None:
            return json.dumps({"success": True, "count": 0})
        else:
            return json.dumps({"success": True, "count": park.hunts})

    def get_user_config(self):
        '''
        Returns the JSON for the user configuration record in the db
        '''
        cfg = self.db.get_user_config()
        return UserConfigSchema().dumps(cfg)

    def get_version_num(self):
        result = {
            'success': True,
            'app_ver': __version__,
            'db_ver': self.db.get_version()
        }
        return json.dumps(result)

    def import_adif(self) -> str:
        '''
        Opens a Open File Dialog to allow the user to select a ADIF file
        containing POTA QSOs to be imported into the app's database.
        '''
        ft = ('ADIF files (*.adi;*.adif)', 'All files (*.*)')
        filename = webview.windows[0] \
            .create_file_dialog(
                webview.OPEN_DIALOG,
            file_types=ft)
        if not filename:
            return json.dumps({'success': True, 'message': "user cancel"})

        logging.info("starting import of ADIF file...")
        AdifLog.import_from_log(filename[0], self.db)

        result = {
            'success': True,
            'message': "completed adif import successfully",
        }
        return json.dumps(result)

    def log_qso(self, qso_data):
        '''
        Logs the QSO to the database, adif file, and updates stats. Will force
        a reload of the currently displayed spots.

        :param any qso_data: dict of qso data from the UI
        '''
        try:
            park_json = self.pota.get_park(qso_data['sig_info'])
            logging.debug(f"updating park stat for: {park_json}")
            self.db.parks.inc_park_hunt(park_json)

            logging.debug(f"logging qso: {qso_data}")
            id = self.db.qsos.insert_new_qso(qso_data)
        except Exception:
            logging.exception("Error logging QSO to db")

        # get the data to log to the adif file and remote adif host
        qso = self.db.qsos.get_qso(id)
        cfg = self.db.get_user_config()
        act = self.db.get_activator_name(qso_data['call'])
        qso.name = act if act is not None else 'ERROR NO NAME'
        self.adif_log.log_qso_and_send(qso, cfg)

        j = self.pota.get_spots()
        self.db.update_all_spots(j)

        webview.windows[0].evaluate_js(
            'window.pywebview.state.getSpots()')

    def export_qsos(self):
        '''
        Exports the QSOs logged with this logger app into a file.
        '''
        try:
            qs = self.db.qsos.get_qsos_from_app()
            cfg = self.db.get_user_config()

            dt = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            log = AdifLog(filename=f"{dt}_export.adi")
            for q in qs:
                log.log_qso(q, cfg)
        except Exception:
            logging.exception("Error exporting the DB")

    def set_user_config(self, config_json: any):
        logging.debug(f"setting config {config_json}")
        self.db.update_user_config(config_json)

    def set_band_filter(self, band: int):
        logging.debug(f"api setting band filter to: {band}")
        self.db.set_band_filter(band)

    def set_region_filter(self, region: str):
        logging.debug(f"api setting region filter to: {region}")
        self.db.set_region_filter(region)

    def set_location_filter(self, location: str):
        logging.debug(f"setting region filter to {location}")
        self.db.set_location_filter(location)

    def set_qrt_filter(self, is_qrt: bool):
        logging.debug(f"api setting qrt filter to: {is_qrt}")
        self.db.set_qrt_filter(is_qrt)

    def set_hunted_filter(self, filter_hunted: bool):
        logging.debug(f"api setting qrt filter to: {filter_hunted}")
        self.db.set_hunted_filter(filter_hunted)

    def update_activator_stats(self, callsign: str) -> int:
        j = self.pota.get_activator_stats(callsign)

        if j is not None:
            # the json will be none if say the call doesn't return success
            # from api. probably they dont have an account
            return self.db.update_activator_stat(j)
        else:
            logging.warn(f"activator callsign {callsign} not found")
            return -1

    def launch_pota_window(self):
        self.pw = webview.create_window(
            title='POTA APP', url='https://pota.app/#/user/stats')

    def load_location_data(self):
        logging.debug("downloading location data...")
        locations = PotaApi.get_locations()
        self.db.locations.load_location_data(locations)
        result = {
            'success': True,
            'message': "downloaded location data successfully",
        }
        return json.dumps(result)

        # self.pota.get_user_hunt(token, cookies)

        # self.pw.js_api_endpoint.e

    # def get_id_token(self, win: webview.Window) -> tuple[str, any]:
    #     logging.debug("looking thru cookies for idToken...")
    #     cookies = win.get_cookies()
    #     tok = None
    #     jar = {}

    #     for c in cookies:
    #         co = c.output()
    #         x = co.split('=')
    #         k = x[0][12:]
    #         jar[k] = x[1]
    #         m = re.match(IDTOKENPAT, co)
    #         if m:
    #             # logging.debug(f"matched group {m.group(1)}")
    #             tok = m.group(1)

    #     logging.debug(jar)
    #     if tok is None:
    #         logging.warn("no POTA idToken found in cookies!")
    #     return (tok, jar)

    def qsy_to(self, freq, mode: str):
        '''Use CAT control to QSY'''
        logging.debug(f"qsy_to {freq} {mode}")
        x = float(freq) * 1000.0
        logging.debug(f"adjusted freq {x}")
        if mode == "SSB" and x > 10000000:
            mode = "USB"
        elif mode == "SSB":
            mode = "LSB"
        logging.debug(f"adjusted mode {mode}")
        self.cat.set_mode(mode)
        self.cat.set_vfo(x)

    def update_park_hunts_from_csv(self) -> str:
        '''
        Will use the current pota stats from hunter.csv to update the db with
        new park hunt numbers. It will then update all the parks with data from
        the POTA API. This method will run a while depending on how many parks
        are in the csv file.
        '''
        ft = ('CSV files (*.csv;*.txt)', 'All files (*.*)')
        filename = webview.windows[0] \
            .create_file_dialog(
                webview.OPEN_DIALOG,
                file_types=ft)
        if not filename:
            return json.dumps({'success': True, 'message': "user cancel"})

        logging.info(f"updating park hunts from {filename[0]}")
        stats = PotaStats(filename[0])
        hunts = stats.get_all_hunts()

        for park in hunts:
            count = stats.get_park_hunt_count(park)
            j = {'reference': park, 'hunts': count}
            self.db.parks.update_park_hunts(j, count)

        self.db.commit_session()

        return self._update_all_parks()

    def export_park_data(self) -> str:
        '''
        Dumps the entire parks table into a file named 'park_export.json'.

        This can then be later used to import. This is useful to avoid having
        to download park info from the POTA endpoints.
        '''
        logging.debug("export_park_data: dumping parks table...")
        parks = self.db.parks.get_parks()
        schema = ParkSchema()
        data = schema.dumps(parks, many=True)

        with open("park_export.json", "w") as out:
            out.write(data)

        return json.dumps({
            'success': True,
            'message': "park data exported successfully",
        })

    def import_park_data(self) -> str:
        '''
        Loads previously exported park data from a file into the parks table.

        The opposite of :meth:`export_park_data`
        '''
        logging.debug("import_park_data: loading table...")

        ft = ('JSON files (*.json)', 'All files (*.*)')
        filename = webview.windows[0] \
            .create_file_dialog(
                webview.OPEN_DIALOG,
            file_types=ft)
        if not filename:
            return json.dumps({'success': True, 'message': "user cancel"})

        with open(filename[0], "r") as input:
            text = input.read()
            obj = json.loads(text)
            self.db.parks.import_park_data(obj)

        return json.dumps({
            'success': True,
            'message': "park data import successfully",
        })

    def _update_all_parks(self) -> str:
        logging.info("updating all parks in db")

        parks = self.db.parks.get_parks()
        for park in parks:
            if park.name is not None:
                continue

            api_res = self.pota.get_park(park.reference)
            self.db.parks.update_park_data(api_res)  # delay_commit=True

            time.sleep(0.001)  # dont want to hurt POTA

        # self.db.commit_session()

        return json.dumps({
            'success': True,
            'message': "completed park update successfully",
        })

    def _send_msg(self, msg: str):
        """
        Send a UDP adif message to a remote endpoint
        """
        host = self.settings.get("host", "127.0.0.1")
        port = self.settings.get("port", 8073)
        type = socket.SOCK_DGRAM

        try:
            with socket.socket(socket.AF_INET, type) as sock:
                sock.connect((host, port))
                sock.send(msg.encode())
        except Exception:
            logging.exception("send_msg exception")

    def _get_activator(self, callsign: str) -> Activator:
        ''''
        Gets the activator model from the db or pulls the data to create a
        new one or update and old one.
        '''
        def update():
            logging.info("activator needs update from POTA API...")
            id = self.update_activator_stats(callsign)
            if id > 0:
                activator = self.db.get_activator_by_id(id)
                return activator
            return None

        ac = self.db.get_activator(callsign)
        if (ac is None):
            # not found pull new data
            return update()
        else:
            # check timestamp
            if (datetime.datetime.utcnow() - ac.updated < timedelta(days=1)):
                return update()

        return ac
