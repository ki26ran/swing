"""
Atomic file writes to prevent data corruption from concurrent processes.
Writes to a .tmp file first, then renames atomically.
Retries on PermissionError/OSError (concurrent write collisions).
"""
import os, json, csv, io, time


def _atomic_write(path, write_fn, max_retries=3, delay=0.5):
    """Write to .tmp then replace, with retry on concurrent access."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    for attempt in range(max_retries):
        try:
            write_fn(tmp)
            os.replace(tmp, path)
            return
        except (PermissionError, OSError):
            if attempt < max_retries - 1:
                time.sleep(delay * (attempt + 1))
                continue
            try:
                os.remove(tmp)
            except Exception:
                pass
            raise


def save_json(path, data):
    """Atomically write JSON to file with retry."""
    def _write(tmp):
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
    _atomic_write(path, _write)


def load_json(path, default=None):
    """Safely load JSON, returning default on failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def append_csv(path, fieldnames, row_dict):
    """Atomically append a row to CSV with retry."""
    exists = os.path.exists(path)
    def _write(tmp):
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            if exists:
                with open(path, "r", encoding="utf-8") as orig:
                    for line in orig:
                        f.write(line)
            else:
                w.writeheader()
            w.writerow({k: row_dict.get(k, "") for k in fieldnames})
    _atomic_write(path, _write)


def save_csv(path, fieldnames, rows):
    """Atomically write CSV file from list of dicts with retry."""
    def _write(tmp):
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in fieldnames})
    _atomic_write(path, _write)
