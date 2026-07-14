import os, json, requests, socket

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE, "data", "telegram_config.json")
HOST_TAG = "[" + socket.gethostname().replace("LAPTOP-", "WIN-")[:12] + "]"


def get_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"bot_token": "", "chat_id": ""}


def update_config(bot_token, chat_id):
    cfg = get_config()
    cfg["bot_token"] = bot_token
    cfg["chat_id"] = chat_id
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def is_configured():
    cfg = get_config()
    return bool(cfg.get("bot_token") and cfg.get("chat_id"))


def send_message(text, parse_mode="Markdown"):
    cfg = get_config()
    if not cfg.get("bot_token") or not cfg.get("chat_id"):
        return False
    try:
        url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage"
        r = requests.post(url, json={"chat_id": cfg["chat_id"], "text": text + " " + HOST_TAG, "parse_mode": parse_mode}, timeout=10)
        return r.status_code == 200
    except Exception:
        return False
