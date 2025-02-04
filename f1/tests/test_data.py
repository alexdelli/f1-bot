import re
import unittest
from unittest.mock import patch
from datetime import date, datetime

import pandas as pd
from discord.ext.commands import Bot
from aiohttp_client_cache import CachedSession

from f1 import utils
from f1.api import ergast, fetch
from f1.config import Config
from f1.errors import MissingDataError, MessageTooLongError, DriverNotFoundError
from f1.tests.async_test import async_test
from f1.tests.mock_response.response import models, get_mock_response

# Path for patch should be module where it is used, not where defined
fetch_path = 'f1.api.ergast.fetch'


class BaseTest(unittest.TestCase):
    """Base testing class."""

    def check_data(self, data):
        self.assertTrue(len(data) > 0, "Results are empty.")
        self.assertNotIn(None, [i for i in data[0]], "None values present. Check keys.")

    def check_total_and_num_results(self, total, data):
        self.assertTrue(isinstance(total, int), "Total is not valid.")
        self.assertEqual(total, len(data), "Total and number of results don't match.")


class ConfigTests(BaseTest):

    def test_config_singleton(self):
        c1 = Config()
        c2 = Config()
        self.assertIs(c1, c2)

    def test_config_data(self):
        cfg = Config()
        data = cfg.settings['BOT']['PREFIX']
        self.assertIsNotNone(data)
        self.assertIsInstance(data, str)

    def test_config_bot_instance(self):
        cfg = Config()
        bot = cfg.bot
        self.assertIsInstance(bot, Bot)


