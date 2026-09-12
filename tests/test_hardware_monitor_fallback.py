import urllib.error
from types import SimpleNamespace

import hardware_monitor
import raven_control


def test_snapshot_keeps_native_telemetry_online_without_lhm(monkeypatch):
    monkeypatch.setattr(hardware_monitor, "fetch_sensors", lambda: (_ for _ in ()).throw(urllib.error.URLError("offline")))
    monkeypatch.setattr(hardware_monitor, "native_disks", lambda: [{"name": "C:", "used": 10, "free_gb": 90.0}])
    monkeypatch.setattr(hardware_monitor, "native_processes", lambda _settings: [{"name": "test.exe", "pid": 1}])
    monkeypatch.setattr(hardware_monitor, "system_usage_snapshot", lambda: {"cpu_percent": 12.0, "memory_percent": 34.0})
    monkeypatch.setattr(hardware_monitor, "collect_extended", lambda *_args: {})

    result = hardware_monitor.snapshot()

    assert result["online"] is True
    assert result["sensor_online"] is False
    assert result["temperature_online"] is False
    assert result["source"] == "Windows / psutil"
    assert result["system_usage"]["cpu_percent"] == 12.0
    assert result["cpu"]["load"] == 12.0
    assert result["ram"]["load"] == 34.0
    assert result["gpu"]["load"] == 0.0
    assert result["performance"]["ram_used_gb"] is not None
    assert result["performance"]["ram_available_gb"] is not None
    assert result["disks"][0]["name"] == "C:"
    assert result["processes"][0]["name"] == "test.exe"
    assert result["availability"]["disks"]["available"] is True


def test_snapshot_exposes_native_frequency_battery_and_system_details(monkeypatch):
    monkeypatch.setattr(hardware_monitor, "fetch_sensors", lambda: [])
    monkeypatch.setattr(hardware_monitor, "native_disks", lambda: [{"name": "C:", "used": 10, "free_gb": 90.0}])
    monkeypatch.setattr(hardware_monitor, "native_processes", lambda _settings: [])
    monkeypatch.setattr(hardware_monitor, "system_usage_snapshot", lambda: {"cpu_percent": 1.0, "memory_percent": 2.0, "network_mbps": 3.5})
    monkeypatch.setattr(hardware_monitor, "collect_extended", lambda *_args: {})
    monkeypatch.setattr(hardware_monitor.psutil, "cpu_freq", lambda: SimpleNamespace(current=2396.0))
    monkeypatch.setattr(hardware_monitor.psutil, "sensors_battery", lambda: SimpleNamespace(percent=94.0, power_plugged=True, secsleft=3600))
    monkeypatch.setattr(hardware_monitor.psutil, "cpu_count", lambda logical=True: 4 if logical else 2)

    result = hardware_monitor.snapshot()

    assert result["performance"]["cpu_clock_mhz"] == 2396.0
    assert result["performance"]["network_load"] == 3.5
    assert result["performance"]["battery_percent"] == 94.0
    assert result["performance"]["battery_plugged"] is True
    assert result["performance"]["cpu_physical_cores"] == 2
    assert result["performance"]["cpu_logical_cores"] == 4
    assert result["availability"]["battery"]["available"] is True


def test_native_disks_do_not_depend_on_optional_sensor_setting(monkeypatch):
    monkeypatch.setattr(hardware_monitor, "load_telemetry_settings", lambda: {"enabled": True, "features": {"hardware_sensors": False, "process_monitoring": False}})
    monkeypatch.setattr(hardware_monitor, "native_disks", lambda: [{"name": "E:", "used": 50, "free_gb": 20.0}])
    monkeypatch.setattr(hardware_monitor, "native_processes", lambda _settings: [])
    monkeypatch.setattr(hardware_monitor, "system_usage_snapshot", lambda: {"cpu_percent": 1.0, "memory_percent": 2.0, "network_mbps": 0.0})
    monkeypatch.setattr(hardware_monitor, "collect_extended", lambda *_args: {})

    result = hardware_monitor.snapshot()

    assert result["disks"] == [{"name": "E:", "used": 50, "free_gb": 20.0}]


def test_elevated_sensor_start_reports_real_temperature_count(monkeypatch):
    monkeypatch.setattr(raven_control, "run_powershell", lambda _command, elevated: {"exit_code": 0, "elevated": elevated})
    monkeypatch.setattr(raven_control.hardware_monitor, "fetch_sensors", lambda: [
        {"hardware": "CPU", "name": "Package", "type": "temperature", "value": 51.0, "unit": "51 °C"},
        {"hardware": "CPU", "name": "Fan", "type": "fan", "value": 1800.0, "unit": "1800 RPM"},
    ])

    result = raven_control.start_hardware_sensors_elevated()

    assert result["status"] == "running"
    assert result["sensor_count"] == 2
    assert result["temperature_count"] == 1
    assert result["temperature_online"] is True
