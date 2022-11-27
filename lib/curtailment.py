import pandas as pd

from typing import Optional

MINUTES_TO_HOURS = 1 / 60


def resolve_applied_bid_offer_level(df_linear: pd.DataFrame):
    """
    We can have multiple levels for a given timepoint, because levels are fixed
    at one point and then overwitten at a later timepoint, before the moment in
    question has arrived.

    We need to resolve them, choosing the latest possible commitment for each timepoint.

    We need to upsample the data first to achieve this.
    """

    out = []

    for accept_id, data in df_linear.groupby("Accept ID"):
        high_freq = (
            data.reset_index()
            .rename(columns={"index": "Unit"})
            .set_index("Time")
            .resample("T")
            .first()
        )
        out.append(high_freq.interpolate("ffill").fillna(method="ffill"))

    recombined = pd.concat(out)

    # Select the latest commitment for every timepoint
    resolved = recombined.reset_index().groupby("Time").last()

    return resolved


def linearize_physical_data(df: pd.DataFrame):
    """Convert a From/To horizontal format to a long format with values at different timepoitns"""

    df = df.copy()
    from_columns = ["levelFrom", "timeFrom"]
    to_columns = ["levelTo", "timeTo"]

    base_columns = [x for x in df.columns.copy() if x not in from_columns + to_columns]

    df = pd.concat(
        (
            df[base_columns + from_columns].rename(
                columns={"levelFrom": "Level", "timeFrom": "Time"}
            ),
            df[base_columns + to_columns].rename(columns={"levelTo": "Level", "timeTo": "Time"}),
        )
    )

    df["Level"] = df["Level"].astype(float)
    return df


def calculate_curtailment_in_mwh(df_merged: pd.DataFrame) -> float:
    """
    Calculate the curtailment implied by the difference between FPN levels and BOAL

    """

    # TODO is this right? is delta in MW or MWH
    # idea change delta to 'delta_mw' to be sure
    mw_minutes = df_merged["delta"].sum()

    return mw_minutes * MINUTES_TO_HOURS


def calculate_curtailment_costs_in_gbp(df_merged: pd.DataFrame) -> float:
    """
    Calculate the curtailment implied by the difference between FPN levels and BOAL

    """
    # delta is in mw so to get energy in 30 mins we get
    df_merged["energy_mwh"] = df_merged["delta"] * 0.5

    # total costs in pounds
    costs_gbp = - (df_merged["energy_mwh"] * df_merged["bidPrice"]).sum()

    return costs_gbp


def calculate_notified_generation_in_mwh(df_merged: pd.DataFrame) -> float:
    """
    Calculate the total generation implied by the FPN levels

    """

    mw_minutes = df_merged["Level_FPN"].sum()

    return mw_minutes * MINUTES_TO_HOURS


def analyze_one_unit(
    df_boal_unit: pd.DataFrame,
    df_fpn_unit: pd.DataFrame,
    df_bod_unit: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Product a dataframe of actual (curtailed) vs. proposed generation"""

    # Make time linear
    df_boal_linear = linearize_physical_data(df_boal_unit)
    df_boal_linear["Accept Time str"] = df_boal_linear["Accept Time"].astype(str)

    # resolve boa data
    unit_boal_resolved = resolve_applied_bid_offer_level(df_boal_linear)
    unit_boal_resolved.head()

    if type(df_fpn_unit) == pd.Series:
        df_fpn_unit = pd.DataFrame(df_fpn_unit).T

    unit_fpn_resolved = (
        linearize_physical_data(df_fpn_unit).set_index("Time").resample("T").mean().interpolate()
    )
    unit_fpn_resolved["Notification Type"] = "FPN"

    # We merge BOAL to FPN, so all FPN data is preserved. We want to include
    # units with an FPN but not BOAL
    df_merged = unit_fpn_resolved.join(unit_boal_resolved["Level"], lsuffix="_FPN", rsuffix="_BOAL")

    # If there is no BOALF, then the level after the BOAL is the same as the FPN!
    df_merged["Level_After_BOAL"] = df_merged["Level_BOAL"].fillna(df_merged["Level_FPN"])
    df_merged["delta"] = df_merged["Level_FPN"] - df_merged["Level_After_BOAL"]

    # unsure if we should take '1' or '-1'. they seemd to have the same 'bidPrice'
    if df_bod_unit is not None:
        df_bod_unit = df_bod_unit[df_bod_unit["bidOfferPairNumber"] == "1"]
        df_bod_unit["bidPrice"] = df_bod_unit["bidPrice"].astype(float)

        # put bid Price into returned dat
        df_merged["local_datetime"] = pd.to_datetime(df_merged.index)
        df_bod_unit["local_datetime"] = pd.to_datetime(df_bod_unit["local_datetime"])
        df_merged = df_merged.merge(
            df_bod_unit[["bidPrice", "local_datetime"]], on=["local_datetime"]
        )

        # bid price is negative
        df_merged["energy_mwh"] = df_merged["delta"] * 0.5
        df_merged["cost_gbp"] = -df_merged["bidPrice"] * df_merged["energy_mwh"]

    return df_merged