class UtilityTests(BaseTest):
    """Testing utility functions not tied to API data."""

    def test_driver_age(self):
        age_str = '1981-07-29'
        age = utils.age(age_str[:4])
        # arbirary check for extreme values, active F1 drivers avg 18-40
        self.assertTrue(int(age) > 0 and int(age) < 99, "Age not valid.")

    def test_age_with_future_yob(self):
        yob = '3000'
        self.assertEqual(utils.age(yob), 0)

    def test_message_too_long_raises_exception(self):
        # discord limit 2000 chars
        msg = ['x'] * 3000
        with self.assertRaises(MessageTooLongError):
            utils.make_table(msg, headers='first_row')

    def test_is_future_with_future_year(self):
        year = '3000'
        self.assertTrue(utils.is_future(year))

    def test_is_future_with_past_year(self):
        year = '1999'
        self.assertFalse(utils.is_future(year))

    def test_lap_time_to_seconds(self):
        laps = ['1:30.202', '1:29.505', '0:00.000']
        seconds = [utils.lap_time_to_seconds(x) for x in laps]
        self.assertEqual(seconds[0], 90.202)
        self.assertEqual(seconds[1], 89.505)
        self.assertEqual(seconds[2], 0.0)

    def test_rank_best_lap_times(self):
        times = models.best_laps
        sorted_times = utils.rank_best_lap_times(times)
        self.assertTrue(sorted_times[0]['Rank'] == 1)
        prev_rank = 0
        self.assertTrue(t['Rank'] > prev_rank for t in sorted_times)

    def test_filter_laps(self):
        laps = {'data': {
            1: [{'id': 'alonso', 'pos': 1, 'time': '1:30.202'},
                {'id': 'vettel', 'pos': 2, 'time': '1:30.205'},
                {'id': 'bottas', 'pos': 3, 'time': '1:30.205'}],
            2: [{'id': 'alonso', 'pos': 2, 'time': '1:30.102'},
                {'id': 'vettel', 'pos': 1, 'time': '1:29.905'},
                {'id': 'bottas', 'pos': 3, 'time': '1:30.105'}]
        }}
        filtered_laps = utils.filter_laps_by_driver(laps, ['vettel'])
        # Only one driver given, so check only one timing
        self.assertEqual(len(filtered_laps['data'][1]), 1, "Timing entries for 1 driver arg don't match result.")
        # Check driver matches
        self.assertEqual(filtered_laps['data'][1][0]['id'], 'vettel', "Driver ID doesn't match provided arg.")

    def test_filter_laps_multiple_drivers(self):
        laps = {'data': {
            1: [{'id': 'alonso', 'pos': 1, 'time': '1:30.202'},
                {'id': 'vettel', 'pos': 2, 'time': '1:30.205'},
                {'id': 'bottas', 'pos': 3, 'time': '1:30.205'}],
            2: [{'id': 'alonso', 'pos': 2, 'time': '1:30.102'},
                {'id': 'vettel', 'pos': 1, 'time': '1:29.905'},
                {'id': 'bottas', 'pos': 3, 'time': '1:30.105'}]
        }}
        filtered_laps = utils.filter_laps_by_driver(laps, ['alonso', 'vettel'])
        # Two drivers given, check timings for both
        self.assertEqual(len(filtered_laps['data'][1]), 2, "Timing entries for 2 drivers args don't match result.")
        # Check the drivers
        self.assertEqual(filtered_laps['data'][1][0]['id'], 'alonso')
        self.assertEqual(filtered_laps['data'][1][1]['id'], 'vettel')

    def test_filter_times(self):
        times = models.best_laps
        sorted_times = utils.rank_best_lap_times(times)
        [fast, slow, top, bottom] = [
            utils.filter_times(sorted_times, 'fastest'),
            utils.filter_times(sorted_times, 'slowest'),
            utils.filter_times(sorted_times, 'top'),
            utils.filter_times(sorted_times, 'bottom')
        ]
        # Check lengths
        self.assertEqual(len(fast), 1, "Fastest filter should return 1 item.")
        self.assertEqual(len(slow), 1, "Slowest filter should return 1 item.")
        self.assertEqual(len(top), 5, "Should return top 5.")
        self.assertEqual(len(bottom), 5, "Should return bottom 5.")
        # Compare data with mocked model data which has 7 laps
        self.assertEqual(fast[0]['Rank'], 1, "Fastest should return top rank.")
        self.assertEqual(slow[0]['Rank'], 7, "Slowest should return bottom rank.")

    def test_countdown_with_past_date(self):
        past_date = datetime(1999, 1, 1)
        result = utils.countdown(past_date)
        countdown_str = result[0]
        d, h, m, s = result[1]
        self.assertTrue(d == 0, "No of days for past date should be zero.")
        self.assertTrue(re.findall(r'(\d+ days?|\d+ hours?|\d+ minutes?|\d+ seconds?)+',
                                   countdown_str), "Invalid string output.")

    def test_remove_driver_duplicates_from_timing(self):
        timing_data = [
            {'Driver': "ALO", 'time': "1:15.200"},
            {'Driver': "ALO", 'time': "1:15.300"},
            {'Driver': "VER", 'time': "1:15.310"},
            {'Driver': "HAM", 'time': "1:16.200"},
            {'Driver': "VER", 'time': "1:15.311"},
        ]
        expected = [{'Driver': "ALO", 'time': "1:15.200"},
                    {'Driver': "VER", 'time': "1:15.310"},
                    {'Driver': "HAM", 'time': "1:16.200"}]
        res = utils.keep_fastest(timing_data, "time")
        self.assertEqual(res, expected, "Duplicates should be removed, keeping lowest key value.")

    def test_convert_season(self):
        year = "2023"
        expected = 2023
        self.assertEqual(utils.convert_season(year), expected)

    def test_convert_season_current(self):
        year = "current"
        expected = date.today().year
        self.assertEqual(utils.convert_season(year), expected)

    def test_sprint_qual_type(self):
        year_2021 = 2021
        year_2022 = 2022
        year_current = "current"
        self.assertEqual(utils.sprint_qual_type(year_2021), "Sprint")
        self.assertEqual(utils.sprint_qual_type(year_2022), "Sprint")
        self.assertEqual(utils.sprint_qual_type(year_current), "Sprint Shootout")

    def test_format_timedelta(self):
        td = pd.Timedelta("0 days 01:27:46.548296")
        expected = "27:46.548"
        with_hours = "1:27:46.548"
        self.assertEqual(utils.format_timedelta(td), expected)
        self.assertEqual(utils.format_timedelta(td, hours=True), with_hours)

    def test_format_timedelta_nan(self):
        td = pd.Timedelta("")
        self.assertEqual(utils.format_timedelta(td), "")


