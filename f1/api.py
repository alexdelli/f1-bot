"""
Utilities to grab latest F1 results from Ergast API.
"""
import logging
import asyncio
from operator import itemgetter
from bs4 import BeautifulSoup
from datetime import datetime

from f1 import utils
from f1.errors import MissingDataError
from f1.fetch import fetch

BASE_URL = 'http://ergast.com/api/f1'

logger = logging.getLogger(__name__)


async def get_soup(url):
    """Request the URL and return response as BeautifulSoup object or None."""
    res = await fetch(url)
    if res is None:
        logger.warning('Unable to get soup, response was None.')
        return None
    return BeautifulSoup(res, 'lxml')


async def get_driver_info(driver_id):
    """Get the driver name, age, nationality, code and number.

    Parameters
    ----------
    `driver_id`
        must be a valid ID used by Ergast API, e.g. 'alonso', 'michael_schumacher'.

    Returns
    -------
    dict
        {
            'firstname': str,
            'surname': str,
            'code': str,
            'id': str,
            'url': str,
            'number': str,
            'age': int,
            'nationality': str
        }

    Raises
    ------
    `MissingDataError`
    """
    url = f'{BASE_URL}/drivers/{driver_id}'
    soup = await get_soup(url)
    if soup:
        driver = soup.find('driver')
        res = {
            'firstname': driver.givenname.string,
            'surname': driver.familyname.string,
            'code': driver['code'],
            'id': driver['driverid'],
            'url': driver['url'],
            'number': driver.permanentnumber.string,
            'age': utils.age(driver.dateofbirth.string[:4]),
            'nationality': driver.nationality.string,
        }
        return res
    raise MissingDataError()


async def get_driver_standings(season):
    """Get the driver championship standings.

    Fetches results from API. Response XML is parsed into a list of dicts to be tabulated.
    Data includes position, driver code, total points and wins.

    Parameters
    ----------
    `season` : int

    Returns
    -------
    `res` : dict
        {
            'season': str,
            'round': str,
            'data': list[dict] [{
                'Pos': int,
                'Driver': str,
                'Points': int,
                'Wins': int,
            }]
        }

    Raises
    ------
    `MissingDataError`
        if API response unavailable.
    """
    url = f'{BASE_URL}/{season}/driverStandings'
    soup = await get_soup(url)
    if soup:
        # tags are lowercase
        standings = soup.standingslist
        results = {
            'season': standings['season'],
            'round': standings['round'],
            'data': [],
        }
        for standing in standings.find_all('driverstanding'):
            results['data'].append(
                {
                    'Pos': int(standing['position']),
                    'Driver': standing.driver['code'],
                    'Points': int(standing['points']),
                    'Wins': int(standing['wins']),
                }
            )
        return results
    raise MissingDataError()


async def get_team_standings(season):
    """Get the constructor championship standings.

    Fetches results from API. Response XML is parsed into a list of dicts to be tabulated.
    Data includes position, team, total points and wins.

    Parameters
    ----------
    `season` : int

    Returns
    -------
    `res` : dict
        {
            'season': str,
            'round': str,
            'data': list[dict] [{
                'Pos': int,
                'Team': str,
                'Points': int,
                'Wins': int,
            }]
        }

    Raises
    ------
    `MissingDataError`
        if API response unavailable.
    """
    url = f'{BASE_URL}/{season}/constructorStandings'
    soup = await get_soup(url)
    if soup:
        standings = soup.standingslist
        results = {
            'season': standings['season'],
            'round': standings['round'],
            'data': [],
        }
        for standing in standings.find_all('constructorstanding'):
            results['data'].append(
                {
                    'Pos': int(standing['position']),
                    'Team': standing.constructor.find('name').string,
                    'Points': int(standing['points']),
                    'Wins': int(standing['wins']),
                }
            )
        return results
    raise MissingDataError()


