import urllib.error

import hardware_monitor


def test_snapshot_keeps_native_telemetry_online_without_lhm(monkeypatch):
    monkeypatch.setattr(hardware_monitor, "fetch_sensors", lambda: (_ for _ in ()).throw(urllib.error.URLError("offline")))
    monkeypatch.setattr(hardware_monitor, "native_disks", lambda: [{"name": "C:", "used": 10, "free_gb": 90.0}])
    monkeypatch.setattr(hardware_monitor, "native_processes", lambda _settings: [{"name": "test.exe", "pid": 1}])
    monkeypatch.setattr(hardware_monitor, "system_usage_snapshot", lambda: {"cpu_percent": 12.0, "memory_percent": 34.0})
    monkeypatch.setattr(hardware_monitor, "collect_extended", lambda *_args: {})

    result = hardware_monitor.snapshot()

    assert result["online"] is True
    assert result["sensor_online"] is False
    assert result["source"] == "Windows / psutil"
    assert result["system_usage"]["cpu_percent"] == 12.0
    assert result["cpu"]["load"] == 12.0
    assert result["ram"]["load"] == 34.0
    assert result["gpu"]["load"] == 0.0
    assert result["performance"]["ram_used_gb"] is not None
    assert result["performance"]["ram_available_gb"] is not None
    assert result["disks"][0]["name"] == "C:"
    assert result["processes"][0]["name"] == "test.exe"
