# Claude Micro - everything is generated from config/device.json.
#
#   make            build all outputs and verify them
#   make stl        printable parts only
#   make pcb        route the board, write KiCad + gerbers + drill + BOM
#   make html       the single-file viewer
#   make test       the full verification suite
#   make clean      remove build/

PY      ?= python3
TOOLS    = tools
BUILD    = build

.PHONY: all stl pcb firmware html test clean check dist

all: stl pcb html test

stl:
	@echo "== printable parts"
	@$(PY) $(TOOLS)/build_stl.py

pcb:
	@echo "== board: place, route, pour, check"
	@$(PY) $(TOOLS)/build_pcb.py

firmware:
	@echo "== firmware descriptors"
	@$(PY) $(TOOLS)/build_firmware.py

html: stl pcb
	@echo "== viewer"
	@$(PY) $(TOOLS)/build_html.py

test:
	@echo "== verification"
	@$(PY) tests/test_all.py

check: test

clean:
	@rm -rf $(BUILD)
	@echo "removed $(BUILD)/"

dist: all
	@cd $(BUILD) && zip -qr claude-micro-$(shell $(PY) -c "import json;print(json.load(open('config/device.json'))['meta']['version'])").zip \
		claude-micro.html stl pcb stl_report.json
	@echo "packaged $(BUILD)/claude-micro-*.zip"
