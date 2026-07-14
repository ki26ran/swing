import sys
sys.path.extend(["/opt/swing", "/opt/swing/SwingPortfolio"])
import common.universes
import agents.live_trader as lt
print(len(lt.get_all_strategies()), "strategies")
