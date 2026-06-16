
##########################################################################
# Title: Lifespan Analysis Script
# Author: Terrence M Trinca & Joaquin deNavascues
# Date: 2025-11-28
#
# Description:
#   This R script performs survival analysis on lifespan data, including:
#     1. Kaplan-Meier survival curves
#     2. Cox proportional hazards modeling
#        - Main effects and interactions
#        - Testing proportional hazards assumption
#        - PH assumption plots using Schoenfeld residuals
#
# Inputs:
#   - Excel file containing lifespan data (user selects via file.choose())
#   - Columns used as explanatory variables are specified in `explanatory_vars`
#   - Time variable specified in `Time_var`
#
# Outputs:
#   - PDFs:
#       * Kaplan-Meier plots
#       * Cox proportional hazards tables
#       * Proportional hazards assumption plots
#   - CSVs:
#       * Summary statistics by strata
#
# Notes:
#   - The script is modular and can handle varying numbers of explanatory variables.
##########################################################################


##############################################################################
###                             Functions                                  ###
##############################################################################

pkg_check <- function(pkgs){
  new <- pkgs[!(pkgs %in% installed.packages()[,"Package"])]
  if(length(new)) install.packages(new, dependencies = TRUE)
  invisible(lapply(pkgs, library, character.only = TRUE))
}

select_vars <- function(data, multiple = TRUE, title = "Select variables") {
  choices <- colnames(data)

  selected <- utils::select.list(
    choices = choices,
    multiple = multiple,
    title = title
  )

  # optional: warn if nothing selected
  if (length(selected) == 0) {
    warning("No variables selected.")
  }

  return(selected)
}

get_colors_by_var <- function(myfit, explanatory_vars, var_index = 1, palette = rainbow) {
  # Extract stratum names
  strata_names <- names(myfit$strata)
  if (is.null(strata_names)) stop("No strata found in myfit")

  # Split each stratum into components (e.g., "Var1=Level1,Var2=Level2")
  strata_split <- strsplit(strata_names, ",")

  # Extract the levels for the variable of interest
  var_levels <- sapply(strata_split, function(x) {
    gsub(paste0(explanatory_vars[var_index], "="), "", x[var_index])
  })

  # Assign a unique color to each level
  unique_colors <- palette(length(unique(var_levels)))
  color_map <- setNames(unique_colors, unique(var_levels))

  # Return colors mapped to each stratum
  colors <- color_map[var_levels]
  return(colors)
}

# Function to assign line types by levels of a given variable in a survfit object
get_lty_by_var <- function(myfit, explanatory_vars, var_index = 2, lty_values = 1:6) {
  # Extract stratum names
  strata_names <- names(myfit$strata)
  if (is.null(strata_names)) stop("No strata found in myfit")

  # Split each stratum into components (e.g., "Var1=Level1,Var2=Level2")
  strata_split <- strsplit(strata_names, ",")

  # Extract the levels for the variable of interest
  var_levels <- sapply(strata_split, function(x) {
    gsub(paste0(explanatory_vars[var_index], "="), "", x[var_index])
  })

  # Assign line types to unique levels
  n_levels <- length(unique(var_levels))
  if (n_levels > length(lty_values)) {
    warning("Not enough line types supplied; repeating line types")
  }
  lty_map <- setNames(rep(lty_values, length.out = n_levels), unique(var_levels))

  # Map line types to each stratum
  ltys <- lty_map[var_levels]
  return(ltys)
}

##############################################################################
###                             Packages                                   ###
##############################################################################

pkg_check(c("readxl", "survival", "survminer", "rstudioapi", "dplyr", "gridExtra", "grid"))

library(readxl)
library(survival)
library(survminer)
library(rstudioapi)
library(dplyr)
library(gridExtra)
library(grid)



##############################################################################
###                              Analysis                                  ###
##############################################################################


### 1. Select data and explanatory variables for analysis
file_path <- file.choose()
file_base <- tools::file_path_sans_ext(basename(file_path))
current_date <- format(Sys.Date(), "%Y%m%d")
LifespanData <- read_excel(file_path)
attach(LifespanData)
View(LifespanData)


# Choose Time variable
Time_var <- select_vars(LifespanData, multiple = FALSE)
# Choose Event variable
Event_var <- select_vars(LifespanData, multiple = FALSE)
# Choose explanatory variables
explanatory_vars <- select_vars(LifespanData, multiple = TRUE)


### 2. Automatic data modelling
mySurv<-Surv(LifespanData[[Time_var]], event = LifespanData[[Event_var]])

# dynamic formula creation
rhs <- paste(explanatory_vars, collapse = " + ")
formula <- as.formula(paste("mySurv ~", rhs))
myfit <- survfit(formula, data = LifespanData)


### 3. Plotting data
## 3.1 box plots
# Extract summary statistics from your fitted model

LifespanData$stratum <- apply(LifespanData[, explanatory_vars], 1, function(x) paste(explanatory_vars, "=", x, collapse = ", "))

summary_stats <- LifespanData %>%
  group_by(stratum) %>%
  summarise(
    n = n(),
    mean_time = mean(.data[[Time_var]], na.rm = TRUE),
    sd_time = sd(.data[[Time_var]], na.rm = TRUE),
    se = sd_time / sqrt(n),
    lower_CI = mean_time - qt(0.975, n - 1) * se,
    upper_CI = mean_time + qt(0.975, n - 1) * se
  )

csv_file <- file.path(dirname(file_path), paste0(file_base, "_SummaryStats_", format(Sys.Date(), "%Y%m%d"), ".csv"))
write.csv(summary_stats, file = csv_file, row.names = FALSE)
cat("Summary statistics saved to:", csv_file, "\n")