class MockAPITests(BaseTest):
    """Using mock data models to test response parsing and data output."""

    @patch(fetch_path)
    @async_test
    async def test_get_driver_info(self, mock_fetch):
        mock_fetch.return_value = models.driver_info_json
        res_with_id = await ergast.get_driver_info('alonso')
        res_with_no = await ergast.get_driver_info('14')
        res_with_code = await ergast.get_driver_info('ALO')
        self.assertEqual(res_with_id['id'], 'alonso')
        self.assertEqual(res_with_no['id'], 'alonso')
        self.assertEqual(res_with_no['number'], '14')
        self.assertEqual(res_with_code['id'], 'alonso')
        self.assertEqual(res_with_code['code'], 'ALO')

    @patch(fetch_path)
    @async_test
    async def test_get_driver_info_with_invalid_driver(self, mock_fetch):
        mock_fetch.return_value = models.driver_info_json
        with self.assertRaises(DriverNotFoundError):
            await ergast.get_driver_info('smc12')

    @patch(fetch_path)
    @async_test
    async def test_none_result_raises_error(self, mock_fetch):
        # return None to simulate invalid API response
        mock_fetch.return_value = get_mock_response(None)
        with self.assertRaises(MissingDataError):
            await ergast.get_driver_standings('current')

    @patch(fetch_path)
    @async_test
    async def test_get_driver_standings(self, mock_fetch):
        mock_fetch.return_value = get_mock_response('driver_standings')
        res = await ergast.get_driver_standings('current')
        self.check_data(res['data'])

    @patch(fetch_path)
    @async_test
    async def test_get_team_standings(self, mock_fetch):
        mock_fetch.return_value = get_mock_response('constructor_standings')
        res = await ergast.get_team_standings('current')
        self.check_data(res['data'])

    @patch(fetch_path)
    @async_test
    async def test_get_race_results(self, mock_fetch):
        mock_fetch.return_value = get_mock_response('race_results')
        res = await ergast.get_race_results('last', 'current')
        self.check_data(res['data'])
        self.check_data(res['timings'])

    @patch(fetch_path)
    @async_test
    async def test_get_qualifying_results(self, mock_fetch):
        mock_fetch.return_value = get_mock_response('qualifying_results')
        res = await ergast.get_qualifying_results('last', 'current')
        self.check_data(res['data'])

    @patch(fetch_path)
    @async_test
    async def test_get_all_laps(self, mock_fetch):
        mock_fetch.return_value = get_mock_response('all_laps')
        res = await ergast.get_all_laps(1, 2019)
        self.assertNotIn(None, res['data'][1])

    @patch(fetch_path)
    @async_test
    async def test_get_all_laps_for_driver(self, mock_fetch):
        mock_fetch.side_effect = [models.driver_info_json, get_mock_response('all_laps')]
        driver = await ergast.get_driver_info('alonso')
        laps = await ergast.get_all_laps(15, 2008)
        res = await ergast.get_all_laps_for_driver(driver, laps)
        self.check_data(res['data'])
        self.assertEqual(res['data'][0]['Lap'], 1, "First lap should be 1.")
        self.assertEqual(res['driver']['surname'], 'Alonso', "Driver doesn't match that provided.")

    @patch(fetch_path)
    @async_test
    async def test_get_pitstops(self, mock_fetch):
        mock_fetch.side_effect = [
            get_mock_response('pitstops'),
            get_mock_response('race_results'),
            models.driver_info_json,
            models.driver_info_json,
            models.driver_info_json]
        res = await ergast.get_pitstops('last', 'current')
        self.check_data(res['data'])

    # test career
    @patch(fetch_path)
    @async_test
    async def test_get_driver_wins(self, mock_fetch):
        mock_fetch.return_value = get_mock_response('driver_wins')
        res = await ergast.get_driver_wins('alonso')
        self.check_data(res['data'])
        self.check_total_and_num_results(res['total'], res['data'])

    @patch(fetch_path)
    @async_test
    async def test_get_driver_poles(self, mock_fetch):
        mock_fetch.return_value = get_mock_response('driver_poles')
        res = await ergast.get_driver_poles('alonso')
        self.check_data(res['data'])
        self.check_total_and_num_results(res['total'], res['data'])

    @patch(fetch_path)
    @async_test
    async def test_get_driver_seasons(self, mock_fetch):
        mock_fetch.return_value = get_mock_response('driver_seasons')
        res = await ergast.get_driver_seasons('alonso')
        self.check_data(res['data'])
        self.assertEqual(len(res['data']), 1)
        self.assertTrue(res['data'][0]['year'] == 2001)

    @patch(fetch_path)
    @async_test
    async def test_get_driver_teams(self, mock_fetch):
        mock_fetch.return_value = get_mock_response('driver_teams')
        res = await ergast.get_driver_teams('alonso')
        self.assertTrue(res['data'], "Results empty.")
        self.assertEqual(len(res['data']), 1)
        self.assertTrue(res['data'][0] == 'Ferrari')

    @patch(fetch_path)
    @async_test
    async def test_get_driver_career(self, mock_fetch):
        mock_fetch.side_effect = [
            models.driver_info_json,
            get_mock_response('driver_championships'),
            get_mock_response('driver_wins'),
            get_mock_response('driver_poles'),
            get_mock_response('driver_seasons'),
            get_mock_response('driver_teams'),
        ]
        driver = await ergast.get_driver_info('alonso')
        res = await ergast.get_driver_career(driver)
        self.assertEqual(res['driver']['surname'], 'Alonso')
        # Check length of results
        data = res['data']
        self.check_total_and_num_results(data['Championships']['total'], data['Championships']['years'])
        self.check_total_and_num_results(data['Seasons']['total'], data['Seasons']['years'])
        self.check_total_and_num_results(data['Teams']['total'], data['Teams']['names'])

    # boundary tests


