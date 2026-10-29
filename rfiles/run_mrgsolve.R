library(mrgsolve)
library(dplyr)
library(ggplot2)

# ============================================================
# mrgsolve concurrent-session load test
# ============================================================

# ------------------------------------------------------------
# USER SETTINGS
# ------------------------------------------------------------

TARGET_SECONDS     <- 50 * 60   # 20-minute minimum runtime
N_SUBJECTS         <- 10000     
END_TIME           <- 168       
DELTA              <- 0.5       
SIMS_PER_ITERATION <- 3         # sequential simulations per iteration

# ------------------------------------------------------------
# UNIQUE RUN IDENTIFIER
# ------------------------------------------------------------

RUN_ID <- paste0(
  "PID_",
  Sys.getpid(),
  "_",
  format(Sys.time(), "%Y%m%d_%H%M%S")
)

# ------------------------------------------------------------
# OUTPUT FILES
# ------------------------------------------------------------

TIMING_FILE <- paste0(
  "mrgsolve_timing_",
  RUN_ID,
  ".csv"
)

RUN_SUMMARY_FILE <- paste0(
  "mrgsolve_run_summary_",
  RUN_ID,
  ".csv"
)

PLOT_FILE <- paste0(
  "mrgsolve_CP_profile_",
  RUN_ID,
  ".png"
)

# ------------------------------------------------------------
# PK MODEL
# ------------------------------------------------------------

code <- '
$PARAM
CL = 5,
V1 = 50,
Q  = 8,
V2 = 100,
KA = 1.2

$OMEGA
0.09
0.09
0.04

$CMT GUT CENT PERIPH

$MAIN

double CLi = CL * exp(ETA(1));
double V1i = V1 * exp(ETA(2));
double KAi = KA * exp(ETA(3));

$ODE

dxdt_GUT =
  -KAi * GUT;

dxdt_CENT =
   KAi * GUT
   - (CLi/V1i) * CENT
   - (Q/V1i) * CENT
   + (Q/V2) * PERIPH;

dxdt_PERIPH =
   (Q/V1i) * CENT
   - (Q/V2) * PERIPH;

$TABLE

double CP = CENT/V1i;

$CAPTURE CP CLi V1i KAi
'

# ------------------------------------------------------------
# COMPILE MODEL
# ------------------------------------------------------------

mod <- mcode(
  "load_test_model",
  code
)

# ------------------------------------------------------------
# DOSING REGIMEN
# 100 mg Q24h x 7 doses
# ------------------------------------------------------------

dose <- ev(
  amt  = 100,
  ii   = 24,
  addl = 6,
  cmt  = 1
)

# ------------------------------------------------------------
# START
# ------------------------------------------------------------

cat("\n")
cat("====================================================\n")
cat("mrgsolve CONCURRENT-SESSION LOAD TEST\n")
cat("====================================================\n")
cat("PID:                 ", Sys.getpid(), "\n")
cat("Run ID:              ", RUN_ID, "\n")
cat("Subjects/simulation: ", N_SUBJECTS, "\n")
cat("Sims/iteration:      ", SIMS_PER_ITERATION, "\n")
cat("Simulation end:      ", END_TIME, " hr\n")
cat("Delta:               ", DELTA, " hr\n")
cat("Target duration:     ", TARGET_SECONDS, " sec\n")
cat("Start:               ", format(Sys.time()), "\n")
cat("====================================================\n\n")

start_time <- Sys.time()

iteration <- 0

timing_results <- list()

final_cp_summary <- NULL

# ------------------------------------------------------------
# MAIN LOAD-TEST LOOP
# ------------------------------------------------------------

