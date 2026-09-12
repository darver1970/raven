"""Lokální sběr hardwarové telemetrie pro HUD RAVEN.

Čte výhradně localhost rozhraní Libre Hardware Monitor a ukládá JSON pro HUD.
Pokud senzor není k dispozici, neodhadujeme hodnoty – zobrazí se jako N/A.
"""

import json
import logging
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import psutil

from telemetry_extensions import collect_extended


ROOT = Path(__file__).resolve().parent
HUD_STATUS = ROOT / "hud" / "hardware-status.json"
LOG_PATH = ROOT / "runtime" / "hardware-monitor.log"
TELEMETRY_SETTINGS_PATH = ROOT / "runtime" / "telemetry-settings.json"
TELEMETRY_DEFAULTS_PATH = ROOT / "defaults" / "telemetry-settings.json"
SENSOR_URL = "http://127.0.0.1:8085/data.json"
POLL_SECONDS = 2
PROCESS_CPU_SAMPLES: dict[int, tuple[float, float]] = {}
PROCESS_IO_SAMPLES: dict[int, tuple[int, int, float]] = {}
GPU_CACHE: tuple[float, dict[int, float]] = (0.0, {})
GPU_FAILURES = 0
GPU_RETRY_AT = 0.0
SENSOR_CACHE: tuple[float, list[dict[str, Any]]] = (0.0, [])
SYSTEM_IO_SAMPLE: tuple[float, int, int] | None = None

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)


