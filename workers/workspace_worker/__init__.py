"""The workspace worker: executes queued runs and builds environment snapshots.

Its own worker (rather than a mode inside the converter worker) because the two
have nothing in common: this one talks to a Docker daemon, runs *arbitrary* code
with a timeout, and starts and stops runtimes, while the converter worker runs a
fixed registry of trusted converters. Sharing a loop would mean one crash domain
for both.
"""
