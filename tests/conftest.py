import pytest

from radioyield.sim.lots import LotConfig, generate_lot


@pytest.fixture(scope="session")
def lots():
    cache = {}

    def get(scenario, seed=11, **kw):
        key = (scenario, seed, tuple(sorted(kw.items())))
        if key not in cache:
            cache[key] = generate_lot(LotConfig(scenario=scenario, seed=seed, **kw))
        return cache[key]
    return get
