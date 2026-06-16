# basic imports
import io
from datetime import datetime
from itertools import product
from pathlib import Path

# stats
import lifelines as ll

# plotting
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from lifelines import CoxPHFitter
from lifelines.statistics import logrank_test, proportional_hazard_test
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.gridspec import GridSpec
from matplotlib.ticker import MaxNLocator
from mpl_toolkits.axes_grid1.inset_locator import inset_axes
from seaborn import light_palette

matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["ps.fonttype"] = 42

###----------------------------------------------------------------------------
### Basic Functions, stats
###----------------------------------------------------------------------------


def distill_logrank(results, variable):
    a = results.null_distribution
    p = results.p_value
    t = results.test_statistic
    d = {
        "variable": variable,
        "p-value": f"{p:.2e}",
        a + " statistic": str(np.round(t, decimals=2)),
    }
    d = pd.DataFrame.from_dict(d, orient="index").transpose().set_index("variable")
    if p < 0.05:
        significant = "significant"
    else:
        significant = "non-significant"
    one_liner = "".join(
        [
            "Log-rank test for difference in survival ",
            f"on {variable} arms was {significant} ",
            "(p = ",
            f"{p:.2e})",
        ]
    )
    return d, p, one_liner


def Explanatory_Variables(dataframe):
    """
    This function determines the number of explanatory variables contained within lifespan dataset
    """
    cols_variable = dataframe.columns
    cols = ["Day_no.", "Event_code", "Rack_no.", "Tube_no."]
    Expl_variables = np.setdiff1d(cols_variable, cols)
    No_variables = len(Expl_variables)

    return Expl_variables, No_variables


def create_overall_KMplot(ax, Time_series, Event_series, Title_string, kmf):
    """
    Create a KM curve
    """
    kmf.fit(Time_series, event_observed=Event_series, label="Entire cohort")
    kmf.plot(ax=ax, show_censors=True, censor_styles={"ms": 10, "marker": "|"})
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_ylabel("Survival function (%)")
    ax.set_xlabel("Lifespan (Days)")
    ax.set_title(Title_string + " - Lifespan of entire cohort")


def create_replicate_KMplot(ax, arm_data, arm, Expl_Variables, ExpFolder, kmf):
    for rep, grouped_df in arm_data.groupby(arm_data["Replicate"]):
        kmf.fit(grouped_df["Day_no."], grouped_df["Event_code"], label=rep)
        kmf.plot(ax=ax, ci_show=False)
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_ylabel("Survival function (%)")
    ax.set_xlabel("Lifespan (Days)")
    if len(Expl_Variables) > 1:
        t = f"Replicates of arm {arm} ({Expl_Variables[0]}-{Expl_Variables[1]})"
    else:
        t = f"Replicates of arm {arm} ({Expl_Variables[0]})"
    ax.set_title(t, fontsize="medium")


def plot_variable_KMplot(ax, T, E, levels, Lifespan_Data, Expl_Variable, kmf):
    t_vars = []
    for i, level in enumerate(levels):
        kmf_level = kmf.fit(T[level], E[level], label=str(Lifespan_Data[Expl_Variable].unique()[i]))
        kmf_level.plot(
            ax=ax,
            legend=True,
            show_censors=True,
            censor_styles={"ms": 6, "marker": "d"},
        )
        t_vars.append(str(Lifespan_Data[Expl_Variable].unique()[i]))
    title = ": ".join([Expl_Variable, " vs. ".join([*t_vars])])
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.set_ylabel("Survival function (%)")
    ax.set_xlabel("Lifespan (Days)")
    ax.set_title(title, fontsize="medium")


