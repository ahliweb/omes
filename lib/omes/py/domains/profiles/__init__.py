"""lib/omes/py/domains/profiles - per-provider capability profiles as
DATA (issue #99: "Cloudflare registrar+DNS capability profiles as data").

A profile module exports plain dict/tuple literals matching
`contracts/domains/v1/registrar-capability.schema.json`, never executable
network logic - see each profile module's docstring for the official
documentation each field is derived from.
"""
