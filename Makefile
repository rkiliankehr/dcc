# DCC - Disk Cleanup Consultant
# Installation Makefile

PREFIX ?= $(HOME)/bin
DCC_HOME := $(shell pwd)
CRON_SCHEDULE ?= 0 9 * * *

.PHONY: install uninstall cron-install cron-uninstall help

help:
	@echo "DCC - Disk Cleanup Consultant"
	@echo ""
	@echo "Targets:"
	@echo "  install        Install dcc to ~/bin"
	@echo "  uninstall      Remove dcc from ~/bin"
	@echo "  cron-install   Add daily scan cron job (9am)"
	@echo "  cron-uninstall Remove cron job"
	@echo "  all            Install + cron"
	@echo ""
	@echo "Variables:"
	@echo "  PREFIX         Install location (default: ~/bin)"
	@echo "  CRON_SCHEDULE  Cron schedule (default: 0 9 * * *)"

install:
	@mkdir -p $(PREFIX)
	@ln -sf $(DCC_HOME)/dcc $(PREFIX)/dcc
	@chmod +x $(DCC_HOME)/dcc
	@echo "Installed: $(PREFIX)/dcc -> $(DCC_HOME)/dcc"

uninstall:
	@rm -f $(PREFIX)/dcc
	@echo "Removed: $(PREFIX)/dcc"

cron-install:
	@(crontab -l 2>/dev/null | grep -v "dcc scan"; echo "$(CRON_SCHEDULE) $(DCC_HOME)/dcc scan >/dev/null 2>&1") | crontab -
	@echo "Cron job installed: $(CRON_SCHEDULE) dcc scan"

cron-uninstall:
	@crontab -l 2>/dev/null | grep -v "dcc scan" | crontab -
	@echo "Cron job removed"

all: install cron-install
