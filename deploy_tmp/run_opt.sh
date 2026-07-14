#!/bin/bash
cd /opt/swing
LOG_DIR=/opt/swing
PYTHON=/opt/swing/venv/bin/python

pkill -f 'optimize_' 2>/dev/null

# Kill any existing stale sessions
tmux kill-session -t opt_d 2>/dev/null
tmux kill-session -t opt_k 2>/dev/null
tmux kill-session -t opt_s 2>/dev/null
sleep 1

# Create new sessions
tmux new-session -d -s opt_d "$PYTHON -u /opt/swing/optimize_donchian.py > $LOG_DIR/optimize_donchian.log 2>&1"
tmux new-session -d -s opt_k "$PYTHON -u /opt/swing/optimize_keltner.py > $LOG_DIR/optimize_keltner.log 2>&1"
tmux new-session -d -s opt_s "$PYTHON -u /opt/swing/optimize_supertrend.py > $LOG_DIR/optimize_supertrend.log 2>&1"

sleep 2
echo "Tmux sessions:"
tmux ls 2>/dev/null
echo ""
echo "Processes:"
ps aux | grep optimize_ | grep -v grep
