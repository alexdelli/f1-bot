import asyncio
import logging

import fastf1 as ff1
import pandas as pd
from fastf1.core import Session, SessionResults
from fastf1.ergast import Ergast
from fastf1.events import Event

from f1 import utils
from f1.api import ergast
from f1.errors import MissingDataError

logger = logging.getLogger("f1-bot")

ff1_erg = Ergast()


async def to_event(year: str, rnd: str) -> Event:
    """Get a `fastf1.events.Event` for a race weekend corresponding to `year` and `round`.

    Handles conversion of "last" round and "current" season from Ergast API.

    The `round` can also be a GP name or circuit.
    """
    # Get the actual round id from the last race endpoint
    if rnd == "last":
        data = await ergast.race_info(year, "last")
        rnd = data["round"]

    if str(rnd).isdigit():
        rnd = int(rnd)

    try:
        event = await asyncio.to_thread(ff1.get_event, year=utils.convert_season(year), gp=rnd)
    except Exception:
        raise MissingDataError()

    return event


async def load_session(event: Event, name: str, **kwargs) -> Session:
    """Searches for a matching `Session` using `name` (session name, abbreviation or number).

    Loads and returns the `Session`.
    """
    try:
        # Run FF1 blocking I/O in async thread so the bot can await
        session = await asyncio.to_thread(event.get_session, identifier=name)
        await asyncio.to_thread(session.load,
                                laps=kwargs.get("laps", False),
                                telemetry=kwargs.get('telemetry', False),
                                weather=kwargs.get("weather", False),
                                messages=kwargs.get("messages", False),
                                livedata=kwargs.get("livedata", None))
    except Exception:
        raise MissingDataError("Unable to get session data, check the event name. " +
                               "Or are you trying to predict the future?")

    return session


async def format_results(session: Session, name: str):
    """Format the data from `Session` results with data pertaining to the relevant session `name`.

    The session should be already loaded.

    Returns
    ------
    `DataFrame` with columns:

    Qualifying / Sprint Shootout - `[Pos, Code, Driver, Team, Q1, Q2, Q3]` \n
    Race / Sprint - `[Pos, Code, Driver, Team, Grid, Finish, Points]` \n
    Practice - `[No, Code, Driver, Team, Fastest, Laps]`
    """

    _sr: SessionResults = session.results

    # Results presentation
    res_df: SessionResults = _sr.rename(columns={
        "Position": "Pos",
        "DriverNumber": "No",
        "Abbreviation": "Code",
        "BroadcastName": "Driver",
        "GridPosition": "Grid",
        "TeamName": "Team"
    })

    # FP1, FP2, FP3
    ###############
    if "Practice" in name:
        # Reload the session to fetch missing lap info
        await asyncio.to_thread(session.load, laps=True, telemetry=False,
                                weather=False, messages=False, livedata=None)

        # Get each driver's fastest lap in the session
        fastest_laps = session.laps.groupby("DriverNumber")["LapTime"] \
            .min().reset_index().set_index("DriverNumber")

        # Combine the fastest lap data with the results data
        fp = pd.merge(
            res_df[["No", "Code", "Driver", "Team"]],
            fastest_laps["LapTime"],
            left_index=True, right_index=True)

        # Get a count of lap entries for each driver
        lap_totals = session.laps.groupby("DriverNumber").count()
        fp["Laps"] = lap_totals["LapNumber"]

        # Format the lap timedeltas to strings
        fp["LapTime"] = fp["LapTime"].apply(lambda x: utils.format_timedelta(x))
        fp = fp.rename(columns={"LapTime": "Fastest"}).sort_values(by="Fastest")

        return fp

    # QUALI / SS
    ############
    if name in ("Qualifying", "Sprint Shootout"):
        res_df["Pos"] = res_df["Pos"].astype(int)
        qs_res = res_df.loc[:, ["Pos", "Code", "Driver", "Team", "Q1", "Q2", "Q3"]]

        # Format the timedeltas to readable strings, replacing NaT with blank
        qs_res.loc[:, ["Q1", "Q2", "Q3"]] = res_df.loc[:, [
            "Q1", "Q2", "Q3"]].applymap(lambda x: utils.format_timedelta(x))

        return qs_res

    # RACE / SPRINT
    ###############

    # Get leader finish time
    leader_time = res_df["Time"].iloc[0]

    # Format the Time column:
    # Leader finish time; followed by gap in seconds to leader
    # Drivers who were a lap behind or retired show the finish status instead, e.g. '+1 Lap' or 'Collision'
    res_df["Finish"] = res_df.apply(lambda r: f"+{r['Time'].total_seconds():.3f}"
                                    if r['Status'] == 'Finished' else r['Status'], axis=1)

    # Format the timestamp of the leader lap
    res_df.loc[res_df.first_valid_index(), "Finish"] = utils.format_timedelta(leader_time, hours=True)

    res_df["Pos"] = res_df["Pos"].astype(int)
    res_df["Pts"] = res_df["Points"].astype(int)
    res_df["Grid"] = res_df["Grid"].astype(int)

    return res_df.loc[:, ["Pos", "Code", "Driver", "Team", "Grid", "Finish", "Pts"]]


