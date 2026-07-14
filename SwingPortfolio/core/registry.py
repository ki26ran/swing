"""
Strategy registry with error isolation.
If a strategy module fails to load, it is skipped and other strategies continue.
"""
import os, json, importlib, traceback

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(BASE, "cfg", "strategies.json")


def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception as e:
        print(f"  [ERROR] Failed to load strategies.json: {e}")
        return {"strategies": []}


def get_strategies(enabled_only=True):
    cfg = load_config()
    for sc in cfg["strategies"]:
        if enabled_only and not sc.get("enabled", True):
            continue
        yield sc


def get_strategy(strategy_id):
    for sc in get_strategies(enabled_only=False):
        if sc["id"] == strategy_id:
            return _instantiate(sc)
    return None


def _instantiate(sc):
    try:
        mod = importlib.import_module(sc["module"])
        cls = getattr(mod, sc["class"])
        instance = cls()
        for k, v in sc.get("params", {}).items():
            setattr(instance, k, v)
        # Attach universe and entry_time config
        instance._universe_name = sc.get("universe", "Margin < 200K")
        instance._entry_time = sc.get("entry_time", "zscore")
        return instance
    except Exception as e:
        print(f"  [ERROR] Failed to load strategy '{sc.get('id','?')}': {e}")
        return None


def get_all_strategies(enabled_only=True):
    result = []
    for sc in get_strategies(enabled_only):
        inst = _instantiate(sc)
        if inst is not None:
            # Validate required methods
            for method in ["prepare_data", "check_signal"]:
                if not hasattr(inst, method):
                    print(f"  [ERROR] Strategy '{sc.get('id','?')}' missing method '{method}'")
                    inst = None
                    break
            if inst is not None:
                result.append(inst)
    return result


def get_strategy_names():
    return {sc["id"]: sc["name"] for sc in get_strategies(enabled_only=False)}


def get_strategy_universe(strategy_id):
    """Return the universe set for a strategy, or None if not found."""
    from cfg.universes import UNIVERSE_MAP
    for sc in get_strategies(enabled_only=False):
        if sc["id"] == strategy_id:
            name = sc.get("universe", "Margin < 200K")
            return UNIVERSE_MAP.get(name, None)
    return None
