import itertools
from pathlib import Path
import pickle as pk
import pandas as pd
import numpy as np
import datetime

from PIL import Image
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

from drosben.config import (
    EXPERIMENTS_DIR,
    eventseq_pkl_path,
    eventseq_xlsx_path,
    observations_xlsx_path,
    experiment_dir_from_infodict,
    infodict_xlsx_path,
    infodict_pkl_path,
)
from drosben.image.process import (
    determine_data2record,
    evaluate_DataSheet_usage,
    extract_DataSheet_metadata,
    get_existing_experiments,
    make_readable_infodict,
    match_ds_experiment,
    process_DataSheet_image,
    generate_DataSheets,
    generate_rack_labels,
)
from drosben.image.utils import read_scan


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%


class Experiment:
    """
    The `Experiment` object will simply store the basic metadata of the experiment
    alonside the data generated through datasheets.
    """

    def __init__(self, infodict=None, storepath="", sequence=None):
        # experimental design information
        if infodict:
            self.infos = infodict
        else:
            self.storepath = storepath
            try:
                store = Path(self.storepath)
                infofile = [x for x in store.iterdir() if x.name.endswith("_infodict.pkl")][0]
                with infofile.open("rb") as f:
                    self.infos = pk.load(f)
            except Exception:
                print(message_except_nodata)
        # folder where info stored
        if not Path(storepath).exists():
            self.storepath = generate_experiment_folder(self.infos)
            # create data files (ds, metadata x2, events)
            initialize_experiment_files(self.infos, self.storepath)
        # load sequence of events (file will exist even if empty)
        self.root_filename = self.infos["xid"].split("-")[0]
        seqfile = eventseq_pkl_path(self.storepath, self.root_filename)
        with seqfile.open("rb") as s:
            self.sequence = pk.load(s)
        # initialise observations (file may not exist)
        self.observ_dict = {}
        obsfile = observations_xlsx_path(self.storepath, self.root_filename)
        if obsfile.exists():
            xls = pd.ExcelFile(obsfile)
            sheet_list = [s for s in xls.sheet_names if s.startswith('Observ_')]
            if len(sheet_list)>0:
                self.observ_dict = pd.read_excel(xls, sheet_list)

    def save_sequence(self):
        seqfilexl = eventseq_xlsx_path(self.storepath, self.root_filename)
        self.sequence.to_excel(
            str(seqfilexl),
            sheet_name = "Event_sequence_raw_data",
            engine     = "xlsxwriter",
        )
        seqfilepk = eventseq_pkl_path(self.storepath, self.root_filename)
        with seqfilepk.open("wb") as s:
            pk.dump(self.sequence, s)

    def prepare_raw_observations(self):
        # turn tube-based data to event-based data:
        observations_raw = seq2obs(self)
        # make experimental conditions readable for analysis file
        infodf = make_readable_infodict(self.infos)
        observations_trimmed = trim_observations(observations_raw, self.infos)
        self.observ_dict.update(
            {'Experimental_conditions': infodf,
             'Observ_raw_readable'    : observations_raw,
             'Observ_raw_analysis'    : observations_trimmed}
        )

    def qc_observations(self):
        # find missing/excessive events
        fly_numbers = check_fly_nos(
            self.infos,
            self.observ_dict['Observ_raw_readable'] # output from seq2obs
        )
        # establish warning messages for report
        xlsfilepath = observations_xlsx_path(self.storepath, self.root_filename)
        _, flyno_warning_string = warning_msg_analysis(xlsfilepath, fly_numbers)
        return (fly_numbers, flyno_warning_string)

    def consolidate_observations(self, fly_numbers, mode):
        observ_missing2censored = censor_missing_obs(
            fly_numbers['differential'],
            self.infos,
            self.observ_dict['Observ_raw_readable'],
            mode = mode,
        )
        observ_m2c_trimmed = trim_observations(observ_missing2censored, self.infos)
        self.observ_dict.update(
            {f'Observ_m2c_{mode}_readable': observ_missing2censored,
             f'Observ_m2c_{mode}_analysis': observ_m2c_trimmed}
        )

    def save_observations(self):
        xlsfilepath = observations_xlsx_path(self.storepath, self.root_filename)
        # write into XLSX file (https://stackoverflow.com/questions/38074678)
        writer = pd.ExcelWriter(xlsfilepath, engine="xlsxwriter")
        workbook = writer.book
        for sheet_name, obs_df in self.observ_dict.items():
            obs_df.to_excel(
            writer,
            sheet_name = sheet_name,
            startrow   = 0,
            startcol   = 0,
            index      = False
        )
        workbook.close()


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
# CLASS / PRE-ANALYSIS HELPERS


def generate_experiment_folder(infodict):
    # Path under HOME / (USERDATA)EXPERIMENTS_DIR / <date>_<name>_<xid>
    # as in config.py
    folder = experiment_dir_from_infodict(infodict)
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder)