def plot_multivariate_KMplot(ax, Lifespan_Data, Expl_Variables, ci_show, kmf):
    # standardised colourblind-friendly hues
    blu = light_palette((0, 0.45, 0.7), 5, reverse=True)[:-1]
    ong = light_palette((0.84, 0.37, 0), 5, reverse=True)[:-1]
    gin = light_palette((0.01, 0.62, 0.45), 5, reverse=True)[:-1]
    pik = light_palette((0.8, 0.47, 0.74), 6, reverse=True)[:-2]
    palettes = [blu, ong, gin, pik]

    if len(Expl_Variables) == 2:
        # stratification
        lvl_no = [
            len(Lifespan_Data[Expl_Variables[0]].unique()),
            len(Lifespan_Data[Expl_Variables[1]].unique()),
        ]
        min_lvl_idx = lvl_no.index(min(lvl_no))
        simpl_var = Expl_Variables[Expl_Variables == Expl_Variables[min_lvl_idx]][0]
        other_var = Expl_Variables[Expl_Variables == Expl_Variables[~min_lvl_idx]][0]
        simpl_levels = Lifespan_Data[simpl_var].unique().tolist()
        other_levels = Lifespan_Data[other_var].unique().tolist()
        strata = ["-".join([str(x[0]), str(x[1])]) for x in product(simpl_levels, other_levels)]
        # map stratification to colours
        colours = [x[: len(simpl_levels)] for x in palettes[: len(other_levels)]]
        colours = [item for sublist in colours for item in sublist]
        for colour, stratum in zip([*colours], strata):
            strat_data = Lifespan_Data[Lifespan_Data.stratified == stratum]
            kmf.fit(strat_data["Day_no."], strat_data["Event_code"], label=stratum)
            if ci_show:
                # all curves different colour
                kmf.plot(ax=ax, ci_show=ci_show)
            else:
                # minimal no. of hues with different lightness
                kmf.plot(
                    ax=ax,
                    color=colour,
                    ci_show=ci_show,
                    show_censors=True,
                    censor_styles={"ms": 6, "marker": "|"},
                )
        # fig elements
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))
        ax.set_ylabel("Survival function (%)")
        ax.set_xlabel("Lifespan (Days)")
        ax.set_title("Multivariate KM plot", fontsize="medium")

    elif len(Expl_Variables) == 1:
        # no stratification
        levels = Lifespan_Data[Expl_Variables[0]].unique().tolist()
        # map levels to colours
        for colour, level in zip(blu, levels):
            level_data = Lifespan_Data[Lifespan_Data[Expl_Variables[0]] == level]
            kmf.fit(level_data["Day_no."], level_data["Event_code"], label=level)
            if ci_show:
                # all curves different colour
                kmf.plot(ax=ax, ci_show=ci_show)
            else:
                # minimal no. of hues with different lightness
                kmf.plot(
                    ax=ax,
                    color=colour,
                    ci_show=ci_show,
                    show_censors=True,
                    censor_styles={"ms": 6, "marker": "|"},
                )
        # fig elements
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))
        ax.set_ylabel("Survival function (%)")
        ax.set_xlabel("Lifespan (Days)")
        ax.set_title("Multivariate KM plot", fontsize="medium")

    else:
        # is this necessary? in case this is a user-compiled?
        print("Inappropriate number of variables")


def plot_CPH_HR(ax, cph):
    cph.plot(ax=ax)


def replot_Schoenfeld(ax, figname, buffer):
    buffer.seek(0)
    im = plt.imread(buffer)
    buffer.close()
    ax.imshow(im, origin="upper", resample=True)


def plot_table(ax, stats_dict):
    # from dataframe to tabled data lists
    data = stats_dict["stats"].values.tolist()
    data = [[str(x) for x in sublist] for sublist in data]
    column_headers = stats_dict["stats"].columns.tolist()
    row_headers = stats_dict["stats"].index.tolist()
    # set manually column widths
    colWidths = []
    for col in range(len(column_headers)):
        maxtxt = max([len(x[col]) for x in data] + [len(max(column_headers[col].split("\n")))])
        colWidths.append(maxtxt)
    colWidths.append(max([len(x) for x in row_headers]))
    colWidths = [x / sum(colWidths) for x in colWidths]
    # add a table at the bottom of the axes
    the_table = ax.table(
        cellText=data,
        rowLabels=row_headers,
        rowLoc="center",
        colLabels=column_headers,
        cellLoc="center",
        loc="center right",
        colWidths=colWidths,
    )
    # style table
    for key, cell in the_table._cells.items():
        cell.PAD = 0.02
        cell.set_edgecolor("w")
        if (list(key)[0] % 2) == 0:
            cell.set_facecolor(plt.cm.Greys(0.1))
        if (list(key)[0] % 2) > 0:
            cell.set_edgecolor(plt.cm.Greys(0.1))
    for key, cell in the_table._cells.items():
        if any(np.array(key) < 0) or list(key)[0] == 0:
            cell.set_facecolor(plt.cm.Greys(0.30))
            cell.set_edgecolor("w")
    # scale height
    if any(["\n" in x for x in column_headers + row_headers]):
        the_table.scale(1, 2)
    else:
        the_table.scale(1, 1.5)
    # hide axes and border
    ax.get_xaxis().set_visible(False)
    ax.get_yaxis().set_visible(False)
    plt.box(on=None)


###----------------------------------------------------------------------------
### Basic Functions, writing the report
###----------------------------------------------------------------------------


