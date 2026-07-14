import sys
sys.path[:0] = ["/opt/swing", "/opt/swing/SwingPortfolio"]
from core.registry import get_strategy
s = get_strategy("donchian_adx")
print(type(s.name))
print(s.name)
if hasattr(s, "strategy_id"):
    print("strategy_id:", s.strategy_id)