def initialize_experiment_files(infodict, storepath):
    storepath = Path(storepath)
    # DataSheet PDF
    # page no. assuming 3 flips per week
    max_expec_lfspn = infodict["max_expec_lfspn"]
    fpw = infodict["fpw"]
    pages_no = int(np.ceil(np.ceil(max_expec_lfspn / 7) * fpw / 8))
    anon = infodict["anon"]
    # create PDF datasheets (anonymous or not)
    if anon:
        for rack in range(len(infodict["racks"]) + 1)[1:]:
            generate_DataSheets(infodict, pages_no, rack, storepath, anon)
    for rack in range(len(infodict["racks"]) + 1)[1:]:
        generate_DataSheets(infodict, pages_no, rack, storepath, anon=False)
    # create PDF labels
    generate_rack_labels(infodict, storepath)
    # Store infos
    filename_pfx = infodict["xid"].split("-")[0]
    # as xlsx
    xlsinfo = infodict_xlsx_path(storepath, filename_pfx)
    infodf = make_readable_infodict(infodict)
    infodf.to_excel(str(xlsinfo), sheet_name="Experiment_information", index=False)
    # as pkl
    pklinfo = infodict_pkl_path(storepath, filename_pfx)
    if not pklinfo.is_file():
        with pklinfo.open("wb") as inf:
            pk.dump(infodict, inf)
    # Event sequence (raw only)
    cols = [
        "Days_int",
        "Dead",
        "Censored",
        "CarriedOver",
        "unassigned",
        "Tube_no.",
        "Area_no.",
        "Page_no.",
        "Rack_no.",
        infodict["var1"]["var1_name"],
        infodict["var2"]["var2_name"],
        "FileName",
        "Dead_slices",
        "Dead_assignation",
        "Censored_slices",
        "Censored_assignation",
        "CarriedOver_slices",
        "CarriedOver_assignation",
        "unassigned_slices",
        "Tube_slice",
        "Rack_slice",
        "Date_slice",
        "Area_slice",
    ]
    raw_init = pd.DataFrame(columns=cols)
    # as xlsx
    xlsdata = eventseq_xlsx_path(storepath, filename_pfx)
    raw_init.to_excel(str(xlsdata), sheet_name="Raw event data", engine="xlsxwriter")
    # as pkl
    pkldata = eventseq_pkl_path(storepath, filename_pfx)
    if not pkldata.is_file():
        with pkldata.open("wb") as raw:
            pk.dump(raw_init, raw)


# ————————————————————————————————————————————————————————————————————————————————


def seq2obs(X):
    """This creates a survminer/lifelines-compatible dataframe after all the data
    has been collected and prior to analysis."""
    infodict = X.infos
    sequence = X.sequence
    init_date = get_exp_init_date(infodict)
    events = ["Dead", "Censored", "CarriedOver", "unassigned"]
    racks = [v for dicty in infodict["racks"] for (k, v) in dicty.items() if k == "rack_no"]
    # loop over experimental conditions to extract events for each
    # with 2 variables
    if all([infodict["var1"]["var1_name"], infodict["var2"]["var2_name"]]):
        # loop in all the combinations of genotype and treatment
        observations = get_observations_2vars(
            infodict,
            sequence,
            init_date,
            events,
            racks,
        )
    # with 1 variable
    else:
        # loop over existing variable levels
        observations = get_observations_1var(
            infodict,
            sequence,
            init_date,
            events,
            racks,
        )
    # turn Dead/CarriedOver into 1 and (by default) Censored into 0
    observations["Event"] = (
        (observations["Event_type"] == "Dead") | (observations["Event_type"] == "CarriedOver")
    ).astype(int)

    return observations


# -- helpers for seq2obs() --


def get_exp_init_date(infodict):
    [y, m, d] = [int(x) for x in reversed(infodict["init_date"].split("."))]
    init_date = datetime.datetime(y, m, d)
    return init_date


def get_observations_2vars(
    infodict,
    sequence,
    init_date,
    events,
    racks,
):
    v1name = infodict["var1"]["var1_name"]
    v2name = infodict["var2"]["var2_name"]
    cols_observ = [
        "Date",
        "Day_no.",
        "Event_type",
        "Rack_no.",
        "Tube_no.",
        v1name,
        v2name,
    ]
    observations = pd.DataFrame(columns=cols_observ)
    end_date = pd.Timestamp.min
    # fill `observations` with event-level rows
    # loop on levels conbinations
    v1l = infodict["var1"]["var1_lvls"]
    v2l = infodict["var2"]["var2_lvls"]
    for v1, v2 in itertools.product(v1l, v2l):
        # distribution of tubes in racks for this condition
        rack_condit_tube = {
            #r: gen_trt_tubes(v1, v2, r, infodict) for r in racks
            r: stratum_tubes_2var(v1, v2, r, infodict) for r in racks
        }
        # loop over racks (tagged with their tubes with a dictionary)
        # compile all events with their dates/days
        for r, c in rack_condit_tube.items():
            # loop over the tubes in this rack with this condition
            for tube in c:
                obsDF = extract_condition_survival_2vars(
                    v1, v2,
                    r,
                    tube,
                    sequence,
                    infodict
                )
                obsDF["Day_no."] = obsDF["Days_int"].cumsum(axis=0)
                obsDF["date"] = init_date + pd.to_timedelta(obsDF["Day_no."], unit="d")
                end_date = max( end_date, obsDF.date.max() )
                for _ix, row in obsDF.iterrows():
                    # get triplet of events [D, C, CO]
                    counts = [row[x] for x in events]
                    for event_type, count in zip(events, counts):
                        for c in range(count):
                            dt = [ row["date"],
                                   row["Day_no."],
                                   event_type,
                                   row["Rack_no."],
                                   row["Tube_no."],
                                   row[v1name],
                                   row[v2name] ]
                            dfa = observations if not observations.empty else None
                            dfb = pd.DataFrame([dt], columns=cols_observ)
                            observations = pd.concat([dfa, dfb], ignore_index=True)
    # trim Deaths after CarryOvers and consecutive CarryOvers
    observations = consolidate_events(observations)
    return observations

    