async def get_all_drivers_and_teams(season):
    """Get all drivers and teams on the grid.

    Parameters
    ----------
    `season` : int

    Returns
    -------
    `res` : dict
        {
            'season': str,
            'round': str,
            'data': list[dict] [{
                'Code': str,
                'No': int,
                'Name': str,
                'Age': int,
                'Nationality': str,
                'Team': str,
            }]
        }

    Raises
    ------
    `MissingDataError`
        if API response unavailable.
    """
    url = f'{BASE_URL}/{season}/driverStandings'
    soup = await get_soup(url)
    if soup:
        standings = soup.find_all('driverstanding')
        results = {
            'season': soup.standingslist['season'],
            'round': soup.standingslist['round'],
            'data': []
        }
        for standing in standings:
            driver = standing.driver
            team = standing.constructor
            results['data'].append(
                {
                    'Code': driver['code'],
                    'No': int(driver.permanentnumber.string),
                    'Name': f'{driver.givenname.string} {driver.familyname.string}',
                    'Age': utils.age(driver.dateofbirth.string[:4]),
                    'Nationality': driver.nationality.string,
                    'Team': team.find('name').string,
                }
            )
        return results
    raise MissingDataError()


async def get_race_schedule():
    """Get full race calendar with circuit names and date as dict.

    Returns
    -------
    `res` : dict
        {
            'season': str,
            'data': list[dict] [{
                'Round': int,
                'Circuit': str,
                'Date': str,
                'Time': str,
                'Country': str,
            }]
        }

    Raises
    ------
    `MissingDataError`
        if API response unavailable.
    """
    url = f'{BASE_URL}/current'
    soup = await get_soup(url)
    if soup:
        races = soup.find_all('race')
        results = {
            'season': soup.racetable['season'],
            'data': []
        }
        for race in races:
            results['data'].append(
                {
                    'Round': int(race['round']),
                    'Circuit': race.circuit.circuitname.string,
                    'Date': utils.date_parser(race.date.string),
                    'Time': utils.time_parser(race.time.string),
                    'Country': race.location.country.string,
                }
            )
        return results
    raise MissingDataError()


async def get_next_race():
    """Get the next race in the calendar and a countdown (from moment of req) as dict.

    Returns
    -------
    `res` : dict
        {
            'season': str,
            'countdown': str,
            'url': str,
            'data': list[dict] [{
                'Round': int,
                'Name': str,
                'Date': str,
                'Time': str,
                'Circuit': str,
                'Country': str,
            }]
        }

    Raises
    ------
    `MissingDataError`
        if API response unavailable.
    """
    #  TODO - Get image of circuit

    url = f'{BASE_URL}/current/next'
    soup = await get_soup(url)
    if soup:
        race = soup.race
        date, time = (race.date.string, race.time.string)
        cd = utils.countdown(datetime.strptime(
            f'{date} {time}', '%Y-%m-%d %H:%M:%SZ'
        ))
        result = {
            'season': race['season'],
            'countdown': cd[0],
            'url': race['url'],
            'data': {
                'Round': int(race['round']),
                'Name': race.racename.string,
                'Date': f"{utils.date_parser(date)} {race['season']}",
                'Time': utils.time_parser(time),
                'Circuit': race.circuit.circuitname.string,
                'Country': race.location.country.string,
            }
        }
        return result
    raise MissingDataError()


async def get_race_results(rnd, season):
    """Get race results for `round` in `season` as dict.

    E.g. `get_race_results(12, 2008)` --> Results for 2008 season, round 12.

    Data includes finishing position, fastest lap, finish status, pit stops per driver.

    Parameters
    ----------
    `rnd` : int
    `season` : int

    Returns
    -------
    `res` : dict
        {
            'season': str,
            'round': str,
            'race': str,
            'url': str,
            'date': str,
            'time': str,
            'data': list[dict] [{
                'Pos': int,
                'Driver': str,
                'Team': str,
                'Laps': int,
                'Start': int,
                'Time': str,
                'Status': str,
                'Points': int,
            }],
            'timings': list[dict] [{
                'Rank': int,
                'Driver': str,
                'Time': str,
                'Speed': int,
            }]
        }

    Raises
    ------
    `MissingDataError`
        if API response unavailable.
    """
    url = f'{BASE_URL}/{season}/{rnd}/results'
    soup = await get_soup(url)
    if soup:
        race = soup.race
        race_results = race.resultslist.find_all('result')
        date, time = (race.date.string, race.time.string)
        res = {
            'season': race['season'],
            'round': race['round'],
            'race': race.racename.string,
            'url': race['url'],
            'date': f"{utils.date_parser(date)} {race['season']}",
            'time': utils.time_parser(time),
            'data': [],
            'timings': [],
        }
        for result in race_results:
            driver = result.driver
            # Finish time and fastest lap both use <time> tag, soup.find() will return first match
            # use sibling search instead to get second time
            finish_time = result.find_next_sibling('time')
            fastest_lap = result.fastestlap
            res['data'].append(
                {
                    'Pos': int(result['position']),
                    'Driver': f'{driver.givenname.string} {driver.familyname.string}',
                    'Team': result.constructor.find('name').string,
                    'Laps': int(result.laps.string),
                    'Start': int(result.grid.string),
                    # If DNF finish time will be missing so replace with None
                    'Time': None if finish_time is None else finish_time.string,
                    'Status': result.status.string,
                    'Points': int(result['points']),
                }
            )
            # Fastest lap data if available
            if fastest_lap is not None:
                res['timings'].append(
                    {
                        'Rank': int(fastest_lap['rank']),
                        'Driver': driver['code'],
                        'Time': fastest_lap.time.string,
                        'Speed': int(float(fastest_lap.averagespeed.string)),
                    }
                )
        return res
    raise MissingDataError()


