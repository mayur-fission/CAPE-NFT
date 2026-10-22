# ==============================================================================
# vcs_status_poll.R - version-control status polling as a live user session does it
#
# In production every Workbench session refreshes the version-control status
# of its working copy every 10 seconds - `git status` for Bitbucket repos,
# `svn status` for SVN working copies - on a large repository (~150,000 files)
# on shared network storage, in the background while the user works. This
# script reproduces that, and records how long every call takes: under load,
# rising call times are what users feel, and they show when the storage behind
# the repository becomes the limit.
#
# How it behaves like the real thing:
#   - a fixed timer, every INTERVAL_SECONDS (default 10); a call that runs
#     longer delays the next one, calls of one session never overlap
#   - each session's timer starts at a random point of the first interval,
#     as real users open their sessions at different moments (otherwise every
#     session would hit the storage in the same second)
#   - only the working copy is read - no remote (Bitbucket / SVN server) access
#   - BACKGROUND = TRUE runs the polling in its own process and returns at
#     once, so the session stays free for real work (e.g. an analysis script)
#
# Modes
#   Foreground (default): polls in the console until stopped with Stop / Esc /
#     Ctrl+C (the test automation stops it with Esc), or for RUN_MINUTES.
#   Background (VCS_POLL_BACKGROUND=true): starts the poller as a separate
#     Rscript process and returns. It stops on its own when the session that
#     started it ends, when logs/STOP is created, or after RUN_MINUTES
#     (default 240 in this mode, so a forgotten poller cannot run for ever).
#
# Output (./vcs_poll_output/logs/ under the working directory):
#   status_calls.csv   one row per call: time, call, vcs, seconds, exit_code,
#                      changed_entries, late_start_s, error
#   run_log.txt        a progress line every LOG_EVERY_SECONDS
#   run_summary.txt    call count, call-time median / p95 / max, errors
#   poller_errors.txt  (background mode) anything the poller process printed
#
# Settings: section 1, or environment variables (e.g. in ~/.Renviron), so one
# copy serves every repository unedited. Base R only; needs git and/or svn.
#
# Caution: on shared network storage, many sessions polling a ~150,000-file
# repository load that storage for everyone using it. Agree test runs with the
# storage owners and start small (20-50 sessions).
# ==============================================================================

# ---- 1. Settings (environment variables override these defaults) ---------------
env_or <- function(name, default) {
  value <- Sys.getenv(name, "")
  if (nzchar(value)) value else default
}
REPO_DIR             <- env_or("VCS_POLL_REPO", "")             # working copy to poll (required)
VCS                  <- env_or("VCS_POLL_VCS", "auto")          # "auto", "git" or "svn"
INTERVAL_SECONDS     <- as.numeric(env_or("VCS_POLL_INTERVAL_SECONDS", "10"))
RANDOM_START         <- tolower(env_or("VCS_POLL_RANDOM_START", "true")) == "true"
BACKGROUND           <- tolower(env_or("VCS_POLL_BACKGROUND", "false")) == "true"
CALL_TIMEOUT_SECONDS <- as.numeric(env_or("VCS_POLL_CALL_TIMEOUT_SECONDS", "300"))
LOG_EVERY_SECONDS    <- as.numeric(env_or("VCS_POLL_LOG_EVERY_SECONDS", "60"))
# Status command arguments. Match production's exact command if it differs
# (e.g. the IDE may add --untracked-files=all): VCS_POLL_STATUS_ARGS="status --porcelain ..."
STATUS_ARGS_DEFAULT  <- list(git = c("status", "--porcelain"), svn = c("status", "--non-interactive"))
STATUS_ARGS_OVERRIDE <- env_or("VCS_POLL_STATUS_ARGS", "")

PARENT_PID <- suppressWarnings(as.integer(Sys.getenv("VCS_POLL_PARENT_PID", "")))
IS_CHILD   <- !is.na(PARENT_PID)          # this process is a background poller
RUN_MINUTES <- as.numeric(env_or("VCS_POLL_RUN_MINUTES", if (BACKGROUND || IS_CHILD) "240" else "Inf"))