def get_observations_1var(
    infodict,
    sequence,
    init_date,
    events,
    racks,
):
    # deduces if the single variable is 'var1' or 'var2'
    varUsed = [
        f"var{x}"
        for x in range(1, 3)
        if infodict[f"var{x}"][f"var{x}_name"]
    ][0]
    # deduces which variable was not used ('var1' or 'var2')
    varNull = [
        f"var{x}"
        for x in range(1, 3)
        if not infodict[f"var{x}"][f"var{x}_name"]
    ][0]
    # initialises observations df
    vUname = infodict[varUsed][f"{varUsed}_name"]
    vNname = infodict[varNull][f"{varNull}_name"]
    cols_observ = [
        "Date",
        "Day_no.",
        "Event_type",
        "Rack_no.",
        "Tube_no.",
        vUname,
        vNname,
    ]
    observations = pd.DataFrame(columns=cols_observ)
    end_date = pd.Timestamp.min
    # extract events for each level of the only variable
    for vl in infodict[varUsed][f"{varUsed}_lvls"]:
        # distribution of tubes in racks for this condition
        rack_condit_tube = {r: stratum_tube_1var(varUsed, vl, r, infodict) for r in racks}
        # loop over racks (tagged with their tubes with a dictionary)
        for r, c in rack_condit_tube.items():
            # loop over the tubes in this rack with this condition
            for tube in c:
                # get all rows for this condition
                obsDF = extract_condition_survival_1var(
                    vl,
                    r,
                    varUsed, varNull,
                    tube,
                    sequence,
                    infodict,
                )
                # turn day intervals into days since experiment start date
                obsDF["Day_no."] = obsDF["Days_int"].cumsum(axis=0)
                # now work out dates per time point
                obsDF["date"] = init_date + pd.to_timedelta(obsDF["Day_no."], unit="d")
                end_date = max( end_date, obsDF.date.max() )
                # make the per-time rows become per-observation rows
                #    multiplying by number of observations (per type)
                #    this filters all rows with no observations.
                for _ix, row in obsDF.iterrows():
                    # get triplet of events [D, C, CO, unassigned]
                    counts = [row[x] for x in events]
                    for event_type, count in zip(events, counts):
                        for c in range(count):
                            dt = [ row["date"],
                                   row["Day_no."],
                                   event_type,
                                   row["Rack_no."],
                                   row["Tube_no."],
                                   row[vUname],
                                   row[vNname] ]
                            dfa = observations if not observations.empty else None
                            dfb = pd.DataFrame([dt], columns=cols_observ)
                            observations = pd.concat([dfa, dfb], ignore_index=True)
    # trim Deaths after CarryOvers and consecutive CarryOvers
    observations = consolidate_events(observations)
    return observations

    
def stratum_tubes_2var(
    v1,
    v2,
    rack_no,
    infodict
):
    """Identifies tubes in a specified rack that make a stratum based on values of two variables."""
    # get index for variable values
    v1_idx = infodict["var1"]["var1_lvls"].index(v1)
    v2_idx = infodict["var2"]["var2_lvls"].index(v2)
    # get boolean for tube indices in rack with both these values
    stratum_tubes = (
        (infodict["racks"][rack_no - 1]["var1"] == v1_idx)
        & (infodict["racks"][rack_no - 1]["var2"] == v2_idx)
    )
    # get tube numbers that contain both variable levels (same stratum)
    condition_tubes = np.where(stratum_tubes)[0] + 1
    return condition_tubes


def stratum_tubes_1var(
    var,
    level,
    rack_no,
    infodict
):
    """Identifies tubes in a specified rack with the same variable name and value.
    Used for single-variable experiments."""
    level_idx = infodict[var][f"{var}_lvls"].index(level)
    stratum_tubes = (infodict["racks"][rack_no - 1][var] == vl_idx)
    condition_tubes = np.where(stratum_tubes)[0] + 1
    return condition_tubes

    
def extract_condition_survival_2vars(
    v1,
    v2,
    rack_no,
    tube,
    df,
    infodict
):
    v1name = infodict["var1"]["var1_name"]
    v2name = infodict["var2"]["var2_name"]
    # get the tubes where that condition was used
    col_list = [
        "Days_int",
        "Dead",
        "Censored",
        "CarriedOver",
        "unassigned",
        "Tube_no.",
        "Area_no.",
        "Page_no.",
        "Rack_no.",
        v1name,
        v2name,
    ]
    D = df[ (df[v1name] == v1)
            & (df[v2name] == v2)
            & (df["Rack_no."] == rack_no)
            & (df["Tube_no."] == tube) ][col_list]
    # sort by chronological order
    D = D.sort_values(
        by=["Rack_no.", "Page_no.", "Area_no."],
        axis=0,
        ignore_index=True)
    return D


def extract_condition_survival_1var(
    vl,
    rack_no,
    varUsed,
    varNull,
    tube,
    df,
    infodict,
):
    vUname = infodict[varUsed][f"{varUsed}_name"]
    vNname = infodict[varNull][f"{varNull}_name"]
    # get the tubes where that condition was used
    col_list = [
        "Days_int",
        "Dead",
        "Censored",
        "CarriedOver",
        "unassigned",
        "Tube_no.",
        "Area_no.",
        "Page_no.",
        "Rack_no.",
        vUname,
        vNname,
    ]
    D = df[ (df[vUname] == vl)
            & (df["Rack_no."] == rack_no)
            & (df["Tube_no."] == tube) ][col_list]
    # and sort by chronological order
    D = D.sort_values(
        by=["Rack_no.", "Page_no.", "Area_no."],
        axis=0,
        ignore_index=True
    )
    return D


def consolidate_events(obs:pd.DataFrame):
    """
    Returns a filtered obs with CarriedOver duplicates and
    their corresponding first Dead removed.
    """
    rows_to_drop = []

    for (rack,tube), tube_obs in obs.groupby(['Rack_no.', 'Tube_no.']):
        tube_obs = tube_obs.sort_values("Day_no.")
        carriedover_pending = 0    # carriedovers awaiting recording as dead
    
        # consider all events per day
        for day, day_df in tube_obs.groupby('Day_no.'):
            # find out dead and carriedovers
            dead_idx        = day_df[day_df['Event_type'] == 'Dead'].index.tolist()
            carriedover_idx = day_df[day_df['Event_type'] == 'CarriedOver'].index.tolist()
            
            # count new carriedovers today, discard the rest
            new_carriedovers = max(0, len(carriedover_idx) - carriedover_pending)
            rows_to_drop.extend( carriedover_idx[new_carriedovers:] )
            carriedover_pending += new_carriedovers
    
            # drop as many dead today as carriedover_pending can account for
            # any excess are genuine new deaths
            claimed = min(len(dead_idx), carriedover_pending)
            rows_to_drop.extend(dead_idx[:claimed])
            carriedover_pending -= claimed

    return obs.drop(index=rows_to_drop)


# ————————————————————————————————————————————————————————————————————————————————