repeat {
  
  iteration <- iteration + 1
  
  iteration_start <- Sys.time()
  
  sim_times <- numeric(SIMS_PER_ITERATION)
  
  # ----------------------------------------------------------
  # SEQUENTIAL SIMULATIONS
  # ----------------------------------------------------------
  
  for (j in seq_len(SIMS_PER_ITERATION)) {
    
    sim_start <- Sys.time()
    
    out <- mrgsim(
      mod,
      events = dose,
      nid    = N_SUBJECTS,
      end    = END_TIME,
      delta  = DELTA,
      output = "df"
    )
    
    sim_end <- Sys.time()
    
    sim_times[j] <- as.numeric(
      difftime(
        sim_end,
        sim_start,
        units = "secs"
      )
    )
    
    cat(
      sprintf(
        "  Simulation %d/%d | %.2f sec\n",
        j,
        SIMS_PER_ITERATION,
        sim_times[j]
      )
    )
    
    # Delete intermediate simulation outputs
    if (j < SIMS_PER_ITERATION) {
      
      rm(out)
      
      gc(verbose = FALSE)
    }
  }
  
  # ----------------------------------------------------------
  # CP SUMMARY BY TIME
  #
  # Median and 90% prediction interval across subjects
  # ----------------------------------------------------------
  
  cp_summary <- out %>%
    group_by(time) %>%
    summarise(
      
      median_CP = median(
        CP,
        na.rm = TRUE
      ),
      
      p05_CP = unname(
        quantile(
          CP,
          probs = 0.05,
          na.rm = TRUE
        )
      ),
      
      p95_CP = unname(
        quantile(
          CP,
          probs = 0.95,
          na.rm = TRUE
        )
      ),
      
      .groups = "drop"
    )
  
  # ----------------------------------------------------------
  # OVERALL CP STATISTICS
  #
  # These pool observations across subjects and time.
  # ----------------------------------------------------------
  
  cp_median_all <- median(
    out$CP,
    na.rm = TRUE
  )
  
  cp_p05_all <- unname(
    quantile(
      out$CP,
      probs = 0.05,
      na.rm = TRUE
    )
  )
  
  cp_p95_all <- unname(
    quantile(
      out$CP,
      probs = 0.95,
      na.rm = TRUE
    )
  )
  
  # ----------------------------------------------------------
  # PEAK MEDIAN PROFILE
  # ----------------------------------------------------------
  
  peak_row <- cp_summary %>%
    slice_max(
      median_CP,
      n = 1,
      with_ties = FALSE
    )
  
  peak_time <- peak_row$time
  
  peak_median_CP <- peak_row$median_CP
  peak_p05_CP    <- peak_row$p05_CP
  peak_p95_CP    <- peak_row$p95_CP
  
  # ----------------------------------------------------------
  # VALUE AT FINAL SIMULATION TIME
  # ----------------------------------------------------------
  
  final_row <- cp_summary %>%
    slice_max(
      time,
      n = 1,
      with_ties = FALSE
    )
  
  final_time <- final_row$time
  
  final_median_CP <- final_row$median_CP
  final_p05_CP    <- final_row$p05_CP
  final_p95_CP    <- final_row$p95_CP
  
  # ----------------------------------------------------------
  # RETAIN SMALL SUMMARY ONLY
  # ----------------------------------------------------------
  
  final_cp_summary <- cp_summary
  
  # ----------------------------------------------------------
  # REMOVE PREVIOUS PLOT OBJECT
  # ----------------------------------------------------------
  
  # if (exists("p_live")) {
  #   rm(p_live)
  # }
  if (exists("p_live")) {
    ggsave(PLOT_FILE, p_live, width = 8, height = 5)
  } else {
    message("p_live not found: final plot was not saved.")
  }
  # ----------------------------------------------------------
  # CREATE ANNOTATION TEXT
  # ----------------------------------------------------------
  
  annotation_text <- paste0(
    "Peak median CP: ",
    sprintf("%.3f", peak_median_CP),
    "\n",
    "90% PI: ",
    sprintf("%.3f", peak_p05_CP),
    " - ",
    sprintf("%.3f", peak_p95_CP),
    "\n",
    "Peak time: ",
    sprintf("%.1f", peak_time),
    " h"
  )
  
  # ----------------------------------------------------------
  # CREATE LIVE PLOT
  # ----------------------------------------------------------
  
  p_live <- ggplot(
    cp_summary,
    aes(x = time)
  ) +
    
    # 90% prediction interval
    geom_ribbon(
      aes(
        ymin = p05_CP,
        ymax = p95_CP
      ),
      fill = "#5B9BD5",
      alpha = 0.25
    ) +
    
    # Median
    geom_line(
      aes(y = median_CP),
      color = "#1F4E79",
      linewidth = 1.1
    ) +
    
    # Mark maximum median concentration
    geom_point(
      data = peak_row,
      aes(
        x = time,
        y = median_CP
      ),
      color = "#C00000",
      size = 3
    ) +
    
    # CP statistics box
    annotate(
      "label",
      x = Inf,
      y = Inf,
      label = annotation_text,
      hjust = 1.05,
      vjust = 1.15,
      size = 4,
      color = "#1F1F1F",
      fill = "white",
      label.size = 0.3
    ) +
    
    labs(
      
      title =
        "Simulated Concentration-Time Profile",
      
      subtitle = paste0(
        "Iteration ",
        iteration,
        " | ",
        format(
          N_SUBJECTS,
          big.mark = ",",
          scientific = FALSE
        ),
        " subjects"
      ),
      
      x =
        "Time (hours)",
      
      y =
        "Concentration (CP)",
      
      caption =
        "Solid line: median | Shaded region: 5th-95th percentiles"
    ) +
    
    theme_minimal(
      base_size = 13
    ) +
    
    theme(
      
      panel.grid.minor =
        element_blank(),
      
      plot.title =
        element_text(
          face = "bold",
          size = 16
        ),
      
      plot.subtitle =
        element_text(
          color = "grey35"
        ),
      
      axis.title =
        element_text(
          face = "bold"
        ),
      
      plot.caption =
        element_text(
          color = "grey45",
          hjust = 0
        )
    )
  
  # ----------------------------------------------------------
  # DISPLAY UPDATED PLOT
  # ----------------------------------------------------------
  
  LIVE_PLOT_FILE <- paste0(
    "mrgsolve_live_",
    RUN_ID,
    ".png"
  )
  
  ggsave(
    filename = LIVE_PLOT_FILE,
    plot = p_live,
    width = 9,
    height = 5.5,
    units = "in",
    dpi = 120,
    bg = "white"
  )
  
  rm(p_live)
  gc(verbose = FALSE)
  
  # ----------------------------------------------------------
  # DELETE SUBJECT-LEVEL DATA
  # ----------------------------------------------------------
  
  rm(out)
  
  gc(verbose = FALSE)
  
  # ----------------------------------------------------------
  # TIMING
  # ----------------------------------------------------------
  
  iteration_end <- Sys.time()
  
  iteration_sec <- as.numeric(
    difftime(
      iteration_end,
      iteration_start,
      units = "secs"
    )
  )
  
  elapsed_sec <- as.numeric(
    difftime(
      iteration_end,
      start_time,
      units = "secs"
    )
  )
  
  # ----------------------------------------------------------
  # SAVE TIMING + COMPACT CP SUMMARY
  #
  # No subject-level data are stored.
  # ----------------------------------------------------------
  
  timing_results[[iteration]] <- data.frame(
    
    run_id =
      RUN_ID,
    
    pid =
      Sys.getpid(),
    
    iteration =
      iteration,
    
    subjects_per_sim =
      N_SUBJECTS,
    
    sims_per_iteration =
      SIMS_PER_ITERATION,
    
    subject_sims =
      N_SUBJECTS * SIMS_PER_ITERATION,
    
    iteration_sec =
      iteration_sec,
    
    mean_sim_sec =
      mean(sim_times),
    
    min_sim_sec =
      min(sim_times),
    
    max_sim_sec =
      max(sim_times),
    
    elapsed_sec =
      elapsed_sec,
    
    pooled_median_CP =
      cp_median_all,
    
    pooled_p05_CP =
      cp_p05_all,
    
    pooled_p95_CP =
      cp_p95_all,
    
    peak_time_hr =
      peak_time,
    
    peak_median_CP =
      peak_median_CP,
    
    peak_p05_CP =
      peak_p05_CP,
    
    peak_p95_CP =
      peak_p95_CP,
    
    final_time_hr =
      final_time,
    
    final_median_CP =
      final_median_CP,
    
    final_p05_CP =
      final_p05_CP,
    
    final_p95_CP =
      final_p95_CP
  )
  
  # ----------------------------------------------------------
  # CONSOLE OUTPUT
  # ----------------------------------------------------------
  
  cat(
    sprintf(
      paste0(
        "\nIteration %3d",
        " | iteration time = %.2f sec",
        " | mean sim = %.2f sec",
        " | elapsed = %.1f sec\n"
      ),
      iteration,
      iteration_sec,
      mean(sim_times),
      elapsed_sec
    )
  )
  
  cat(
    sprintf(
      paste0(
        "  Overall CP median: %.4f",
        " | 90%% PI: [%.4f, %.4f]\n"
      ),
      cp_median_all,
      cp_p05_all,
      cp_p95_all
    )
  )
  
  cat(
    sprintf(
      paste0(
        "  Peak median CP: %.4f",
        " at %.1f hr",
        " | 90%% PI: [%.4f, %.4f]\n"
      ),
      peak_median_CP,
      peak_time,
      peak_p05_CP,
      peak_p95_CP
    )
  )
  
  cat(
    sprintf(
      paste0(
        "  CP at %.1f hr:",
        " median = %.4f",
        " | 90%% PI: [%.4f, %.4f]\n\n"
      ),
      final_time,
      final_median_CP,
      final_p05_CP,
      final_p95_CP
    )
  )
  
  # ----------------------------------------------------------
  # STOP CONDITION
  # ----------------------------------------------------------
  
  if (elapsed_sec >= TARGET_SECONDS) {
    break
  }
}

