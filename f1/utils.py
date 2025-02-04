import json
import logging
from operator import itemgetter
from datetime import date, datetime

import pandas as pd
from tabulate import tabulate
from discord import ApplicationContext, Colour
from discord.ext import commands
from f1.api.fetch import fetch

from f1.target import MessageTarget
from f1.config import CACHE_DIR
from f1.errors import MessageTooLongError, DriverNotFoundError

logger = logging.getLogger("f1-bot")

F1_RED = Colour.from_rgb(226, 36, 32)


async def check_season(ctx: commands.Context | ApplicationContext, season):
    """Raise error if the given season is in the future. Otherwise returns the year as an int."""
    if is_future(season):
        tgt = MessageTarget(ctx)
        await tgt.send("Can't predict future :thinking:")
        raise commands.BadArgument('Given season is in the future.')


def convert_season(season):
    """Return the season as an int, works for 'current' season."""
    if season == "current":
        return current_year()
    return int(season)


def sprint_qual_type(season):
    """Get the name used for the Saturday sprint qualifying session.

    Naming between 2021-2022 is synonymous. 2023+ uses new format.
    """
    s_int = convert_season(season)
    if s_int < 2023:
        return 'Sprint'
    return 'Sprint Shootout'


def contains(first, second):
    """Returns True if any item in `first` matches an item in `second`."""
    return any(i in first for i in second)


def is_future(year):
    """Return True if `year` is greater than current year."""
    if year == 'current':
        return False
    return datetime.now().year < int(year)


def too_long(message: str):
    """Returns True if the message exceeds discord's 2000 character limit."""
    return len(message) >= 2000


def make_table(data, headers='keys', fmt='fancy_grid', **kwargs):
    """Tabulate data into an ASCII table. Return value is a str.

    The `fmt` param defaults to 'fancy_grid' which includes borders for cells. If the table exceeds
    Discord message limit the table is rebuilt with borders removed.

    If still too large raise `MessageTooLongError`.
    """
    table = tabulate(data, headers=headers, tablefmt=fmt, **kwargs)
    # remove cell borders if too long
    if too_long(table):
        table = tabulate(data, headers=headers, tablefmt='plain', **kwargs)
        # cannot send table if too large even without borders
        if too_long(table):
            raise MessageTooLongError('Table too large to send.', table)
    return table


def current_year():
    return date.today().year


def age(yob):
    if current_year() < int(yob):
        return 0
    return current_year() - int(yob)


def date_parser(date_str):
    return datetime.strptime(date_str, '%Y-%m-%d').strftime('%d %b')


def time_parser(time_str):
    return datetime.strptime(time_str, '%H:%M:%SZ').strftime('%H:%M UTC')


def pluralize(number: int, singular: str, plural: str = None):
    if not plural:
        plural = singular + 's'
    return f"{number} {singular if number == 1 else plural}"


def countdown(target: datetime):
    """
    Calculate time to `target` datetime object from current time when invoked.
    Returns a list containing the string output and tuple of (days, hrs, mins, sec).
    """
    delta = target - datetime.now()
    d = delta.days if delta.days > 0 else 0
    # timedelta only stores seconds so calculate mins and hours by dividing remainder
    h, rem = divmod(delta.seconds, 3600)
    m, s = divmod(rem, 60)
    # text representation
    stringify = (
        f"{pluralize(int(d), 'day')}, "
        f"{pluralize(int(h), 'hour')}, "
        f"{pluralize(int(m), 'minute')}, "
        f"{pluralize(int(s), 'second')} "
    )
    return [stringify, (d, h, m, s)]


def format_timedelta(delta: pd.Timedelta, hours=False):
    """Get a time string in `[%H]:%M:%S.%f` with a precision of 3 places. If not a valid time the string is empty."""
    if pd.isna(delta):
        return ''
    return (datetime.min + delta).strftime(f"{'%H:' if hours else ''}%M:%S.%f").lstrip('0')[:-3]


def lap_time_to_seconds(time_str: str):
    """Returns the lap time string as a float representing total seconds.

    E.g. '1:30.202' -> 90.202
    """
    min, secs = time_str.split(':')
    total = int(min) * 60 + float(secs)
    return total


def load_drivers():
    """Load drivers JSON from file and return as dict."""
    with open(CACHE_DIR.joinpath('drivers.json'), 'r', encoding='utf-8') as f:
        data = json.load(f)
        DRIVERS = data['MRData']['DriverTable']['Drivers']
        logger.info('Drivers loaded.')
        return DRIVERS


