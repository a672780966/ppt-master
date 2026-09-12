"""PPT Master - Unified Workbench (P5). See scripts/docs/workbench.md.

UI Adapter over the existing runtime -- displays Runtime Truth, never
creates it. No route here reimplements a P1-P4 rule; every mutation goes
through the same runtime.revisions/tools.dispatch/runtime.hook_rules
functions the CLI already uses.
"""