def get_figure_size():
    fig_width_cm = 21  # A4 page
    fig_height_cm = 29.7
    inches_per_cm = 1 / 2.54  # cm to inches
    fig_width = fig_width_cm * inches_per_cm  # A4 width, inches
    fig_height = fig_height_cm * inches_per_cm  # A4 height, inches
    fig_size = [fig_width, fig_height]
    return fig_size


def plot_running_title(ax, Experiment_name):
    ax.set_xticks([])
    ax.set_yticks([])
    for side in ["top", "bottom", "left", "right"]:
        ax.spines[side].set_visible(False)
    ax.text(
        1,
        1,
        f"Stats report for experiment: {Experiment_name}",
        horizontalalignment="right",
        verticalalignment="center",
        fontsize="small",
        fontweight="normal",
        backgroundcolor="w",
    )


def plot_report_header(ax, Experiment_name):

    date = datetime.now().strftime("%Y-%m-%d (%H:%M)")
    ax.set_xticks([])
    ax.set_yticks([])
    for side in ["top", "bottom", "left", "right"]:
        ax.spines[side].set_visible(False)
    ax.text(
        0.5,
        0.8,
        "Survival Summary Statistics Report",
        horizontalalignment="center",
        verticalalignment="center",
        fontsize="xx-large",
        fontweight="bold",
    )
    ax.text(
        0.5,
        0.45,
        f"Experiment:\n{Experiment_name}",
        horizontalalignment="center",
        verticalalignment="center",
        fontsize="x-large",
        fontweight="bold",
    )
    ax.text(
        0.5,
        0.15,
        f"Report date: {date}",
        horizontalalignment="center",
        verticalalignment="center",
        fontsize="medium",
        fontweight="light",
    )


def plot_standard_element(ax, stats_dict):
    # plot title
    ax.set_xticks([])
    ax.set_yticks([])
    for side in ["bottom", "left", "right"]:
        ax.spines[side].set_visible(False)
    ax.spines["top"].set_linewidth(1)
    title = stats_dict["title"]
    ax.text(
        0,
        1.03,
        f" {title} ",
        horizontalalignment="left",
        verticalalignment="center",
        fontsize="large",
        fontweight="bold",
        color="k",
    )  # , backgroundcolor='w')
    ax.text(
        0.02,
        0.91,
        stats_dict["one_liner"],
        horizontalalignment="left",
        verticalalignment="top",
        fontsize="small",
    )
    # plot graph & table
    if stats_dict["stats"] is None:
        # only graph
        axin1 = inset_axes(
            ax,
            width="100%",
            height="100%",
            bbox_to_anchor=(0.035, 0.035, 0.95, 0.7),
            bbox_transform=ax.transAxes,
            loc=3,
        )
        axin1.xaxis.set_major_locator(MaxNLocator(integer=True))
        stats_dict["graph_func"](axin1, *stats_dict["graph_data"])
    else:
        # graph
        axin1 = inset_axes(
            ax,
            width="100%",
            height="100%",
            bbox_to_anchor=(0, 0.035, 0.6, 0.65),
            bbox_transform=ax.transAxes,
            loc=3,
        )
        axin1.xaxis.set_major_locator(MaxNLocator(integer=True))
        stats_dict["graph_func"](axin1, *stats_dict["graph_data"])
        # table
        axin2 = inset_axes(
            ax,
            width="100%",
            height="100%",
            bbox_to_anchor=(0.7, 0.035, 0.3, 0.65),
            bbox_transform=ax.transAxes,
            loc=3,
        )
        plot_table(axin2, stats_dict)


def plot_first_element(ax, stats_dict):
    # plot title
    ax.set_xticks([])
    ax.set_yticks([])
    for side in ["bottom", "left", "right"]:
        ax.spines[side].set_visible(False)
    ax.spines["top"].set_linewidth(1)
    title = stats_dict["title"]
    ax.text(
        0,
        1.03,
        f" {title} ",
        horizontalalignment="left",
        verticalalignment="center",
        fontsize="large",
        fontweight="bold",
    )  # , backgroundcolor='w',)
    ax.text(
        0.02,
        0.96,
        stats_dict["one_liner"],
        horizontalalignment="left",
        verticalalignment="top",
        fontsize="small",
    )
    # plot graph
    axin1 = inset_axes(
        ax,
        width="100%",
        height="100%",
        bbox_to_anchor=(0.035, 0.035, 0.95, 0.76),
        bbox_transform=ax.transAxes,
        loc=3,
    )
    axin1.xaxis.set_major_locator(MaxNLocator(integer=True))
    stats_dict["graph_func"](axin1, *stats_dict["graph_data"])