def find_driver(id: str, drivers: list[dict]):
    """Find the driver entry and return as a dict.

    Parameters
    ----------
    `id` : str
        Can be either a valid Ergast API ID e.g. 'alonso', 'max_verstappen' or the
        driver code e.g. 'HAM' or the driver number e.g. '44'.
    `drivers` : list[dict]
        The drivers dataset to search.

    Returns
    -------
    `driver` : dict

    Raises
    ------
    `DriverNotFoundError`
    """
    for d in drivers:
        if str(id).casefold() in (str(v).casefold() for v in d.values()):
            return d
        continue
    raise DriverNotFoundError()


def rank_best_lap_times(timings):
    """Sorts the list of lap times returned by `api.get_best_laps()` dataset."""
    sorted_times = sorted(timings['data'], key=itemgetter('Rank'))
    return sorted_times


def rank_pitstops(times):
    """Sort pitstop times based on the duration. `times` is the response from `api.get_pitstops()`."""
    sorted_times = sorted(times['data'], key=itemgetter('Duration'))
    return sorted_times


def filter_laps_by_driver(laps, drivers):
    """Filter lap time data to get only laps driven by the driver in `drivers`.

    Parameters
    ----------
    `laps` : dict
        Timings for each driver per lap as returned by `api.get_all_laps` data key
    `*drivers` : list
        A valid driver_id used by Ergast API

    Returns
    -------
    dict
        `laps` filtered to contain only a list of timings per lap for the specified drivers
    """
    if len(drivers) == 0:
        return laps
    else:
        result = {
            'data': {},
            'race': laps.get('race', ''),
            'season': laps.get('season', ''),
            'round': laps.get('round', '')
        }

        for lap, times in laps['data'].items():
            result['data'][lap] = [t for t in times if t['id'] in drivers]
        return result


def filter_times(sorted_times, filter: str | None):
    """Filters the list of times by the filter keyword. If no filter is given the
    times are returned unfiltered.

    Parameters
    -----------
    `sorted_times` : list
        Collection of already sorted items, e.g. pitstops or laptimes data.
    `filter` : str
        The type of filter to apply;
            'slowest' - single slowest time
            'fastest' - single fastest time
            'top'     - top 5 fastest times
            'bottom'  - bottom 5 slowest times

    Returns
    -------
    list
        A subset of the `sorted_times` according to the filter.
    """
    # Force list return type instead of pulling out single string element for slowest and fastest
    # Top/Bottom already outputs a list type with slicing
    # slowest
    if filter == 'slowest':
        return [sorted_times[len(sorted_times) - 1]]
    # fastest
    elif filter == 'fastest':
        return [sorted_times[0]]
    # fastest 5
    elif 'top' in filter:
        return sorted_times[:5]
    # slowest 5
    elif 'bottom' in filter:
        return sorted_times[len(sorted_times) - 5:]
    # no filter given, return full sorted results
    else:
        return sorted_times


def keep_fastest(lst: list[dict], key: str):
    """Checks list of sorted timing data e.g. pitstops and removes duplicates to
    keep only the fastest entry.

    Parameters
    ----------
    `lst` : list[dict]
        Collection of *sorted* timing entries per driver, e.g. from `utils.rank_pitstops`.
    `key` : str
        Dict key to sort by.

    Returns
    ----------
    list[dict]
        A new list of entries with duplicates removed where only the lower `key` values are kept.
    """
    seen = {}
    for d in lst:
        if d['Driver'] not in seen or d[key] < seen[d['Driver']][key]:
            seen[d['Driver']] = d
    return list(seen.values())


async def get_wiki_thumbnail(url: str):
    """Get image thumbnail from Wikipedia link. Returns the thumbnail URL."""
    if url is None or url == '':
        return 'https://i.imgur.com/kvZYOue.png'
    # Get URL name after the first '/'
    wiki_title = url.rsplit('/', 1)[1]
    # Get page thumbnail from wikipedia API if it exists
    api_query = ('https://en.wikipedia.org/w/api.php?action=query&format=json&formatversion=2' +
                 '&prop=pageimages&piprop=thumbnail&pithumbsize=600' + f'&titles={wiki_title}')
    res = await fetch(api_query)
    first = res['query']['pages'][0]
    # Get page thumb or return placeholder
    if 'thumbnail' in first:
        return first['thumbnail']['source']
    else:
        return 'https://i.imgur.com/kvZYOue.png'