def check_fly_nos(infodict, observations_raw):
    # first find what we have observed
    flyno_obser = {}
    flyno_unasg = {}
    for rack in range(1, len(infodict["racks"]) + 1):
        fly_nos_tube = []
        fly_unasg_tube = []
        for tube in range(1, 13):
            flyno = len(
                observations_raw[
                    (observations_raw["Rack_no."] == rack)
                    & (observations_raw["Tube_no."] == tube)
                ]
            )
            fly_nos_tube.append(flyno)
            fly_unasg = len(
                observations_raw[
                    (observations_raw["Rack_no."] == rack)
                    & (observations_raw["Tube_no."] == tube)
                    & (observations_raw["Event_type"] == "unassigned")
                ]
            )
            fly_unasg_tube.append(fly_unasg)

        flyno_obser[rack] = np.array(fly_nos_tube)
        flyno_unasg[rack] = np.array(fly_unasg_tube)
    # compare with what is expected
    flyno_expec = {}
    for rack in infodict["racks"]:
        key = rack["rack_no"]
        # get rid of the None values with list comprehension:
        vals = np.array([x if x else 0 for x in rack["fly_no"]])
        flyno_expec[key] = vals
    # store differential
    flyno_diffr = {}
    for key in flyno_obser:
        flyno_diffr[key] = flyno_obser[key] - flyno_expec[key]
    flyno_false = {
        key: np.array(
            [(x == 0 and y > 0) for (x, y) in zip(flyno_obser[key], flyno_expec[key])]
        )
        for key in flyno_obser
    }
    fly_numbers = {
        'differential': flyno_diffr,
        'expected':     flyno_expec,
        'observed':     flyno_obser,
        'unassigned':   flyno_unasg,
        'falsepos':     flyno_false,
    }
    return fly_numbers


def warning_msg_analysis(
    xlsfilepath,
    fly_numbers,
):
    
    def conform_rack(rack, fly_numbers, flyno_warning):
        flyno_warning = conform_unassigned(rack, fly_numbers, flyno_warning, warning_unassigned)
        flyno_warning = conform_null(rack, fly_numbers, flyno_warning, warning_null)
        flyno_warning = conform_missing(rack, fly_numbers, flyno_warning, warning_missing)
        flyno_warning = conform_excess(rack, fly_numbers, flyno_warning, warning_excess)
        return flyno_warning

    def conform_unassigned(rack, fly_numbers, flyno_warning, warning_unassigned):
        flyno_unasg = simple_flynos(fly_numbers)[3][rack] # 'unassigned' for $rack
        # check for unassigned data
        if any(flyno_unasg):
            unasg_tb = np.nonzero(flyno_unasg)[0] # tube indices
            flyno_warning["unassigned"] += warning_unassigned.format(
                rack,
                ", ".join([str(x) for x in unasg_tb + 1]), # tube numbers for print
                ", ".join([str(x) for x in flyno_unasg[unasg_tb]]),
                ("" if len(unasg_tb) == 1 else " (respectively)"),
            )
        return flyno_warning

    def conform_null(rack, fly_numbers, flyno_warning, warning_null):
        flyno_false = simple_flynos(fly_numbers)[4][rack] # 'falsepos' for $rack
        flyno_expec = simple_flynos(fly_numbers)[1][rack] # 'expected' for $rack
        # check for non-empty tubes with no observations at all
        if any(flyno_false):
            null_tb = np.where(flyno_false)[0] # tube indices
            flyno_warning["null"] += warning_null.format(
                rack,
                ", ".join([str(x) for x in null_tb + 1]), # tube numbers for print
                ("This was" if len(unasg_tb) == 1 else "These were"),
                ", ".join([str(x) for x in flyno_expec[null_tb]]),
                ("" if len(null_tb) == 1 else " (respectively)"),
            )
        return flyno_warning

    def conform_missing(rack, fly_numbers, flyno_warning, warning_missing):
        flyno_diffr = simple_flynos(fly_numbers)[0][rack] # 'differential' for $rack
        flyno_expec = simple_flynos(fly_numbers)[1][rack] # 'expected' for $rack
        flyno_obser = simple_flynos(fly_numbers)[2][rack] # 'observed' for $rack
        flyno_unasg = simple_flynos(fly_numbers)[3][rack] # 'unassigned' for $rack
        # check for tubes with some missing flies
        if any(flyno_diffr < 0):
            missing_tb = np.nonzero(flyno_diffr < 0)[0]  # tube indices
            unasg_tb = np.nonzero(flyno_unasg)[0] # tube indices
            flyno_warning["missing"] += warning_missing.format(
                rack,
                ", ".join([str(x) for x in missing_tb + 1]),  # tube numbers for print
                ("This was" if len(missing_tb) == 1 else "These were"),
                ", ".join([str(x) for x in flyno_expec[missing_tb]]),
                ("" if len(missing_tb) == 1 else " (respectively)"),
                ", ".join([str(x) for x in flyno_obser[missing_tb]]),
                ("" if len(missing_tb) == 1 else " (respectively)"),
            )
            # add a note if some of these also had unassigned events
            if any(np.isin(unasg_tb, missing_tb)):
                miss_unasg = unasg_tb[np.isin(unasg_tb, missing_tb)] # tube indices
                tun = ", ".join([str(x) for x in miss_unasg + 1])  # tube numbers for print
                flyno_warning["missing"] += f"Note there are unassigned events in some of these tubes ({tun})."
        return flyno_warning

    def conform_excess(rack, fly_numbers, flyno_warning, warning_excess):
        flyno_diffr = simple_flynos(fly_numbers)[0][rack] # 'differential' for $rack
        flyno_expec = simple_flynos(fly_numbers)[1][rack] # 'expected' for $rack
        flyno_obser = simple_flynos(fly_numbers)[2][rack] # 'observed' for $rack
        flyno_unasg = simple_flynos(fly_numbers)[3][rack] # 'unassigned' for $rack
        # check for tubes with some missing flies
        if any(flyno_diffr > 0):
            excess_tb = np.nonzero(flyno_diffr > 0)[0] # tube indicess
            unasg_tb = np.nonzero(flyno_unasg)[0] # tube indices
            flyno_warning["excess"] += warning_excess.format(
                rack,
                ", ".join([str(x) for x in excess_tb + 1]),  # tube numbers for print
                ("This was" if len(excess_tb) == 1 else "These were"),
                ", ".join([str(x) for x in flyno_expec[excess_tb]]),
                ("" if len(excess_tb) == 1 else " (respectively)"),
                ", ".join([str(x) for x in flyno_obser[excess_tb]]),
                ("" if len(excess_tb) == 1 else " (respectively)"),
            )
            # add a note if some of these also had unassigned events
            if any(np.isin(unasg_tb, excess_tb)):
                excs_unasg = unasg_tb[np.isin(unasg_tb, excess_tb)] # tube indices
                tun = ", ".join([str(x) for x in excs_unasg + 1])  # tube numbers for print
                flyno_warning["excess"] += f"Note there are unassigned events in some of these tubes ({tun})."
        return flyno_warning

    def simple_flynos(d):
        # ['differential', 'expected', 'observed', 'unassigned', 'falsepos']
        return [d[k] for k in d.keys()]

    # create warnings for different cases for each rack separately
    warning_keys = ["unassigned", "null", "missing", "excess"]
    flyno_warning = dict.fromkeys(warning_keys, "")
    for rack in fly_numbers['observed'].keys():
        flyno_warning = conform_rack(rack, fly_numbers, flyno_warning)

    # create full warning text
    flyno_warning_string = ""
    if len(flyno_warning["null"]) > 0:
        flyno_warning_string += "\n* TUBES WITH NO DATA.  \n"
        flyno_warning_string += flyno_warning["null"]
        flyno_warning_string += message_null
    if len(flyno_warning["missing"]) > 0:
        flyno_warning_string += "\n* TUBES WITH MISSING EVENTS.  \n"
        flyno_warning_string += flyno_warning["missing"]
        flyno_warning_string += message_missing.format(xlsfilepath)
    if len(flyno_warning["excess"]) > 0:
        flyno_warning_string += "\n* TUBES WITH TOO MANY EVENTS.  \n"
        flyno_warning_string += flyno_warning["excess"]
        flyno_warning_string += message_excess
    if len(flyno_warning["unassigned"]) > 0:
        flyno_warning_string += "\n* TUBES WITH UNASSIGNED EVENTS.  \n"
        flyno_warning_string += flyno_warning["unassigned"]
        flyno_warning_string += message_unassigned

    return flyno_warning, flyno_warning_string

    
