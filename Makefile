# RadioYield. `make` builds the C++ STDF extension and runs every test that needs no hardware.
PY ?= .venv/bin/python
JOBS ?= 8

.PHONY: all venv build stdf fw-host test test-fast score rig-sim reports bench fuzz uwb clean

all: build test

venv:
	uv venv --python 3.12 .venv && uv pip install --python $(PY) -e '.[dev,bench,rig]'

build: stdf fw-host

stdf:
	cmake -S stdf -B build/stdf -G Ninja -DCMAKE_BUILD_TYPE=Release -DPython_EXECUTABLE=$(abspath $(PY))
	cmake --build build/stdf -j $(JOBS)

fw-host:
	cmake -S firmware/host_tests -B build/fw_host -G Ninja
	cmake --build build/fw_host -j $(JOBS)

test: build
	ctest --test-dir build/stdf --output-on-failure
	ctest --test-dir build/fw_host --output-on-failure
	$(PY) -m pytest -q

test-fast:
	$(PY) -m pytest -q -m "not slow and not data"

# Virtual rig (SIMULATED): the whole RIG.md session against a simulated hallway, scored
# against its truth. Writes docs/results/sim_rig*.json and examples/sim_rig_session.html.
rig-sim:
	$(PY) -m radioyield.sim.rig score --seeds 30 --as-written --out docs/results/sim_rig_as_written.json --workers $(JOBS)
	$(PY) -m radioyield.sim.rig score --seeds 30 --out docs/results/sim_rig.json --workers $(JOBS)
	$(PY) -m radioyield.sim.rig session --seed 7 --out out/sim_rig/seed7 > /dev/null
	$(PY) -m radioyield.cli report --rig out/sim_rig/seed7 --out examples/sim_rig_session.html

# Known-answer scoring of every detector (SIMULATED). Writes docs/results/sim_score.json.
score:
	$(PY) -m radioyield.sim.score --seeds 50 --sensitivity-seeds 20 --calibration-seeds 40 --workers $(JOBS)

# One HTML report per simulated scenario into examples/.
reports:
	$(PY) -m radioyield.cli demo --out examples

uwb:
	$(PY) -m radioyield.uwb losnlos
	$(PY) -m radioyield.uwb ranging

clean:
	rm -rf build out .pytest_cache
