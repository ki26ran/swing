import sys, os
sys.path[0:0] = ["/opt/swing", "/opt/swing/SwingPortfolio"]
os.environ["APP_ENV"] = "prod"
from agents.stock_selection import run
run()
print("SCAN OK")