def rackNtube2condition(rack, tube, infodict):
    # little utility to get easy access to data
    tube_var1_ix = infodict["racks"][rack - 1]["var1"][tube - 1]
    if tube_var1_ix is not None:
        var1_lvl = infodict["var1"]["var1_lvls"][tube_var1_ix]
    else:
        var1_lvl = None

    tube_var2_ix = infodict["racks"][rack - 1]["var2"][tube - 1]
    if tube_var2_ix is not None:
        var2_lvl = infodict["var2"]["var2_lvls"][tube_var2_ix]
    else:
        var2_lvl = None
    return var1_lvl, var2_lvl

    
def censor_missing_obs(
    flyno_diffr,
    infodict,
    observations_raw,
    mode = 'atEnd', # 'atEnd' | 'atStart'
):
    """
    Takes the event-based observation table and censors the missing observations.
    """
    # establish mode:    
    init_date = get_exp_init_date(infodict)
    if mode == 'atStart':
        censored_day = 1
    elif mode == 'atEnd':
        censored_day = np.max(observations_raw['Day_no.'])
    missing_date = init_date + pd.to_timedelta(censored_day, unit="d")
    # add missing obsv if there are any
    observ_missing = pd.DataFrame(columns=observations_raw.columns)
    fly_discrepancies = np.hstack( list(flyno_diffr.values()) )
    if any(fly_discrepancies < 0):
        for rack in flyno_diffr.keys():
            missing_tb = np.nonzero(flyno_diffr[rack] < 0)[0] + 1
            for tube in missing_tb:
                dt = [
                    [
                        missing_date,
                        censored_day,
                        "Censored_deduced",
                        rack,
                        tube,
                        rackNtube2condition(rack, tube, infodict)[0],
                        rackNtube2condition(rack, tube, infodict)[1],
                        0,
                    ]
                ]
                missno = np.abs(flyno_diffr[rack][tube - 1])
                missdf = pd.DataFrame(np.repeat(dt, missno, axis=0))
                missdf.columns = observations_raw.columns
                observ_missing = pd.concat(
                    [observ_missing if not observ_missing.empty else None, missdf],
                    ignore_index=True,
                )
    observ_missing = pd.concat(
        [observations_raw if not observations_raw.empty else None, observ_missing],
        ignore_index=True
    )
    observ_missing.sort_values(
        by=[
            "Day_no.",
            "Rack_no.",
            "Tube_no.",
            infodict["var1"]["var1_name"],
            infodict["var1"]["var1_name"],
        ],
        ignore_index=True,
        inplace=True,
    )
    return observ_missing


# ————————————————————————————————————————————————————————————————————————————————


def trim_observations(observations_raw, infodict):
    v1name = infodict["var1"]["var1_name"]
    v2name = infodict["var2"]["var2_name"]
    keepers = ["Day_no.", "Rack_no.", "Tube_no.", v1name, v2name, "Event"]
    trimmed = observations_raw[keepers].drop(
        observations_raw[observations_raw["Event_type"] == "unassigned"].index
    )
    return trimmed


# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
# ADDING DATA


