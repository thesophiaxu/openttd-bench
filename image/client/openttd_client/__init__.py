"""openttd_client: the agent-facing Python library (stdlib only).

from openttd_client import Game
g = Game()                                  # OPENTTD_API_URL + OPENTTD_API_TOKEN from the env
towns = g.inspect.towns(limit=50)           # one call = one batch of one
with g.batch() as b:                        # many calls = ONE game round trip
    towns = b.inspect.towns(limit=50)
    engines = b.rail.engines()
towns.value, engines.value                  # materialized when the context exits
g.rail.validate.station(x=50, y=90, direction="NE_SW", platforms=2, length=3)
g.wait(months=3); g.state(); g.events()
"""

from openttd_client.game import (
    Game,
    OpenTTDError,
    Pending,
    Record,
    Result,
    Ticket,
)

__all__ = ["Game", "OpenTTDError", "Pending", "Record", "Result", "Ticket"]
