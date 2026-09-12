# JobSignal source integration

The local workspace incorporates the user's JobSignal source at baseline `a11b052`, including the then-uncommitted changes in `jobsignal/collectors.py` and `jobsignal/models.py`.

Reused components are the CSS visual system, public-source collectors, employer programme definitions, country/model definitions and pure matching logic. These now live under `src/pilot/static` and `src/pilot/job_sources`. The new dashboard calls ApplyPilot's runtime and authoritative database. JobSignal's writable store, automatic expiry deletion, public login, SMTP and Discord delivery code are not used by the unified runtime.

Collector and programme tests are ported with their synthetic data. The original repository, uncommitted diff, environment, data and Git history are preserved outside this repository. No unrelated Git history or private configuration is merged into ApplyPilot.

Dependencies retain their respective licenses; no dependency source, binaries or fonts are bundled. The original JobSignal notice states that its Gmail-alert reference project was not vendored and is not a runtime dependency. This integration likewise copies no source from that reference project and adds no email-to-Discord service.
