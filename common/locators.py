"""Selectors and visible texts for the Posit Workbench / RStudio Pro UI,
grouped by the screen they belong to. Update them here when the UI changes.
"""

# --- Sign-in page (login()) -------------------------------------------------
USERNAME_LABEL = "Username"
PASSWORD_LABEL = "Password"
SIGN_IN_BUTTON = "Sign in"

# --- Session list ("home") page --------------------------------------------
NEW_SESSION_TEXT = "New Session"
SESSION_ROW_SELECTOR = "table tr"
SESSION_STATUS_CELL_SELECTOR = "[data-testid^='cell-status-']"
SESSION_STATUS_CELL_BY_ID = "[data-testid='%s']"
# Confirmation dialogs (quit, remove, save, Workbench job)
DIALOG_SELECTOR = "[role='dialog'], [role='alertdialog']"

# --- New Session dialog (launch_new_session()) ------------------------------
SESSION_NAME_FIELD_ROLE = "textbox"
SESSION_NAME_FIELD_NAME = "Session Name"
LAUNCH_BUTTON = "Launch"

# --- Session IDE (console automation) --------------------------------------
CONSOLE_TAB_TEXT = "Console"
CONSOLE_INPUT_SELECTOR = "#rstudio_console_input .ace_text-input"
CONSOLE_OUTPUT_SELECTOR = "#rstudio_console_output"

# --- Source editor pane (create_text_file()) --------------------------------
# No fixed selector: each source tab gets its own suffixed editor id and all
# tabs stay in the DOM. This JS returns the index of the one visible
# ".ace_text-input" outside the console (hidden tabs report 0x0).
ACTIVE_EDITOR_INPUT_INDEX_JS = """
() => {
    const inputs = Array.from(document.querySelectorAll('.ace_text-input'));
    for (let i = 0; i < inputs.length; i++) {
        const el = inputs[i];
        const r = el.getBoundingClientRect();
        const inConsole = el.closest('#rstudio_console_input') !== null;
        if (!inConsole && r.width > 0 && r.height > 0) return i;
    }
    return -1;
}
"""

# --- Session Details panel / force-quit (quit_session()) -------------------
DETAILS_LINK_TEXT = "Details"
SESSION_DETAILS_HEADING_TEXT = "Session Details"
FORCE_QUIT_BUTTON = "Force quit"
DISMISS_BUTTON = "Dismiss"
REMOVE_LINK_TEXT = "Remove"

# --- Bulk session selection / scoped Quit (bulk_quit_sessions()) -----------
# Each row's checkbox is found by role within the row (its name includes the
# session name). The Quit button's name includes the checked count, e.g.
# "Quit (3)", so BULK_QUIT_BUTTON_TEXT is a %-format template. It quits
# immediately, with no confirmation dialog.
SESSION_ROW_CHECKBOX_ROLE = "checkbox"
SESSION_ROW_CHECKBOX_NAME = None
BULK_QUIT_BUTTON_TEXT = "Quit (%d)"

# --- File menu / New Project Wizard (create_project_from_existing_directory())
FILE_MENU_SELECTOR = "#rstudio_label_file_menu"
NEW_PROJECT_MENU_ITEM_TEXT = "New Project..."
SAVE_WORKSPACE_DONT_SAVE_BUTTON = "Don't Save"
EXISTING_DIRECTORY_OPTION_TEXT = "Existing Directory"
CREATE_PROJECT_BUTTON = "Create Project"

# --- File menu / New File > Text File (create_text_file()) -----------------
NEW_FILE_MENU_ITEM_SELECTOR = "#rstudio_label_new_file_command"
TEXT_FILE_MENU_ITEM_TEXT = "Text File"

# --- Save File dialog (create_text_file()) ----------------------------------
SAVE_FILE_NAME_INPUT_SELECTOR = "#file_dialog_name_prompt"
SAVE_FILE_SAVE_BUTTON = "Save"

# --- Workbench Jobs pane / Start Workbench Job dialog (start_workbench_job()) -
# The Start Workbench Job button's accessible name is "Run a job on a
# cluster", so it is found by id. The dialog is titled "Run Script as
# Workbench Job"; its R Script box is read-only (Browse... only) and is
# pre-filled from the active editor tab.
WORKBENCH_JOBS_TAB_SELECTOR = "#rstudio_workbench_tab_workbench_jobs"
WORKBENCH_JOBS_PANEL_SELECTOR = "#rstudio_workbench_panel_workbench_jobs"
START_WORKBENCH_JOB_BUTTON_SELECTOR = "#rstudio_tb_startworkbenchjob"
WORKBENCH_JOB_DIALOG_TITLE = "Run Script as Workbench Job"
WORKBENCH_JOB_OPTIONS_TAB_TEXT = "Workbench Job Options"
WORKBENCH_JOB_ENVIRONMENT_TAB_SELECTOR = "#rstudio_job_launcher_pro_environment_tab"
WORKBENCH_JOB_SCRIPT_INPUT_SELECTOR = "#rstudio_tbb_text_pro_job_script"
WORKBENCH_JOB_START_BUTTON_SELECTOR = "#rstudio_dlg_ok"
WORKBENCH_JOB_CANCEL_BUTTON_SELECTOR = "#rstudio_dlg_cancel"
# Each job entry has a "Stop Job" button; it asks for confirmation in a
# "Stop Workbench Job" box whose own button is also named "Stop Job".
WORKBENCH_JOB_STOP_BUTTON = "Stop Job"
WORKBENCH_JOB_STOP_CONFIRM_TEXT = "Do you want to stop the job?"
WORKBENCH_JOB_ENVIRONMENT_LOADED_TEXT = "Current Environment"
WORKBENCH_JOB_FINAL_STATES = ("Finished", "Succeeded", "Failed", "Canceled", "Killed")
WORKBENCH_JOB_FAILED_STATES = ("Failed", "Canceled", "Killed")
WORKBENCH_JOB_ANY_STATES = ("Pending", "Running") + WORKBENCH_JOB_FINAL_STATES