def plot_page_no(ax, page_no, page_total):
    ax.set_xticks([])
    ax.set_yticks([])
    for side in ["top", "bottom", "left", "right"]:
        ax.spines[side].set_visible(False)
    ax.text(
        1,
        1,
        f"page {page_no}/{page_total}",
        horizontalalignment="right",
        verticalalignment="center",
        fontsize="small",
        fontweight="normal",
        backgroundcolor="w",
    )


def plot_first_page(fig_size, stats_dict, page_no, page_total, Experiment_name):
    # basic makup of the figure
    plt.ioff()
    plt.rc("text", usetex=False)  # so that LaTeX is not required
    fig = plt.figure()
    fig.set_size_inches(fig_size)
    gs = GridSpec(58, 42, wspace=0.1, hspace=0.1)
    # all background to control colour
    ax0 = fig.add_subplot(gs[:, :])
    ax0.set_xticks([])
    ax0.set_yticks([])
    for side in ["top", "bottom", "left", "right"]:
        ax0.spines[side].set_visible(False)
    # plot title/header or first element
    ax1 = fig.add_subplot(gs[3:19, 2:40])
    plot_report_header(ax1, Experiment_name)
    # axis 4
    ax4 = fig.add_subplot(gs[24:50, 0:42])
    plot_first_element(ax4, stats_dict)
    # page no
    ax5 = fig.add_subplot(gs[56:58, 34:42])
    plot_page_no(ax5, 1, page_total)


def plot_following_page(fig_size, stats_dict_list, page_no, page_total, Experiment_name):
    # basic makup of the figure
    plt.ioff()
    plt.rc("text", usetex=False)  # so that LaTeX is not required
    fig = plt.figure()
    fig.set_size_inches(fig_size)
    gs = GridSpec(58, 42, wspace=0.1, hspace=0.1)
    # all background to control colour
    ax0 = fig.add_subplot(gs[:, :])
    ax0.set_xticks([])
    ax0.set_yticks([])
    for side in ["top", "bottom", "left", "right"]:
        ax0.spines[side].set_visible(False)
    # running title
    ax1 = fig.add_subplot(gs[0:2, 2:42])
    plot_running_title(ax1, Experiment_name)
    # axs 1-3
    ax2 = fig.add_subplot(gs[4:26, 0:42])
    plot_standard_element(ax2, stats_dict_list[0])
    if len(stats_dict_list) == 2:
        ax3 = fig.add_subplot(gs[30:52, 0:42])
        plot_standard_element(ax3, stats_dict_list[1])
    # page no
    ax4 = fig.add_subplot(gs[56:58, 38:42])
    plot_page_no(ax4, page_no, page_total)


###----------------------------------------------------------------------------
### Statistical functions
###----------------------------------------------------------------------------


def add_km(T, E, Experiment_Name, ExpFolder, Statistic_results, kmf):
    # REPORT SECTION 1 --- KAPLAN-MEIER PLOT OF ENTIRE COHORT
    # data for report
    Entire_dataset_dict = {
        "title": "KM curve of entire dataset",
        "one_liner": "\n".join(
            [
                "Visual inspection to determine if there are sudden increases in mortality",
                "(e.g. due to issues with food batch or incubator temperature)",
            ]
        ),
        "graph_data": [T, E, Experiment_Name, kmf],
        "graph_func": create_overall_KMplot,
        "stats": None,
    }
    Statistic_results.append(dict(Entire_dataset_dict))
    # individual figure
    ax = plt.subplot(1, 1, 1)
    create_overall_KMplot(ax, T, E, Experiment_Name, kmf)
    figname = Path(ExpFolder) / f"{Experiment_Name}__entire_cohort.pdf"
    ax.get_figure().savefig(str(figname))
    plt.close()


