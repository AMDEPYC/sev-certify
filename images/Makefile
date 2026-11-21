IMAGES := $(sort $(notdir $(wildcard host-* guest-*)))
GENERATE_MANIFEST ?=
CLEAN_IMAGES := $(addprefix clean-,$(IMAGES))

# Pinned component versions (override on the command line with make VAR=x).
# An empty value means "use the latest upstream release".
VERSIONS_ENV := ../versions.env
include $(VERSIONS_ENV)

# Space character for use in $(subst) (e.g., $(subst $(space),|,$(list)))
space := $(subst ,, )

# $(call latest_release,owner/repo)
#   Returns the tag of the latest GitHub release of owner/repo, e.g.
#   $(call latest_release,virtee/snpguest) -> v0.11.0
#
#   How it works:
#     1. curl requests github.com/<owner/repo>/releases/latest without following
#        redirects (-I: headers only, -o /dev/null: discard the body) and
#        prints the redirect target, .../releases/tag/<tag>.
#     2. sed keeps only the part after "/releases/tag/", i.e. the tag.
#     3. If the result is empty (network error, unknown repo, no releases),
#        $(error) stops make with a hint to pin the tag in versions.env.
#
#   The redirect is used instead of the GitHub REST API because it needs no
#   token and is not subject to the API's 60 requests/hour anonymous limit.
define latest_release
$(or
    $(shell curl -sfI -o /dev/null -w '%{redirect_url}' https://github.com/$(1)/releases/latest | sed -n 's|.*/releases/tag/||p'),
    $(error Failed to fetch latest release tag for $(1); pin it in $(VERSIONS_ENV) or check network access to github.com))
endef

# $(call resolve_tag,VAR,owner/repo)
#   Returns the tag to build with for one component, e.g.
#   $(call resolve_tag,SNPGUEST_TAG,virtee/snpguest)
#
#   $(or) evaluates its arguments left to right and returns the first
#   non-empty one:
#     1. $(VAR)                    - the version pinned in versions.env or on
#                                    the command line (make VAR=x).
#     2. $(call latest_release,..) - otherwise, the latest release of owner/repo.
#
#   Because $(or) stops at the first non-empty argument, a pinned version skips
#   the network lookup entirely.
#
#     $(call resolve_tag,SNPGUEST_TAG,virtee/snpguest)
#                         |
#                         v
#            +---------------------------+  non-empty
#            | 1. $(SNPGUEST_TAG) set?   |------------------> return pinned tag
#            +---------------------------+                    (no network)
#                         | empty
#                         v
#            +---------------------------+
#            | 2. latest_release(repo)   |--> curl github.com, return latest
#            +---------------------------+    tag (error if empty)
#
#   Because TAGS is a recursive (=) variable, this runs only when a target
#   uses the tags, i.e. once per make run, in the versions target.
define resolve_tag
$(or
    $($(1)),
    $(call latest_release,$(2)))
endef

# Tags validated in versions.env (components passed to mkosi)
TAGS_TO_VALIDATE := SNPHOST_TAG SNPGUEST_TAG BEACON_TAG

# Tags allowed in versions.env but not validated (not used by mkosi)
EXCLUDE_TAGS := DISPATCH_TAG

# $(call check_versions_env,file)
#   Recipe that fails, listing the offending lines, if file contains anything
#   other than blank lines, comments, or NAME=TAG for a known NAME.
#   Validates TAGS_TO_VALIDATE; allows EXCLUDE_TAGS without validation.
define check_versions_env
@pattern='^[[:space:]]*(#|$$)|^(SNPHOST_TAG|SNPGUEST_TAG|BEACON_TAG|DISPATCH_TAG)=[^[:space:]]*$$'; \
bad=$$(grep -nvE "$$pattern" $(1) || true); \
if [ -n "$$bad" ]; then \
	echo "$(1): unexpected line(s); expected NAME=TAG with NAME one of $(TAGS_TO_VALIDATE)" >&2; \
	echo "$$bad" >&2; \
	exit 1; \
fi
endef

# $(call check_release,VAR,owner/repo)
#   Shell command that, if VAR is pinned, checks that owner/repo has a GitHub
#   release tagged $(VAR) by requesting github.com/<owner/repo>/releases/tag/<tag>
#   (200: the release exists, 404: it does not). On failure it prints why and
#   sets failed=1. Unpinned components are skipped, since latest_release only
#   ever returns existing releases. The case patterns are written as (pattern)
#   so their parentheses stay balanced inside $(if ...).
check_release = $(if $($(1)),\
	code=$$(curl -sI -o /dev/null -w '%{http_code}' https://github.com/$(2)/releases/tag/$($(1))); \
	case "$$code" in \
		(200) ;; \
		(404) echo "$(1)=$($(1)): no such release of $(2); see https://github.com/$(2)/releases" >&2; failed=1;; \
		(*) echo "$(1)=$($(1)): could not check https://github.com/$(2)/releases/tag/$($(1)) (HTTP $$code); check network access to github.com" >&2; failed=1;; \
	esac;)

# $(call check_download,VAR,owner/repo,asset)
#   Shell command that checks that the asset $(3) of the release of owner/repo
#   that builds will use ($(call resolve_tag,VAR,owner/repo): the pinned tag, or
#   the latest release if unpinned) can be downloaded, by fetching its first
#   byte from the same URL the mkosi.build scripts use (following redirects;
#   200/206: downloadable). On failure it prints why and sets failed=1.
check_download = \
	tag=$(call resolve_tag,$(1),$(2)); \
	url=https://github.com/$(2)/releases/download/$$tag/$(3); \
	code=$$(curl -sL -r 0-0 -o /dev/null -w '%{http_code}' $$url); \
	case "$$code" in \
		(200|206) ;; \
		(*) echo "$(1)=$$tag: cannot download $$url (HTTP $$code); check that the release has this asset and that github.com is reachable" >&2; failed=1;; \
	esac;

# Components checked by check-versions, one VAR:owner/repo:asset entry each:
#   VAR        - the versions.env variable pinning the component's tag
#   owner/repo - the GitHub repository publishing its releases
#   asset      - the release asset checked for compatibility
COMPONENTS := \
	SNPGUEST_TAG:virtee/snpguest:snpguest \
	BEACON_TAG:AMDEPYC/beacon:beacon-linux-x86_64 \
	SNPHOST_TAG:virtee/snphost:snphost

# Tags excluded from mkosi image building validation (e.g., tracked for compatibility
# but not passed to mkosi; see TAGS definition)
EXCLUDE_TAGS := DISPATCH_TAG

# $(call field,n,VAR:owner/repo:asset)
#   Returns the n-th colon-separated field of a COMPONENTS entry, e.g.
#   $(call field,2,SNPGUEST_TAG:virtee/snpguest:snpguest) -> virtee/snpguest
field = $(word $(1),$(subst :, ,$(2)))

# $(call check_component,VAR:owner/repo:asset)
#   Shell commands running check_release and check_download for one
#   COMPONENTS entry.
check_component = \
	$(call check_release,$(call field,1,$(1)),$(call field,2,$(1))) \
	$(call check_download,$(call field,1,$(1)),$(call field,2,$(1)),$(call field,3,$(1)))

# Component tags passed to every image build, as NAME=TAG. DISPATCH_TAG is not passed to mkosi
# but is checked for compatibility and recorded with the resolved versions.
TAGS = SNPGUEST_TAG=$(call resolve_tag,SNPGUEST_TAG,virtee/snpguest) \
       BEACON_TAG=$(call resolve_tag,BEACON_TAG,AMDEPYC/beacon) \
       SNPHOST_TAG=$(call resolve_tag,SNPHOST_TAG,virtee/snphost)

.PHONY: all check-versions clean list status versions $(IMAGES) $(CLEAN_IMAGES)

# Validate ../versions.env, write the tags image builds will use to .tags
# (passed to mkosi with --env-file) and show them
versions: check-versions
	@printf '%s\n' $(TAGS) > .tags.tmp && mv .tags.tmp .tags
	@cat .tags

# Validate ../versions.env and check that every pinned tag (from versions.env
# or the command line) is an existing release and that the release assets the
# builds download are reachable, so a typo fails here rather than midway
# through a build.
check-versions:
	$(call check_versions_env,$(VERSIONS_ENV))
	@failed=0; \
	$(foreach c,$(COMPONENTS),$(call check_component,$(c))) \
	exit $$failed

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

# MKOSI_ENV accumulates --environment flags; add new case blocks to extend
$(IMAGES): | versions
	@MKOSI_ENV=""; \
	case "$(GENERATE_MANIFEST)" in \
		y|yes|1) MKOSI_ENV="$$MKOSI_ENV --environment=GENERATE_MANIFEST=yes";; \
	esac; \
	/home/amd/mkosi-v26/mkosi/bin/mkosi --image-id=$@ -C $@ --env-file=../.tags build $$MKOSI_ENV

$(CLEAN_IMAGES):
	/home/amd/mkosi-v26/mkosi/bin/mkosi --image-id=$(patsubst clean-%,%,$@) -C $(patsubst clean-%,%,$@) clean