def process_DataSheet_batch(
    dsfolderpath,
    OW = True,
    dry_run = False,
    verbose = False,
):
    """
    `process_DataSheet_batch` is simply a loop-wrap of the `process_DataSheet`
    routine so that it can be invoked in bulk.
    """

    # -- check files are valid
    folder = Path(dsfolderpath)
    filelist = [x.name for x in folder.iterdir() if x.is_file()]
    valid_endings = (".tiff", ".tif", ".jpg", ".jpeg", ".png", ".pdf")
    dslist = [x for x in filelist if x.endswith(valid_endings)]
    # if no valid files
    feedback_text = {
        "scans_path"     : None,
        "scans_matching" : [],
        "data_status"    : None,
        "fly_numbers"    : None
        }
    if len(dslist) == 0:
        feedback_text["scans_path"] = message_noimgfiles
        return feedback_text
    # if valid files
    else:
        feedback_text["scans_path"] = f"{str(dsfolderpath)} contains valid image files for datasheet scans."

        
    # -- loop over all datasheet files
    infodict_list = get_existing_experiments()
    experiments_updated = []
    for dsfilename in dslist:
        if verbose:
            file_message = f"\n----------<oOo>----------\n\nDataSheet scan file: {dsfilename}"
            print(file_message)
        # id datasheet
        dsfilepath = folder / dsfilename
        metadata, dsimg, QRside = extract_DataSheet_metadata(dsfilepath, verbose=verbose)
        page_no = metadata["page_no"]
        rack_no = metadata["rack_no"]
        # match to experiment, extract data, write if specified
        try:
            out = process_DataSheet(
                metadata,
                infodict_list,
                dsfilepath,
                dsimg,
                QRside,
                page_no,
                rack_no,
                OW=OW,
                dry_run=dry_run,
                verbose=verbose,
                )
            if len(out) == 1:
                # then out is an error message
                feedback_text["scans_matching"].append( f"\n\nThere is no experiment match for:\n{out}" )
            if len(out) == 2:
                # then out is a milestone message and a directory path to Experiment data
                feedback_text["scans_matching"].append( f"\n\n{out[0]}" )
                experiments_updated.append(out[1])

        except Exception as e:
            if verbose:
                import traceback
                print(f"\nException at file {dsfilename}:\n{traceback.format_exc()}")
            feedback_text["scans_matching"].append(f"\n\n{dsfilename} induced Exception")
            pass # to the next dsfilename

    return (list(set(experiments_updated)), feedback_text)


def process_DataSheet(
    metadata,
    infodict_list,
    dsfilepath,
    dsimg,
    QRside,
    page_no,
    rack_no,
    OW,
    dry_run,
    verbose,
):
    # check there is matching Experiment
    # Xmatchpath:str (directory path or error message)
    Xmatchpath = match_ds_experiment(metadata, infodict_list, dsfilepath)
    if not Path(Xmatchpath).exists():
        feedback = Xmatchpath
        if verbose:
            msg = f"{dsfilepath.name} scan file could not be matched to any experiments in this computer." \
            + f"\nThese are stored at:\n{EXPERIMENTS_DIR}"
            print(msg)
        return feedback
    else:
        pass

    # extract data and add to Experiment
    X = Experiment(storepath=Xmatchpath)
    feedback = f"\nMatched file: {dsfilepath}\nExperiment: {X.infos['exp_name']}"
    if verbose: print(feedback)
    # identify available data
    inpt_regs_status = evaluate_DataSheet_usage(
        dsimg,
        QRside,
        page_no,
        str(dsfilepath),
        verbose=verbose
    )
    # decide which inputs to record or overwrite
    inpt_regs_add = determine_data2record(
        inpt_regs_status,
        page_no,
        rack_no,
        X.sequence,
        OW=OW,
        verbose=verbose
    )
    # find events in the selected racks and (over)write in Experiment.sequence
    # save DS image for 'safeguarding'
    X.sequence = process_DataSheet_image(
        X.infos,
        X.sequence,
        dsimg,
        QRside,
        page_no,
        rack_no,
        dsfilepath,
        inpt_regs_add,
        X.storepath,
        OW=OW,
        dry_run=dry_run,
        verbose=verbose
    )
    X.sequence.sort_values(
        by=["Page_no.", "Rack_no."],
        ignore_index=True,
        inplace=True
    )
    if not dry_run:
        X.save_sequence()
    
    del X
    
    # Return feedback + path (even on dry-run, so caller can summarise)
    return [feedback, Xmatchpath]


def collate_experiment_data(
    experiments_updated,
    feedback_text,
    OW,
    dry_run,
):
    fly_numbers_dict = {}
    if not dry_run:
        for experiment_path in experiments_updated:
            # prepare and qc data for one experiment
            X = Experiment(storepath=experiment_path)
            X.prepare_raw_observations()
            if OW:
                X.save_observations()
            (fly_numbers, flyno_warning_string) = X.qc_observations()
            # collect messages
            milestone_text = message_milestone.format(X.infos["exp_name"])
            feedback_text["data_status"] = f"\n\n{milestone_text}"
            _t = f"\nPlease consider the following information:\n{flyno_warning_string}"
            feedback_text["fly_numbers"] = _t
            # collect fly_numbers
            fly_numbers_dict[X.infos['xid']] = fly_numbers

    elif dry_run and len(experiments_updated) > 0:
        l = len(experiments_updated)
        dry_run_msg = f"DRY RUN: {l} experiment(s) would have been updated otherwise."
        feedback_text['data_status'] = f"\n\n{dry_run_msg}"
        _msg = "\n\nNo checks on numbers of events vs. flies per tube have been done."
        feedback_text['fly_numbers'] = _msg

    return (feedback_text, fly_numbers_dict)

    