def write_status(payload: dict[str, Any]) -> None:
    """Zapíše stav atomicky, aby HUD nikdy nečetl neúplný JSON."""
    temporary = HUD_STATUS.with_name(f"{HUD_STATUS.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    for attempt in range(4):
        try:
            os.replace(temporary, HUD_STATUS)
            return
        except PermissionError:
            if attempt == 3:
                temporary.unlink(missing_ok=True)
                raise
            time.sleep(0.05 * (attempt + 1))


def load_telemetry_settings() -> dict[str, Any]:
    """Načte oddělené lokální nastavení bez závislosti na řídicím serveru."""
    fallback = {
        "enabled": True, "sampling_seconds": 2,
        "features": {
            "process_monitoring": True, "process_disk_io": True,
            "process_network_connections": True, "process_gpu": True,
            "hardware_sensors": True, "temperatures": True, "process_details": True,
        },
    }
    source = TELEMETRY_SETTINGS_PATH if TELEMETRY_SETTINGS_PATH.is_file() else TELEMETRY_DEFAULTS_PATH
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return fallback
        features = fallback["features"].copy()
        if isinstance(data.get("features"), dict):
            features.update({key: value for key, value in data["features"].items() if isinstance(value, bool)})
        interval = int(data.get("sampling_seconds", 2))
        return {"enabled": data.get("enabled", True) is True, "sampling_seconds": interval if interval in {1, 2, 5, 10} else 2, "features": features}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return fallback


def number(value: Any) -> float | None:
    """Převede text senzoru typu '58.4 °C' na číslo."""
    if value is None:
        return None
    text = str(value).replace(",", ".")
    token = ""
    for char in text:
        if char.isdigit() or char in ".-":
            token += char
        elif token:
            break
    try:
        return float(token)
    except ValueError:
        return None


def walk(node: dict[str, Any], hardware: str = "") -> list[dict[str, Any]]:
    """Zploští strom JSON poskytovaný LHM do seznamu senzorů."""
    current_hardware = str(node.get("Text") or hardware) if node.get("HardwareId") else hardware
    value = node.get("Value")
    sensor_type = str(node.get("Type") or node.get("SensorType") or "")
    items: list[dict[str, Any]] = []
    if value not in (None, ""):
        items.append({
            "hardware": hardware,
            "name": str(node.get("Text") or "Senzor"),
            "type": sensor_type.lower(),
            "value": number(value),
            "unit": str(value),
        })
    for child in node.get("Children") or []:
        if isinstance(child, dict):
            items.extend(walk(child, current_hardware))
    return items


def fetch_sensors() -> list[dict[str, Any]]:
    """Načte senzory pouze z lokálního LHM serveru."""
    global SENSOR_CACHE
    request = urllib.request.Request(SENSOR_URL, headers={"User-Agent": "RavenLocalHUD/1"})
    try:
        with urllib.request.urlopen(request, timeout=1.5) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        if SENSOR_CACHE[1] and time.monotonic() - SENSOR_CACHE[0] < 30:
            return SENSOR_CACHE[1]
        raise
    sensors: list[dict[str, Any]] = []
    for root in data.get("Children") or []:
        if isinstance(root, dict):
            sensors.extend(walk(root))
    SENSOR_CACHE = (time.monotonic(), sensors)
    return sensors


def choose(sensors: list[dict[str, Any]], words: tuple[str, ...], kinds: tuple[str, ...]) -> dict[str, Any] | None:
    """Vybere první reálně hlášený senzor podle názvu a typu."""
    for sensor in sensors:
        label = f"{sensor['hardware']} {sensor['name']}".lower()
        if sensor["value"] is not None and any(word in label for word in words) and sensor["type"] in kinds:
            return sensor
    return None


def native_disks() -> list[dict[str, Any]]:
    """Vrátí obsazení lokálních pevných disků bez spouštění dalšího procesu."""
    drives: list[dict[str, Any]] = []
    for partition in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(partition.mountpoint)
            drives.append({
                "name": partition.device.rstrip("\\") or partition.mountpoint,
                "used": round(usage.percent),
                "free_gb": round(usage.free / 1073741824, 1),
            })
        except (psutil.Error, OSError):
            continue
    return drives


def gpu_usage_by_pid() -> dict[int, float]:
    """Sečte aktivitu GPU enginů Windows podle PID a krátce ji cachuje."""
    global GPU_CACHE, GPU_FAILURES, GPU_RETRY_AT
    now = time.monotonic()
    if now - GPU_CACHE[0] < 4:
        return GPU_CACHE[1]
    if now < GPU_RETRY_AT:
        return GPU_CACHE[1]
    script = (
        "$ErrorActionPreference='Stop'; "
        "(Get-Counter '\\GPU Engine(*)\\Utilization Percentage').CounterSamples | "
        "Where-Object {$_.CookedValue -gt 0} | Select-Object InstanceName,CookedValue | ConvertTo-Json -Compress"
    )
    usage: dict[int, float] = {}
    try:
        environment = os.environ.copy()
        environment["PSModulePath"] = r"C:\Windows\System32\WindowsPowerShell\v1.0\Modules;C:\Program Files\WindowsPowerShell\Modules"
        result = subprocess.run(
            [r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", timeout=6, check=False, env=environment,
        )
        if result.returncode != 0:
            raise OSError((result.stderr or "GPU čítač vrátil chybu.").strip())
        samples = json.loads(result.stdout or "[]")
        if isinstance(samples, dict):
            samples = [samples]
        for sample in samples:
            match = re.search(r"pid_(\d+)", str(sample.get("InstanceName", "")), re.IGNORECASE)
            if match:
                pid = int(match.group(1))
                usage[pid] = usage.get(pid, 0.0) + float(sample.get("CookedValue") or 0)
        usage = {pid: round(min(value, 100.0), 1) for pid, value in usage.items()}
        GPU_FAILURES = 0
        GPU_RETRY_AT = 0.0
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError, ValueError) as error:
        GPU_FAILURES += 1
        if GPU_FAILURES <= 3:
            logging.warning("GPU procesní čítače nejsou dostupné: %s", error)
        if GPU_FAILURES >= 3:
            GPU_RETRY_AT = now + 60
        usage = GPU_CACHE[1]
    GPU_CACHE = (now, usage)
    return usage


def native_processes(settings: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Vrátí všechny procesy s CPU, RAM, diskem, GPU a síťovými spojeními."""
    features = (settings or load_telemetry_settings()).get("features", {})
    if features.get("process_monitoring", True) is not True:
        return []
    now = time.monotonic()
    logical_processors = max(os.cpu_count() or 1, 1)
    total_memory = max(psutil.virtual_memory().total, 1)
    gpu_usage = gpu_usage_by_pid() if features.get("process_gpu", True) else {}
    connections: dict[int, int] = {}
    if features.get("process_network_connections", True):
        try:
            for connection in psutil.net_connections(kind="inet"):
                if connection.pid:
                    connections[connection.pid] = connections.get(connection.pid, 0) + 1
        except (psutil.Error, OSError):
            pass

    active_pids: set[int] = set()
    output: list[dict[str, Any]] = []
    attributes = ["pid", "name", "status", "memory_info", "cpu_times", "num_threads", "ppid"]
    if features.get("process_details", True):
        attributes.extend(["exe", "username"])
    for process in psutil.process_iter(attributes):
        try:
            info = process.info
            pid = int(info["pid"])
            if pid == 0:
                continue
            active_pids.add(pid)
            cpu_times = info.get("cpu_times")
            cpu_seconds = float(cpu_times.user + cpu_times.system) if cpu_times else 0.0
            previous_cpu = PROCESS_CPU_SAMPLES.get(pid)
            cpu_percent = 0.0
            if previous_cpu and now > previous_cpu[1]:
                cpu_percent = max(0.0, (cpu_seconds - previous_cpu[0]) / (now - previous_cpu[1]) / logical_processors * 100)
            PROCESS_CPU_SAMPLES[pid] = (cpu_seconds, now)

            memory_bytes = int(getattr(info.get("memory_info"), "rss", 0) or 0)
            read_bytes = write_bytes = 0
            if features.get("process_disk_io", True):
                try:
                    io = process.io_counters()
                    read_bytes, write_bytes = int(io.read_bytes), int(io.write_bytes)
                except (psutil.AccessDenied, AttributeError, OSError):
                    pass
            previous_io = PROCESS_IO_SAMPLES.get(pid)
            read_rate = write_rate = 0.0
            if previous_io and now > previous_io[2]:
                elapsed = now - previous_io[2]
                read_rate = max(0.0, (read_bytes - previous_io[0]) / elapsed / 1048576)
                write_rate = max(0.0, (write_bytes - previous_io[1]) / elapsed / 1048576)
            PROCESS_IO_SAMPLES[pid] = (read_bytes, write_bytes, now)

            gpu_percent = gpu_usage.get(pid, 0.0)
            memory_percent = memory_bytes / total_memory * 100
            network_connections = connections.get(pid, 0)
            component_scores = {
                "CPU": cpu_percent,
                "RAM": memory_percent,
                "DISK": min((read_rate + write_rate) * 2, 100),
                "GPU": gpu_percent,
                "SÍŤ": min(network_connections * 2, 100),
            }
            dominant_component = max(component_scores, key=component_scores.get)
            output.append({
                "name": str(info.get("name") or "proces"),
                "pid": pid,
                "parent_pid": int(info.get("ppid") or 0),
                "cpu_percent": round(min(cpu_percent, 100.0), 1),
                "memory_mb": round(memory_bytes / 1048576, 1),
                "memory_percent": round(memory_percent, 1),
                "disk_mbps": round(read_rate + write_rate, 2),
                "disk_read_mbps": round(read_rate, 2),
                "disk_write_mbps": round(write_rate, 2),
                "gpu_percent": gpu_percent,
                "network_connections": network_connections,
                "dominant_component": dominant_component,
                "status": str(info.get("status") or "unknown").upper(),
                "handles": process.num_handles() if features.get("process_details", True) and hasattr(process, "num_handles") else 0,
                "threads": int(info.get("num_threads") or 0),
                "username": str(info.get("username") or "") if features.get("process_details", True) else "",
                "executable": str(info.get("exe") or "") if features.get("process_details", True) else "",
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError, TypeError, ValueError):
            continue

    for samples in (PROCESS_CPU_SAMPLES, PROCESS_IO_SAMPLES):
        for pid in tuple(samples):
            if pid not in active_pids:
                samples.pop(pid, None)
    return sorted(output, key=lambda item: (item["cpu_percent"], item["memory_mb"]), reverse=True)


def system_usage_snapshot() -> dict[str, float]:
    """Vrátí souhrnné vytížení pro hlavičku tabulky ve stylu Správce úloh."""
    global SYSTEM_IO_SAMPLE
    now = time.monotonic()
    disk = psutil.disk_io_counters()
    network = psutil.net_io_counters()
    disk_busy = int(getattr(disk, "busy_time", 0) or 0)
    network_bytes = int(getattr(network, "bytes_sent", 0) or 0) + int(getattr(network, "bytes_recv", 0) or 0)
    disk_percent = network_mbps = network_percent = 0.0
    if SYSTEM_IO_SAMPLE and now > SYSTEM_IO_SAMPLE[0]:
        elapsed = now - SYSTEM_IO_SAMPLE[0]
        disk_percent = min(100.0, max(0.0, (disk_busy - SYSTEM_IO_SAMPLE[1]) / (elapsed * 1000) * 100))
        network_mbps = max(0.0, (network_bytes - SYSTEM_IO_SAMPLE[2]) / elapsed * 8 / 1_000_000)
        link_speed = max((stats.speed for stats in psutil.net_if_stats().values() if stats.isup and stats.speed > 0), default=0)
        if link_speed:
            network_percent = min(100.0, network_mbps / link_speed * 100)
    SYSTEM_IO_SAMPLE = (now, disk_busy, network_bytes)
    return {
        "cpu_percent": round(psutil.cpu_percent(interval=None), 1),
        "memory_percent": round(psutil.virtual_memory().percent, 1),
        "disk_percent": round(disk_percent, 1),
        "network_percent": round(network_percent, 1),
        "network_mbps": round(network_mbps, 1),
    }


def sensor_value(sensors: list[dict[str, Any]], words: tuple[str, ...], kinds: tuple[str, ...]) -> float | None:
    """Vrátí numerickou hodnotu senzoru, pokud ji hardware skutečně hlásí."""
    item = choose(sensors, words, kinds)
    return item["value"] if item else None


def sensor_value_all(sensors: list[dict[str, Any]], words: tuple[str, ...], kinds: tuple[str, ...]) -> float | None:
    """Najde senzor, který obsahuje všechny požadované části názvu."""
    for sensor in sensors:
        label = f"{sensor['hardware']} {sensor['name']}".lower()
        if sensor["type"] in kinds and sensor["value"] is not None and all(word in label for word in words):
            return sensor["value"]
    return None


def snapshot() -> dict[str, Any]:
    """Sestaví bezpečný snímek bez smyšlených teplot."""
    settings = load_telemetry_settings()
    features = settings.get("features", {})
    if not settings.get("enabled", True):
        return {
            "updated_at": datetime.now(timezone.utc).isoformat(), "source": "Telemetrie vypnuta v nastavení",
            "online": False, "disabled": True, "message": "TELEMETRIE VYPNUTA",
            "cpu": {}, "gpu": {}, "ram": {}, "performance": {}, "temperatures": [], "disks": [], "processes": [], "system_usage": {},
        }
    sensors: list[dict[str, Any]] = []
    sensor_online = False
    sensor_error = ""
    if features.get("hardware_sensors", True):
        try:
            sensors = fetch_sensors()
            sensor_online = True
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            # LibreHardwareMonitor je rozšíření pro teploty/napětí. Jeho výpadek
            # nesmí shodit nativní Windows/psutil telemetrii portable verze.
            sensor_error = str(error)[:300]
    cpu_temp = choose(sensors, ("cpu", "package", "tdie", "core"), ("temperature",))
    gpu_temp = choose(sensors, ("gpu", "radeon", "nvidia", "geforce"), ("temperature",))
    cpu_load = choose(sensors, ("cpu", "total"), ("load",))
    gpu_load = choose(sensors, ("gpu", "radeon", "nvidia", "geforce"), ("load",))
    ram_load = choose(sensors, ("memory", "ram"), ("load",))
    disk_activity = max(
        (sensor["value"] for sensor in sensors if sensor["type"] == "load" and "total activity" in sensor["name"].lower() and sensor["value"] is not None),
        default=None,
    )
    network_utilization = max(
        (sensor["value"] for sensor in sensors if sensor["type"] == "load" and "network utilization" in sensor["name"].lower() and sensor["value"] is not None),
        default=None,
    )
    temperatures = [sensor for sensor in sensors if features.get("temperatures", True) and sensor["type"] == "temperature" and sensor["value"] is not None]
    performance = {
        "cpu_clock_mhz": sensor_value(sensors, ("cpu", "cores"), ("clock",)),
        "cpu_power_w": sensor_value(sensors, ("cpu", "package"), ("power",)),
        "gpu_clock_mhz": sensor_value(sensors, ("gpu", "radeon", "nvidia"), ("clock",)),
        "gpu_power_w": sensor_value(sensors, ("gpu", "radeon", "nvidia"), ("power",)),
        "ram_used_gb": sensor_value_all(sensors, ("total memory", "used"), ("data",)),
        "ram_available_gb": sensor_value_all(sensors, ("total memory", "available"), ("data",)),
        "network_load": sensor_value(sensors, ("network utilization",), ("load",)),
        "fan_rpm": sensor_value(sensors, ("cpu", "fan"), ("fan",)),
    }
    system_usage = system_usage_snapshot()
    if disk_activity is not None:
        system_usage["disk_percent"] = round(disk_activity, 1)
    if network_utilization is not None:
        system_usage["network_percent"] = round(network_utilization, 1)
    disks = native_disks() if features.get("hardware_sensors", True) else []
    processes = native_processes(settings)
    memory = psutil.virtual_memory()
    cpu_load_value = cpu_load["value"] if cpu_load else system_usage.get("cpu_percent")
    ram_load_value = ram_load["value"] if ram_load else system_usage.get("memory_percent")
    process_gpu_load = min(100.0, sum(float(item.get("gpu_percent") or 0) for item in processes))
    gpu_load_value = gpu_load["value"] if gpu_load else round(process_gpu_load, 1)
    if performance["ram_used_gb"] is None:
        performance["ram_used_gb"] = round((memory.total - memory.available) / (1024 ** 3), 2)
    if performance["ram_available_gb"] is None:
        performance["ram_available_gb"] = round(memory.available / (1024 ** 3), 2)
    extended = collect_extended(
        features, sensors, processes, system_usage, disks,
        gpu_load_value,
    )
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Libre Hardware Monitor + Windows" if sensor_online else "Windows / psutil",
        "online": True,
        "sensor_online": sensor_online,
        "sensor_message": "Rozšířené teplotní senzory jsou připojené." if sensor_online else "Rozšířené teploty nejsou dostupné; základní telemetrie je plně aktivní.",
        "sensor_error": sensor_error,
        "cpu": {"temperature": cpu_temp and cpu_temp["value"], "load": cpu_load_value},
        "gpu": {"temperature": gpu_temp and gpu_temp["value"], "load": gpu_load_value},
        "ram": {"load": ram_load_value},
        "performance": performance,
        "temperatures": temperatures[:20],
        "disks": disks,
        "processes": processes,
        "system_usage": system_usage,
        "extended": extended,
    }


def main() -> None:
    """Běží jako samostatný lokální proces HUD."""
    logging.info("Spouštím hardware monitor")
    while True:
        try:
            write_status(snapshot())
        except Exception as error:
            logging.exception("Sběrač telemetrie pokračuje po chybě modulu: %s", error)
            write_status({
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "source": "Windows / psutil · obnovení po chybě",
                "online": False,
                "sensor_online": False,
                "message": "TELEMETRIE SE OBNOVUJE",
                "cpu": {}, "gpu": {}, "ram": {}, "performance": {}, "temperatures": [],
                "disks": native_disks(), "processes": native_processes(load_telemetry_settings()), "system_usage": system_usage_snapshot(),
            })
        time.sleep(load_telemetry_settings().get("sampling_seconds", POLL_SECONDS))


if __name__ == "__main__":
    main()