# ------------------------------------------------------------
# END RUN
# ------------------------------------------------------------

end_time <- Sys.time()

total_time <- as.numeric(
  difftime(
    end_time,
    start_time,
    units = "secs"
  )
)

# ------------------------------------------------------------
# COMBINE TIMING RESULTS
# ------------------------------------------------------------

timing_results <- do.call(
  rbind,
  timing_results
)

# ------------------------------------------------------------
# SAVE TIMING RESULTS
# ------------------------------------------------------------

write.csv(
  timing_results,
  TIMING_FILE,
  row.names = FALSE
)

# ------------------------------------------------------------
# PERFORMANCE METRICS
# ------------------------------------------------------------

total_simulations <-
  iteration * SIMS_PER_ITERATION

total_subject_sims <-
  total_simulations * N_SUBJECTS

# ------------------------------------------------------------
# RUN SUMMARY
# ------------------------------------------------------------

run_summary <- data.frame(
  
  run_id =
    RUN_ID,
  
  pid =
    Sys.getpid(),
  
  start_time =
    as.character(start_time),
  
  end_time =
    as.character(end_time),
  
  target_runtime_sec =
    TARGET_SECONDS,
  
  actual_runtime_sec =
    total_time,
  
  iterations =
    iteration,
  
  sims_per_iteration =
    SIMS_PER_ITERATION,
  
  total_simulations =
    total_simulations,
  
  subjects_per_sim =
    N_SUBJECTS,
  
  total_subject_sims =
    total_subject_sims,
  
  simulation_end_hr =
    END_TIME,
  
  delta_hr =
    DELTA,
  
  mean_iteration_sec =
    mean(
      timing_results$iteration_sec
    ),
  
  min_iteration_sec =
    min(
      timing_results$iteration_sec
    ),
  
  max_iteration_sec =
    max(
      timing_results$iteration_sec
    ),
  
  mean_sim_sec =
    mean(
      timing_results$mean_sim_sec
    ),
  
  min_sim_sec =
    min(
      timing_results$min_sim_sec
    ),
  
  max_sim_sec =
    max(
      timing_results$max_sim_sec
    ),
  
  simulations_per_min =
    total_simulations /
    (total_time / 60),
  
  subject_sims_per_sec =
    total_subject_sims /
    total_time,
  
  final_peak_time_hr =
    tail(
      timing_results$peak_time_hr,
      1
    ),
  
  final_peak_median_CP =
    tail(
      timing_results$peak_median_CP,
      1
    ),
  
  final_peak_p05_CP =
    tail(
      timing_results$peak_p05_CP,
      1
    ),
  
  final_peak_p95_CP =
    tail(
      timing_results$peak_p95_CP,
      1
    ),
  
  timing_file =
    TIMING_FILE,
  
  plot_file =
    PLOT_FILE
)

