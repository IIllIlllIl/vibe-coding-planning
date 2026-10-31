# Archived entrypoints

These scripts are retained for historical reproducibility only. Neither
`scripts/run_batch.sh` nor `scripts/long_run_watchdog.py` is a current launch
default on the Behavioral branch.

Do not add new production references to files in this directory.

The fixed-case Behavioral launch/Stage-B probes, clean411-specific deficit
builder and shell-damaged legacy Plan replay builder are retained here with
their original inputs and format assumptions. They are not generic recovery
or current learning entry points.

The old `login_sif_preheat_watchdog.py` and its original regression file are
also historical: the wrapper imports downloader defaults removed by the
maintained acquisition path and has no current caller. Its test is kept here,
outside default test discovery, for reproduction with its original source
revision. New acquisition uses `tools/login_apptainer_sif_preheat.py` and the
reviewed service/config entry points, not a second watchdog implementation.