def add_replicates(Expl_Variables, Lifespan_Data, ExpFolder, Statistic_results, kmf):
    # REPORT SECTION 2 --- REPLICATES
    # create a stratified column which incorporates entries from all explanatory variables
    """
    ###
    ### HERE WE NEED TO ENFORCE THAT THE ORDER OF THE
    ### VARIABLES CORRESPONDS TO THE ASCENDING ORDER
    ### IN NUMBER OF LEVELS TO MATCH THE PLOTTING OF MULTIVARIATE KM
    ###
    """
    if len(Expl_Variables) == 1:
        # no stratification required
        Lifespan_Data["stratified"] = Lifespan_Data[Expl_Variables[0]]
    elif len(Expl_Variables) == 2:
        Lifespan_Data["stratified"] = (
            Lifespan_Data[[Expl_Variables[0], Expl_Variables[1]]]
            .astype(str)
            .apply("-".join, axis=1)
        )
    elif len(Expl_Variables) == 3:
        Lifespan_Data["stratified"] = (
            Lifespan_Data[[Expl_Variables[0], Expl_Variables[1], Expl_Variables[2]]]
            .astype(str)
            .apply("-".join, axis=1)
        )
    elif len(Expl_Variables) == 4:
        Lifespan_Data["stratified"] = (
            Lifespan_Data[
                [
                    Expl_Variables[0],
                    Expl_Variables[1],
                    Expl_Variables[2],
                    Expl_Variables[3],
                ]
            ]
            .astype(str)
            .apply("-".join, axis=1)
        )

    # prepare data
    Lifespan_Data["stratum_reps"] = (
        Lifespan_Data[["Rack_no.", "Tube_no.", "stratified"]].astype(str).apply("-".join, axis=1)
    )
    strata_replicate_map = {}
    for stratum in Lifespan_Data.stratified.unique().tolist():
        replicates = (
            Lifespan_Data[Lifespan_Data.stratified == stratum].stratum_reps.unique().tolist()
        )
        replicates_map = dict(zip(replicates, range(1, len(replicates) + 1)))
        strata_replicate_map.update(replicates_map)
    strata_replicate_map
    Lifespan_Data["Replicate"] = Lifespan_Data.stratum_reps.map(strata_replicate_map)
    Lifespan_Data = Lifespan_Data.drop(columns=["stratum_reps"])

    # can potentially have up to 16 levels in stratified column
    Experimental_arm_names = Lifespan_Data["stratified"].unique().tolist()
    for i, arm in enumerate(Experimental_arm_names):
        arm_data = Lifespan_Data[Lifespan_Data.stratified == arm]
        replicate_median_dict = {}
        # create individual figure
        ax = plt.subplot(111)
        create_replicate_KMplot(ax, arm_data, arm, Expl_Variables, ExpFolder, kmf)
        figname = Path(ExpFolder) / f"Arm_{arm}_Replicates.pdf"
        ax.get_figure().savefig(str(figname))
        plt.close()
        for rep, grouped_df in arm_data.groupby(arm_data["Replicate"]):
            kmf.fit(grouped_df["Day_no."], grouped_df["Event_code"], label=rep)
            # data for report
            replicate_name = f"Replicate {rep}"
            # median survival, lower 95% CI, and upper 95% CI.
            replicate_median_dict["columns"] = [
                f"Median lifespan\nfor {arm} (days)",
                "95% CI around S(t)=0.5",
            ]
            replicate_median_dict[replicate_name] = [
                kmf.median_survival_time_,
                (
                    np.round(
                        kmf.confidence_interval_.loc[kmf.median_survival_time_][0],
                        decimals=3,
                    ),
                    np.round(
                        kmf.confidence_interval_.loc[kmf.median_survival_time_][1],
                        decimals=3,
                    ),
                ),
            ]
        replicates_df = pd.DataFrame.from_dict(
            {key: value for key, value in replicate_median_dict.items() if key != "columns"},
            orient="index",
            columns=replicate_median_dict["columns"],
        )
        if len(Expl_Variables) > 1:
            title = f"KM plots, replicates of arm {arm} ({Expl_Variables[0]}-{Expl_Variables[1]})"
        else:
            title = f"KM plots, replicates of arm {arm} ({Expl_Variables[0]})"
        Replicates_dict = {
            "title": title,
            "one_liner": "This allows to determine if a particular replicate (tube) is an outlier",
            "graph_data": [arm_data, arm, Expl_Variables, ExpFolder, kmf],
            "graph_func": create_replicate_KMplot,
            "stats": replicates_df,
        }
        Statistic_results.append(Replicates_dict)


