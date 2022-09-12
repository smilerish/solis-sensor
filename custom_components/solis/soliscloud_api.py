"""
Access to the Soliscloud API for PV monitoring.
Works for all Ginlong brands using the Soliscloud API

For more information: https://github.com/hultenvp/solis-sensor/
"""
from __future__ import annotations

import hashlib
#from hashlib import sha1
import hmac
import base64
import asyncio
from datetime import datetime
from datetime import timezone
from http import HTTPStatus
import json
import logging
from typing import Any
from aiohttp import ClientError, ClientSession
import async_timeout
import yaml
import os.path

from .ginlong_base import BaseAPI, GinlongData, PortalConfig
from .ginlong_const import *
from .soliscloud_const import *

_LOGGER = logging.getLogger(__name__)
SOLIS_PATH = os.path.dirname(__file__)

# VERSION
VERSION = '0.1.8'

# Response constants
SUCCESS = 'Success'
CONTENT = 'Content'
STATUS_CODE = 'StatusCode'
MESSAGE = 'Message'

#VALUE_RECORD = '_from_record'
#VALUE_ELEMENT = ''

VERB = "POST"

INVERTER_DETAIL = '/v1/api/inveterDetail'
PLANT_DETAIL = '/v1/api/stationDetail'

InverterDataType = dict[str, dict[str, list]]

"""{endpoint: [payload type, {key type, decimal precision}]}"""
INVERTER_DATA: InverterDataType = {
    INVERTER_DETAIL: {
        INVERTER_SERIAL:                  ['sn', str, None],
        INVERTER_PLANT_ID:                ['stationId', str, None],
        INVERTER_DEVICE_ID:               ['id', str, None],
        INVERTER_DATALOGGER_SERIAL:       ['collectorId', str, None],
        # Timestamp of measurement
        INVERTER_TIMESTAMP_UPDATE:        ['dataTimestamp', int, None],
        INVERTER_STATE:                   ['state', int, None],
        INVERTER_TEMPERATURE:             ['inverterTemperature', float, 1],
        INVERTER_POWER_STATE:             ['currentState', int, None],
        INVERTER_ACPOWER:                 ['pac', float, 3],
        INVERTER_ACPOWER_STR:             ['pacStr', str, None],
        INVERTER_ACFREQUENCY:             ['fac', float, 2],
        #INVERTER_ENERGY_TODAY:            ['eToday', float, 2], # Moved to PLANT_DETAIL
        INVERTER_ENERGY_THIS_MONTH:       ['eMonth', float, 2],
        INVERTER_ENERGY_THIS_YEAR:        ['eYear', float, 2],
        INVERTER_ENERGY_THIS_YEAR_STR:    ['eYearStr', str, None],
        INVERTER_ENERGY_TOTAL_LIFE:       ['eTotal', float, 2],
        INVERTER_ENERGY_TOTAL_LIFE_STR:   ['eTotalStr', str, None],
        STRING_COUNT:                     ['dcInputtype', int, None],
        STRING1_VOLTAGE:                  ['uPv1', float, 2],
        STRING2_VOLTAGE:                  ['uPv2', float, 2],
        STRING3_VOLTAGE:                  ['uPv3', float, 2],
        STRING4_VOLTAGE:                  ['uPv4', float, 2],
        STRING1_CURRENT:                  ['iPv1', float, 2],
        STRING2_CURRENT:                  ['iPv2', float, 2],
        STRING3_CURRENT:                  ['iPv3', float, 2],
        STRING4_CURRENT:                  ['iPv4', float, 2],
        STRING1_POWER:                    ['pow1', float, 2], # Undocumented
        STRING2_POWER:                    ['pow2', float, 2], # Undocumented
        STRING3_POWER:                    ['pow3', float, 2], # Undocumented
        STRING4_POWER:                    ['pow4', float, 2], # Undocumented
        PHASE1_VOLTAGE:                   ['uAc1', float, 2],
        PHASE2_VOLTAGE:                   ['uAc2', float, 2],
        PHASE3_VOLTAGE:                   ['uAc3', float, 2],
        PHASE1_CURRENT:                   ['iAc1', float, 2],
        PHASE2_CURRENT:                   ['iAc2', float, 2],
        PHASE3_CURRENT:                   ['iAc3', float, 2],
        BAT_POWER:                        ['batteryPower', float, 3],
        BAT_POWER_STR:                    ['batteryPowerStr', str, None],
        BAT_REMAINING_CAPACITY:           ['batteryCapacitySoc', float, 2],
        BAT_TOTAL_ENERGY_CHARGED:         ['batteryTotalChargeEnergy', float, 3],
        BAT_TOTAL_ENERGY_CHARGED_STR:     ['batteryTotalChargeEnergyStr', str, None],
        BAT_TOTAL_ENERGY_DISCHARGED:      ['batteryTotalDischargeEnergy', float, 3],
        BAT_TOTAL_ENERGY_DISCHARGED_STR:  ['batteryTotalDischargeEnergyStr', str, None],
        BAT_DAILY_ENERGY_CHARGED:         ['batteryTodayChargeEnergy', float, 2],
        BAT_DAILY_ENERGY_DISCHARGED:      ['batteryTodayDischargeEnergy', float, 2],
        GRID_DAILY_ON_GRID_ENERGY:        ['gridSellTodayEnergy', float, 2],
        GRID_DAILY_ENERGY_PURCHASED:      ['gridPurchasedTodayEnergy', float, 2],
        GRID_DAILY_ENERGY_USED:           ['homeLoadTodayEnergy', float, 2],
        GRID_MONTHLY_ENERGY_PURCHASED:    ['gridPurchasedMonthEnergy', float, 2],
        GRID_YEARLY_ENERGY_PURCHASED:     ['gridPurchasedYearEnergy', float, 2],
        GRID_TOTAL_ON_GRID_ENERGY:        ['gridSellTotalEnergy', float, 2],
        GRID_TOTAL_POWER:                 ['psum', float, 3],
        GRID_TOTAL_POWER_STR:             ['psumStr', str, None],
        GRID_TOTAL_CONSUMPTION_POWER:     ['familyLoadPower', float, 3],
        GRID_TOTAL_CONSUMPTION_POWER_STR: ['familyLoadPowerStr', str, None],
        GRID_TOTAL_ENERGY_USED:           ['homeLoadTotalEnergy', float, 3],
        GRID_TOTAL_ENERGY_USED_STR:       ['homeLoadTotalEnergyStr', str, None],
    },
    PLANT_DETAIL: {
        INVERTER_LAT:                     ['latitude', float, 7],
        INVERTER_LON:                     ['longitude', float, 7],
        INVERTER_ADDRESS:                 ['cityStr', str, None],
        INVERTER_ENERGY_TODAY:            ['dayEnergy', float, 2]
    },
}

