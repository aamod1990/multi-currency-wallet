from app.core.error_monitor import ErrorRateMonitor, MonitorConfig


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make():
    clock = FakeClock()
    cfg = MonitorConfig(
        window_seconds=60,
        min_requests=10,
        threshold_5xx=0.1,
        threshold_4xx=0.5,
        cooldown_seconds=300,
    )
    return ErrorRateMonitor(cfg, clock=clock), clock


def test_no_alert_below_min_requests():
    m, _ = make()
    assert all(m.record(500) == [] for _ in range(9))


def test_fires_5xx_alert_once_per_cooldown():
    m, clock = make()
    for _ in range(8):
        m.record(200)
    m.record(500)
    assert m.record(500) == ["5xx"]  # 2/10 = 20% > 10%
    assert m.record(500) == []  # still in cooldown
    clock.t = 400  # window expired and cooldown elapsed
    for _ in range(8):
        m.record(200)
    m.record(500)
    assert m.record(500) == ["5xx"]


def test_window_expiry_resets_ratio():
    m, clock = make()
    for _ in range(10):
        m.record(500)
    clock.t = 61
    m.record(200)
    snap = m.snapshot()
    assert snap["requests"] == 1
    assert snap["ratio_5xx"] == 0.0


def test_4xx_alert():
    m, _ = make()
    fired = [m.record(401) for _ in range(10)]
    assert ["4xx"] in fired