def qc_mosaic(
    obs: pd.DataFrame,
    infodict: dict,
    storepath: str,
    mode: str = "tube",          # "tube" | "rack"
    tube_no: int | None = None,  # required for mode="tube"
    rack_no: int | None = None,  # required for both modes (defines stratum/experimental arm)
    day_label: str = "interval",   # "interval" | "absolute"
    figsize_mm: tuple = (210, 297),
    dpi: int = 150,
    label_fontsize: int = 10,
    title: str | None = None,
    save_path: str | Path | None = None,
):

    img_dir = Path(storepath) / 'processed_datasheets'

    # mode settings
    crop_level = "tube" if mode == "tube" else "rack_area"
    n_cols     = 8      if mode == "tube" else 4
    pad        = 3      if mode == "tube" else 6

    # filter X.sequence
    mask = pd.Series(True, index=obs.index)
    if rack_no is None:
        raise ValueError("rack_no is required")
    mask &= obs["Rack_no."] == rack_no
    if mode == "tube":
        if tube_no is None:
            raise ValueError("tube_no is required for mode='tube'")
        mask &= obs["Tube_no."] == tube_no
    sub = obs[mask].copy()
    if sub.empty:
        raise ValueError("No rows match the given filters.")

    # get stratum from the data
    stratum = []
    for var in ['var1', 'var2']:
        varname = infodict[var][var+"_name"]
        var_level = sub.iloc[0][varname]
        stratum.append( var_level )

    # enforce chronological sorting
    sub = sub.sort_values(["Page_no.", "Area_no.", "Tube_no."]).reset_index(drop=True)

    # rack mode: split df data to crop images (1 row / timepoint)
    # and to keep (grouped) individual tube and event slices for overlays
    if mode == "rack":
        PA = ["Page_no.", "Area_no."]
        sub_crop = sub.drop_duplicates(subset=PA).reset_index(drop=True)
        sub_tubes = {(p, a): grp for (p, a), grp in sub.groupby(PA)}
    else:
        sub_crop = sub
        sub_tubes = None

    # build list of panels (cropped areas to plot)
    if mode == "rack":
        panels = [
            _make_panel(
                row, img_dir, crop_level,
                tube_rows=sub_tubes.get((row["Page_no."], row["Area_no."]))
            )
            for _, row in sub_crop.iterrows()
        ]
    else:
        panels = [_make_panel(row, img_dir, crop_level)
                  for _, row in sub.iterrows()]

    # compute per-panel labels (day intervals and DataSheet page where they are located)
    cumday = 0
    for i, panel in enumerate(panels):
        cumday += panel["timeint"]
        if day_label == "absolute":
            panel["time_label"] = f"d{cumday}"
        else:  # interval
            panel["time_label"] = f"d{cumday}" if i == 0 else f"+{panel['timeint']}d"
        panel["page_marker"] = (f"p.{panel['page']}"
                                if i == 0 or panel["page"] != panels[i-1]["page"]
                                else None)

    # layout
    plt.rcParams['font.family'] = "sans-serif"
    plt.rcParams['font.sans-serif'] = "DejaVu Sans"
    plt.rcParams['mathtext.fontset'] = 'dejavusans'
    plt.rcParams['text.usetex'] = False
    
    n = len(panels)
    n_rows = int(np.ceil(n / n_cols))

    w_in = figsize_mm[0] / 25.4
    h_in = figsize_mm[1] / 25.4
    fig = plt.figure(figsize=(w_in, h_in), dpi=dpi)

    if mode == "tube":
        gs = fig.add_gridspec(
            n_rows, n_cols,
            hspace=-0.7, # gets tube rows closer together
            wspace=0.02, # gets tubes in row closer
            top=0.9,
            bottom=0.01,
            left=0.01,
            right=0.99,
        )
    else:  # rack mode
        gs = fig.add_gridspec(
            n_rows, n_cols,
            hspace=0.35,    # enough room for both labels without overlap
            wspace=0.01,    # nearly contiguous columns
            top=0.9,
            bottom=0.01,
            left=0.2,      # push grid inward so columns aren't stretched
            right=0.8,     #    across the full page width
        )

    axes = np.array([[fig.add_subplot(gs[r, c]) for c in range(n_cols)]
                     for r in range(n_rows)])

    # loop over panels
    for idx, panel in enumerate(panels):
        r, c = divmod(idx, n_cols)
        ax = axes[r][c]
        # build the image for that gs axis
        crop = panel["crop"]
        H, W = crop.shape[:2]
        border = 2
        bordered = np.zeros((H + 2*border, W + 2*border, 3), dtype=crop.dtype)
        bordered[border:-border, border:-border] = crop
        ax.imshow(bordered)
        ax.axis("off")
        # draw rectangles around events
        _draw_overlays(ax, panel, infodict, mode, pad)
        # label panels
        fs_corr = 0 if mode == "tube" else -1
        ax.text(0.5, -0.01,
                panel["time_label"],
                transform=ax.transAxes,
                fontsize=label_fontsize + fs_corr,
                fontweight="bold",
                ha="center", va="top",
                color="dimgray")
        # label panel row
        if panel["page_marker"]:
            ax.text(-0.2, 0.5,
                    panel["page_marker"],
                    transform=ax.transAxes,
                    fontsize=label_fontsize + fs_corr,
                    fontweight="extra bold",
                    ha="center", va="bottom",
                    color="black",
                    style="italic")
    # add title
    if title is None:
        title = _auto_title(infodict, stratum, mode, rack_no, tube_no)
    y_marker = 0.79 if mode == "tube" else 0.92
    fs_corr = 3 if mode == "tube" else 1
    fig.suptitle(title, fontsize=label_fontsize + fs_corr, fontweight="bold", y=y_marker)
    # save fig
    if save_path:
        save_path = Path(save_path).expanduser()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(save_path, dpi=dpi)
        print(f"Saved → {save_path}")
    else:
        plt.show()

    return fig, axes


# -- helpers for qc_mosaic() --

def _crop_from_row(
    img_array: np.ndarray,
    row: pd.Series,
    crop_level: str
) -> np.ndarray:
    crop = img_array
    # img > data area > rack area
    if crop_level in ("rack_area", "tube"):
        area_slice = row.get("Area_slice")
        if area_slice is not None:
            crop = crop[area_slice]
        rack_slice = row.get("Rack_slice")
        if rack_slice is not None:
            crop = crop[rack_slice]
    # rack area > individual tube
    if crop_level == "tube":
        tube_slice = row.get("Tube_slice")
        if tube_slice is not None:
            crop = crop[tube_slice]
    return crop


def _make_panel(
    row: pd.Series,
    img_dir: Path,
    crop_level: str, 
    tube_rows: pd.DataFrame | None = None
) -> dict:
    
    filepath = img_dir / row["FileName"]
    img = read_scan(str(filepath))
    crop = _crop_from_row(img, row, crop_level)
    return {
        "crop":      crop,
        "row":       row,
        "tube_rows": tube_rows,   # all 12 tube rows for this time point
        "timeint":   row.get("Days_int"),
        "page":      row.get("Page_no."),
    }