def add_variables(
    T,
    E,
    Expl_Variables,
    Lifespan_Data,
    ExpFolder,
    Statistic_results,
    kmf,
    Experiment_Name,
):
    # REPORT SECTION 3 --- VARIABLES

    def process_variable(variable):
        variable_logrank_dict = {}
        var_levels = Lifespan_Data[variable].unique().tolist()

        if len(var_levels) == 1:
            pass

        elif len(var_levels) == 2:
            level1 = Lifespan_Data[variable] == var_levels[0]
            # individual fiture
            ax = plt.subplot(111)
            levels = [level1, ~level1]
            plot_variable_KMplot(ax, T, E, levels, Lifespan_Data, variable, kmf)
            figname = Path(ExpFolder) / f"{Experiment_Name}_Variable_{variable}.pdf"
            ax.get_figure().savefig(str(figname))
            plt.close()
            # data for report
            # Log-rank test
            lvl1_df = Lifespan_Data[Lifespan_Data[variable] == var_levels[0]]
            lvl2_df = Lifespan_Data[Lifespan_Data[variable] == var_levels[1]]
            T_lvl1 = lvl1_df["Day_no."]
            E_lvl1 = lvl1_df["Event_code"]
            T_lvl2 = lvl2_df["Day_no."]
            E_lvl2 = lvl2_df["Event_code"]
            results_variable = logrank_test(T_lvl1, T_lvl2, E_lvl1, E_lvl2)
            d, p, one_liner = distill_logrank(results_variable, variable)
            variable_logrank_dict = {
                "title": f"Log-Rank results: {variable}",
                "one_liner": one_liner,
                "graph_data": [T, E, levels, Lifespan_Data, variable, kmf],
                "graph_func": plot_variable_KMplot,
                "stats": d,
            }

        elif len(var_levels) == 3:
            level1 = Lifespan_Data[variable] == var_levels[0]
            level2 = Lifespan_Data[variable] == var_levels[1]
            level3 = Lifespan_Data[variable] == var_levels[2]
            # individual figure
            levels = [level1, level2, level3]
            ax = plt.subplot(111)
            plot_variable_KMplot(ax, T, E, levels, Lifespan_Data, variable, kmf)
            f"{Experiment_Name}_Variable_{variable}.pdf"
            figname = Path(ExpFolder) / f"{Experiment_Name}_Variable_{variable}.pdf"
            ax.get_figure().savefig(str(figname))
            plt.close()
            # data for report
            # Log-rank test
            lvl1_df = Lifespan_Data[Lifespan_Data[variable] == var_levels[0]]
            lvl2_df = Lifespan_Data[Lifespan_Data[variable] == var_levels[1]]
            lvl3_df = Lifespan_Data[Lifespan_Data[variable] == var_levels[2]]
            T_lvl1 = lvl1_df["Day_no."]
            E_lvl1 = lvl1_df["Event_code"]
            T_lvl2 = lvl2_df["Day_no."]
            E_lvl2 = lvl2_df["Event_code"]
            T_lvl3 = lvl3_df["Day_no."]
            E_lvl3 = lvl3_df["Event_code"]
            results_variable = logrank_test(T_lvl1, T_lvl2, T_lvl3, E_lvl1, E_lvl2, E_lvl3)
            d, p, one_liner = distill_logrank(results_variable, variable)
            variable_logrank_dict = {
                "title": f"Log-Rank results: {Expl_Variables[0]}",
                "one_liner": one_liner,
                "graph_data": [T, E, levels, Lifespan_Data, variable, kmf],
                "graph_func": plot_variable_KMplot,
                "stats": d,
            }

        elif len(Lifespan_Data[Expl_Variables[0]].unique().tolist()) == 4:
            level1 = Lifespan_Data[variable] == var_levels[0]
            level2 = Lifespan_Data[variable] == var_levels[1]
            level3 = Lifespan_Data[variable] == var_levels[2]
            level4 = Lifespan_Data[variable] == var_levels[3]
            # individual figure
            levels = [level1, level2, level3, level4]
            ax = plt.subplot(111)
            plot_variable_KMplot(ax, T, E, levels, Lifespan_Data, variable, kmf)
            figname = Path(ExpFolder) / f"{Experiment_Name}_Variable_{variable}.pdf"
            ax.get_figure().savefig(str(figname))
            plt.close()
            # data for report
            # Log-rank test
            lvl1_df = Lifespan_Data[Lifespan_Data[variable] == var_levels[0]]
            lvl2_df = Lifespan_Data[Lifespan_Data[variable] == var_levels[1]]
            lvl3_df = Lifespan_Data[Lifespan_Data[variable] == var_levels[2]]
            lvl4_df = Lifespan_Data[Lifespan_Data[variable] == var_levels[3]]
            T_lvl1 = lvl1_df["Day_no."]
            E_lvl1 = lvl1_df["Event_code"]
            T_lvl2 = lvl2_df["Day_no."]
            E_lvl2 = lvl2_df["Event_code"]
            T_lvl3 = lvl3_df["Day_no."]
            E_lvl3 = lvl3_df["Event_code"]
            T_lvl4 = lvl4_df["Day_no."]
            E_lvl4 = lvl4_df["Event_code"]
            results_variable = logrank_test(
                T_lvl1, T_lvl2, T_lvl3, T_lvl4, E_lvl1, E_lvl2, E_lvl3, E_lvl4
            )
            d, p, one_liner = distill_logrank(results_variable, variable)
            variable_logrank_dict = {
                "title": f"Log-Rank results: {variable}",
                "one_liner": one_liner,
                "graph_data": [T, E, levels, Lifespan_Data, variable, kmf],
                "graph_func": plot_variable_KMplot,
                "stats": d,
            }

        else:
            # is this necessary? in case this is a user-compiled?
            print(f"Too many levels to variable {variable}")

        Statistic_results.append(variable_logrank_dict)

    for variable in Expl_Variables:
        process_variable(variable)


