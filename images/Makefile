IMAGES := $(sort $(notdir $(wildcard host-* guest-*)))
CLEAN_IMAGES := $(addprefix clean-,$(IMAGES))

GH_AUTH := $(if $(GITHUB_TOKEN),-H "Authorization: Bearer $(GITHUB_TOKEN)")
GH_LATEST = curl -sf $(GH_AUTH) https://api.github.com/repos/$(1)/releases/latest | jq -r '.tag_name'

.PHONY: all clean list status tags $(IMAGES) $(CLEAN_IMAGES)

tags:
	@set -e; \
	trap 'rm -f .tags.tmp' EXIT; \
	fetch() { \
		tag=$$($(call GH_LATEST,$$1)); \
		if [ -z "$$tag" ] || [ "$$tag" = "null" ]; then \
			echo "Failed to fetch tag for $$1. Try: export GITHUB_TOKEN=\$$(gh auth token)" >&2; \
			exit 1; \
		fi; \
		echo "$$2=$$tag"; \
	}; \
	{ fetch virtee/snpguest SNPGUEST_TAG; \
	  fetch AMDEPYC/beacon   BEACON_TAG; \
	  fetch virtee/snphost   SNPHOST_TAG; } > .tags.tmp; \
	mv .tags.tmp .tags
	@cat .tags

.tags:
	@echo "Missing .tags — run 'make tags' first to fetch component versions." >&2; exit 1

all: $(IMAGES)

clean: $(CLEAN_IMAGES)

list:
	@echo $(IMAGES)

status:
	@for img in $(IMAGES); do \
		if [ -e $$img/$$img ]; then \
			echo "$$img:"; \
			ls $$img/$$img $$img/$$img.* 2>/dev/null | sed 's/^/\t/'; \
		fi; \
	done

$(IMAGES): | .tags
	mkosi --image-id=$@ -C $@ --env-file=../.tags build

$(CLEAN_IMAGES):
	mkosi --image-id=$(patsubst clean-%,%,$@) -C $(patsubst clean-%,%,$@) clean
