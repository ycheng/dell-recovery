#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
# «dell-bootstrap» - Ubiquity plugin for Dell Factory Process
#
# Copyright (C) 2010-2014, Dell Inc.
#
# Author:
#  - Mario Limonciello <Mario_Limonciello@Dell.com>
#
# This is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation; either version 2 of the License, or at your option)
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this application; if not, write to the Free Software Foundation, Inc., 51
# Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
################################################################################

from ubiquity.plugin import InstallPlugin, Plugin, PluginUI
from ubiquity import misc
from threading import Thread
import time
from Dell.recovery_threading import ProgressBySize
import debconf
import Dell.recovery_common as magic
from Dell.recovery_xml import BTOxml
import subprocess
import os
import re
import shutil
import dbus
from dbus.mainloop.glib import DBusGMainLoop
DBusGMainLoop(set_as_default=True)
import syslog
import glob
import zipfile
import tarfile
import hashlib
from apt.cache import Cache
from gi.repository import GLib, UDisks

NAME = 'dell-bootstrap'
BEFORE = 'language'
WEIGHT = 12
OEM = False

#Partition Definitions
STANDARD_UP_PARTITION   =     '1'
STANDARD_RP_PARTITION   =     '2'
STANDARD_OS_PARTITION   =     '3'
STANDARD_SWAP_PARTITION =     '4'

DUAL_OS_PARTITION       =     '4'

EFI_ESP_PARTITION       =     '1'
EFI_UP_PARTITION        =     '2'
EFI_RP_PARTITION        =     '3'
EFI_OS_PARTITION        =     '4'
EFI_SWAP_PARTITION      =     '5'

TYPE_NTFS = '07'
TYPE_NTFS_RE = '27'
TYPE_VFAT = '0b'
TYPE_VFAT_LBA = '0c'

#Continually Reused ubiquity templates
RECOVERY_TYPE_QUESTION =  'dell-recovery/recovery_type'
DUAL_BOOT_QUESTION = 'dell-recovery/dual_boot'
DUAL_BOOT_LAYOUT_QUESTION = 'dell-recovery/dual_boot_layout'
ACTIVE_PARTITION_QUESTION = 'dell-recovery/active_partition'
FAIL_PARTITION_QUESTION = 'dell-recovery/fail_partition'
DISK_LAYOUT_QUESTION = 'dell-recovery/disk_layout'
SWAP_QUESTION = 'dell-recovery/swap'
RP_FILESYSTEM_QUESTION = 'dell-recovery/recovery_partition_filesystem'
DRIVER_INSTALL_QUESTION = 'dell-recovery/disable-driver-install'

no_options = GLib.Variant('a{sv}', {})

#######################
# Noninteractive Page #
#######################
class PageNoninteractive(PluginUI):
    """Non-Interactive frontend for the dell-bootstrap ubiquity plugin"""
    def __init__(self, controller, *args, **kwargs):
        self.controller = controller
        PluginUI.__init__(self, controller, *args, **kwargs)
    
    def get_type(self):
        '''For the noninteractive frontend, get_type always returns an empty str
            This is because the noninteractive frontend always runs in "factory"
            mode, which expects such a str""'''
        return ""

    def set_type(self, value, stage):
        """Empty skeleton function for the non-interactive UI"""
        pass

    def show_dialog(self, which, data = None):
        """Empty skeleton function for the non-interactive UI"""
        pass

    def get_selected_device(self):
        """Empty skeleton function for the non-interactive UI"""
        pass

    def populate_devices(self, devices):
        """Empty skeleton function for the non-interactive UI"""
        pass

    def set_advanced(self, item, value):
        """Empty skeleton function for the non-interactive UI"""
        pass

    def get_advanced(self, item):
        """Empty skeleton function for the non-interactive UI"""
        return ''

############
# GTK Page #
############
class PageGtk(PluginUI):
    """GTK frontend for the dell-bootstrap ubiquity plugin"""
    #OK, so we're not "really" a language page
    #We are just cheating a little bit to make sure our widgets are translated
    plugin_is_language = True

    def __init__(self, controller, *args, **kwargs):
        self.plugin_widgets = None

        oem = 'UBIQUITY_OEM_USER_CONFIG' in os.environ

        self.efi = False
        self.genuine = magic.check_vendor()

        if not oem:
            from gi.repository import Gtk
            builder = Gtk.Builder()
            builder.add_from_file('/usr/share/ubiquity/gtk/stepDellBootstrap.ui')
            builder.connect_signals(self)
            self.controller = controller
            self.controller.add_builder(builder)
            self.plugin_widgets = builder.get_object('stepDellBootstrap')
            self.automated_recovery = builder.get_object('automated_recovery')
            self.automated_recovery_box = builder.get_object('automated_recovery_box')
            self.automated_combobox = builder.get_object('hard_drive_combobox')
            self.interactive_recovery = builder.get_object('interactive_recovery')
            self.interactive_recovery_box = builder.get_object('interactive_recovery_box')
            self.hdd_recovery = builder.get_object('hdd_recovery')
            self.hdd_recovery_box = builder.get_object('hdd_recovery_box')
            self.hidden_radio = builder.get_object('hidden_radio')
            self.info_box = builder.get_object('info_box')
            self.info_spinner = Gtk.Spinner()
            builder.get_object('info_spinner_box').add(self.info_spinner)
            self.restart_box = builder.get_object('restart_box')
            self.err_dialog = builder.get_object('err_dialog')

            #advanced page widgets
            icon = builder.get_object('dell_image')
            icon.set_tooltip_markup("Dell Recovery Advanced Options")
            self.advanced_page = builder.get_object('advanced_window')
            self.advanced_table = builder.get_object('advanced_table')
            self.version_detail = builder.get_object('version_detail')
            self.mount_detail = builder.get_object('mountpoint_detail')
            self.memory_detail = builder.get_object('memory_detail')
            self.proprietary_combobox = builder.get_object('disable_proprietary_driver_combobox')
            self.dual_combobox = builder.get_object('dual_combobox')
            self.dual_layout_combobox = builder.get_object('dual_layout_combobox')
            self.active_partition_combobox = builder.get_object('active_partition_combobox')
            self.rp_filesystem_combobox = builder.get_object('recovery_partition_filesystem_checkbox')
            self.disk_layout_combobox = builder.get_object('disk_layout_combobox')
            self.swap_combobox = builder.get_object('swap_behavior_combobox')
            self.ui_combobox = builder.get_object('default_ui_combobox')

            if not (self.genuine and 'UBIQUITY_AUTOMATIC' in os.environ):
                builder.get_object('error_box').show()
            PluginUI.__init__(self, controller, *args, **kwargs)

    def plugin_get_current_page(self):
        """Called when ubiquity tries to realize this page.
           * Disable the progress bar
           * Check whether we are on genuine hardware
        """
        #are we real?
        if not (self.genuine and 'UBIQUITY_AUTOMATIC' in os.environ):
            self.advanced_table.set_sensitive(False)
            self.interactive_recovery_box.hide()
            self.automated_recovery_box.hide()
            self.automated_recovery.set_sensitive(False)
            self.interactive_recovery.set_sensitive(False)
            self.controller.allow_go_forward(False)
        self.toggle_progress()

        return self.plugin_widgets

    def toggle_progress(self):
        """Toggles the progress bar for RP build"""
        if 'UBIQUITY_AUTOMATIC' in os.environ and \
                            hasattr(self.controller, 'toggle_progress_section'):
            self.controller.toggle_progress_section()

    def get_type(self):
        """Returns the type of recovery to do from GUI"""
        if self.automated_recovery.get_active():
            return "automatic"
        elif self.interactive_recovery.get_active():
            return "interactive"
        else:
            return ""

    def get_selected_device(self):
        """Returns the selected device from the GUI"""
        device = size = ''
        model = self.automated_combobox.get_model()
        iterator = self.automated_combobox.get_active_iter()
        if iterator is not None:
            device = model.get_value(iterator, 0)
            size = model.get_value(iterator, 1)
        return (device, size)

    def set_type(self, value, stage):
        """Sets the type of recovery to do in GUI"""
        if not self.genuine:
            return
        self.hidden_radio.set_active(True)

        if value == "automatic":
            self.automated_recovery.set_active(True)
        elif value == "interactive":
            self.interactive_recovery.set_active(True)
        elif value == "factory":
            if stage == 2:
                self.plugin_widgets.hide()
        else:
            self.controller.allow_go_forward(False)
            if value == "hdd":
                self.advanced_table.set_sensitive(False)
                self.hdd_recovery_box.show()
                self.interactive_recovery_box.hide()
                self.automated_recovery_box.hide()
                self.interactive_recovery.set_sensitive(False)
                self.automated_recovery.set_sensitive(False)

    def toggle_type(self, widget):
        """Allows the user to go forward after they've made a selection'"""
        self.controller.allow_go_forward(True)
        self.automated_combobox.set_sensitive(self.automated_recovery.get_active())

    def show_dialog(self, which, data = None):
        """Shows a dialog"""
        if which == "info":
            self.controller._wizard.quit.set_label(
                         self.controller.get_string('ubiquity/imported/cancel'))
            self.controller.allow_go_forward(False)
            self.automated_recovery_box.hide()
            self.interactive_recovery_box.hide()
            self.info_box.show_all()
            self.info_spinner.start()
            self.toggle_progress()
        elif which == "forward":
            self.automated_recovery_box.hide()
            self.interactive_recovery_box.hide()
            self.toggle_progress()
        else:
            self.info_spinner.stop()
            if which == "exception":
                self.err_dialog.format_secondary_text(str(data))
                self.err_dialog.run()
                self.err_dialog.hide()
                return

    def populate_devices(self, devices):
        """Feeds a selection of devices into the GUI
           devices should be an array of 3 column arrays
        """
        #populate the devices
        liststore = self.automated_combobox.get_model()
        for device in devices:
            liststore.append(device)

        #default to the first item active (it should be sorted anyway)
        self.automated_combobox.set_active(0)

    ##                      ##
    ## Advanced GUI options ##
    ##                      ##
    def toggle_advanced(self, widget, data = None):
        """Shows the advanced page"""
        self.plugin_widgets.set_sensitive(False)
        self.advanced_page.run()
        self.advanced_page.hide()
        self.plugin_widgets.set_sensitive(True)

    def _map_combobox(self, item):
        """Maps a combobox to a question"""
        combobox = None
        if item == DRIVER_INSTALL_QUESTION:
            combobox = self.proprietary_combobox
        elif item == ACTIVE_PARTITION_QUESTION:
            combobox = self.active_partition_combobox
        elif item == RP_FILESYSTEM_QUESTION:
            combobox = self.rp_filesystem_combobox
        elif item == DISK_LAYOUT_QUESTION:
            combobox = self.disk_layout_combobox
        elif item == SWAP_QUESTION:
            combobox = self.swap_combobox
        elif item == DUAL_BOOT_QUESTION:
            combobox = self.dual_combobox
        elif item == DUAL_BOOT_LAYOUT_QUESTION:
            combobox = self.dual_layout_combobox
        return combobox

    def set_advanced(self, item, value):
        """Populates the options that should be on the advanced page"""

        if item == 'efi' and value:
            self.efi = True
            self.disk_layout_combobox.set_sensitive(False)
            self.active_partition_combobox.set_sensitive(False)
            self.dual_combobox.set_sensitive(False)
        elif item == "mem" and value:
            self.memory_detail.set_markup("Total Memory: %f GB" % value)
        elif item == "version":
            self.version_detail.set_markup("Version: %s" % value)
        elif item == "mount":
            self.mount_detail.set_markup("Mounted From: %s" % value)
        else:
            if type(value) is bool:
                if value:
                    value = 'true'
                else:
                    value = 'false'
            combobox = self._map_combobox(item)
            if combobox:
                iterator = find_item_iterator(combobox, value)
                if iterator is not None:
                    combobox.set_active_iter(iterator)
                else:
                    syslog.syslog("DEBUG: setting %s to %s failed" % \
                                                                  (item, value))
                    combobox.set_active(0)

            #dual boot mode. ui changes for this
            if item == DUAL_BOOT_QUESTION and self.genuine:
                value = misc.create_bool(value)
                self.dual_layout_combobox.set_sensitive(value)
                if value:
                    self.interactive_recovery_box.hide()
                else:
                    self.interactive_recovery_box.show()
                self.interactive_recovery.set_sensitive(not value)

    def get_advanced(self, item):
        """Returns the value in an advanced key"""
        combobox = self._map_combobox(item)
        if combobox:
            model = combobox.get_model()
            iterator = combobox.get_active_iter()
            return model.get_value(iterator, 0)
        else:
            return ""
 
    def advanced_callback(self, widget, data = None):
        """Callback when an advanced widget is toggled"""
        if widget == self.proprietary_combobox:
            #nothing changes if we change proprietary drivers currently
            pass
        elif widget == self.active_partition_combobox:
            #nothing changes if we change active partition currently
            pass
        elif widget == self.rp_filesystem_combobox:
            #nothing changes if we change RP filesystem currently
            pass
        elif widget == self.swap_combobox:
            #nothing change if we change swap currently
            pass
        else:
            model = widget.get_model()
            iterator = widget.get_active_iter()
            if iterator is not None:
                answer = model.get_value(iterator, 0)
                
            if widget == self.disk_layout_combobox:
                if answer == "gpt":
                    find_n_set_iterator(self.active_partition_combobox, \
                                                         EFI_ESP_PARTITION)
                    self.active_partition_combobox.set_sensitive(False)
                else:
                    self.active_partition_combobox.set_sensitive(True)
            elif widget == self.dual_combobox:
                answer = misc.create_bool(answer)
                if not self.efi:
                    #set the type back to msdos
                    find_n_set_iterator(self.disk_layout_combobox, "msdos")
                    self.disk_layout_combobox.set_sensitive(not answer)
                #hide in the UI - this is a little special because it hides
                #some basic settings too
                self.set_advanced(DUAL_BOOT_QUESTION, answer)

