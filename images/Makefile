IMAGES := $(sort $(notdir $(wildcard host-* guest-*)))
CLEAN_IMAGES := $(addprefix clean-,$(IMAGES))

.PHONY: all clean list status $(IMAGES) $(CLEAN_IMAGES)

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

$(IMAGES):
	mkosi --image-id=$@ -C $@ build

$(CLEAN_IMAGES):
	mkosi --image-id=$(patsubst clean-%,%,$@) -C $(patsubst clean-%,%,$@) clean