class LiveAPITests(BaseTest):
    """Using real requests to check API status, validate response structure and error handling."""

    def setUp(self):
        fetch.use_cache = False

    @async_test
    async def test_response_structure(self):
        # test response for alonso info against mocked data
        # Get BeautifulSoup obj from API response to test tags
        actual_result = await ergast.get_soup(f'{ergast.BASE_URL}/drivers/alonso')
        with patch(fetch_path) as mock_get:
            mock_get.return_value = get_mock_response('driver_info_xml')
            # url never used as fetch is mocked
            expected_result = await ergast.get_soup('mock_url')
        # Check root tag of real response body for changes
        self.assertTrue(actual_result.body.find('mrdata'),
                        "Parent response tag not as expected, API may have changed.")
        # Check tag structure matches for real and mocked
        self.assertEqual(expected_result.drivertable, actual_result.drivertable,
                         "Expected and actual tags don't match. Check API data structure.")

    @async_test
    async def test_get_past_race_results(self):
        past_res = await ergast.get_race_results('12', '2017')
        self.check_data(past_res['data'])
        self.assertEqual(past_res['season'], '2017', "Requested season and result don't match.")
        self.assertEqual(past_res['round'], '12', "Requested round and result don't match.")

    @async_test
    async def test_get_next_race_countdown(self):
        res = await ergast.get_next_race()
        time = res['data']['Time']
        date = res['data']['Date']
        self.assertTrue(res['data'], "Results empty.")
        self.assertTrue(datetime.strptime(date, '%d %b %Y'), "Date not valid.")
        self.assertTrue(datetime.strptime(time, '%H:%M UTC'), "Time not valid.")

    @async_test
    async def test_cached_results(self):
        url = "https://ergast.com/api/f1/current/next.json"
        async with CachedSession(cache=fetch.cache) as session:
            res = await session.get(url=url, expire_after=60)
            self.assertEqual(res.from_cache, False)
            cached_res = await session.get(url=url, expire_after=10)
            self.assertEqual(cached_res.from_cache, True)

    def tearDown(self):
        fetch.use_cache = True
