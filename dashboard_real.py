import streamlit as st
import pandas as pd
import paho.mqtt.client as mqtt
import json
import time
import plotly.express as px
import plotly.graph_objects as go
from streamlit_option_menu import option_menu 
import queue
from datetime import datetime, timedelta
import numpy as np
import random

# =========================================================
# CONFIG
# =========================================================
st.set_page_config(page_title="LumiNode Intelligence", page_icon="💡", layout="wide", initial_sidebar_state="expanded")
BROKER = "broker.hivemq.com"
PORT = 1883
TOPIC_SUB = "luminode/v4/stream"
TOPIC_PUB = "luminode/v4/control"
TARIFF_PER_KWH = 1600

# =========================================================
# CSS
# =========================================================
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap');
    .stApp { background-color: #F4F7FE; font-family: 'Roboto', sans-serif; }
    h1, h2, h3, h4, h5, h6, p, div, span, label, li { color: #2B3674; }
    section[data-testid="stSidebar"] { background-color: #0F1A3B; }
    section[data-testid="stSidebar"] h1, section[data-testid="stSidebar"] h2 { color: #FFFFFF !important; }
    section[data-testid="stSidebar"] span { color: #B9C4E8 !important; }
    div[data-baseweb="select"] > div { background-color: #111C44 !important; color: white !important; border: 1px solid #2B3674; border-radius: 8px; }
    div[data-testid="stSelectbox"] div[data-baseweb="select"] div { color: white !important; }
    div[data-baseweb="select"] svg { fill: white !important; }
    div[data-baseweb="menu"] { background-color: #111C44 !important; }
    div[data-baseweb="option"] { color: white !important; }
    li[role="option"]:hover, li[role="option"][aria-selected="true"] { background-color: #4318FF !important; color: white !important; }
    .intel-card { background-color: #FFFFFF; border-radius: 15px; padding: 20px; box-shadow: 0px 4px 12px rgba(0,0,0,0.05); margin-bottom: 20px; border: 1px solid #E0E5F2; }
    .card-header { font-size: 14px; font-weight: 700; color: #6E7BB0; margin-bottom: 10px; text-transform: uppercase; letter-spacing: 0.05em; }
    .metric-big { font-size: 28px; font-weight: 700; color: #2B3674; }
    .metric-sub { font-size: 12px; color: #05CD99; font-weight: 500; }
    .stButton button { width: 100%; border-radius: 8px; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

# =========================================================
# SESSION STATE (INISIAL)
# =========================================================
if "data_queue" not in st.session_state: st.session_state["data_queue"] = queue.Queue()
if "latest" not in st.session_state: st.session_state["latest"] = {}
if "history" not in st.session_state:
    st.session_state["history"] = pd.DataFrame(columns=["node_id", "timestamp", "voltage", "current", "power", "lux", "fault_code", "status", "energy_total"])
if "control_state" not in st.session_state: st.session_state["control_state"] = {}
if "map_view" not in st.session_state: st.session_state["map_view"] = {"center": None, "zoom": 12}

# helpers to track changes to avoid unnecessary reruns
if "last_data_count" not in st.session_state: st.session_state["last_data_count"] = 0
if "last_latest_sig" not in st.session_state: st.session_state["last_latest_sig"] = ""
if "last_page" not in st.session_state: st.session_state["last_page"] = None
if "current_page" not in st.session_state: st.session_state["current_page"] = None

# =========================================================
# MQTT HANDLERS
# =========================================================
def on_message(client, userdata, message):
    try:
        raw = message.payload.decode("utf-8")
        payload = json.loads(raw)
        if "timestamp" not in payload:
            payload["timestamp"] = datetime.now()
        else:
            if isinstance(payload["timestamp"], str):
                try:
                    payload["timestamp"] = datetime.strptime(payload["timestamp"], "%Y-%m-%d %H:%M:%S")
                except:
                    try:
                        payload["timestamp"] = datetime.fromisoformat(payload["timestamp"])
                    except:
                        payload["timestamp"] = datetime.now()
        userdata.put(payload)
    except Exception:
        pass

@st.cache_resource
def start_mqtt_client():
    q = queue.Queue()
    rnd_id = f"dash_{random.randint(1000,9999)}"
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=rnd_id, userdata=q)
    client.on_message = on_message
    try:
        client.connect(BROKER, PORT, keepalive=60)
        client.subscribe(TOPIC_SUB)
        client.loop_start()
    except Exception as e:
        st.error(f"MQTT Error: {e}")
    return client, q

client, client_queue = start_mqtt_client()

# --- PROCESS QUEUE (MASUKKAN DATA BARU KE SESSION) ---
start_loop_t = time.time()
while not client_queue.empty():
    try:
        payload = client_queue.get_nowait()
        nid = payload.get("node_id", "Unknown")

        if nid not in st.session_state["control_state"]:
            st.session_state["control_state"][nid] = {
                "mode": "AUTO (Lux)", "lux_threshold": 300, "schedule_start": "18:00", "schedule_end": "06:00"
            }

        curr_status = payload.get("status", "OFF")
        try:
            curr_power = float(payload.get("power", 0))
        except:
            curr_power = 0

        if curr_status == "ON" and curr_power < 0.5:
            payload["fault_code"] = 4
        else:
            payload["fault_code"] = 0

        st.session_state["latest"][nid] = payload

        new_row = pd.DataFrame([payload])
        new_row['timestamp'] = pd.to_datetime(new_row['timestamp'])
        st.session_state["history"] = pd.concat([st.session_state["history"], new_row], ignore_index=True)

        if len(st.session_state["history"]) > 2000:
            st.session_state["history"] = st.session_state["history"].iloc[-2000:].reset_index(drop=True)

        # limit processing time in this loop
        if time.time() - start_loop_t > 0.5:
            break
    except Exception:
        break

# =========================================================
# HELPERS
# =========================================================
def style_chart(fig, uirev=None):
    fig.update_layout(
        paper_bgcolor='#111C44', plot_bgcolor='#111C44',
        font=dict(color='white'),
        margin=dict(l=20, r=20, t=30, b=20),
        xaxis=dict(showgrid=False, zeroline=False),
        yaxis=dict(showgrid=True, gridcolor='#2B3674'),
        legend=dict(orientation="h")
    )
    if uirev:
        fig.update_layout(uirevision=uirev)
    return fig

def get_latest_signature():
    """
    Build a deterministic signature string for current latest payloads:
    node_id:ISO_TIMESTAMP|node2:ISO_TIMESTAMP|...
    """
    parts = []
    for k in sorted(st.session_state["latest"].keys()):
        v = st.session_state["latest"].get(k, {})
        ts = v.get("timestamp", "")
        if isinstance(ts, datetime):
            ts = ts.isoformat()
        parts.append(f"{k}:{ts}")
    return "|".join(parts)

# =========================================================
# 1. MAIN DASHBOARD
# =========================================================
def render_dashboard():
    c_head, c_filt = st.columns([3, 1])
    with c_head:
        st.markdown("## 🏙️ Main Dashboard")
    with c_filt:
        all_nodes = sorted(list(st.session_state["latest"].keys()))
        nodes_list = ["All Nodes"] + all_nodes if all_nodes else ["Waiting Data..."]
        dash_node_filter = st.selectbox("Select Node:", nodes_list, key="dash_select")

    df_latest = st.session_state["latest"]
    target_data = []
    if dash_node_filter != "Waiting Data...":
        if dash_node_filter != "All Nodes" and dash_node_filter in df_latest:
            target_data = [df_latest[dash_node_filter]]
        elif dash_node_filter == "All Nodes":
            target_data = list(df_latest.values())

    total_power = sum(float(d.get('power', 0) or 0) for d in target_data)
    fault_count = len([d for d in target_data if int(d.get('fault_code', 0) or 0) != 0])
    active_count = max(0, len(target_data) - fault_count)

    k1, k2, k3, k4 = st.columns(4)
    k1.markdown(f'<div class="intel-card"><div class="card-header">Total Load</div><div class="metric-big">{total_power:.1f} W</div><div class="metric-sub">Real-time</div></div>', unsafe_allow_html=True)
    k2.markdown(f'<div class="intel-card"><div class="card-header">Total Nodes</div><div class="metric-big">{len(target_data)}</div><div class="metric-sub">Connected</div></div>', unsafe_allow_html=True)
    k3.markdown(f'<div class="intel-card"><div class="card-header">Healthy</div><div class="metric-big">{active_count}</div><div class="metric-sub" style="color:#2ecc71">Online</div></div>', unsafe_allow_html=True)
    k4.markdown(f'<div class="intel-card"><div class="card-header">Faults</div><div class="metric-big" style="color:{"#e74c3c" if fault_count > 0 else "#05CD99"}">{fault_count}</div><div class="metric-sub">Alerts</div></div>', unsafe_allow_html=True)

    c_left, c_right = st.columns([2, 1])
    with c_left:
        st.markdown('<div class="intel-card"><div class="card-header">Power Trend (Realtime)</div>', unsafe_allow_html=True)
        try:
            df_hist = st.session_state["history"].copy()
            if not df_hist.empty:
                if dash_node_filter != "All Nodes":
                    df_viz = df_hist[df_hist["node_id"] == dash_node_filter]
                else:
                    df_viz = df_hist.copy()
                df_viz["power"] = pd.to_numeric(df_viz.get("power", 0), errors="coerce").fillna(0)
                fig = px.area(df_viz, x="timestamp", y="power", color="node_id", template="plotly_dark")
                # uirevision unique per page to avoid cross-page carry-over
                uirev = f"{st.session_state.get('current_page','Dashboard')}_dash_pwr"
                st.plotly_chart(style_chart(fig, uirev), use_container_width=True, key="dash_pwr")
            else:
                st.info("Waiting for data...")
        except Exception:
            st.caption("Loading chart...")
        st.markdown('</div>', unsafe_allow_html=True)

    with c_right:
        st.markdown('<div class="intel-card"><div class="card-header">Health Status</div>', unsafe_allow_html=True)
        try:
            if len(target_data) > 0:
                fig_p = px.pie(names=["Healthy", "Fault"], values=[active_count, fault_count],
                              color_discrete_sequence=["#4318FF", "#FFB547"], hole=0.6)
                uirev = f"{st.session_state.get('current_page','Dashboard')}_dash_pie"
                st.plotly_chart(style_chart(fig_p, uirev), use_container_width=True, key="dash_pie")
            else:
                st.caption("No data available")
        except Exception:
            pass
        st.markdown('</div>', unsafe_allow_html=True)

# =========================================================
# 2. CONTROL CENTER — DROPDOWN NODE (TIDAK MENGUBAH LAYOUT)
# =========================================================
def render_control():
    st.markdown("## 🎛️ Smart Control")
    if not st.session_state["latest"]:
        st.warning("Waiting for nodes...")
        return

    all_nodes = sorted(st.session_state["latest"].keys())
    selected = st.selectbox("Select Node to Control:", all_nodes, key="ctrl_select")

    node = selected
    state = st.session_state["control_state"].get(node, {
        "mode": "AUTO (Lux)",
        "lux_threshold": 300,
        "schedule_start": "18:00",
        "schedule_end": "06:00"
    })

    with st.container():
        st.markdown(f'<div class="intel-card">', unsafe_allow_html=True)
        c1, c2, c3 = st.columns([1, 1, 2])

        # ========================================================
        # LEFT PANEL: NODE ID, STATUS, LUX
        # ========================================================
        with c1:
            st.markdown(f"#### 💡 {node}")
            curr = st.session_state["latest"].get(node, {})
            st.caption(f"Status: **{curr.get('status','OFF')}** | Lux: **{curr.get('lux',0)}**")

        # ========================================================
        # CENTER PANEL: MODE SELECTOR
        # ========================================================
        with c2:
            modes = ["AUTO (Lux)", "MANUAL", "SCHEDULED"]
            try:
                idx = modes.index(state.get("mode", "AUTO (Lux)"))
            except Exception:
                idx = 0
            new_mode = st.selectbox("Mode", modes, index=idx, key=f"cm_{node}", label_visibility="collapsed")

            if new_mode != state.get("mode"):
                st.session_state["control_state"][node]["mode"] = new_mode
                try:
                    client.publish(TOPIC_PUB, json.dumps({
                        "node_id": node,
                        "mode_update": new_mode
                    }))
                except:
                    pass
                st.toast(f"Mode set: {new_mode}")

        # ========================================================
        # RIGHT PANEL: MODE CONFIGURATION
        # ========================================================
        with c3:

            # ----------------------------------------------
            # AUTO MODE → slider threshold tetap
            # ----------------------------------------------
            if "AUTO" in new_mode:
                st.markdown(f"**Threshold: {state.get('lux_threshold',300)}**")
                thr = st.slider("Lux", 0, 1000, state.get("lux_threshold", 300), key=f"sl_{node}")
                if thr != state.get("lux_threshold"):
                    st.session_state["control_state"][node]["lux_threshold"] = thr
                    client.publish(TOPIC_PUB, json.dumps({
                        "node_id": node,
                        "lux_threshold": thr
                    }))

            # ----------------------------------------------
            # MANUAL MODE → ON/OFF buttons
            # ----------------------------------------------
            elif "MANUAL" in new_mode:
                b1, b2 = st.columns(2)
                if b1.button("ON", key=f"bon_{node}"):
                    client.publish(TOPIC_PUB, json.dumps({
                        "node_id": node,
                        "command": "ON",
                        "mode": "MANUAL"
                    }))
                if b2.button("OFF", key=f"boff_{node}"):
                    client.publish(TOPIC_PUB, json.dumps({
                        "node_id": node,
                        "command": "OFF",
                        "mode": "MANUAL"
                    }))

            # ----------------------------------------------
            # SCHEDULED MODE → Dropdown Start & End Time
            # ----------------------------------------------
            elif "SCHEDULED" in new_mode:
                st.markdown("**Schedule Time Window**")

                # daftar jam 00:00–23:00
                hours = [f"{h:02d}:00" for h in range(24)]

                st_time = st.selectbox(
                    "Start Time",
                    hours,
                    index=hours.index(state.get("schedule_start", "18:00")),
                    key=f"st_{node}"
                )

                ed_time = st.selectbox(
                    "End Time",
                    hours,
                    index=hours.index(state.get("schedule_end", "06:00")),
                    key=f"et_{node}"
                )

                # update jika berubah
                if st_time != state.get("schedule_start") or ed_time != state.get("schedule_end"):
                    st.session_state["control_state"][node]["schedule_start"] = st_time
                    st.session_state["control_state"][node]["schedule_end"] = ed_time

                    client.publish(TOPIC_PUB, json.dumps({
                        "node_id": node,
                        "mode": "SCHEDULED",
                        "schedule_start": st_time,
                        "schedule_end": ed_time
                    }))

                    st.toast(f"Schedule updated: {st_time} → {ed_time}")

        st.markdown('</div>', unsafe_allow_html=True)

# =========================================================
# 3. ANALYTICS
# =========================================================
def render_analytics():
    c_head, c_opt1, c_opt2 = st.columns([2, 1, 1])
    with c_head:
        st.markdown("## 📈 Analytics")

    with c_opt1:
        nodes = sorted(list(st.session_state["latest"].keys()))
        ana_node = st.selectbox("Select Node", ["All Nodes"] + nodes, key="ana_node")

    with c_opt2:
        period = st.selectbox("Period", ["Past 1 Hour", "Past 24 Hours", "Past Week", "Past Month"], key="ana_time")

    if st.button("Refresh Analytics"):
        st.rerun()

    df = st.session_state["history"].copy()
    if df.empty:
        st.info("No data yet.")
        return

    try:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        now = datetime.now()
        if period == "Past 1 Hour":
            cut = now - timedelta(hours=1)
        elif period == "Past 24 Hours":
            cut = now - timedelta(days=1)
        elif period == "Past Week":
            cut = now - timedelta(days=7)
        else:
            cut = now - timedelta(days=30)

        df = df[df["timestamp"] >= cut]
        if ana_node != "All Nodes":
            df = df[df["node_id"] == ana_node]

        latest_data = st.session_state["latest"]
        total_acc_kwh = 0.0

        if ana_node == "All Nodes":
            for n in latest_data:
                total_acc_kwh += float(latest_data[n].get("energy_total", 0))
        elif ana_node in latest_data:
            total_acc_kwh = float(latest_data[ana_node].get("energy_total", 0))

        total_cost_real = total_acc_kwh * TARIFF_PER_KWH

        c1, c2, c3 = st.columns(3)
        c1.markdown(f'<div class="intel-card"><div class="card-header">Total Energy</div><div class="metric-big">{total_acc_kwh:.4f} kWh</div><div class="metric-sub">Accumulated Meter</div></div>', unsafe_allow_html=True)
        c2.markdown(f'<div class="intel-card"><div class="card-header">Total Cost</div><div class="metric-big">Rp {total_cost_real:,.0f}</div><div class="metric-sub">Real Bill</div></div>', unsafe_allow_html=True)

        faults = df[df.get("fault_code", 0) != 0] if "fault_code" in df.columns else pd.DataFrame()
        c3.markdown(f'<div class="intel-card"><div class="card-header">Fault Events</div><div class="metric-big">{len(faults)}</div></div>', unsafe_allow_html=True)

        g1, g2 = st.columns(2)
        with g1:
            st.markdown('<div class="intel-card"><div class="card-header">Power Trend</div>', unsafe_allow_html=True)
            if not df.empty:
                df["power"] = pd.to_numeric(df.get("power", 0), errors="coerce").fillna(0)
                fig = px.line(df, x="timestamp", y="power", color="node_id" if ana_node == "All Nodes" else None)
                uirev = f"{st.session_state.get('current_page','Analytics')}_ana_pwr"
                st.plotly_chart(style_chart(fig, uirev), use_container_width=True, key="ana_pwr")
            st.markdown('</div>', unsafe_allow_html=True)

        with g2:
            st.markdown('<div class="intel-card"><div class="card-header">V/I Trend</div>', unsafe_allow_html=True)
            if not df.empty:
                if ana_node == "All Nodes":
                    df_viz = df.groupby("timestamp")[["voltage", "current"]].mean().reset_index()
                else:
                    df_viz = df
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=df_viz["timestamp"], y=df_viz["voltage"], name="V", line=dict(color="#4318FF")))
                fig.add_trace(go.Scatter(x=df_viz["timestamp"], y=df_viz["current"], name="I", line=dict(color="#FFB547"), yaxis="y2"))
                fig.update_layout(yaxis2=dict(overlaying="y", side="right", title="I"))
                uirev = f"{st.session_state.get('current_page','Analytics')}_ana_vi"
                st.plotly_chart(style_chart(fig, uirev), use_container_width=True, key="ana_vi")
            st.markdown('</div>', unsafe_allow_html=True)

        st.markdown("### ⚠️ Fault Logs")
        df_broken = df[df.get("fault_code", 0) == 4].copy() if "fault_code" in df.columns else pd.DataFrame()

        if not df_broken.empty:
            st.dataframe(
                df_broken[["timestamp", "node_id", "status", "power", "voltage"]].sort_values("timestamp", ascending=False),
                use_container_width=True,
                hide_index=True
            )
        else:
            st.success("No critical faults detected.")

    except Exception as e:
        st.error(f"Analytics Error: {e}")

# =========================================================
# 4. ASSET MAP
# =========================================================
def render_map():
    st.markdown("## 🗺️ Asset Map")
    if st.session_state["latest"]:
        if st.button("Refresh Map"):
            st.rerun()
        try:
            df = pd.DataFrame(list(st.session_state["latest"].values()))
            for c in ["lat", "lng"]:
                df[c] = pd.to_numeric(df.get(c, 0), errors="coerce")
            df = df.dropna(subset=["lat", "lng"])
            if not df.empty:
                if not st.session_state["map_view"]["center"]:
                    st.session_state["map_view"] = {"center": {"lat": df["lat"].mean(), "lon": df["lng"].mean()}, "zoom": 12}

                df["color"] = df.get("fault_code", 0).apply(lambda x: "#E74C3C" if int(x) != 0 else "#05CD99")
                st.markdown('<div class="intel-card" style="padding:10px;">', unsafe_allow_html=True)
                fig = px.scatter_mapbox(df, lat="lat", lon="lng", color="color", size_max=15,
                                        zoom=st.session_state["map_view"]["zoom"], height=500,
                                        color_discrete_map="identity",
                                        hover_name="node_id",
                                        hover_data={"lat": False, "lng": False, "color": False, "status": True, "power": ":.1f", "energy_total": ":.4f"})
                fig.update_layout(mapbox_style="carto-positron", margin={"r": 0, "t": 0, "l": 0, "b": 0}, mapbox_center=st.session_state["map_view"]["center"])
                uirev = f"{st.session_state.get('current_page','Asset Map')}_map_u"
                st.plotly_chart(style_chart(fig, uirev), use_container_width=True, key="map_u")
                st.markdown('</div>', unsafe_allow_html=True)
        except Exception:
            pass
    else:
        st.info("Waiting GPS...")

# =========================================================
# MAIN
# =========================================================
def main():
    with st.sidebar:
        st.image("https://cdn-icons-png.flaticon.com/512/3665/3665923.png", width=50)
        st.markdown("### LumiNode Manager")
        sel = option_menu(None, ["Dashboard", "Control Center", "Analytics", "Asset Map"], icons=["grid", "toggles", "bar-chart", "map"], default_index=0)

    # set current page so renderers can use it for uirevision
    st.session_state["current_page"] = sel

    # reset per-page chart session keys when page actually changes (cleans any leftover keys)
    if st.session_state["last_page"] != sel:
        # delete only chart-related keys stored in session_state (if any)
        for k in ["dash_pwr", "dash_pie", "ana_pwr", "ana_vi", "map_u"]:
            if k in st.session_state:
                del st.session_state[k]
        st.session_state["last_page"] = sel

    holder = st.empty()
    with holder.container():
        if sel == "Dashboard":
            render_dashboard()
        elif sel == "Control Center":
            render_control()
        elif sel == "Analytics":
            render_analytics()
        elif sel == "Asset Map":
            render_map()

    # -----------------------------
    # Kondisional rerun: hanya kalau data/latest berubah atau halaman berganti.
    # Ini mencegah rerun terus-menerus yang menyebabkan ghosting/blinking.
    # -----------------------------
    current_count = len(st.session_state.get("history", []))
    current_sig = get_latest_signature()
    page = st.session_state.get("current_page")

    data_changed = (current_count != st.session_state.get("last_data_count")) or (current_sig != st.session_state.get("last_latest_sig"))
    page_changed = (page != st.session_state.get("last_page"))

    if data_changed or page_changed:
        # update trackers BEFORE rerun to avoid immediate repeated rerun
        st.session_state["last_data_count"] = current_count
        st.session_state["last_latest_sig"] = current_sig
        st.session_state["last_page"] = page
        # short sleep to allow UI to settle (keeps behavior responsive but avoids tight loop)
        time.sleep(0.1)
        # replaced experimental_rerun() -> use st.rerun() for compatibility
        st.rerun()
    # else: jangan rerun — biarkan app idle sampai ada perubahan

if __name__ == "__main__":
    main()