def add_variables_multiple(
    Expl_Variables, Lifespan_Data, ExpFolder, Statistic_results, kmf, Experiment_Name
):
    # REPORT SECTION 4 --- MULTIPLE VARIABLES
    # plotting multiple variables - with CI
    ax = plt.subplot(111)
    plot_multivariate_KMplot(ax, Lifespan_Data, Expl_Variables, True, kmf)
    figname = Path(ExpFolder) / f"{Experiment_Name}_MultivariatePlot_CI.pdf"
    ax.get_figure().savefig(str(figname))
    plt.close()
    Multivariate_dict_CI = {
        "title": "Multivariate KM curves, with CI",
        "one_liner": "Visual inspection of the different variables",
        "graph_data": [Lifespan_Data, Expl_Variables, True, kmf],
        "graph_func": plot_multivariate_KMplot,
        "stats": None,
    }
    Statistic_results.append(Multivariate_dict_CI)
    # plotting multiple variables - minimal colour no.
    ax = plt.subplot(111)
    plot_multivariate_KMplot(ax, Lifespan_Data, Expl_Variables, False, kmf)
    figname = Path(ExpFolder) / f"{Experiment_Name}_MultivariatePlot_mincol.pdf"
    ax.get_figure().savefig(str(figname))
    plt.close()
    Multivariate_dict_mincol = {
        "title": "Multivariate KM curves, simple colours",
        "one_liner": "Visual inspection of the different variables",
        "graph_data": [Lifespan_Data, Expl_Variables, False, kmf],
        "graph_func": plot_multivariate_KMplot,
        "stats": None,
    }
    Statistic_results.append(Multivariate_dict_mincol)


def add_cph(Expl_Variables, Lifespan_Data, ExpFolder, Statistic_results, Experiment_Name):
    # REPORT SECTION 5 - COX PROPORTIONAL HAZARDS MODELLING
    # preparing data for CPH
    # turn levels of strata into integer indices
    to_remove = ["stratified", "stratum_reps", "Rack_no.", "Tube_no."]
    for col in Expl_Variables.tolist():
        if not pd.to_numeric(Lifespan_Data[col], errors="coerce").notnull().all():
            to_remove.append(col)
            levels = Lifespan_Data[col].unique()
            Lifespan_Data[col + "_Integer"] = Lifespan_Data[col].map({levels[0]: 0, levels[1]: 1})
    # remove columns with string data types
    Lifespan_Data_CPH_friendly = Lifespan_Data[
        [x for x in Lifespan_Data.columns if x not in to_remove]
    ]
    # rename columns with reporting-friendly names
    Lifespan_Data_CPH_friendly = Lifespan_Data_CPH_friendly.rename(
        columns=dict(
            zip(
                Lifespan_Data_CPH_friendly.columns,
                [x.split("_Integer")[0] for x in Lifespan_Data_CPH_friendly.columns],
            )
        )
    )

    # CPH modelling
    cph = CoxPHFitter()
    cph.fit(Lifespan_Data_CPH_friendly, duration_col="Day_no.", event_col="Event_code")
    # individual figure
    ax = plt.subplot(111)
    plot_CPH_HR(ax, cph)
    figname = Path(ExpFolder) / f"{Experiment_Name}_CoxPH_model.pdf"
    ax.get_figure().savefig(str(figname))
    plt.close()
    # data for report
    CPH_dict = {}
    for row_number, row in cph.summary.iterrows():
        HR_CI = [row[1], row[5], row[6]]
        if row[8] > 0.05:
            cph_p = [row[8], "non-significant"]
        else:
            cph_p = [row[8], "significant"]
    summ2rep = cph.summary.copy(deep=True)
    summ2rep = summ2rep[["exp(coef)", "exp(coef) lower 95%", "exp(coef) upper 95%", "p"]]
    summ2rep = summ2rep.rename(
        columns={
            "exp(coef)": "Hazard Ratio",
            "exp(coef) lower 95%": "HR lower 95%",
            "exp(coef) upper 95%": "HR upper 95%",
            "p": "p-value",
        }
    )
    repmap = dict(
        zip(
            summ2rep.values.flatten(),
            [f"{x:.2e}" for x in summ2rep.values.flatten()],
        )
    )
    summ2rep = summ2rep.replace(repmap)
    one_liner = "".join(
        [
            f"Hazard ratio for {row_number} ",
            f"was {HR_CI[0]:.3f} (95% CI = {HR_CI[1]:.3f}, ",
            f"{HR_CI[2]:.3f}), p value was {cph_p[1]} ",
            f"at {cph_p[0]:.2e}",
        ]
    )
    CPH_dict = {
        "title": "Cox Proportional Hazard model",
        "one_liner": one_liner,
        "graph_data": [cph],
        "graph_func": plot_CPH_HR,
        "stats": summ2rep,
    }
    Statistic_results.append(CPH_dict)
    return cph, Lifespan_Data_CPH_friendly