# plot summary statistics
pdf_barplot <- file.path(dirname(file_path), paste0(file_base, "_Barplot_", format(Sys.Date(), "%Y%m%d"), ".pdf"))
pdf(pdf_barplot, width = 10, height = 8)  # width and height in inches

bar_centres <- barplot(summary_stats$mean_time,
                       names.arg = summary_stats$stratum,
                       col = "white",
                       border = "black",
                       lwd = 2,
                       las = 2,
                       ylim = c(0, max(summary_stats$upper_CI) * 1.3),
                       ylab = bquote("Mean " * .(Time_var)),
                       main = bquote("Mean " * .(Time_var) * " ± 95% CI"))

# Add error bars for 95% CI
arrows(x0 = bar_centres,
       y0 = summary_stats$lower_CI,
       x1 = bar_centres,
       y1 = summary_stats$upper_CI,
       angle = 90,
       code = 3,
       length = 0.1,
       lwd = 2)

dev.off()
cat("Barplot saved to:", pdf_barplot, "\n")


## 3.2 KM curve
pdf_file <- file.path(dirname(file_path),paste0(file_base, "_KM_", current_date, ".pdf"))
pdf(pdf_file,width=7,height=5)

colours <- get_colors_by_var(myfit, explanatory_vars, var_index = 1)
ltys <- get_lty_by_var(myfit, explanatory_vars, var_index = 2)

plot(myfit, col = colours, lty=ltys, lwd = 2)
lLab <- gsub(",", ", ", names(myfit$strata))
legend("bottomleft", legend=lLab, col=colours,lty=ltys, horiz=FALSE, bty='n')
abline(h=0.5)
title(xlab=bquote(.(Time_var)), ylab="Survival function")
axis(1, at=seq(10 , 90, by=20), lwd=0, lwd.ticks=1)

dev.off()
cat("KM plot saved to:", pdf_file, "\n")


## 3.3 cumulative hazard curve
pdf_file_ch <- file.path(dirname(file_path),paste0(file_base, "_CH_", current_date, ".pdf"))
pdf(pdf_file_ch,width=7,height=5)

plot(myfit, fun = "event", col = colours, lty = ltys, lwd = 2, mark = 3)
legend("topleft", legend=lLab, col=colours,lty=ltys, horiz=FALSE, bty='n')
title(xlab = bquote(.(Time_var)), ylab = "Cumulative Hazard")
axis(1, at = seq(10, 90, by = 20), lwd = 1, lwd.ticks = 1)

dev.off()

cat("Cumulative hazard plot saved to:", pdf_file_ch, "\n")


### 4. Multi-variant analysis -
## 4.1 Cox Proportional Hazard testing

# main effect cox model
rhs_maineffect <- paste(explanatory_vars, collapse = " + ")
formula_maineffect <- as.formula(paste("mySurv ~", rhs_maineffect))
coxph_maineffect <- coxph(formula_maineffect)
table_maineffect <- as.data.frame(summary(coxph_maineffect)$coefficients)
table_maineffect$Variable <- rownames(table_maineffect)

# main effect + interaction cox model
rhs_withinteractions <- paste(explanatory_vars, collapse = " * ")
formula_withinteractions <- as.formula(paste("mySurv ~", rhs_withinteractions))
coxph_withinteractions <- coxph(formula_withinteractions)
table_interaction <- as.data.frame(summary(coxph_withinteractions)$coefficients)
table_interaction$Variable <- rownames(table_interaction)


pdf_file <- file.path(dirname(file_path), paste0(file_base, "_Coxph_Models_", format(Sys.Date(), "%Y%m%d"), ".pdf"))
pdf(pdf_file, width = 14, height = 8)

# Convert data frames to tableGrob
tbl_main <- tableGrob(table_maineffect, rows = NULL)
tbl_inter <- tableGrob(table_interaction, rows = NULL)

tbl_main <- gtable::gtable_add_rows(tbl_main, heights = unit(1, "line"), pos = 0)
tbl_main <- gtable::gtable_add_grob(tbl_main, textGrob("MAIN EFFECTS", gp = gpar(fontsize = 16, fontface = "bold")), t = 1, l = 1, r = ncol(tbl_main))
tbl_inter <- gtable::gtable_add_rows(tbl_inter, heights = unit(1, "line"), pos = 0)
tbl_inter <- gtable::gtable_add_grob(tbl_inter, textGrob("MAIN EFFECTS + INTERACTIONS", gp = gpar(fontsize = 16, fontface = "bold")), t = 1, l = 1, r = ncol(tbl_inter))

page_title <- textGrob(paste("Cox Proportional Hazard Models of:", file_base), gp = gpar(fontsize = 20, fontface = "bold"))

grid.arrange(page_title, tbl_main, tbl_inter, ncol = 1, heights = c(0.1, 0.45, 0.45))

dev.off()
cat("Cox model tables saved to:", pdf_file, "\n")


## 4.2 Testing assumptions of CoxPH

ph_main <- cox.zph(coxph_maineffect)
ph_inter <- cox.zph(coxph_withinteractions)

pdf_file <- file.path(dirname(file_path), paste0(file_base, "_CoxPH_Assumptions_", format(Sys.Date(), "%Y%m%d"), ".pdf"))
pdf(pdf_file, width = 10, height = 12)

grid.text("PH Assumption - Main Effects", gp = gpar(fontsize = 16, fontface = "bold"), x = 0.5, y = 0.97)
print(ggcoxzph(ph_main))
grid.newpage()
grid.text("PH Assumption - Main Effects + Interactions", gp = gpar(fontsize = 16, fontface = "bold"), x = 0.5, y = 0.97)
print(ggcoxzph(ph_inter))

dev.off()
cat("Cox PH assumption plots saved to:", pdf_file, "\n")



