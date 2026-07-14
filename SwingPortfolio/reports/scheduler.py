import streamlit as st
import pandas as pd
import os, sys, subprocess
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(BASE)
XML_DIR = os.path.join(ROOT, "deploy", "legacy", "scheduled_tasks", "SwingPortfolio")
# Auto-detect OS — works on both Windows and Ubuntu without config dependency
OS = "ubuntu" if sys.platform.startswith("linux") else "windows"
os.makedirs(XML_DIR, exist_ok=True)

CSS = """
<style>
    .task-card { border-radius: 8px; padding: 10px 14px; margin: 4px 0; border-left: 4px solid; background: #1a1a2e; }
    .status-ok { color: #00e676; font-weight: 600; }
    .status-warn { color: #ffd740; font-weight: 600; }
    .status-err { color: #ff5252; font-weight: 600; }
</style>
"""

TASK_NAMES = ["Live Trader", "Stock Selection"]
FOLDER = "SwingPortfolio"
PYTHON_EXE = sys.executable


def _run_schtasks(args):
    cmd = f'schtasks {args}'
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, shell=True, timeout=15)
        return r.returncode == 0, r.stdout.strip() or r.stderr.strip()
    except Exception as e:
        return False, str(e)


def get_task_info(name):
    ok, out = _run_schtasks(f'/Query /TN "{FOLDER}\\{name}" /FO LIST /V')
    if not ok:
        return None
    info = {}
    for line in out.splitlines():
        line = line.strip()
        if ':' in line:
            k, v = line.split(':', 1)
            info[k.strip()] = v.strip()
    return info