def add_cph_checks(ExpFolder, Statistic_results, Experiment_Name, cph, Lifespan_Data_CPH_friendly):
    # SECTION 6 --- CPH ASSUMPTIONS
    cph.check_assumptions(
        Lifespan_Data_CPH_friendly,
        p_value_threshold=0.05,
        advice=False,
        show_plots=True,
    )
    # plot figure
    fig = plt.gcf()
    figname = Path(ExpFolder) / f"{Experiment_Name}_Schoenfeld_residuals"
    fig.savefig(str(figname) + ".pdf")
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=600)
    # data for report
    results = proportional_hazard_test(cph, Lifespan_Data_CPH_friendly, time_transform="rank")
    R = results.summary.copy(deep=True)
    R = R.rename(columns={"p": "p-value", "test_statistic": "Chi-squared statistic"})
    repmap = dict(zip(R.values.flatten(), [f"{x:.2e}" for x in R.values.flatten()]))
    R = R.replace(repmap)
    CPH_assumption_dict = {
        "title": "Proportional Hazards assumption",
        "one_liner": "Check the Proportional Hazards assumption (null hypothesis is that it is not met)",
        "graph_data": [str(figname), buffer],
        "graph_func": replot_Schoenfeld,
        "stats": R,
    }
    Statistic_results.append(CPH_assumption_dict)


###----------------------------------------------------------------------------
### Make PDF report
###----------------------------------------------------------------------------


def make_stats_report(Lifespan_Data, Experiment_Name, ExpFolder):

    # handle folder and file names
    ExpFolder = Path(ExpFolder)
    ExpFolder.mkdir(parents=True, exist_ok=True)
    filename = f"{Experiment_Name}_Stats_Report.pdf"
    filepath = ExpFolder / filename
    while filepath.is_file():
        if filename.split("_")[-1] == "Report.pdf":
            filename = f"{Experiment_Name}_Stats_Report_a.pdf"
        else:
            letter = filename.split("_")[-1].split(".")[0]
            from string import ascii_lowercase

            idx = ascii_lowercase.index(letter) + 1
            filename = f"{Experiment_Name}_Stats_Report_{ascii_lowercase[idx]}.pdf"
        filepath = ExpFolder / filename

    # plotting settings
    matplotlib.rcParams["pdf.fonttype"] = 42
    matplotlib.rcParams["ps.fonttype"] = 42

    # extract names for lifelines
    Expl_Variables, No_variables = Explanatory_Variables(Lifespan_Data)

    kmf = ll.KaplanMeierFitter()
    T = Lifespan_Data["Day_no."]
    E = Lifespan_Data["Event_code"]

    # make list of dictionaries storing elements for PDF report
    Statistic_results = []
    bundle = (Expl_Variables, Lifespan_Data, ExpFolder, Statistic_results)

    add_km(T, E, Experiment_Name, ExpFolder, Statistic_results, kmf)
    add_replicates(*bundle, kmf)
    add_variables(T, E, *bundle, kmf, Experiment_Name)
    add_variables_multiple(*bundle, kmf, Experiment_Name)
    cph, Lifespan_Data_CPH_friendly = add_cph(*bundle, Experiment_Name)
    add_cph_checks(ExpFolder, Statistic_results, Experiment_Name, cph, Lifespan_Data_CPH_friendly)
    # page total: 1 with header+1st element + ceil of no. elems/2 (2 elems/page)
    page_total = np.ceil((len(Statistic_results) - 1) / 2).astype(int) + 1
    # divide elements in 1st + groups of two + rest<2
    page_elems = [Statistic_results[x : x + 2] for x in range(1, len(Statistic_results), 2)]
    # to create PDF file
    figsize = get_figure_size()
    with PdfPages(str(filepath)) as pdf:
        for page_no in range(1, page_total + 1):
            if page_no == 1:
                plot_first_page(figsize, Statistic_results[0], page_no, page_total, Experiment_Name)
            else:
                plot_following_page(
                    figsize,
                    page_elems[page_no - 2],
                    page_no,
                    page_total,
                    Experiment_Name,
                )
            pdf.savefig(dpi=600, orientation="portrait")
            plt.close("all")

    return filepath