################
# Debconf Page #
################
class Page(Plugin):
    """Debconf driven page for the dell-bootstrap ubiquity plugin"""
    def __init__(self, frontend, db=None, ui=None):
        self.device = None
        self.device_size = 0
        self.efi = False
        self.preseed_config = ''
        self.rp_builder = None
        self.os_part = None
        self.disk_size = None
        self.rp_filesystem = None
        self.fail_partition = None
        self.disk_layout = None
        self.swap_part = None
        self.swap = None
        self.dual = None
        self.rp_part = None
        self.up_part = None
        self.stage = 1
        Plugin.__init__(self, frontend, db, ui)

    def log(self, error):
        """Outputs a debugging string to /var/log/installer/debug"""
        self.debug("%s: %s" % (NAME, error))

    def delete_swap(self):
        """Disables any swap partitions in use"""
        udisks = UDisks.Client.new_sync(None)
        manager = udisks.get_object_manager()
        for item in manager.get_objects():
            swap = item.get_swapspace()
            if not swap:
                continue
            part = item.get_partition()
            if not part:
                continue
            #Check if the swap is active or not
            swap_active = swap.get_cached_property("Active").get_boolean()
            if not swap_active:
                continue
            swap.call_stop_sync(no_options)
            part.call_delete_sync(no_options)

    def sleep_network(self):
        """Requests the network be disabled for the duration of install to
           prevent conflicts"""
        bus = dbus.SystemBus()
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        try:
            backend_iface = dbus.Interface(bus.get_object(magic.DBUS_BUS_NAME, '/RecoveryMedia'), magic.DBUS_INTERFACE_NAME)
            backend_iface.force_network(False)
            backend_iface.request_exit() 
        except Exception:
            pass

    def test_swap(self):
        """Tests what to do with swap"""
        if not self.swap or (self.swap == "dynamic" and \
                                       (self.mem >= 32 or self.disk_size <= 64)):
            return True
        else:
            return False

    def clean_recipe(self):
        """Cleans up the recipe to remove swap if we have a small drive"""

        #don't mess with dual boot recipes
        if self.dual:
            return

        #If we are in dynamic (dell-recovery/swap=dynamic) and small drive 
        #   or we explicitly disabled (dell-recovery/swap=false)
        if self.test_swap():
            self.log("Performing swap recipe fixup (%s, hdd: %i, mem: %f)" % \
                                        (self.swap, self.disk_size, self.mem))
            try:
                recipe = self.db.get('partman-auto/expert_recipe')
                self.db.set('partman-auto/expert_recipe',
                                     ' . '.join(recipe.split('.')[0:-2])+' .')
            except debconf.DebconfError as err:
                self.log(str(err))

    def remove_extra_partitions(self):
        """Removes partitions we are installing on for the process to start"""
        if self.disk_layout == 'msdos':
            #First set the new partition active
            active = misc.execute_root('sfdisk', '-A%s' % self.fail_partition, \
                                                                    self.device)
            if active is False:
                self.log("Failed to set partition %s active on %s" % \
                                             (self.fail_partition, self.device))
        #check for small disks.
        #on small disks or big mem, don't look for extended or delete swap.
        if self.test_swap():
            self.swap_part = ''
            total_partitions = 0
        else:
            #check for extended partitions
            with misc.raised_privileges():
                total_partitions = len(magic.fetch_output(['partx', self.device]).split('\n'))-1
        #remove extras
        for number in (self.os_part, self.swap_part):
            if number.isdigit():
                remove = misc.execute_root('parted', '-s', self.device, 'rm', number)
                if remove is False:
                    self.log("Error removing partition number: %s on %s (this may be normal)'" % (number, self.device))
                refresh = misc.execute_root('partx', '-d', '--nr', number, self.device)
                if refresh is False:
                    self.log("Error updating partition %s for kernel device %s (this may be normal)'" % (number, self.device))
        #if there were extended, cleanup
        if total_partitions > 4 and self.disk_layout == 'msdos':
            refresh = misc.execute_root('partx', '-d', '--nr', '5-' + str(total_partitions), self.device)
            if refresh is False:
                self.log("Error removing extended partitions 5-%s for kernel device %s (this may be normal)'" % (total_partitions, self.device))

    def explode_sdr(self):
        '''Explodes all content explicitly defined in an SDR
           If no SDR was found, don't change drive at all
        '''
        sdr_file = glob.glob(magic.CDROM_MOUNT + "/*SDR")
        if not sdr_file:
            sdr_file = glob.glob(magic.ISO_MOUNT + "/*SDR")
        if not sdr_file:
            return

        #RP Needs to be writable no matter what
        if not os.path.exists(magic.ISO_MOUNT):
            cd_mount = misc.execute_root('mount', '-o', 'remount,rw', magic.CDROM_MOUNT)
            if cd_mount is False:
                raise RuntimeError("Error remounting RP to explode SDR.")

        #Parse SDR
        srv_list = []
        dest = ''
        with open(sdr_file[0], 'r') as rfd:
            sdr_lines = rfd.readlines()
        for line in sdr_lines:
            if line.startswith('SI'):
                columns = line.split()
                if len(columns) > 2:
                    #always assume lower case (in case case sensitive FS)
                    srv_list.append(columns[2].lower())
            if line.startswith('HW'):
                columns = line.split()
                if len(columns) > 2 and columns[1] == 'destination':
                    dest = columns[2]

        #Explode SRVs that match SDR
        for srv in srv_list:
            fname = os.path.join(os.path.join(magic.CDROM_MOUNT, 'srv', '%s' % srv))
            if os.path.exists('%s.tgz' % fname):
                archive = tarfile.open('%s.tgz' % fname)
            elif os.path.exists('%s.zip' % fname):
                archive = zipfile.ZipFile('%s.zip' % fname)
            else:
                self.log("Skipping SRV %s. No file on filesystem." % srv)
                continue
            with misc.raised_privileges():
                self.log("Extracting SRV %s onto filesystem" % srv)
                archive.extractall(path=magic.CDROM_MOUNT)
            archive.close()

        #if the destination is somewhere special, change the language
        if dest:
            self.preseed('dell-recovery/destination', dest)
        if dest == 'CN':
            self.preseed('debian-installer/locale', 'zh_CN.UTF-8')
            self.ui.controller.translate('zh_CN.UTF-8')

    def explode_utility_partition(self):
        '''Explodes all content onto the utility partition
        '''

        #Check if we have FIST on the system.  FIST indicates this is running
        #through factory process (of some sort) and the UP will be written
        #out outside of our control
        cache = Cache()
        for key in cache.keys():
            if key == 'fist' and cache[key].is_installed:
                self.log("FIST was found, not building a UP.")
                return
        del cache

        #For now on GPT we don't include an UP since we can't boot
        # 16 bit code as necessary for the UP to be working
        if self.disk_layout == 'gpt':
            self.log("A GPT layout was found, not building a UP.")
            return

        mount = False
        path = ''
        if os.path.exists('/usr/share/dell/up/drmk.zip'):
            path = '/usr/share/dell/up/drmk.zip'
        elif os.path.exists(os.path.join(magic.CDROM_MOUNT, 'misc', 'drmk.zip')):
            path = os.path.join(magic.CDROM_MOUNT, 'misc', 'drmk.zip')
        #If we have DRMK available, explode that first
        if path:
            self.log("Extracting DRMK onto utility partition %s" % self.device + self.up_part)
            mount = misc.execute_root('mount', self.device + self.up_part, '/boot')
            if mount is False:
                raise RuntimeError("Error mounting utility partition pre-explosion.")
            archive = zipfile.ZipFile(path)
            with misc.raised_privileges():
                try:
                    archive.extractall(path='/boot')
                except IOError as msg:
                    #Partition is corrupted, abort doing anything else here but don't
                    #fail the install
                    #TODO ML (1/10/11) - instead rebuild the UP if possible.
                    self.log("Ignoring corrupted utility partition(%s)." % msg)
                    return
            archive.close()

        #Now check for additional UP content to explode
        for fname in magic.UP_FILENAMES:
            if os.path.exists(os.path.join(magic.CDROM_MOUNT, fname)):
                #Restore full UP backup (dd)
                if '.bin' in fname or '.gz' in fname:
                    self.log("Exploding utility partition from %s" % fname)
                    with misc.raised_privileges():
                        with open(self.device + self.up_part, 'wb') as partition:
                            p1 = subprocess.Popen(['gzip', '-dc', os.path.join(magic.CDROM_MOUNT, fname)], stdout=subprocess.PIPE)
                            partition.write(p1.communicate()[0])
                #Restore UP (zip/tgz)
                elif '.zip' in fname or '.tgz' in fname:
                    self.log("Extracting utility partition from %s" % fname)
                    if not mount:
                        mount = misc.execute_root('mount', self.device + self.up_part, '/boot')
                        if mount is False:
                            raise RuntimeError("Error mounting utility partition pre-explosion.")
                    if '.zip' in fname:
                        archive = zipfile.ZipFile(os.path.join(magic.CDROM_MOUNT, fname))
                    elif '.tgz' in file:
                        archive = tarfile.open(os.path.join(magic.CDROM_MOUNT, fname))
                    with misc.raised_privileges():
                        archive.extractall(path='/boot')
                    archive.close()
        #If we didn't include an autoexec.bat (as is the case from normal DellDiags releases)
        #Then make the files we need to be automatically bootable
        if not os.path.exists('/boot/autoexec.bat') and os.path.exists('/boot/autoexec.up'):
            with misc.raised_privileges():
                shutil.copy('/boot/autoexec.up', '/boot/autoexec.bat')
        if not os.path.exists('/boot/config.sys') and os.path.exists('/boot/config.up'):
            with misc.raised_privileges():
                shutil.copy('/boot/config.up', '/boot/config.sys')
        if mount:
            umount = misc.execute_root('umount', '/boot')
            if umount is False:
                raise RuntimeError("Error unmounting utility partition post-explosion.")

    def usb_boot_preseeds(self, more_keys=None):
        """Sets/unsets preseeds that are common to a USB boot scenario.
           This can either happen if booted from USB stick while in stage 2
           or if booted from USB stick in stage 1 and choosing to only restore
           the linux partition
        """
        keys = ['ubiquity/poweroff', 'ubiquity/reboot']
        if more_keys:
            keys += more_keys
        for key in keys:
            self.db.fset(key, 'seen', 'false')
            self.db.set(key, '')
        self.db.set('ubiquity/partman-skip-unmount', 'false')
        self.db.set('partman/filter_mounted', 'true')

    def unset_drive_preseeds(self):
        """Unsets any preseeds that are related to setting a drive"""
        keys = [ 'partman-auto/init_automatically_partition',
                 'partman-auto/disk',
                 'partman-auto/expert_recipe',
                 'partman-basicfilesystems/no_swap',
                 'grub-installer/only_debian',
                 'grub-installer/with_other_os',
                 'grub-installer/bootdev',
                 'grub-installer/make_active',
                 'oem-config/early_command',
                 'oem-config/late_command',
                 'dell-recovery/active_partition',
                 'dell-recovery/fail_partition']
        self.usb_boot_preseeds(keys)

    def fixup_recovery_devices(self):
        """Discovers the first hard disk to install to"""
        disks = []
        udisks = UDisks.Client.new_sync(None)
        manager = udisks.get_object_manager()
        drive = None
        for item in manager.get_objects():
            loop = item.get_loop()
            block = item.get_block()
            partition = item.get_partition()
            if loop or \
               partition or \
               not block or \
               not block.get_cached_property("HintPartitionable").get_boolean() or \
               block.get_cached_property("ReadOnly").get_boolean():
                continue

            #Check the disk is type of NVME SSD
            device_path = block.get_cached_property("Device").get_bytestring().decode('utf-8')
            if device_path.startswith('/dev/nvme'):
                with misc.raised_privileges():
                    getdisk_subp = subprocess.Popen(['smartctl', '-d', 'scsi', '--all', device_path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                    output = getdisk_subp.communicate()[0]
                    vendor = ""
                    model = ""
                    for prop in output.decode('utf-8').split('\n'):
                        if prop.startswith('Vendor:'):
                            vendor = prop.split(':')[1].strip()
                        elif prop.startswith('Product:'):
                            model = prop.split(':')[1].strip()
                        else:
                            continue

                nvme_dev_size = block.get_cached_property("Size").unpack()
                nvme_size_gb = "%i" % (nvme_dev_size / 1000000000)
                symlink = block.get_cached_property("Symlinks")[0]
                nvme_dev_file = ''.join(chr(i) for i in symlink)
                disks.append([nvme_dev_file, nvme_dev_size, "%s GB %s %s (%s)" % (nvme_size_gb, vendor, model, device_path)])
                continue
1
            drive_obj = block.get_cached_property("Drive").get_string()
            if drive_obj == '/':
                continue
                
            drive = udisks.get_object(drive_obj).get_drive()
            if drive:
                bus = drive.get_cached_property("ConnectionBus").get_string()
                if bus == 'usb':
                    continue
                elif bus == 'sdio':
                    media = drive.get_cached_property("Media").get_string()
                    if not media:
                        continue
                elif drive.get_cached_property("Removable").get_boolean():
                    continue
            else:
                continue
        
            devicesize = drive.get_cached_property("Size").unpack()
            if devicesize == 0:
                continue

            devicefile = block.get_cached_property("Device").get_bytestring().decode('utf-8')
            devicemodel = drive.get_cached_property("Model").get_string()
            devicevendor = drive.get_cached_property("Vendor").get_string()
            devicesize_gb = "%i" % (devicesize / 1000000000)
            
            disks.append([devicefile, devicesize, "%s GB %s %s (%s)" % (devicesize_gb, devicevendor, devicemodel, devicefile)])

        #If multiple candidates were found, record in the logs
        if len(disks) == 0:
            raise RuntimeError("Unable to find and candidate hard disks to install to.")
        if len(disks) > 1:
            disks.sort()
            self.log("Multiple disk candidates were found: %s" % disks)

        #Always choose the first candidate to start
        self.device = disks[0][0]
        self.log("Initially selected candidate disk: %s" % self.device)

        #populate UI
        self.ui.populate_devices(disks)

    def fixup_factory_devices(self, rec_part):
        """Find the factory recovery partition, and re-adjust preseeds to use that data"""
        #Ignore any EDD settings - we want to just plop on the same drive with
        #the right FS label (which will be valid right now)
        #Don't you dare put a USB stick in the system with that label right now!

        self.device = rec_part["slave"]

        if os.path.exists(magic.ISO_MOUNT):
            location = magic.ISO_MOUNT
        else:
            location = magic.CDROM_MOUNT

        early = '/usr/share/dell/scripts/oem_config.sh early %s' % location
        self.db.set('oem-config/early_command', early)
        self.db.set('partman-auto/disk', self.device)

        if self.disk_layout == 'msdos':
            self.db.set('grub-installer/bootdev', self.device + self.os_part)
        elif self.disk_layout == 'gpt':
            self.db.set('grub-installer/bootdev', self.device)

        if rec_part["fs"] == "ntfs":
            self.rp_filesystem = TYPE_NTFS_RE
        elif rec_part["fs"] == "vfat":
            self.rp_filesystem = TYPE_VFAT_LBA
        else:
            raise RuntimeError("Unknown filesystem on recovery partition: %s" % rec_part["fs"])

        if self.dual_layout == 'logical':
            expert_question = 'partman-auto/expert_recipe'
            self.db.set(expert_question,
                    self.db.get(expert_question).replace('primary', 'logical'))
            self.db.set('ubiquity/install_bootloader', 'false')

        self.disk_size = rec_part["size_gb"]

        self.log("Detected device we are operating on is %s" % self.device)
        self.log("Detected a %s filesystem on the %s recovery partition" % (rec_part["fs"], rec_part["label"]))

    def prepare(self, unfiltered=False):
        """Prepare the Debconf portion of the plugin and gather all data"""
        #version
        with misc.raised_privileges():
            version = magic.check_version()
        self.log("version %s" % version)
        
        #mountpoint
        mount = find_boot_device()
        self.log("mounted from %s" % mount)

        #recovery type
        rec_type = None
        try:
            rec_type = self.db.get(RECOVERY_TYPE_QUESTION)
        except debconf.DebconfError as err:
            self.log(str(err))
            rec_type = 'dynamic'
            self.db.register('debian-installer/dummy', RECOVERY_TYPE_QUESTION)
            self.db.set(RECOVERY_TYPE_QUESTION, rec_type)

        #If we were preseeded to dynamic, look for an RP
        rec_part = magic.find_factory_partition_stats('rp')
        if "slave" in rec_part:
            self.stage = 2
        if rec_type == 'dynamic':
            # we rebooted with no USB stick or DVD in drive and have the RP
            # mounted at /cdrom
            if self.stage == 2 and rec_part["slave"] in mount:
                self.log("Detected RP at %s, setting to factory boot" % mount)
                rec_type = 'factory'
            else:
                self.log("No (matching) RP found.  Assuming media based boot")
                rec_type = 'dvd'

        #Media boots should be interrupted at first screen in --automatic mode
        if rec_type == 'factory':
            self.db.fset(RECOVERY_TYPE_QUESTION, 'seen', 'true')
        else:
            self.db.set(RECOVERY_TYPE_QUESTION, '')
            self.db.fset(RECOVERY_TYPE_QUESTION, 'seen', 'false')

        #In case we preseeded the partitions we need installed to
        try:
            self.up_part = self.db.get('dell-recovery/up_partition')
        except debconf.DebconfError as err:
            self.log(str(err))
            self.up_part = '1'

        try:
            self.rp_part = self.db.get('dell-recovery/rp_partition')
        except debconf.DebconfError as err:
            self.log(str(err))
            self.rp_part = '2'

        try:
            self.os_part = self.db.get('dell-recovery/os_partition')
        except debconf.DebconfError as err:
            self.log(str(err))
            self.os_part = '3'

        try:
            self.swap_part = self.db.get('dell-recovery/swap_partition')
        except debconf.DebconfError as err:
            self.log(str(err))
            self.swap_part = '4'

        #Support cases where the recovery partition isn't a linux partition
        try:
            self.rp_filesystem = self.db.get(RP_FILESYSTEM_QUESTION)
        except debconf.DebconfError as err:
            self.log(str(err))
            self.rp_filesystem = TYPE_VFAT_LBA

        #Check if we are set in dual-boot mode
        try:
            self.dual = misc.create_bool(self.db.get(DUAL_BOOT_QUESTION))
        except debconf.DebconfError as err:
            self.log(str(err))
            self.dual = False

        try:
            self.dual_layout = self.db.get(DUAL_BOOT_LAYOUT_QUESTION)
        except debconf.DebconfError as err:
            self.log(str(err))
            self.dual_layout = 'primary'

        #If we are successful for an MBR install, this is where we boot to
        try:
            pass_partition = self.db.get(ACTIVE_PARTITION_QUESTION)
        except debconf.DebconfError as err:
            self.log(str(err))
            pass_partition = self.os_part
            self.preseed(ACTIVE_PARTITION_QUESTION, pass_partition)

        #In case an MBR install fails, this is where we boot to
        try:
            self.fail_partition = self.db.get(FAIL_PARTITION_QUESTION)
        except debconf.DebconfError as err:
            self.log(str(err))
            self.fail_partition = STANDARD_RP_PARTITION
            self.preseed(FAIL_PARTITION_QUESTION, self.fail_partition)

        #The requested disk layout type
        #This is generally for debug purposes, but will be overridden if we
        #determine that we are actually going to be doing an EFI install
        try:
            self.disk_layout = self.db.get(DISK_LAYOUT_QUESTION)
        except debconf.DebconfError as err:
            self.log(str(err))
            self.disk_layout = 'msdos'
            self.preseed(DISK_LAYOUT_QUESTION, self.disk_layout)

        #Behavior of the swap partition
        try:
            self.swap = self.db.get(SWAP_QUESTION)
            if self.swap != "dynamic":
                self.swap = misc.create_bool(self.swap)
        except debconf.DebconfError as err:
            self.log(str(err))
            self.swap = 'dynamic'

        #Proprietary driver installation preventions
        try:
            proprietary = self.db.get(DRIVER_INSTALL_QUESTION)
        except debconf.DebconfError as err:
            self.log(str(err))
            proprietary = ''

        #If we detect that we are booted into uEFI mode, then we only want
        #to do a GPT install.  Actually a MBR install would work in most
        #cases, but we can't make assumptions about 16-bit anymore (and
        #preparing a UP because of it)
        if os.path.isdir('/proc/efi') or os.path.isdir('/sys/firmware/efi'):
            self.efi = True
            self.disk_layout = 'gpt'
            self.preseed(DISK_LAYOUT_QUESTION, self.disk_layout)

        #dynamic partition map.
        #EFI layout:        esp, up, rp, os, swap
        #MBR layout:        up, rp, os, swap
        #dual (pri) layout: up, rp, win, ubx
        #dual (log) layout: up, rp, win, ubx
        if self.up_part == 'dynamic':
            if self.efi:
                self.up_part = EFI_UP_PARTITION
            else:
                self.up_part = STANDARD_UP_PARTITION
        if self.rp_part == 'dynamic':
            if self.efi:
                self.rp_part = EFI_RP_PARTITION
            else:
                self.rp_part = STANDARD_RP_PARTITION
        if self.os_part == 'dynamic':
            if self.efi or self.disk_layout == 'gpt':
                self.os_part = EFI_OS_PARTITION
            elif self.dual:
                self.os_part = DUAL_OS_PARTITION
            else:
                self.os_part = STANDARD_OS_PARTITION
        if self.swap_part == 'dynamic':
            if self.efi:
                self.swap_part = EFI_SWAP_PARTITION
            else:
                self.swap_part = STANDARD_SWAP_PARTITION
        if self.fail_partition == 'dynamic':
            self.fail_partition = self.rp_part
            self.preseed(FAIL_PARTITION_QUESTION, self.fail_partition)
        if pass_partition == 'dynamic':
            #Force EFI partition or bios_grub partition active
            if self.disk_layout == 'gpt':
                pass_partition = EFI_ESP_PARTITION
            #Force (new) OS partition to be active
            else:
                pass_partition = self.os_part
            self.preseed(ACTIVE_PARTITION_QUESTION, pass_partition)

        #Amount of memory in the system
        self.mem = 0
        if os.path.exists('/sys/firmware/memmap'):
            for root, dirs, files in os.walk('/sys/firmware/memmap', topdown=False):
                if os.path.exists(os.path.join(root, 'type')):
                    with open(os.path.join(root, 'type')) as rfd:
                        type = rfd.readline().strip('\n')
                    if type != "System RAM":
                        continue
                    with open(os.path.join(root, 'start')) as rfd:
                        start = int(rfd.readline().strip('\n'),0)
                    with open(os.path.join(root, 'end')) as rfd:
                        end = int(rfd.readline().strip('\n'),0)
                    self.mem += (end - start + 1)
            self.mem = float(self.mem/1024)
        if self.mem == 0:
            with open('/proc/meminfo','r') as rfd:
                for line in rfd.readlines():
                    if line.startswith('MemTotal'):
                        self.mem = float(line.split()[1].strip())
                        break
        self.mem = round(self.mem/1048575) #in GB

        #Fill in UI data
        twiddle = {"mount": mount,
                   "version": version,
                   DUAL_BOOT_LAYOUT_QUESTION: self.dual_layout,
                   DUAL_BOOT_QUESTION: self.dual,
                   ACTIVE_PARTITION_QUESTION: pass_partition,
                   DISK_LAYOUT_QUESTION: self.disk_layout,
                   SWAP_QUESTION: self.swap,
                   DRIVER_INSTALL_QUESTION: proprietary,
                   RP_FILESYSTEM_QUESTION: self.rp_filesystem,
                   "mem": self.mem,
                   "efi": self.efi}
        # The order invoking set_advanced() is important. (LP: #1324394)
        for twaddle in reversed(sorted(twiddle)):
            self.ui.set_advanced(twaddle, twiddle[twaddle])
        self.ui.set_type(rec_type, self.stage)

        #Make sure some locale was set so we can guarantee automatic mode
        try:
            language = self.db.get('debian-installer/locale')
        except debconf.DebconfError:
            language = ''
        if not language:
            language = 'en_US.UTF-8'
            self.preseed('debian-installer/locale', language)
            self.ui.controller.translate(language)

        # If there is a Kylin overlay, set language to zh_CN.UTF-8
        client_type = os.path.join('/cdrom', '.oem', 'client_type')
        if os.path.isfile(client_type):
            with open (client_type, "r") as myfile:
                content=myfile.read().replace('\n', '') 
            if content == "kylin":
                language = 'zh_CN.UTF-8'
                self.preseed('debian-installer/locale', language)
                self.ui.controller.translate(language)

        #Clarify which device we're operating on initially in the UI
        try:
            self.fixup_recovery_devices()
            if (rec_type == 'factory' and self.stage == 2) or rec_type == 'hdd':
                self.fixup_factory_devices(rec_part)
        except Exception as err:
            self.handle_exception(err)
            self.cancel_handler()

        return (['/usr/share/ubiquity/dell-bootstrap'], [RECOVERY_TYPE_QUESTION])

    def ok_handler(self):
        """Copy answers from debconf questions"""
        #basic questions
        rec_type = self.ui.get_type()
        self.log("recovery type set to %s" % rec_type)
        self.preseed(RECOVERY_TYPE_QUESTION, rec_type)
        (device, size) = self.ui.get_selected_device()
        if device:
            self.device = device
        if size:
            self.device_size = size

        #advanced questions
        for question in [DUAL_BOOT_QUESTION,
                         DUAL_BOOT_LAYOUT_QUESTION,
                         ACTIVE_PARTITION_QUESTION,
                         DISK_LAYOUT_QUESTION,
                         SWAP_QUESTION,
                         DRIVER_INSTALL_QUESTION,
                         RP_FILESYSTEM_QUESTION]:
            answer = self.ui.get_advanced(question)
            if answer:
                self.log("advanced option %s set to %s" % (question, answer))
                self.preseed_config += question + "=" + answer + " "
                if question == RP_FILESYSTEM_QUESTION:
                    self.rp_filesystem = answer
                elif question == DISK_LAYOUT_QUESTION:
                    self.disk_layout = answer
                elif question == DUAL_BOOT_QUESTION:
                    answer = misc.create_bool(answer)
                    self.dual = answer
                elif question == DUAL_BOOT_LAYOUT_QUESTION:
                    self.dual_layout = answer
            if type(answer) is bool:
                self.preseed_bool(question, answer)
            else:
                self.preseed(question, answer)

        return Plugin.ok_handler(self)

    def report_progress(self, info, percent):
        """Reports to the frontend an update about th progress"""
        self.frontend.debconf_progress_info(info)
        self.frontend.debconf_progress_set(percent)

    def cleanup(self):
        """Do all the real processing for this plugin.
           * This has to be done here because ok_handler won't run in a fully
             automated load, and we need to run all steps in all scenarios
           * Run is the wrong time too because it runs before the user can
             answer potential questions
        """
        rec_type = self.db.get('dell-recovery/recovery_type')

        try:
            # User recovery - need to copy RP
            if rec_type == "automatic" or \
               (rec_type == "factory" and self.stage == 1):

                if not (rec_type == "factory" and self.stage == 1):
                    self.ui.show_dialog("info")
                self.sleep_network()
                self.delete_swap()

                #init progress bar and size thread
                self.frontend.debconf_progress_start(0, 100, "")
                size_thread = ProgressBySize("Copying Files",
                                               "/mnt",
                                               "0")
                size_thread.progress = self.report_progress
                #init builder
                self.rp_builder = RPbuilder(self.device, 
                                            self.device_size,
                                            self.rp_filesystem,
                                            self.mem,
                                            self.dual,
                                            self.dual_layout,
                                            self.disk_layout,
                                            self.efi,
                                            self.preseed_config,
                                            size_thread)
                self.rp_builder.exit = self.exit_ui_loops
                self.rp_builder.status = self.report_progress
                self.rp_builder.start()
                self.enter_ui_loop()
                self.rp_builder.join()
                if self.rp_builder.exception:
                    self.handle_exception(self.rp_builder.exception)
                reboot_machine(None)

            # User recovery - resizing drives
            elif rec_type == "interactive":
                self.ui.show_dialog("forward")
                self.unset_drive_preseeds()

            # Factory install, and booting from RP
            else:
                if 'dell-recovery/recovery_type=hdd' in open('/proc/cmdline', 'r').read().split():
                    self.ui.toggle_progress()
                self.sleep_network()
                self.delete_swap()
                self.clean_recipe()
                self.remove_extra_partitions()
                self.explode_utility_partition()
                self.explode_sdr()
        except Exception as err:
            #For interactive types of installs show an error then reboot
            #Otherwise, just reboot the system
            if rec_type == "automatic" or rec_type == "interactive" or \
               ('UBIQUITY_DEBUG' in os.environ and 'UBIQUITY_ONLY' in os.environ):
                self.handle_exception(err)
            self.cancel_handler()

        #translate languages
        self.ui.controller.translate(just_me=False, not_me=True, reget=True)
        Plugin.cleanup(self)

    def cancel_handler(self):
        """Called when we don't want to perform recovery'"""
        misc.execute_root('reboot')

    def handle_exception(self, err):
        """Handle all exceptions thrown by any part of the application"""
        self.log(str(err))
        self.ui.show_dialog("exception", err)

############################
# RP Builder Worker Thread #
############################
class RPbuilder(Thread):
    """The recovery partition builder worker thread"""
    def __init__(self, device, size, rp_type, mem, dual, dual_layout, disk_layout, efi, preseed_config, sizing_thread):
        self.device = device
        self.device_size = size
        self.rp_type = rp_type
        self.mem = mem
        self.dual = dual
        self.dual_layout = dual_layout
        self.disk_layout = disk_layout
        self.efi = efi
        self.preseed_config = preseed_config
        self.exception = None
        self.file_size_thread = sizing_thread
        self.xml_obj = BTOxml()
        Thread.__init__(self)

    def build_rp(self, cushion=600):
        """Copies content to the recovery partition using a parted wrapper.

           This might be better implemented in python-parted or parted_server/partman,
           but those would require extra dependencies, and are generally more complex
           than necessary for what needs to be accomplished here."""

        black_pattern = re.compile('casper-rw|casper-uuid')

        #Things we know ahead of time will cause us to error out
        if self.disk_layout == 'gpt':
            if self.dual:
                raise RuntimeError("Dual boot is not yet supported when configuring the disk as GPT.")
        elif self.disk_layout == 'msdos':
            pass
        else:
            raise RuntimeError("Unsupported disk layout: %s" % self.disk_layout)

        #Check if we are booted from same device as target
        mounted_device = find_boot_device()
        if self.device in mounted_device:
            raise RuntimeError("Attempting to install to the same device as booted from.\n\
You will need to clear the contents of the recovery partition\n\
manually to proceed.")

        #Adjust recovery partition type to something parted will recognize
        if self.rp_type == TYPE_NTFS or \
           self.rp_type == TYPE_NTFS_RE:
            self.rp_type = 'ntfs'
        elif self.rp_type == TYPE_VFAT or \
             self.rp_type == TYPE_VFAT_LBA:
            self.rp_type = 'fat32'
        else:
            raise RuntimeError("Unsupported recovery partition filesystem: %s" % self.rp_type)

        #Default partition numbers
        up_part   = ''
        rp_part   = ''
        grub_part = STANDARD_RP_PARTITION

        #Calculate RP size
        rp_size = magic.black_tree("size", black_pattern, magic.CDROM_MOUNT)
        #in mbytes
        rp_size_mb = (rp_size / 1000000) + cushion

        # Build new partition table
        command = ('parted', '-s', self.device, 'mklabel', self.disk_layout)
        result = misc.execute_root(*command)
        if result is False:
            raise RuntimeError("Error creating new partition table %s on %s" % (self.disk_layout, self.device))

        #Utility partition files (tgz/zip)#
        up_size = 33

        #Utility partition image (dd)#
        for fname in magic.UP_FILENAMES:
            if 'img' in fname and os.path.exists(os.path.join(magic.CDROM_MOUNT, fname)):
                #in a string
                up_size = magic.fetch_output(['gzip', '-lq', os.path.join(magic.CDROM_MOUNT, fname)])
                #in bytes
                up_size = float(up_size.split()[1])
                #in mbytes
                up_size = 1 + (up_size / 1000000)

        self.status("Creating Partitions", 1)
        if self.disk_layout == 'msdos':
            up_part   = STANDARD_UP_PARTITION
            rp_part   = STANDARD_RP_PARTITION
        
            #Create an MBR
            path = '/usr/share/dell/up/mbr.bin'
            if os.path.exists(path):
                pass
            elif os.path.exists('/usr/lib/syslinux/mbr.bin'):
                path = '/usr/lib/syslinux/mbr.bin'
            else:
                raise RuntimeError("Missing both DRMK and syslinux MBR")
            with open(path, 'rb') as mbr:
                with misc.raised_privileges():
                    with open(self.device, 'wb') as out:
                        out.write(mbr.read(440))

            #Build UP
            commands = [('parted', '-a', 'optimal', '-s', self.device, 'mkpart', 'primary', 'fat16', '1', str(up_size)),
                        ('mkfs.msdos', self.device + '1'),
                        ('udevadm', 'settle')] # Wait for the event queue to finish.

            for command in commands:
                result = misc.execute_root(*command)
                if result is False:
                    raise RuntimeError("Error creating new %s mb utility partition on %s" % (up_size, self.device))

            with misc.raised_privileges():
                #parted marks it as w95 fat16 (LBA).  It *needs* to be type 'de'
                data = 't\nde\n\nw\n'
                magic.fetch_output(['fdisk', self.device], data)

                #build the bootsector of the partition
                magic.write_up_bootsector(self.device, up_part)

            #Build RP
            command = ('parted', '-a', 'optimal', '-s', self.device, 'mkpart', 'primary', self.rp_type, str(up_size), str(up_size + rp_size_mb))
            result = misc.execute_root(*command)
            if result is False:
                raise RuntimeError("Error creating new %s mb recovery partition on %s" % (rp_size_mb, self.device))

            #Set RP active (bootable)
            command = ('parted', '-s', self.device, 'set', rp_part, 'boot', 'on')
            result = misc.execute_root(*command)
            if result is False:
                raise RuntimeError("Error setting recovery partition active %s" % (self.device))

            #Dual boot creates more partitions
            if self.dual:
                my_os_part = 5120 #mb
                other_os_part_end = (int(self.device_size) / 1000000) - my_os_part

                commands = [('parted', '-a', 'optimal', '-s', self.device, 'mkpart', 'primary', 'ntfs', str(up_size + rp_size_mb), str(other_os_part_end)),
                            ('mkfs.ntfs' , '-f', '-L', 'OS', self.device + '3')]
                if self.dual_layout == 'primary':
                    commands.append(('parted', '-a', 'optimal', '-s', self.device, 'mkpart', 'primary', 'fat32', str(other_os_part_end), str(other_os_part_end + my_os_part)))
                    commands.append(('mkfs.msdos', '-n', 'ubuntu'  , self.device + '4'))
                    #Grub needs to be on the 4th partition to kick off the ubuntu install
                    grub_part = '4'
                else:
                    grub_part = '1'
                for command in commands:
                    result = misc.execute_root(*command)
                    if result is False:
                        raise RuntimeError("Error building dual boot partitions")

        #GPT Layout
        elif self.disk_layout == 'gpt':
            #default partition numbers
            up_part = EFI_UP_PARTITION
            rp_part = EFI_RP_PARTITION

            #In GPT we have a UP, but also a BIOS grub partition
            if self.efi:
                grub_size = 50
                commands = [('parted', '-a', 'optimal', '-s', self.device, 'mkpart', 'primary', 'fat16', '0', str(grub_size)),
                            ('parted', '-s', self.device, 'name', '1', "'EFI System Partition'"),
                            ('parted', '-s', self.device, 'set', '1', 'boot', 'on'),
                            ('mkfs.msdos', self.device + '1')]
            else:
                grub_size = 1.5
                commands = [('parted', '-a', 'optimal', '-s', self.device, 'mkpart', 'biosboot', '0', str(grub_size)),
                            ('parted', '-s', self.device, 'set', '1', 'bios_grub', 'on')]
            for command in commands:
                result = misc.execute_root(*command)
                if result is False:
                    if self.efi:
                        raise RuntimeError("Error creating new %s mb EFI boot partition on %s" % (grub_size, self.device))
                    else:
                        raise RuntimeError("Error creating new %s mb grub partition on %s" % (grub_size, self.device))

            up_part = '2'
            commands = [('parted', '-a', 'optimal', '-s', self.device, 'mkpart', 'primary', 'fat16', str(grub_size), str(grub_size+up_size)),
                        ('parted', '-s', self.device, 'set', up_part, 'diag', 'on'),
                        ('parted', '-s', self.device, 'name', up_part, 'DIAGS'),
                        ('mkfs.msdos', self.device + up_part)]
            for command in commands:
                result = misc.execute_root(*command)
                if result is False:
                    raise RuntimeError("Error creating new %s mb utility partition on %s" % (up_size, self.device))

            with misc.raised_privileges():
                #build the bootsector of the partition
                magic.write_up_bootsector(self.device, up_part)


            #GPT Doesn't support active partitions, so we must install directly to the disk rather than
            #partition
            grub_part = ''

            #Build RP
            command = ('parted', '-a', 'optimal', '-s', self.device, 'mkpart', self.rp_type, self.rp_type, str(up_size + grub_size), str(up_size + rp_size_mb + grub_size))
            result = misc.execute_root(*command)
            if result is False:
                raise RuntimeError("Error creating new %s mb recovery partition on %s" % (rp_size_mb, self.device))

        #Build RP filesystem
        self.status("Formatting Partitions", 2)
        if self.rp_type == 'fat32':
            command = ('mkfs.msdos', '-n', 'OS', self.device + rp_part)
        elif self.rp_type == 'ntfs':
            command = ('mkfs.ntfs', '-f', '-L', 'RECOVERY', self.device + rp_part)
        result = misc.execute_root(*command)
        if result is False:
            raise RuntimeError("Error creating %s filesystem on %s%s" % (self.rp_type, self.device, rp_part))

        #Mount RP
        mount = misc.execute_root('mount', self.device + rp_part, '/mnt')
        if mount is False:
            raise RuntimeError("Error mounting %s%s" % (self.device, rp_part))

        #Update status and start the file size thread
        self.file_size_thread.reset_write(rp_size)
        self.file_size_thread.set_scale_factor(85)
        self.file_size_thread.set_starting_value(2)
        self.file_size_thread.start()

        #Copy RP Files
        with misc.raised_privileges():
            if os.path.exists(magic.ISO_MOUNT):
                magic.black_tree("copy", re.compile(".*\.iso$"), magic.ISO_MOUNT, '/mnt')
            magic.black_tree("copy", black_pattern, magic.CDROM_MOUNT, '/mnt')

        self.file_size_thread.join()

        #If dual boot, mount the proper /boot partition first
        if self.dual:
            mount = misc.execute_root('mount', self.device + grub_part, '/mnt')
            if mount is False:
                raise RuntimeError("Error mounting %s%s" % (self.device, grub_part))

        #find uuid of drive
        with misc.raised_privileges():
            blkid = magic.fetch_output(['blkid', self.device + rp_part, "-p", "-o", "udev"]).split('\n')
            for item in blkid:
                if item.startswith('ID_FS_UUID'):
                    uuid = item.split('=')[1]
                    break

        #read in any old seed
        seed = os.path.join('/mnt', 'preseed', 'dell-recovery.seed')
        keys = magic.parse_seed(seed)

        #process the new options
        for item in self.preseed_config.split():
            if '=' in item:
                key, value = item.split('=')
                keys[key] = value

        #write out a dell-recovery.seed configuration file
        with misc.raised_privileges():
            if not os.path.isdir(os.path.join('/mnt', 'preseed')):
                os.makedirs(os.path.join('/mnt', 'preseed'))
            magic.write_seed(seed, keys)

        #Check for a grub.cfg - replace as necessary
        files = {'recovery_partition.cfg': 'grub.cfg',
                 'common.cfg' : 'common.cfg'} 
        for item in files:
            full_path = os.path.join('/mnt', 'factory', files[item])
            if os.path.exists(full_path):
                with misc.raised_privileges():
                    shutil.move(full_path, full_path + '.old')

            with misc.raised_privileges():
                magic.process_conf_file('/usr/share/dell/grub/' + item, \
                                        full_path, uuid, rp_part)
                #Allow these to be invoked from a recovery solution launched by the BCD.
                if self.dual:
                    shutil.copy(full_path, os.path.join('/tmp', files[item]))

        #If we don't have grub binaries, build them
        if self.efi:
            grub_files = [ '/mnt/factory/grubx64.efi']
        else:
            grub_files = [ '/mnt/factory/core.img',
                           '/mnt/factory/boot.img']
        for item in grub_files:
            if not os.path.exists(item):
                os.environ['TARGET_GRUBCFG'] = '/dev/null'
                os.environ['ISO_LOADER'] = '/dev/null'
                build = misc.execute_root('/usr/share/dell/grub/build-binaries.sh')
                if build is False:
                    raise RuntimeError("Error building grub binaries.")
                with misc.raised_privileges():
                    magic.white_tree("copy", re.compile('.'), '/var/lib/dell-recovery', '/mnt/factory')
                break

        #set install_in_progress flag
        with misc.raised_privileges():
            magic.fetch_output(['grub-editenv', '/mnt/factory/grubenv', 'set', 'install_in_progress=1'])

        #Install grub
        self.status("Installing GRUB", 88)
        if self.efi:
            #Secure boot?
            secure_boot = False
            if os.path.exists('/sys/firmware/efi/vars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c/data'):
                with misc.raised_privileges():
                    with open('/sys/firmware/efi/vars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c/data', 'r') as rfd:
                        output = rfd.read()
                        if output:
                            secure_boot = bool(ord(output))

            #secure boot on then we need to use that bootloader
            if secure_boot:
                grub_files = ['/cdrom/efi/boot/bootx64.efi',
                              '/cdrom/efi/boot/grubx64.efi']

            #Mount ESP
            if not os.path.exists('/mnt/efi'):
                with misc.raised_privileges():
                    os.makedirs('/mnt/efi')
            mount = misc.execute_root('mount', self.device + EFI_ESP_PARTITION, '/mnt/efi')
            if mount is False:
                raise RuntimeError("Error mounting %s%s" % (self.device, EFI_ESP_PARTITION))

            #find old entries and prep directory
            direct_path = '/mnt/efi' + '/efi/ubuntu'
            with misc.raised_privileges():
                os.makedirs(direct_path)

                #copy boot loader
                for item in grub_files:
                    if not os.path.exists(item):
                        raise RuntimeError("Error, %s doesn't exist." % item)
                    shutil.copy(item, direct_path)

                #find old entries
                bootmgr_output = magic.fetch_output(['efibootmgr', '-v']).split('\n')

            #delete old entries
            for line in bootmgr_output:
                bootnum = ''
                if line.startswith('Boot') and 'ubuntu' in line:
                    bootnum = line.split('Boot')[1].replace('*', '').split()[0]
                if bootnum:
                    bootmgr = misc.execute_root('efibootmgr', '-q', '-b', bootnum, '-B')
                    if bootmgr is False:
                        raise RuntimeError("Error removing old EFI boot manager entries")

            if secure_boot:
                target = 'shimx64.efi'
                with misc.raised_privileges():
                    os.rename(os.path.join(direct_path, 'bootx64.efi'),
                              os.path.join(direct_path, target))
            else:
                target = 'grubx64.efi'

            add = misc.execute_root('efibootmgr', '-c', '-d', self.device, '-p', EFI_ESP_PARTITION, '-l', '\\EFI\\ubuntu\\%s' % target, '-L', 'ubuntu')
            if add is False:
                raise RuntimeError("Error adding efi entry to %s%s" % (self.device, EFI_ESP_PARTITION))

            #clean up ESP mount
            misc.execute_root('umount', '/mnt/efi')
        else:
            grub = misc.execute_root('grub-bios-setup', '-d', '/mnt/factory', self.device)
            if grub is False:
                raise RuntimeError("Error installing grub to %s" % (self.device))

        #dual boot needs primary #4 unmounted
        if self.dual:
            misc.execute_root('umount', '/mnt')
            self.status("Building G2LDR", 90)
            #build g2ldr
            magic.create_g2ldr('/', '/mnt', '')
            if not os.path.isdir(os.path.join('/mnt', 'factory')):
                os.makedirs(os.path.join('/mnt', 'factory'))
            for item in files:
                shutil.copy(os.path.join('/tmp', files[item]), \
                            os.path.join('/mnt', 'factory', files[item]))

        #update bto.xml
        path = os.path.join(magic.CDROM_MOUNT, 'bto.xml')
        if os.path.exists(path):
            self.xml_obj.load_bto_xml(path)
        bto_version = self.xml_obj.fetch_node_contents('iso')
        bto_date = self.xml_obj.fetch_node_contents('date')
        with misc.raised_privileges():
            dr_version = magic.check_version('dell-recovery')
            ubi_version = magic.check_version('ubiquity')
            self.xml_obj.replace_node_contents('bootstrap', dr_version)
            self.xml_obj.replace_node_contents('ubiquity' , ubi_version)
            if os.path.exists('/var/log/syslog'):
                with open('/var/log/syslog', 'rb') as rfd:
                    self.xml_obj.replace_node_contents('syslog', rfd.read())
            if os.path.exists('/var/log/installer/debug'):
                with open('/var/log/installer/debug', 'rb') as rfd:
                    self.xml_obj.replace_node_contents('debug', rfd.read())
            if not bto_version:
                self.xml_obj.replace_node_contents('iso', '[native]')
            if not bto_date:
                with open(os.path.join(magic.CDROM_MOUNT, '.disk', 'info')) as rfd:
                    line = rfd.readline().strip()
                date = line.split()[len(line.split())-1]
                self.xml_obj.replace_node_contents('date', date)
            self.xml_obj.write_xml('/mnt/bto.xml')
        misc.execute_root('umount', '/mnt')

        for count in range(100,0,-10):
            self.status("Restarting in %d seconds." % int(count/10), count)
            time.sleep(1)


    def exit(self):
        """Function to request the builder thread to close"""
        pass

    def status(self, info, percent):
        """Stub function for passing data back up"""
        pass

    def run(self):
        """Start the RP builder thread"""
        try:
            self.build_rp()
        except Exception as err:
            self.exception = err
        self.exit()

####################
# Helper Functions #
####################
def find_boot_device():
    """Finds the device we're booted from'"""
    mounted_device = ''
    with open('/proc/mounts', 'r') as mounts:
        for line in mounts.readlines():
            if magic.ISO_MOUNT in line:
                mounted_device = line.split()[0]
                break
            if magic.CDROM_MOUNT in line:
                found = line.split()[0]
                if not 'loop' in found:
                    mounted_device = line.split()[0]
                    break
    return mounted_device

def reboot_machine(objpath):
    """Reboots the machine"""
    reboot_cmd = '/sbin/reboot'
    reboot = misc.execute_root(reboot_cmd)
    if reboot is False:
        raise RuntimeError("Reboot failed from %s" % str(objpath))

def find_item_iterator(combobox, value, column = 0):
    """Searches a combobox for a value and returns the iterator that matches"""
    model = combobox.get_model()
    iterator = model.get_iter_first()
    while iterator is not None:
        if value == model.get_value(iterator, column):
            break
        iterator = model.iter_next(iterator)
    return iterator

def find_n_set_iterator(combobox, value, column = 0):
    """Searches a combobox for a value, and sets the iterator to that value if
       it's found"""
    iterator = find_item_iterator(combobox, value, column)
    if iterator is not None:
        combobox.set_active_iter(iterator)

###########################################
# Commands Processed During Install class #
###########################################
class Install(InstallPlugin):
    """The install time dell-bootstrap ubiquity plugin"""
    
    def __init__(self, frontend, db=None, ui=None):
        self.progress = None
        self.target = None
        InstallPlugin.__init__(self, frontend, db, ui)

    def remove_ricoh_mmc(self):
        '''Removes the ricoh_mmc kernel module which is known to cause problems
           with MDIAGS'''
        lsmod = magic.fetch_output('lsmod').split('\n')
        for line in lsmod:
            if line.startswith('ricoh_mmc'):
                misc.execute('rmmod', line.split()[0])

    def enable_oem_config(self):
        '''Enables OEM config on the target'''
        oem_dir = os.path.join(self.target, 'var/lib/oem-config')
        if not os.path.exists(oem_dir):
            os.makedirs(oem_dir)
        with open(os.path.join(oem_dir, 'run'), 'w'):
            pass

    def propagate_kernel_parameters(self):
        '''Copies in kernel command line parameters that were needed during
           installation'''
        extra = magic.find_extra_kernel_options()
        new = ''
        for item in extra.split():
            if not 'debian-installer/'                in item and \
               not 'console-setup/'                   in item and \
               not 'locale='                          in item and \
               not 'BOOT_IMAGE='                      in item and \
               not 'iso-scan/'                        in item and \
               not 'ubiquity'                         in item:
                new += '%s ' % item
        extra = new.strip()

        grubf = os.path.join(self.target, 'etc/default/grub')
        if extra and os.path.exists(grubf):
            #read/write new grub
            with open(grubf, 'r') as rfd:
                default_grub = rfd.readlines()
            with open(grubf, 'w') as wfd:
                for line in default_grub:
                    if 'GRUB_CMDLINE_LINUX_DEFAULT' in line:
                        line = line.replace('GRUB_CMDLINE_LINUX_DEFAULT="', \
                                      'GRUB_CMDLINE_LINUX_DEFAULT="%s ' % extra)
                    wfd.write(line)
            from ubiquity import install_misc
            install_misc.chrex(self.target, 'update-grub')

    def remove_unwanted_drivers(self):
        '''Removes drivers that were preseeded to not used for postinstall'''
        drivers = ''

        try:
            drivers = self.progress.get(DRIVER_INSTALL_QUESTION).split(',')
        except debconf.DebconfError:
            pass

        if len(drivers) > 0:
            for driver in drivers:
                if driver:
                    with open (os.path.join(self.target, 'usr/share/jockey/modaliases/', driver), 'w') as wfd:
                        wfd.write('reset %s\n' % driver)

    def g2ldr(self):
        '''Builds a grub2 based loader to allow booting a logical partition'''
        #Mount the disk
        if os.path.exists(magic.ISO_MOUNT):
            mount = magic.ISO_MOUNT
        else:
            mount = magic.CDROM_MOUNT
            misc.execute_root('mount', '-o', 'remount,rw', magic.CDROM_MOUNT)

        magic.create_g2ldr(self.target, mount, self.target)

        #Don't re-run installation
        if os.path.exists(os.path.join(mount, 'grub', 'grub.cfg')):
            os.unlink(os.path.join(mount, 'grub', 'grub.cfg'))

    def wake_network(self):
        """Wakes the network back up"""
        bus = dbus.SystemBus()
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        try:
            backend_iface = dbus.Interface(bus.get_object(magic.DBUS_BUS_NAME, '/RecoveryMedia'), magic.DBUS_INTERFACE_NAME)
            backend_iface.force_network(True)
            backend_iface.request_exit()
        except Exception:
            pass


    def install(self, target, progress, *args, **kwargs):
        '''This is highly dependent upon being called AFTER configure_apt
        in install.  If that is ever converted into a plugin, we'll
        have some major problems!'''
        genuine = magic.check_vendor()
        if not genuine:
            raise RuntimeError("This recovery media requires Dell Hardware.")

        self.target = target
        self.progress = progress

        utility_part,  rec_part  = magic.find_partitions()

        from ubiquity import install_misc
        to_install = []
        to_remove  = []

        #Determine if we are doing OOBE
        try:
            if progress.get('oem-config/enable') == 'true':
                self.enable_oem_config()
        except debconf.DebconfError:
            pass

        #The last thing to do is set an active partition
        #This happens at the end of success command
        active = ''
        try:
            active = progress.get(ACTIVE_PARTITION_QUESTION)
        except debconf.DebconfError:
            pass
        try:
            layout = progress.get(DISK_LAYOUT_QUESTION)
        except debconf.DebconfError:
            layout = 'msdos'

        if active.isdigit():
            disk = progress.get('partman-auto/disk')
            with open('/tmp/set_bootable', 'w') as wfd:
                #If we have an MBR, 
                if layout == 'msdos':
                    #we use the active partition bit in it
                    wfd.write('sfdisk -A%s %s\n' % (active, disk))

                    #in factory process if we backed up an MBR, that would have already
                    #been restored.
                    if not os.path.exists(os.path.join(magic.CDROM_MOUNT, 'factory', 'mbr.bin')):
                        #test the md5 of the MBR to match DRMK or syslinux
                        #if they don't match, rewrite MBR
                        with misc.raised_privileges():
                            with open(disk, 'rb') as rfd:
                                disk_mbr = rfd.read(440)
                        path = '/usr/share/dell/up/mbr.bin'
                        if not os.path.exists(path):
                            path = '/usr/lib/syslinux/mbr.bin'
                        with open(path, 'rb') as rfd:
                            file_mbr = rfd.read(440)        
                        if hashlib.md5(file_mbr).hexdigest() != hashlib.md5(disk_mbr).hexdigest():
                            if not os.path.exists(path):
                                raise RuntimeError("Missing DRMK and syslinux MBR")
                            wfd.write('dd if=%s of=%s bs=440 count=1\n' % (path, disk))

                #If we have GPT, we need to go down other paths
                elif layout == 'gpt':
                    #If we're booted in EFI mode, then the OS has already set
                    #the correct Bootnum active
                    if os.path.isdir('/proc/efi') or os.path.isdir('/sys/firmware/efi'):
                        pass
                    #If we're not booted to EFI mode, but using GPT,
                    else:
                        #See https://bugs.launchpad.net/ubuntu/+source/partman-partitioning/+bug/592813
                        #for why we need to have this workaround in the first place
                        result = misc.execute_root('parted', '-s', disk, 'set', active, 'bios_grub', 'on')
                        if result is False:
                            raise RuntimeError("Error working around bug 592813.")
                        
                        wfd.write('grub-install --no-floppy %s\n' % disk)
            os.chmod('/tmp/set_bootable', 0o755)

        #if we are loop mounted, make sure the chroot knows it too
        if os.path.isdir(magic.ISO_MOUNT):
            os.makedirs(os.path.join(self.target, magic.ISO_MOUNT.lstrip('/')))
            misc.execute_root('mount', '--bind', magic.ISO_MOUNT, os.path.join(self.target, magic.ISO_MOUNT.lstrip('/')))

        #Fixup pool to only accept stuff on /cdrom or /isodevice
        # - This is reverted during SUCCESS_SCRIPT
        # - Might be in livefs already, but we always copy in in case there was an udpate
        pool_cmd = '/usr/share/dell/scripts/pool.sh'
        shutil.copy(pool_cmd, os.path.join(self.target, 'tmp', os.path.basename(pool_cmd)))
        install_misc.chrex(self.target, os.path.join('/tmp', os.path.basename(pool_cmd)))

        #Stuff that is installed on all configs without fish scripts
        to_install += magic.mark_unconditional_debs()

        #Query Dual boot or not
        try:
            dual = misc.create_bool(progress.get(DUAL_BOOT_QUESTION))
        except debconf.DebconfError:
            dual = False

        if dual:
            #we don't want EULA or dell-recovery in dual mode
            for package in ['dell-eula', 'dell-recovery']:
                try:
                    to_install.remove(package)
                    to_remove.append(package)
                except ValueError:
                    continue
            #build grub2 loader for logical partitions when necessary
            try:
                layout = progress.get(DUAL_BOOT_LAYOUT_QUESTION)
                if layout == 'logical':
                    self.g2ldr()
            except debconf.DebconfError:
                raise RuntimeError("Error determining dual boot layout.")

        #install dell-recovery in non dual mode only if there is an RP
        elif rec_part:
            to_install.append('dell-recovery')
            to_install.append('dell-eula')

            #block os-prober in grub-installer
            os.rename('/usr/bin/os-prober', '/usr/bin/os-prober.real')
            #don't allow OS prober to probe other drives in single OS install
            with open(os.path.join(self.target, 'etc/default/grub'), 'r') as rfd:
                default_grub = rfd.readlines()
            with open(os.path.join(self.target, 'etc/default/grub'), 'w') as wfd:
                found = False
                for line in default_grub:
                    if line.startswith("GRUB_DISABLE_OS_PROBER="):
                        line = "GRUB_DISABLE_OS_PROBER=true\n"
                        found = True
                    wfd.write(line)
                if not found:
                    wfd.write("GRUB_DISABLE_OS_PROBER=true\n")

        #for tos
        try:
            destination = progress.get('dell-recovery/destination')
        except debconf.DebconfError:
            destination = ''
        fname = os.path.join(self.target, 'etc', 'default', 'dell-eula')
        if destination and not os.path.exists(fname):
            with open(fname, 'w') as wfd:
                wfd.write('WARRANTY=%s\n' % destination)

        to_install += magic.mark_upgrades()

        self.remove_unwanted_drivers()
                    
        self.remove_ricoh_mmc()

        self.propagate_kernel_parameters()

        self.wake_network()

        install_misc.record_installed(to_install)
        install_misc.record_removed(to_remove)

        return InstallPlugin.install(self, target, progress, *args, **kwargs)

