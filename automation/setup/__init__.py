"""Pre-staging utilities used by ``mosdat functional`` (and friends).

Currently houses I1's ``inject_config`` — wipes Electron userData and writes a
declarative ``servers.json`` + ``config.json`` to both production and
``(development)``-suffixed userData directories on a target VM.
"""