def _make_xml(name):
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    wd = BASE
    if name == "Live Trader":
        script = os.path.join(wd, "agents", "live_trader.py")
        start = "09:42"
        cmd_args = f'"{script}"'
        extra = ""
    elif name == "Stock Selection":
        script = os.path.join(wd, "agents", "stock_selection.py")
        start = "08:30"
        cmd_args = f'"{script}"'
        extra = ""
    else:
        script = os.path.join(wd, "reports", "dashboard.py")
        start = "09:00"
        cmd_args = "-m streamlit run dashboard.py"
        extra = '''    <Exec>
      <Command>C:\\Windows\\explorer.exe</Command>
      <Arguments>http://localhost:8501</Arguments>
    </Exec>
'''

    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>SwingPortfolio {name}</Description>
    <URI>\\SwingPortfolio\\{name}</URI>
  </RegistrationInfo>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
    </Principal>
  </Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT6H</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <StartWhenAvailable>true</StartWhenAvailable>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
  </Settings>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-06-08T{start}:00+05:30</StartBoundary>
      <ScheduleByWeek>
        <DaysOfWeek><Monday/><Tuesday/><Wednesday/><Thursday/><Friday/></DaysOfWeek>
        <WeeksInterval>1</WeeksInterval>
      </ScheduleByWeek>
    </CalendarTrigger>
  </Triggers>
  <Actions Context="Author">
{extra}    <Exec>
      <Command>{PYTHON_EXE}</Command>
      <Arguments>{cmd_args}</Arguments>
      <WorkingDirectory>{wd}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>'''


def _deploy(name):
    xml_content = _make_xml(name)
    xml_path = os.path.join(XML_DIR, f"SwingPortfolio_{name.replace(' ', '_')}.xml")
    with open(xml_path, "w", encoding="utf-16") as f:
        f.write(xml_content)
    return _run_schtasks(f'/Create /XML "{xml_path}" /TN "{FOLDER}\\{name}" /F')


def show():
    if OS == "ubuntu":
        _show_ubuntu()
    else:
        _show_windows()

def _show_windows():
    st.title("Task Scheduler")
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown("Deploy, enable, disable, run, kill, redeploy, or delete SwingPortfolio scheduled tasks.")

    tab1, tab2 = st.tabs(["Tasks", "Logs"])

    with tab1:
        rows = []
        for name in TASK_NAMES:
            info = get_task_info(name)
            if info is None:
                rows.append({"Task": name, "Status": "NOT FOUND", "Last Run": "---", "Next Run": "---", "Last Result": "---", "Enabled": "---"})
                continue
            rows.append({"Task": name, "Status": info.get("Status", "---"), "Last Run": info.get("Last Run Time", "---"),
                         "Next Run": info.get("Next Run Time", "---"), "Last Result": info.get("Last Result", "---"),
                         "Enabled": info.get("Scheduled Task State", "---")})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.divider()
        for name in TASK_NAMES:
            info = get_task_info(name)
            exists = info is not None
            with st.container(border=True):
                st.markdown(f"**{name}**" + (" ✅ Deployed" if exists else " ❌ Not deployed"))
                col1, col2, col3, col4 = st.columns([1, 1, 1, 1])
                if col1.button("Deploy/Redeploy", key=f"dep_{name}", type="primary"):
                    ok, msg = _deploy(name)
                    st.success(f"{name} deployed!") if ok else st.error(msg)
                    st.rerun()
                if info:
                    is_enabled = info.get("Scheduled Task State") == "Enabled"
                    lbl = "Disable" if is_enabled else "Enable"
                    if col2.button(lbl, key=f"t_{name}"):
                        a = "ENABLE" if not is_enabled else "DISABLE"
                        _run_schtasks(f'/Change /TN "{FOLDER}\\{name}" /{a}')
                        st.rerun()
                    if col3.button("Run Now", key=f"r_{name}"):
                        _run_schtasks(f'/Run /TN "{FOLDER}\\{name}"')
                        st.success(f"{name} triggered")
                    if col4.button("Delete", key=f"del_{name}"):
                        _run_schtasks(f'/Delete /TN "{FOLDER}\\{name}" /F')
                        st.rerun()

def _show_ubuntu():
    st.title("SwingPortfolio — Task Scheduler")
    st.markdown("Manage SwingPortfolio services and scheduled tasks on Ubuntu.")

    tab1, tab2, tab3 = st.tabs(["Services", "Scheduled Scans", "Logs"])

    # ── Services ──────────────────────────────────────────────
    with tab1:
        swing_services = [
            ("swing-sync", "Data Sync Agent", "Market data sync (Mon-Fri)"),
            ("swing-live", "Swing Unified Live Trader", "All 3 swing strategies in one process"),
        ]
        c1, c2 = st.columns(2)
        if c1.button("Start All Swing Live", type="primary", use_container_width=True):
            for svc, _, _ in swing_services:
                if svc.endswith("-live"):
                    subprocess.run(["sudo", "-n", "systemctl", "start", svc], capture_output=True)
            st.success("Started")
            st.rerun()
        if c2.button("Stop All Swing Live", type="secondary", use_container_width=True):
            for svc, _, _ in swing_services:
                if svc.endswith("-live"):
                    subprocess.run(["sudo", "-n", "systemctl", "stop", svc], capture_output=True)
            st.success("Stopped")
            st.rerun()

        for svc, label, desc in swing_services:
            r1 = subprocess.run(["sudo", "-n", "systemctl", "is-active", svc], capture_output=True, text=True)
            status = r1.stdout.strip()
            r2 = subprocess.run(["sudo", "-n", "systemctl", "is-enabled", svc], capture_output=True, text=True)
            enabled = r2.stdout.strip()
            with st.container(border=True):
                c1, c2, c3, c4 = st.columns([2, 1.5, 1.5, 3])
                c1.markdown(f"**{label}**  \n`{svc}`")
                c2.markdown(f"Status: **{status}**")
                c3.markdown(f"Enabled: {enabled}")
                with c4:
                    cols = st.columns(4)
                    if cols[0].button("Start", key=f"sw_start_{svc}", disabled=(status == "active"), use_container_width=True):
                        subprocess.run(["sudo", "-n", "systemctl", "start", svc]); st.rerun()
                    if cols[1].button("Stop", key=f"sw_stop_{svc}", disabled=(status != "active"), use_container_width=True):
                        subprocess.run(["sudo", "-n", "systemctl", "stop", svc]); st.rerun()
                    if cols[2].button("Restart", key=f"sw_restart_{svc}", disabled=(status != "active"), use_container_width=True):
                        subprocess.run(["sudo", "-n", "systemctl", "restart", svc]); st.rerun()
                    if cols[3].button("Logs \u2192", key=f"sw_log_{svc}", use_container_width=True):
                        st.session_state.swing_log_svc = svc

    # ── Scheduled Scans ───────────────────────────────────────
    with tab2:
        st.subheader("SwingPortfolio Cron Jobs")
        cron_content = subprocess.run(["sudo", "-n", "cat", "/etc/cron.d/swing"], capture_output=True, text=True).stdout
        if cron_content:
            for line in cron_content.split("\n"):
                if "SwingPortfolio" in line or any(s in line for s in ("donchian_adx", "keltner_rsi", "supertrend_volume")):
                    st.code(line, language="bash")
        else:
            st.info("No SwingPortfolio cron jobs found")

        st.divider()
        st.markdown("**Run Scan Manually**")
        for sid in ["donchian_adx", "keltner_rsi", "supertrend_volume"]:
            c1, c2 = st.columns([1, 3])
            if c1.button(f"Scan {sid}", key=f"run_scan_{sid}"):
                cmd = ["/opt/swing/venv/bin/python",
                       "/opt/swing/SwingPortfolio/core/strategy_agent.py",
                       "--strategy", sid, "--mode", "scan"]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd="/opt/swing/SwingPortfolio")
                st.code((r.stdout + "\n" + r.stderr).strip()[:2000], language="bash")
            c2.markdown(f"`{sid}`")

    # ── Logs ──────────────────────────────────────────────────
    with tab3:
        st.subheader("SwingPortfolio Logs")
        svc_log = getattr(st.session_state, "swing_log_svc", None)
        opts = {"swing-sync": "Data Sync", "swing-live": "Swing Live Trader"}
        selected = st.selectbox("Service", list(opts.keys()),
                                index=list(opts.keys()).index(svc_log) if svc_log in opts else 0,
                                format_func=lambda s: opts[s])
        lines = st.number_input("Lines", min_value=10, max_value=500, value=50, step=10)
        if st.button("Refresh", use_container_width=True):
            st.rerun()
        rc = subprocess.run(["sudo", "-n", "journalctl", "-u", selected, "--no-pager", "-n", str(lines)],
                            capture_output=True, text=True)
        if rc.stdout.strip():
            st.code(rc.stdout, language="bash")
        else:
            log_file = f"/opt/swing/logs/{selected.replace('swing-', '')}.log"
            r2 = subprocess.run(["sudo", "-n", "tail", "-n", str(lines), log_file], capture_output=True, text=True)
            st.code(r2.stdout if r2.stdout.strip() else "No logs", language="bash")