async def filter_pitstops(year, round, filter: str = None, driver: str = None) -> pd.DataFrame:
    """Return the best ranked pitstops for a race. Optionally restrict results to a `driver` (surname, number or code).

    Use `filter`: `['Best', 'Worst', 'Ranked']` to only show the fastest or slowest stop.
    If not specified the best stop per driver will be used.

    Returns
    ------
    `DataFrame`: `[No, Code, Stop Num, Lap, Duration]`
    """

    # Create a dict with driver info from all drivers in the session
    drv_lst = await ergast.get_all_drivers(year, round)
    drv_info = {d["driverId"]: d for d in drv_lst}

    if driver is not None:
        driver = utils.find_driver(driver, drv_lst)["driverId"]

    # Run FF1 I/O in separate thread
    res = await asyncio.to_thread(
        ff1_erg.get_pit_stops,
        season=year, round=round,
        driver=driver, limit=1000)

    data = res.content[0]

    # Group the rows
    # Show all stops for a driver, which can then be filtered
    if driver is not None:
        row_mask = data["driverId"] == driver
    # Get the fastest stop for each driver when no specific driver is given
    else:
        row_mask = data.groupby("driverId")["duration"].idxmin()

    df = data.loc[row_mask].sort_values(by="duration").reset_index(drop=True)

    # Convert timedelta into seconds for stop duration
    df["duration"] = df["duration"].transform(lambda x: x.total_seconds())

    # Add driver abbreviations and numbers from driver info dict
    df[["No", "Code"]] = df.apply(lambda x: pd.Series({
        "No": drv_info[x.driverId]["permanentNumber"],
        "Code": drv_info[x.driverId]["code"],
    }), axis=1)

    # Get row indices for best/worst stop if provided
    if filter.lower() == "best":
        df = df.loc[[df["duration"].idxmin()]]
    if filter.lower() == "worst":
        df = df.loc[[df["duration"].idxmax()]]

    # Presentation
    df.columns = df.columns.str.capitalize()
    return df.loc[:, ["No", "Code", "Stop", "Lap", "Duration"]]


async def tyre_stints(session: Session, driver: str = None):
    """Return a DataFrame showing each driver's stint on a tyre compound and
    the number of laps driven on the tyre.

    The `session` must be a loaded race session with laps data.

    Raises
    ------
        `MissingDataError`: if session does not support the API lap data
    """
    # Check data availability
    if not session.f1_api_support:
        raise MissingDataError("Lap data not supported before 2018.")

    # Group laps data to individual sints per compound with total laps driven
    stints = session.laps.loc[:, ["Driver", "Stint", "Compound", "LapNumber"]]
    stints = stints.groupby(["Driver", "Stint", "Compound"]).count().reset_index() \
        .rename(columns={"LapNumber": "Laps"})
    stints["Stint"] = stints["Stint"].astype(int)

    # Try to find the driver if given and filter results
    if driver is not None:
        year, rnd = session.event["EventDate"].year, session.event["RoundNumber"]
        drv_code = utils.find_driver(driver, await ergast.get_all_drivers(year, rnd))["code"]

        return stints.loc[stints["Driver"] == drv_code].set_index(["Driver", "Stint"], drop=True)

    return stints


async def team_pace(session: Session):
    """Get the max sector speeds and min sector times from the lap data for each team in the session.

    The `session` must be loaded with laps data.

    Returns
    ------
        `DataFrame` containing max sector speeds and min times indexed by team.

    Raises
    ------
        `MissingDataError`: if session doesn't support lap data.
    """
    # Check lap data support
    if not session.f1_api_support:
        raise MissingDataError("Lap data not supported before 2018.")

    # Get only the quicklaps in session to exclude pits and slow laps
    laps = session.laps.pick_quicklaps()
    times = laps.groupby(["Team"])[["Sector1Time", "Sector2Time", "Sector3Time"]].min()
    speeds = laps.groupby(["Team"])[["SpeedI1", "SpeedI2", "SpeedFL", "SpeedST"]].max()

    df = pd.merge(times, speeds, how="left", left_index=True, right_index=True)

    return df


def tyre_performance(session: Session):
    """Get a DataFrame showing the average lap times for each tyre compound based on the
    number of laps driven on the tyre.

    `session` should already be loaded with lap data.

    Data is grouped by Compound and TyreLife to get the average time for each lap driven.
    Lap time values are based on quicklaps using a threshold of 105%.

    Returns
    ------
        `DataFrame` [Compound, TyreLife, LapTime, Seconds]
    """

    # Check lap data support
    if not session.f1_api_support:
        raise MissingDataError("Lap data not supported for this session.")

    # Filter and group quicklaps within 105% by Compound and TyreLife to get the mean times per driven lap
    laps = session.laps.pick_quicklaps(1.05).groupby(["Compound", "TyreLife"])["LapTime"].mean().reset_index()
    laps["Seconds"] = laps["LapTime"].dt.total_seconds()

    return laps


def pos_change(session: Session):
    """Returns each driver start, finish position and the difference between them. Session must be race."""

    if session.name != "Race":
        raise MissingDataError("The session should be race.")

    diff = session.results.loc[:, ["Abbreviation", "GridPosition", "Position"]].rename(
        columns={
            "Abbreviation": "Driver",
            "GridPosition": "Start",
            "Position": "Finish"
        }
    ).reset_index(drop=True).sort_values(by="Finish")

    diff["Diff"] = diff["Start"] - diff["Finish"]
    diff[["Start", "Finish", "Diff"]] = diff[["Start", "Finish", "Diff"]].astype(int)

    return diff