async def get_all_driver_lap_times(driver_id, rnd, season):
    """Get the driver's lap times for each lap of the race.

    Each dict entry contains lap number, race position and lap time. The API can take time to
    process all of the lap time data.

    Parameters
    ----------
    `driver_id` : str
        must be a valid Ergast API id, e.g. 'alonso', 'di_resta'.
    `rnd` : int or str
        Round number or 'last' for the latest race
    `season` : int or str
        Season year or 'current'

    Returns
    -------
    res : dict
        {
            'driver': dict,
            'season': str,
            'round': str,
            'race': str,
            'url': str,
            'date': str,
            'time': str,
            'data': list[dict] [{
                'No': int,
                'Position': int,
                'Time': str,
            }]
        }

    Raises
    ------
    `MissingDataError`
        if response invalid.
    """
    url = f'{BASE_URL}/{season}/{rnd}/drivers/{driver_id}/laps'
    soup = await get_soup(url)
    if soup:
        race = soup.race
        laps = race.lapslist.find_all('lap')
        date, time = (race.date.string, race.time.string)
        res = {
            'driver': await get_driver_info(driver_id),
            'season': race['season'],
            'round': race['round'],
            'race': race.racename.string,
            'url': race['url'],
            'date': f"{utils.date_parser(date)} {race['season']}",
            'time': utils.time_parser(time),
            'data': []
        }
        for lap in laps:
            res['data'].append(
                {
                    'No': int(lap['number']),
                    'Position': int(lap.timing['position']),
                    'Time': lap.timing['time'],
                }
            )
        return res
    raise MissingDataError()


async def get_qualifying_results(rnd, season):
    """Gets qualifying results for `round` in `season`.

    E.g. `get_qualifying_results(12, 2008)` --> Results for round 12 in 2008 season.

    Data includes Q1, Q2, Q3 times per driver, position, laps per driver.

    Parameters
    ----------
    `rnd` : int or str
        Race number or 'last' for the latest race
    `season` : int or str
        Season year or 'current'

    Returns
    -------
    `res` : dict
        {
            'season': str,
            'round': str,
            'race': str,
            'url': str,
            'date': str,
            'time': str,
            'data': list[dict] [{
                'Pos': int,
                'Driver': str,
                'Team': str,
                'Q1': str,
                'Q2': str,
                'Q3': str,
            }]
        }

    Raises
    ------
    `MissingDataError`
        if API response invalid.
    """
    url = f'{BASE_URL}/{season}/{rnd}/qualifying'
    soup = await get_soup(url)
    if soup:
        race = soup.race
        quali_results = race.qualifyinglist.find_all('qualifyingresult')
        date, time = (race.date.string, race.time.string)
        res = {
            'season': race['season'],
            'round': race['round'],
            'race': race.racename.string,
            'url': race['url'],
            'date': f"{utils.date_parser(date)} {race['season']}",
            'time': utils.time_parser(time),
            'data': []
        }
        for result in quali_results:
            res['data'].append(
                {
                    'Pos': int(result['position']),
                    'Driver': result.driver['code'],
                    'Team': result.constructor.find('name').string,
                    'Q1': result.q1.string if result.q1 is not None else None,
                    'Q2': result.q2.string if result.q2 is not None else None,
                    'Q3': result.q3.string if result.q3 is not None else None,
                }
            )
        return res
    raise MissingDataError()


