"""Python Memory Guardian's runtime profiler. pmg_profile.py is the entry point.

Dependencies go one way: runner -> session -> report and the collectors -> common. pmg_profile.py
calls cli.parse_args and passes the result to runner.run.
Every module here loads before the target starts, because the target's folder goes on sys.path
and the target can change sys.path. Do not add imports that first run after the target starts.
"""