class SoliscloudConfig(PortalConfig):
    """ Portal configuration data """

    def __init__(self,
        portal_domain: str,
        portal_username: str,
        portal_key_id: str,
        portal_secret: bytes,
        portal_plantid: str
    ) -> None:
        super().__init__(portal_domain, portal_username, portal_plantid)
        self._key_id: str = portal_key_id
        self._secret: bytes = portal_secret
        self._workarounds = {} 
        with open(SOLIS_PATH + '/workarounds.yaml', 'r') as file:
            self._workarounds = yaml.safe_load(file)
            _LOGGER.debug("workarounds: %s", self._workarounds)

    @property
    def key_id(self) -> str:
        """ Key ID."""
        return self._key_id

    @property
    def secret(self) -> bytes:
        """ API Key."""
        return self._secret
    
    @property
    def workarounds(self) -> dict[str, Any]:
        """ Return all workaround settings """
        return self._workarounds

class SoliscloudAPI(BaseAPI):
    """Class with functions for reading data from the Soliscloud Portal."""

    def __init__(self, config: SoliscloudConfig) -> None:
        self._config: SoliscloudConfig = config
        self._session: ClientSession | None = None
        self._user_id: int | None = None
        self._data: dict[str, str | int | float] = {}
        self._inverter_list: dict[str, str] | None = None

    @property
    def config(self) -> SoliscloudConfig:
        """ Config this for this API instance."""
        return self._config

    @property
    def is_online(self) -> bool:
        """ Returns if we are logged in."""
        return self._user_id is not None

    async def login(self, session: ClientSession) -> bool:
        """See if we can fetch userId and build a list of inverters"""
        self._session = session
        self._inverter_list = None
        # Building url & params
        canonicalized_resource = '/v1/api/addUser'
        params = {
            "userName": self.config.username,
            "userType":0
        }

        # Request user id
        result = await self._post_data_json(canonicalized_resource, params)
        if result[SUCCESS] is True:
            result_json = result[CONTENT]
            try:
                self._user_id = result_json['data']
                _LOGGER.info('Login Successful!')
                # Request inverter list
                self._inverter_list = await self.fetch_inverter_list(self.config.plantid)
                if len(self._inverter_list)==0:
                    _LOGGER.warning("No inverters found")
                else:
                    _LOGGER.debug("Found inverters: %s", list(self._inverter_list.keys()))
            except KeyError:
                _LOGGER.error(
                    'Unable to communicate with %s, please verify configuration.',
                    self.config.domain)
                self._user_id = None
        else:
            self._user_id = None
        return self.is_online

    async def logout(self) -> None:
        """Hand back session """
        self._session = None
        self._user_id = None
        self._inverter_list = None

    async def fetch_inverter_list(self, plant_id: str) -> dict[str, str]:
        """
        Fetch return list of inverters { inverter serial : device_id }
        """

        device_ids = {}

        params = {
            'stationId': plant_id
        }
        result = await self._post_data_json('/v1/api/inveterList', params)

        if result[SUCCESS] is True:
            result_json: dict = result[CONTENT]
            for record in result_json['data']['page']['records']:
                serial = record.get('sn')
                device_id = record.get('id')
                device_ids[serial] = device_id
        else:
            self._user_id = None

        return device_ids

    async def fetch_inverter_data(self, inverter_serial: str) -> GinlongData | None:
        """
        Fetch data for given inverter. 
        Collect available data from payload and store as GinlongData object
        """

        _LOGGER.debug("Fetching data for serial: %s", inverter_serial)
        self._data = {}
        if self.is_online:
            if self._inverter_list is not None and inverter_serial in self._inverter_list:
                device_id = self._inverter_list[inverter_serial]
                payload = await self._get_inverter_details(device_id, inverter_serial)
                payload2 = await self._get_station_details(self.config.plantid)
                if payload is not None:
                    #_LOGGER.debug("%s", payload)
                    self._collect_inverter_data(payload)
                    self._post_process()
                if payload2 is not None:
                    self._collect_station_data(payload2)
                if self._data is not None and INVERTER_SERIAL in self._data:
                    return GinlongData(self._data)
                else:
                    _LOGGER.debug("Unexpected response from server: %s", payload)
        return None


    async def _get_inverter_details(self,
        device_id: str,
        device_serial: str
    ) -> dict[str, Any] | None:
        """
        Update inverter details
        """

        # Get inverter details
        params = {
            'id': device_id,
            'sn': device_serial
        }

        result = await self._post_data_json(INVERTER_DETAIL, params)

        jsondata = None
        if result[SUCCESS] is True:
            jsondata = result[CONTENT]
        else:
            _LOGGER.info('Unable to fetch details for device with ID: %s', device_id)
        return jsondata

    def _collect_inverter_data(self, payload: dict[str, Any]) -> None:
        """ Fetch dynamic properties """
        jsondata = payload['data']
        attributes = INVERTER_DATA[INVERTER_DETAIL]
        for dictkey in attributes:
            key = attributes[dictkey][0]
            type_ = attributes[dictkey][1]
            precision = attributes[dictkey][2]
            if key is not None:
                value = self._get_value(jsondata, key, type_, precision)
                if value is not None:
                    self._data[dictkey] = value

    async def _get_station_details(self, plant_id: str) -> dict[str, str]:
        """
        Fetch Station Details
        """

        params = {
            'id': plant_id
        }
        result = await self._post_data_json(PLANT_DETAIL, params)

        jsondata = None
        if result[SUCCESS] is True:
            jsondata = result[CONTENT]
        else:
            _LOGGER.info('Unable to fetch details for Station with ID: %s', plant_id)
        return jsondata

    def _collect_station_data(self, payload: dict[str, Any]) -> None:
        """ Fetch dynamic properties """
        jsondata = payload['data']
        attributes = INVERTER_DATA[PLANT_DETAIL]
        for dictkey in attributes:
            key = attributes[dictkey][0]
            type_ = attributes[dictkey][1]
            precision = attributes[dictkey][2]
            if key is not None:
                value = self._get_value(jsondata, key, type_, precision)
                if value is not None:
                    self._data[dictkey] = value


    def _post_process(self) -> None:
        """ Cleanup received data. """
        if self._data:
            # Fix timestamps
            self._data[INVERTER_TIMESTAMP_UPDATE] = \
                float(self._data[INVERTER_TIMESTAMP_UPDATE])/1000

            # Convert kW into W depending on unit returned from API.
            if self._data[GRID_TOTAL_POWER_STR] == "kW":
                self._data[GRID_TOTAL_POWER] = \
                    float(self._data[GRID_TOTAL_POWER])*1000
                self._data[GRID_TOTAL_POWER_STR] = "W"

            if self._data[BAT_POWER_STR] == "kW":
                self._data[BAT_POWER] = \
                    float(self._data[BAT_POWER])*1000
                self._data[BAT_POWER_STR] = "W"    

            if self._data[BAT_TOTAL_ENERGY_CHARGED_STR] == "MWh":
                self._data[BAT_TOTAL_ENERGY_CHARGED] = \
                    float(self._data[BAT_TOTAL_ENERGY_CHARGED])*1000
                self._data[BAT_TOTAL_ENERGY_CHARGED_STR] = "kWh"    

            if self._data[BAT_TOTAL_ENERGY_DISCHARGED_STR] == "MWh":
                self._data[BAT_TOTAL_ENERGY_DISCHARGED] = \
                    float(self._data[BAT_TOTAL_ENERGY_DISCHARGED])*1000
                self._data[BAT_TOTAL_ENERGY_DISCHARGED_STR] = "kWh"    

            if self._data[GRID_TOTAL_CONSUMPTION_POWER_STR] == "kW":
                self._data[GRID_TOTAL_CONSUMPTION_POWER] = \
                    float(self._data[GRID_TOTAL_CONSUMPTION_POWER])*1000
                self._data[GRID_TOTAL_CONSUMPTION_POWER_STR] = "W"

            if self._data[GRID_TOTAL_ENERGY_USED_STR] == "MWh":
                self._data[GRID_TOTAL_ENERGY_USED] = \
                    float(self._data[GRID_TOTAL_ENERGY_USED])*1000
                self._data[GRID_TOTAL_ENERGY_USED_STR] = "kWh"
            elif self._data[GRID_TOTAL_ENERGY_USED_STR] == "GWh":
                self._data[GRID_TOTAL_ENERGY_USED] = \
                    float(self._data[GRID_TOTAL_ENERGY_USED])*1000*1000
                self._data[GRID_TOTAL_ENERGY_USED_STR] = "kWh"
                
            if self._data[INVERTER_ACPOWER_STR] == "kW":
                self._data[INVERTER_ACPOWER] = \
                    float(self._data[INVERTER_ACPOWER])*1000
                self._data[INVERTER_ACPOWER_STR] = "W"

            if self._data[INVERTER_ENERGY_THIS_YEAR_STR] == "MWh":
                self._data[INVERTER_ENERGY_THIS_YEAR] = \
                    float(self._data[INVERTER_ENERGY_THIS_YEAR])*1000
                self._data[INVERTER_ENERGY_THIS_YEAR_STR] = "kWh"

            if self._data[INVERTER_ENERGY_TOTAL_LIFE_STR] == "MWh":
                self._data[INVERTER_ENERGY_TOTAL_LIFE] = \
                    float(self._data[INVERTER_ENERGY_TOTAL_LIFE])*1000
                self._data[INVERTER_ENERGY_TOTAL_LIFE_STR] = "kWh"
            elif self._data[INVERTER_ENERGY_TOTAL_LIFE_STR] == "GWh":
                self._data[INVERTER_ENERGY_TOTAL_LIFE] = \
                    float(self._data[INVERTER_ENERGY_TOTAL_LIFE])*1000*1000
                self._data[INVERTER_ENERGY_TOTAL_LIFE_STR] = "kWh"

            # Just temporary till SolisCloud is fixed
            try:
                if self.config.workarounds['correct_daily_on_grid_energy_enabled']:
                    self._data[GRID_DAILY_ON_GRID_ENERGY] = \
                        float(self._data[GRID_DAILY_ON_GRID_ENERGY])*10
            except KeyError:
                pass
            
            # Unused phases are still in JSON payload as 0.0, remove them
            # FIXME: use acOutputType
            self._purge_if_unused(0.0, PHASE1_CURRENT, PHASE1_VOLTAGE)
            self._purge_if_unused(0.0, PHASE2_CURRENT, PHASE2_VOLTAGE)
            self._purge_if_unused(0.0, PHASE3_CURRENT, PHASE3_VOLTAGE)

            # Unused PV chains are still in JSON payload as 0, remove them
            # FIXME: use dcInputtype (NB num + 1) Unfortunately so are chains that are
            # just making 0 voltage. So this is too simplistic.
            # mypy trips over self_data[STRING_COUNT] as it could be of type str, int or float
            # needs to be fixed at some point in time, but this works.
            for i, stringlist in enumerate(STRING_LISTS):
                if i > int(self._data[STRING_COUNT]):
                    self._purge_if_unused(0, *stringlist)

    def _purge_if_unused(self, value: Any, *elements: str) -> None:
        for element in elements:
            try:
                if self._data[element] != value:
                    return
            except KeyError:
                return
        for element in elements:
            self._data.pop(element)

    def _get_value(self,
        data: dict[str, Any], key: str, type_: type, precision: int = 2
    ) -> str | int | float | None:
        """ Retrieve 'key' from 'data' as type 'type_' with precision 'precision' """
        result = None

        data_raw = data.get(key)
        if data_raw is not None:
            try:
                if type_ is int:
                    result = int(float(data_raw))
                else:
                    result = type_(data_raw)
                # Round to specified precision
                if type_ is float:
                    result = round(result, precision)
            except ValueError:
                _LOGGER.debug("Failed to convert %s to type %s, raw value = %s", key, type_, data_raw)
        return result

    async def _get_data(self,
            url: str,
            params: dict[str, Any]
        ) -> dict[str, Any]:
        """ Http-get data from specified url. """

        result: dict[str, Any] = {SUCCESS: False, MESSAGE: None, STATUS_CODE: None}
        resp = None
        if self._session is None:
            return result
        try:
            with async_timeout.timeout(10):
                resp = await self._session.get(url, params=params)

                result[STATUS_CODE] = resp.status
                result[CONTENT] = await resp.json()
                if resp.status == HTTPStatus.OK:
                    result[SUCCESS] = True
                    result[MESSAGE] = "OK"
                else:
                    result[MESSAGE] = "Got http statuscode: %d" % (resp.status)
                return result
        except (asyncio.TimeoutError, ClientError) as err:
            result[MESSAGE] = "Exception: %s" % err.__class__
            _LOGGER.debug("Error: %s", result[MESSAGE])
            return result
        finally:
            if resp is not None:
                await resp.release()

    def _prepare_header(self, body: dict[str, str], canonicalized_resource: str) -> dict[str, str]:
        now = datetime.now(timezone.utc)
        date = now.strftime("%a, %d %b %Y %H:%M:%S GMT")

        content_md5 = base64.b64encode(
            hashlib.md5(json.dumps(body,separators=(",", ":")).encode('utf-8')).digest()
        ).decode('utf-8')
        content_type = "application/json"

        encrypt_str = (VERB + "\n"
            + content_md5 + "\n"
            + content_type + "\n"
            + date + "\n"
            + canonicalized_resource
        )
        hmac_obj = hmac.new(
            self.config.secret,
            msg=encrypt_str.encode('utf-8'),
            digestmod=hashlib.sha1
        )
        sign = base64.b64encode(hmac_obj.digest())
        authorization = "API " + self.config.key_id + ":" + sign.decode('utf-8')
        header: dict [str, str] = {
            "Content-MD5":content_md5,
            "Content-Type":content_type,
            "Date":date,
            "Authorization":authorization
        }
        return header


    async def _post_data_json(self,
        canonicalized_resource: str,
        params: dict[str, Any]) -> dict[str, Any]:
        """ Http-post data to specified domain/canonicalized_resource. """

        header: dict[str, str] = self._prepare_header(params, canonicalized_resource)
        result: dict[str, Any] = {SUCCESS: False, MESSAGE: None}
        resp = None
        if self._session is None:
            return result
        try:
            with async_timeout.timeout(10):
                url = f"https://{self.config.domain}{canonicalized_resource}"
                resp = await self._session.post(url, json=params, headers=header)

                result[STATUS_CODE] = resp.status
                result[CONTENT] = await resp.json()
                if resp.status == HTTPStatus.OK:
                    result[SUCCESS] = True
                    result[MESSAGE] = "OK"
                else:
                    result[MESSAGE] = "Got http statuscode: %d" % (resp.status)

                return result
        except (asyncio.TimeoutError, ClientError) as err:
            result[MESSAGE] = "%s" % err
            _LOGGER.debug("Error from URI (%s) : %s", canonicalized_resource, result[MESSAGE])
            return result
        finally:
            if resp is not None:
                await resp.release()
