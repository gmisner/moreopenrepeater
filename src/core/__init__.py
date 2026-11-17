"""The autonomous core as its own process (Stage 3 slice 6, #229).

`python -m core` (or `moreopenrepeater-core`) runs the repeater with no web
app: no dashboard, no API, no accounts. It starts from the local saved
configuration and needs no network and no other service. It is an option, off
by default, and is not installed by `install-pi.sh`. The normal single-process
service is unchanged.

Nothing here imports a web framework.
"""
