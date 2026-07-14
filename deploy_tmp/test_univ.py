import sys
sys.path[:0] = ["/opt/swing", "/opt/swing/SwingPortfolio"]
import cfg.universes
print([x for x in dir(cfg.universes) if not x.startswith("_")])