OUT_ROOT  <- file.path(getwd(), "vcs_poll_output")
DIR_LOGS  <- file.path(OUT_ROOT, "logs")
STOP_FILE <- file.path(DIR_LOGS, "STOP")

# ---- 2. Checks -----------------------------------------------------------------
if (!nzchar(REPO_DIR)) {
  stop("Set the working copy to poll: REPO_DIR in section 1, or the VCS_POLL_REPO environment variable")
}
REPO_DIR <- normalizePath(REPO_DIR, mustWork = FALSE)
if (!dir.exists(REPO_DIR)) stop("Repository folder not found: ", REPO_DIR)
if (!is.finite(INTERVAL_SECONDS) || INTERVAL_SECONDS <= 0) stop("VCS_POLL_INTERVAL_SECONDS must be > 0")

# REPO_DIR or the nearest folder above it containing `marker`, else NA
find_upwards <- function(dir, marker) {
  repeat {
    if (file.exists(file.path(dir, marker))) return(dir)
    parent <- dirname(dir)
    if (identical(parent, dir)) return(NA_character_)
    dir <- parent
  }
}
if (identical(VCS, "auto")) {
  VCS <- if (!is.na(find_upwards(REPO_DIR, ".git"))) "git"
         else if (!is.na(find_upwards(REPO_DIR, ".svn"))) "svn"
         else stop("No .git or .svn in or above ", REPO_DIR, " - set VCS_POLL_VCS to git or svn")
}
if (!VCS %in% c("git", "svn")) stop("VCS_POLL_VCS must be auto, git or svn, got: ", VCS)
TOOL <- Sys.which(VCS)
if (!nzchar(TOOL)) stop("`", VCS, "` is not on the PATH of this R session")
STATUS_ARGS <- if (nzchar(STATUS_ARGS_OVERRIDE)) strsplit(trimws(STATUS_ARGS_OVERRIDE), "\\s+")[[1]]
               else STATUS_ARGS_DEFAULT[[VCS]]
# git runs in the working copy with -C; svn takes it as the target
CALL_ARGS <- if (VCS == "git") c("-C", shQuote(REPO_DIR), STATUS_ARGS) else c(STATUS_ARGS, shQuote(REPO_DIR))

if (!dir.exists(DIR_LOGS)) dir.create(DIR_LOGS, recursive = TRUE)
if (!dir.exists(DIR_LOGS)) stop("Cannot create directory: ", DIR_LOGS)

# ---- 3. Background mode: start the poller process and return ---------------------
# Path of this script while it is being source()d (or run by Rscript)
this_script <- function() {
  path <- Sys.getenv("VCS_POLL_SCRIPT", "")
  if (nzchar(path)) return(normalizePath(path))
  for (i in rev(seq_len(sys.nframe()))) {
    f <- sys.frame(i)$ofile
    if (!is.null(f)) return(normalizePath(f))
  }
  arg <- grep("^--file=", commandArgs(FALSE), value = TRUE)
  if (length(arg)) return(normalizePath(sub("^--file=", "", arg[1])))
  stop("Cannot tell where this script is - set VCS_POLL_SCRIPT to its path")
}

start_background_poller <- function() {
  if (file.exists(STOP_FILE)) file.remove(STOP_FILE)
  script <- this_script()
  settings <- c(VCS_POLL_PARENT_PID = Sys.getpid(), VCS_POLL_REPO = REPO_DIR, VCS_POLL_VCS = VCS,
                VCS_POLL_INTERVAL_SECONDS = INTERVAL_SECONDS, VCS_POLL_RUN_MINUTES = RUN_MINUTES,
                VCS_POLL_RANDOM_START = RANDOM_START, VCS_POLL_BACKGROUND = "false",
                VCS_POLL_STATUS_ARGS = paste(STATUS_ARGS, collapse = " "))
  old <- Sys.getenv(names(settings), unset = NA)
  do.call(Sys.setenv, as.list(settings))     # inherited by the poller process
  on.exit({
    for (n in names(old)) if (is.na(old[[n]])) Sys.unsetenv(n) else do.call(Sys.setenv, setNames(list(old[[n]]), n))
  })
  system2(file.path(R.home("bin"), "Rscript"), shQuote(script), wait = FALSE,
          stdout = FALSE, stderr = file.path(DIR_LOGS, "poller_errors.txt"))
  cat(sprintf(paste0("Background `%s status` poller started for %s: every %gs, for at most %g min.\n",
                     "It stops when this session ends, or now with: file.create(\"%s\")\n",
                     "Calls are logged to %s\n"),
              VCS, REPO_DIR, INTERVAL_SECONDS, RUN_MINUTES, STOP_FILE, file.path(DIR_LOGS, "status_calls.csv")))
  invisible(TRUE)
}

