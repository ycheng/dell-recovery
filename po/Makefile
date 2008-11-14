
DOMAIN=dell-recovery
PO_FILES := $(wildcard *.po)

all: update-po

top_srcdir=../

# update the pot
$(DOMAIN).pot:
	XGETTEXT_ARGS=--msgid-bugs-address=$(CONTACT) intltool-update -p -g $(DOMAIN)

# merge the new stuff into the po files
merge-po: $(PO_FILES)
	XGETTEXT_ARGS=--msgid-bugs-address=$(CONTACT) intltool-update -r -g $(DOMAIN); 

# create mo from the POs
%.mo : %.po
	mkdir -p mo/$(subst dell-recovery-,,$(subst .po,,$<))/LC_MESSAGES/ 
	msgfmt $< -o mo/$(subst dell-recovery-,,$(subst .po,,$<))/LC_MESSAGES/$(DOMAIN).mo 

# dummy target 
update-po: $(DOMAIN).pot merge-po $(patsubst %.po,%.mo,$(wildcard *.po))

clean:
	rm -rf *.pot
	rm -rf mo