# ------------------------------------------------------------
# SAVE RUN SUMMARY
# ------------------------------------------------------------

write.csv(
  run_summary,
  RUN_SUMMARY_FILE,
  row.names = FALSE
)

# ------------------------------------------------------------
# SAVE FINAL PLOT
# ------------------------------------------------------------

ggsave(
  filename = PLOT_FILE,
  plot = p_live,
  width = 9,
  height = 5.5,
  units = "in",
  dpi = 300,
  bg = "white"
)

# ------------------------------------------------------------
# FINAL CONSOLE SUMMARY
# ------------------------------------------------------------

cat("\n")
cat("====================================================\n")
cat("LOAD TEST COMPLETE\n")
cat("====================================================\n")

cat("Run ID:                ", RUN_ID, "\n")
cat("PID:                   ", Sys.getpid(), "\n")
cat("Start:                 ", format(start_time), "\n")
cat("End:                   ", format(end_time), "\n")

cat(
  "Actual runtime:        ",
  round(total_time, 1),
  " sec\n"
)

cat(
  "Iterations:            ",
  iteration,
  "\n"
)

cat(
  "Total simulations:     ",
  total_simulations,
  "\n"
)

cat(
  "Subjects/simulation:   ",
  N_SUBJECTS,
  "\n"
)