# ---- 4. The poller -----------------------------------------------------------------
# Errors worth telling apart: another process holding git's index lock or the
# SVN working-copy database (wc.db) - typical when sessions share a working copy.
classify_error <- function(text) {
  if (grepl("index\\.lock", text)) return(paste("git index lock:", text))
  if (grepl("database is locked|E155037|E200033|E155004", text)) return(paste("svn wc.db lock:", text))
  text
}

# The session that started a background poller still exists (Linux /proc);
# where that cannot be checked, the RUN_MINUTES cap stops the poller
parent_alive <- function() {
  if (!IS_CHILD || !dir.exists("/proc")) return(TRUE)
  dir.exists(file.path("/proc", PARENT_PID))
}

run_poller <- function() {
  calls_csv   <- file.path(DIR_LOGS, "status_calls.csv")
  run_log     <- file.path(DIR_LOGS, "run_log.txt")
  summary_txt <- file.path(DIR_LOGS, "run_summary.txt")
  invisible(file.remove(Filter(file.exists, c(calls_csv, run_log, summary_txt))))
  # A STOP file left by an earlier background poller must not stop this run
  # (a background poller's own STOP file was cleared when it was started)
  if (!IS_CHILD && file.exists(STOP_FILE)) file.remove(STOP_FILE)
  writeLines("time,call,vcs,seconds,exit_code,changed_entries,late_start_s,error", calls_csv)

  t_start <- Sys.time()
  elapsed <- function() as.numeric(difftime(Sys.time(), t_start, units = "secs"))
  log_line <- function(fmt, ...) {
    line <- sprintf("[%s %7.1fs] %s", format(Sys.time(), "%H:%M:%S"), elapsed(), sprintf(fmt, ...))
    if (!IS_CHILD) { cat(line, "\n", sep = ""); flush.console() }
    cat(line, "\n", sep = "", file = run_log, append = TRUE)
  }
  csv_text <- function(x) paste0("\"", gsub("\"", "\"\"", gsub("[\r\n]+", " ", x)), "\"")

  status_call <- function() {
    err_file <- tempfile()
    on.exit(unlink(err_file))
    t0 <- proc.time()[["elapsed"]]
    out <- suppressWarnings(tryCatch(
      system2(TOOL, CALL_ARGS, stdout = TRUE, stderr = err_file, timeout = CALL_TIMEOUT_SECONDS),
      error = function(e) structure(character(0), status = -1L, message = conditionMessage(e))
    ))
    seconds <- proc.time()[["elapsed"]] - t0
    code <- attr(out, "status")
    code <- if (is.null(code)) 0L else as.integer(code)
    error <- ""
    if (code != 0L) {
      stderr_text <- if (file.exists(err_file)) paste(readLines(err_file, warn = FALSE), collapse = " ") else ""
      error <- if (code == 124L) sprintf("timed out after %gs", CALL_TIMEOUT_SECONDS)
               else classify_error(trimws(paste(attr(out, "message"), stderr_text)))
    }
    list(seconds = seconds, code = code, changed = if (code == 0L) length(out) else NA_integer_, error = error)
  }

  seconds <- numeric(0); ok <- logical(0); late_s <- numeric(0); errors <- character(0)
  stop_reason <- "run time reached"
  deadline <- if (is.finite(RUN_MINUTES)) RUN_MINUTES * 60 else Inf
  # First call at a random point of the first interval (see the header)
  next_due <- if (RANDOM_START) runif(1, 0, INTERVAL_SECONDS) else 0
  last_log <- 0

  log_line("Polling `%s status` in %s every %gs, first call after %.1fs (%s)", VCS, REPO_DIR, INTERVAL_SECONDS,
           next_due, if (is.finite(RUN_MINUTES)) sprintf("for at most %g min", RUN_MINUTES) else "until stopped")
  tryCatch({
    repeat {
      if (elapsed() >= deadline) { stop_reason <- "run time reached"; break }
      if (file.exists(STOP_FILE)) { stop_reason <- "stop file created"; break }
      if (!parent_alive()) { stop_reason <- "the session that started it ended"; break }
      wait <- next_due - elapsed()
      if (wait > 0) {
        Sys.sleep(min(wait, 1))                  # wake every second to notice a stop
        next
      }
      late <- -wait                              # how long after its due time this call starts
      r <- status_call()
      n <- length(seconds) + 1
      seconds[n] <- r$seconds; ok[n] <- r$code == 0L; late_s[n] <- late; errors[n] <- r$error
      cat(sprintf("%s,%d,%s,%.3f,%d,%s,%.3f,%s\n", format(Sys.time(), "%Y-%m-%d %H:%M:%OS3"), n, VCS,
                  r$seconds, r$code, if (is.na(r$changed)) "" else r$changed, late, csv_text(r$error)),
          file = calls_csv, append = TRUE)
      # Next due time on the fixed schedule; a slow call pushes it to "now"
      next_due <- next_due + INTERVAL_SECONDS
      if (next_due < elapsed()) next_due <- elapsed()

      if (elapsed() - last_log >= LOG_EVERY_SECONDS) {
        recent <- tail(seq_len(n), max(1, round(LOG_EVERY_SECONDS / INTERVAL_SECONDS)))
        log_line("%d calls | recent: median %.2fs, max %.2fs, failed %d | latest: %s", n,
                 median(seconds[recent]), max(seconds[recent]), sum(!ok[recent]),
                 if (r$code == 0L) sprintf("%d changed entries", r$changed) else r$error)
        last_log <- elapsed()
      }
    }
  }, interrupt = function(e) {
    stop_reason <<- "stopped (Esc / Stop / Ctrl+C)"
  })

  good <- seconds[ok]
  pct <- function(p) if (length(good)) unname(quantile(good, p)) else NA_real_
  error_kinds <- table(sub(":.*", "", errors[!ok]))
  summ <- c(
    "VCS STATUS POLLING - RUN SUMMARY",
    sprintf("Finished because:  %s", stop_reason),
    sprintf("Runtime:           %.1f s (%.2f min)", elapsed(), elapsed() / 60),
    sprintf("Repository:        %s (%s)", REPO_DIR, VCS),
    sprintf("Command:           %s %s", VCS, paste(STATUS_ARGS, collapse = " ")),
    sprintf("Interval:          %g s%s", INTERVAL_SECONDS, if (RANDOM_START) ", random start" else ""),
    sprintf("Status calls:      %d (ok %d, failed %d)", length(seconds), sum(ok), sum(!ok)),
    sprintf("Call time:         median %.3f s, p95 %.3f s, max %.3f s", pct(0.5), pct(0.95),
            if (length(good)) max(good) else NA_real_),
    sprintf("Late starts:       %d (a call still running when the next was due)", sum(late_s > 0.5)),
    sprintf("Errors:            %s", if (length(error_kinds))
      paste(names(error_kinds), as.integer(error_kinds), sep = " x", collapse = "; ") else "none"),
    sprintf("Output directory:  %s", normalizePath(OUT_ROOT))
  )
  writeLines(summ, summary_txt)
  if (!IS_CHILD) cat("\n", paste(summ, collapse = "\n"), "\n", sep = "")
  invisible(summ)
}

# ---- 5. Go -----------------------------------------------------------------------
if (BACKGROUND && !IS_CHILD) start_background_poller() else run_poller()