async def get_driver_wins(driver_id):
    """Get total wins for the driver and a list of dicts with details for each race.

    Parameters
    ----------
    `driver_id` : str
        must be valid Eargast API ID, e.g. 'alonso', 'michael_schumacher'.

    Returns
    -------
    `res` : dict
        {
            'total': int,
            'driver': dict,
            'data': list[dict] [{
                'Race': str,
                'Circuit': str,
                'Date': str,
                'Team': str,
                'Grid': int,
                'Laps': int,
                'Time': str,
            }]
        }

    Raises
    ------
    `MissingDataError`
        if API response invalid.
    """
    url = f'{BASE_URL}/drivers/{driver_id}/results/1'
    soup = await get_soup(url)
    if soup:
        races = soup.racetable.find_all('race')
        res = {
            'total': int(soup.MRData['total']),
            'driver': await get_driver_info(driver_id),
            'data': []
        }
        for race in races:
            race_result = race.racelist.result
            res['data'].append(
                {
                    'Race': race.racename.string,
                    'Circuit': race.circuitname.string,
                    'Date': utils.date_parser(race.date.string),
                    'Team': race_result.constructor.name.string,
                    'Grid': int(race_result.grid.string),
                    'Laps': int(race_result.laps.string),
                    'Time': race_result.time.string,
                }
            )
        return res
    return MissingDataError()


async def get_driver_poles(driver_id):
    """Get total pole positions for driver with details for each race.

    Parameters
    ----------
    `driver_id` : str
        must be valid Eargast API ID, e.g. 'alonso', 'michael_schumacher'.

    Returns
    -------
    `res` : dict
        {
            'total': int,
            'driver': dict,
            'data': list[dict] [{
                'Race': str,
                'Circuit': str,
                'Date': str,
                'Team': str,
                'Q1': str,
                'Q2': str,
                'Q3': str,
            }]
        }

    Raises
    ------
    `MissingDataError`
        if API response invalid.
    """
    url = f'{BASE_URL}/drivers/{driver_id}/grid/1'
    soup = await get_soup(url)
    if soup:
        races = soup.racetable.find_all('race')
        res = {
            'total':  int(soup.MRData['total']),
            'driver': await get_driver_info(driver_id),
            'data': []
        }
        for race in races:
            quali_result = race.qualifyinglist.qualifyingresult
            res['data'].append(
                {
                    'Race': race.racename.string,
                    'Circuit': race.circuitname.string,
                    'Date': utils.date_parser(race.date.string),
                    'Team': quali_result.constructor.name.string,
                    'Q1': quali_result.q1.string,
                    'Q2': quali_result.q2.string,
                    'Q3': quali_result.q3.string,
                }
            )
        return res
    return MissingDataError()


async def get_driver_championships(driver_id):
    """Get total championship wins for the driver and details for each season, team, points and wins.

    Parameters
    ----------
    `driver_id` : str
        must be valid Eargast API ID, e.g. 'alonso', 'michael_schumacher'.

    Returns
    -------
    `res` : dict
        {
            'total': int,
            'driver': dict,
            'data': list[dict] [{
                'Season': str,
                'Points': int,
                'Wins': int,
                'Team': str,
            }]
        }

    Raises
    ------
    `MissingDataError`
        if API response invalid.
    """
    url = f'{BASE_URL}/drivers/{driver_id}/driverStandings/1'
    soup = await get_soup(url)
    if soup:
        standings = soup.standingstable.find_all('standingslist')
        res = {
            'total': int(soup.MRData['total']),
            'driver': await get_driver_info(driver_id),
            'data': []
        }
        for standing in standings:
            res['data'].append(
                {
                    'Season': standing['season'],
                    'Points': int(standing.driverstanding['points']),
                    'Wins': int(standing.driverstanding['wins']),
                    'Team': standing.driverstanding.constructor.name.string,
                }
            )
        return res
    raise MissingDataError()


async def get_driver_teams(driver_id):
    """Get total number of teams the driver has driven for and a list of names.

    Parameters
    ----------
    `driver_id` : str
        must be valid Eargast API ID, e.g. 'alonso', 'michael_schumacher'.

    Returns
    -------
    `res` : dict
        {
            'total': int,
            'names': list,
        }

    Raises
    ------
    `MissingDataError`
        if API response invalid.
    """
    url = f'{BASE_URL}/drivers/{driver_id}/constructors'
    soup = await get_soup(url)
    if soup:
        constructors = soup.constructortable.find_all('constructor')
        res = {
            'total': int(soup.MRData['total']),
            'names': [constructor.name.string for constructor in constructors]
        }
        return res
    return MissingDataError()


