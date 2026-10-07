# office_week: overnight full-week generation for ashiorid_office

Started 2026-10-01 ~01:30 EDT. Runs unattended as the user systemd unit
`office-week-gen` (survives this terminal closing; linger is on).

- Config: generator config id 10, "ashiorid_office full week (vllm hermes3-70b,
  28 blocks)", built by `build_week_config.py` from config 9 (the full day).
  168h = 28 six-hour blocks, Sunday 00:00 -> Saturday 24:00, each block with a
  day-specific focus. Story: Halvard & Sons pilot goes live Saturday;
  keystone Wednesday fraud miss at Corvane; Thursday regulator audit.
- Driver: `overnight_week_driver.py`. Arc stage, then per day
  (4 segments): segment -> dialogue -> audit -> move bad slots aside
  (`slots_badSlots_auto/`) -> one dialogue retry. Stops submitting new work
  after 8h. All jobs go through the generator API (visible in campaign-manager).
- Progress: `overnight_status.json`, `overnight_driver.log` (this dir).

## Check / control

    systemctl --user status office-week-gen
    cat .claude/prompts/office_week/overnight_status.json
    python3 .claude/prompts/audit_takes.py utilities/3LayersWeeklyGeneration/output/<run>
    systemctl --user stop office-week-gen          # stop driver (running job finishes)
    # resume after a crash, skipping the arc stage and already-briefed segments:
    systemd-run --user --unit=office-week-gen2 --working-directory=$PWD/.claude/prompts/office_week \
      $PWD/.venv/bin/python $PWD/.claude/prompts/office_week/overnight_week_driver.py --resume-run <run>

Config 10 is left ACTIVE afterwards; re-activate 9 for single-day work.
Nothing is promoted; promotion stays a manual user step.