cat(
  "Total subject sims:    ",
  total_subject_sims,
  "\n"
)

cat(
  "Mean simulation time:  ",
  round(
    mean(timing_results$mean_sim_sec),
    2
  ),
  " sec\n"
)

cat(
  "Mean iteration time:   ",
  round(
    mean(timing_results$iteration_sec),
    2
  ),
  " sec\n"
)

cat(
  "Simulations/minute:    ",
  round(
    total_simulations /
      (total_time / 60),
    2
  ),
  "\n"
)

cat(
  "Subject sims/second:   ",
  round(
    total_subject_sims /
      total_time,
    1
  ),
  "\n"
)

cat("\n")
cat("FINAL CP SUMMARY\n")

cat(
  sprintf(
    paste0(
      "Peak median CP: %.4f",
      " at %.1f hr",
      " | 90%% PI: [%.4f, %.4f]\n"
    ),
    tail(
      timing_results$peak_median_CP,
      1
    ),
    tail(
      timing_results$peak_time_hr,
      1
    ),
    tail(
      timing_results$peak_p05_CP,
      1
    ),
    tail(
      timing_results$peak_p95_CP,
      1
    )
  )
)

cat(
  sprintf(
    paste0(
      "CP at %.1f hr:",
      " median = %.4f",
      " | 90%% PI: [%.4f, %.4f]\n"
    ),
    tail(
      timing_results$final_time_hr,
      1
    ),
    tail(
      timing_results$final_median_CP,
      1
    ),
    tail(
      timing_results$final_p05_CP,
      1
    ),
    tail(
      timing_results$final_p95_CP,
      1
    )
  )
)

cat("\n")
cat("Timing results:        ", TIMING_FILE, "\n")
cat("Run summary:           ", RUN_SUMMARY_FILE, "\n")
cat("Final plot:            ", PLOT_FILE, "\n")
cat("====================================================\n")