async def get_driver_seasons(driver_id):
    """Get the total number of seasons in F1 and a list of dicts with year, team, and pos.

    The Ergast API is queried for all driver championships that `driver_id` has participated in, which may cause a
    slight delay in processing for veteran drivers with many seasons.

    Parameters
    ----------
    `driver_id` : str
        must be valid Eargast API ID, e.g. 'alonso', 'michael_schumacher'.

    Returns
    -------
    `res` : dict
        {
            'total': int,
            'data': list[dict] [{
                'Season': str,
                'Pos': int,
                'Team': str,
            }],
        }

    Raises
    ------
    `MissingDataError`
        if API response invalid.
    """
    url = f'{BASE_URL}/drivers/{driver_id}/driverStandings'
    soup = await get_soup(url)
    if soup:
        standings = soup.standingstable.find_all('standingslist')
        res = {
            'total': int(soup.MRData['total']),
            'data': []
        }
        for standing in standings:
            res['data'].append(
                {
                    'Season': standing['season'],
                    'Pos': int(standing['position']),
                    'Team': standing.constructor.name.string,
                }
            )
        return res
    raise MissingDataError()


async def get_driver_career(driver_id):
    """Total wins, poles, points, seasons, teams and DNF's for the driver.

    Parameters
    ----------
    `driver_id` : str
        Must be valid, e.g. 'alonso', 'vettel', 'di_resta'.

    Returns
    -------
    `res` : dict
        {
            'driver': str,
            'data': dict {
                'Wins': int,
                'Poles': int,
                'Championships': dict {
                    'total': int,
                    'years': list
                },
                'Seasons': dict {
                    'total': int,
                    'years': list
                },
                'Teams': dict {
                    'total': int,
                    'names': list
                }
            }
        }

    Raises
    ------
    `MissingDataError`
        if API response invalid.
    """
    # Get results concurrently
    [wins, poles, champs, seasons, teams] = await asyncio.gather(
        get_driver_wins(driver_id),
        get_driver_poles(driver_id),
        get_driver_championships(driver_id),
        get_driver_seasons(driver_id),
        get_driver_teams(driver_id),
    )
    res = {
        'driver': wins['driver'],
        'data': {
            'Wins': wins['total'],
            'Poles': poles['total'],
            'Championships': {
                'total': champs['total'],
                'years': [x['Season'] for x in champs['data']],
            },
            'Seasons': {
                'total': seasons['total'],
                'years': [x['Season'] for x in seasons['data']],
            },
            'Teams': {
                'total': teams['total'],
                'names': teams['names'],
            },
        }
    }
    return res


async def get_best_laps(rnd, season):
    """Get the best lap for each driver.

    Parameters
    ----------
    `rnd` : int or str
        Race number or 'last' for the latest race
    `season` : int or str
        Season year or 'current'

    Returns
    -------
    `res` : dict
        {   'season': str,
            'round': str,
            'race': str,
            'data': list[dict] {
                'Rank': int,
                'Driver': str,
                'Time': str,
                'Speed': str,
            }
        }

    Raises
    ------
    `MissingDataError`
        If response invalid.
    """
    race_results = await get_race_results(rnd, season)
    res = {
        'season': race_results['season'],
        'round': race_results['round'],
        'race': race_results['race'],
        'data': race_results['timings'],
    }
    return res


async def rank_best_lap_times(data, filter):
    """Returns filtered best lap times per driver based on data obtained
    from `get_race_results()`.

    Sorts the list of lap times returned by `get_race_results()` dataset and splits
    the results based on the filter keyword.

    Parameters
    ----------
    `data` : list
        Returned data from `get_race_results()`.
    `filter` : str
        Type of filter to be applied:
            'slowest' - slowest lap of race
            'fastest' - fastest lap of race
            'top'     - top 5 fastest drivers
            'bottom'  - bottom 5 slowest drivers

    Returns
    -------
    list[dict]
        Sorted list of dicts for each lap
    """
    sorted_times = sorted(data['timings'], key=itemgetter('Rank'))
    # slowest lap
    if filter is 'slowest':
        return sorted_times[len(sorted_times) - 1]
    # fastest lap
    elif filter is 'fastest':
        return sorted_times[0]
    # fastest 5 laps
    elif filter is 'top':
        return sorted_times[:5]
    # slowest 5 laps
    elif filter is 'bottom':
        return sorted_times[len(sorted_times) - 5:]
    # no filter given, return full sorted results
    else:
        return sorted_times