def _draw_overlays(
    ax,
    panel: dict,
    infodict: dict,
    mode: str = "tube",
    pad: int = 3,
):

    fixed_colors = {
        "Dead":        infodict['colour_data']['dead']['RGB'],
        "Censored":    infodict['colour_data']['censored']['RGB'],
        "CarriedOver": infodict['colour_data']['carried-over']['RGB'],
        "unassigned":  [0.55, 0.55, 0.55],
    }
    obs_map = [
        ("Dead",        "Dead_slices"),
        ("Censored",    "Censored_slices"),
        ("CarriedOver", "CarriedOver_slices"),
        ("unassigned",  "unassigned_slices"),
    ]

    # in rack mode, draw overlays for all 12 tubes (rows refer to X.sequence)
    if mode == "rack" and panel.get("tube_rows") is not None:
        rows_to_draw = [row for _, row in panel["tube_rows"].iterrows()]
    else:
        rows_to_draw = [panel["row"]]
    # loop over rows with slices to turn into Patches
    for row in rows_to_draw:
        # get tube origin within rack crop for coordinate offsetting
        tube_slc = row.get("Tube_slice") if hasattr(row, 'get') else row["Tube_slice"]
        if mode == "rack" and tube_slc is not None:
            t_r_off = tube_slc[0].start
            t_c_off = tube_slc[1].start
        else:
            t_r_off = t_c_off = 0
        # loop over event types and their slices in the selected X.sequence rows
        for obs_key, slc_col in obs_map:
            val = row.get(obs_key) if hasattr(row, 'get') else row[obs_key]
            # non-existing event or event with slices
            if not val:
                continue
            try:
                slc_list = row[slc_col]
            except KeyError:
                continue
            if slc_list is None:
                continue
            # obtain colour as defined by event
            color = fixed_colors[obs_key]
            if not isinstance(slc_list, list):
                slc_list = [slc_list]
            # events in tubes are larger - allow for thicker, snuggier and colour-coded outline
            if mode == "tube":
                (lw, ec, fc, alpha, ls) = (1.2, color, "none", 1, (0, (1, 1)))
            # events in racks are just outlined all in thin continuous semi-transparent black outline
            else:
                (lw, ec, fc, alpha, ls) = (0.4, "black", "none", 0.5, "-")
            # define the Patches and plot them
            for slc in slc_list:
                r0, r1 = slc[0].start, slc[0].stop
                c0, c1 = slc[1].start, slc[1].stop
                r0p = r0 - pad + t_r_off
                r1p = r1 + pad + t_r_off
                c0p = c0 - pad + t_c_off
                c1p = c1 + pad + t_c_off
                rect = mpatches.Rectangle(
                    (c0p, r0p), c1p - c0p, r1p - r0p,
                    linewidth = lw,
                    edgecolor = ec,
                    facecolor = fc,
                    linestyle = ls,
                    alpha = alpha,
                )
                ax.add_patch(rect)
                
            
def _auto_title(infodict, stratum, mode, rack_no, tube_no:int|None=None) -> str:
    for ix, n in enumerate(range(1,3)):
        var = f"var{n}"
        var_name = f"{var}_name"
        variable_fullname = infodict[var][var_name]
        if 'genotype' in variable_fullname.lower():
            stratum[ix] = stratum[ix].replace("-", "\/\/")
            stratum[ix] = r"$\mathbfit{" + stratum[ix] + r"}$"
    title_end = f"tube {tube_no}" if mode=="tube" else "all tubes"
    title = f'Rack {rack_no} ({" | ".join(stratum)}) @ {title_end}'
    # example: Rack 3 (mutant | treated) @ tube 1
    return title

# %%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
# MESSAGES

message_noimgfiles = """
THIS BATCH OF FILES DOES NOT CONTAIN ANY VALID IMAGE FILE (jpeg, tiff, png, pdf).
PLEASE CONSIDER RE-SCANNING AND SAVING IMAGES IN ONE OF THESE FORMATS.
"""

message_except_nodata = """
The selected folder does not contain Drosben Experiment data.
"""

message_milestone = "Experiment '{}' is prepared for statistical analysis."

warning_unassigned = """
There are unassigned events in rack {}, tube(s) {}.
The software detected {} unassigned events{}. 
These could be unintended spots in the DataSheet scan (e.g. due to debris
in the scanner or stains in the DataSheet form), or real events where
the colour deviates too much from the expected hue/saturation values.
These will be eliminated from the data spreadsheet for analysis so this
will further reduce the total number of observations in these tubes.
(They will be kept in the 'readable' spreadsheet for manual tracking
and analysis if desired.)
Close inspection of the DataSheet scans is encouraged to detect potential
human mistakes or problems of segmentation that can be corrected by
re-filling and scanning the DataSheet forms in a clearer/cleaner way.
"""

warning_null = """
There are no data recorded for rack {}, tube(s) {}. 
{} expected to contain {} flies{}.
Check that there have not been errors when filling the DataSheet.
"""

warning_missing = """
There are missing events observed in rack {}, tube(s) {}. 
{} expected to contain initially {} flies{}.
But the observations recorded are only {}{}.
"""

warning_excess = """
There are excessive events observed in rack {}, tube(s) {}. 
{} expected to contain initially {} flies{}. 
But the observations recorded are more: {}{}.
"""

message_null = """
    The analysis will proceed with the current data.\n"""

message_missing = """
    The analysis will proceed with the current data (stored in the Excel file:
    {},  
    and assuming that the original number of flies matches the number of observations. 
    A more stringent analysis would consider the missing flies as censored at day 1,
    or at the end of the experiment if these missing events come from ending the experiment
    before all individuals have died (this is the default behaviour).
    You can perform this analysis by using the sheet named `Observations_stringent_analysis` 
    in the same Excel file.\n"""

message_excess = """
    The analysis will proceed with the current data.
    You are encouraged to carefully examine the DataSheet for errors in segmentation,
    in filling in the data, or the initial loading of tubes.\n"""

message_unassigned = """
    The analysis will proceed with the current data.
    You are encouraged to carefully examine the DataSheet for errors in segmentation,
    or in filling in the data.\n"""
