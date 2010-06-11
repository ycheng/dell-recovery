#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# «dell-bootstrap» - Ubiquity plugin for Dell Factory Process
#
# Copyright (C) 2010, Dell Inc.
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
##################################################################################

from ubiquity.plugin import *
from ubiquity import misc
from threading import Thread, Event
import debconf
import Dell.recovery_common as magic
import subprocess
import os
import re
import shutil
import dbus
from dbus.mainloop.glib import DBusGMainLoop
DBusGMainLoop(set_as_default=True)
import syslog
import glob

NAME = 'dell-bootstrap'
BEFORE = 'language'
WEIGHT = 12
OEM = False

EFI_PART =     '1'
STANDARD_UP_PARTITION  =     '1'
STANDARD_RP_PARTITION  =     '2'
CDROM_MOUNT = '/cdrom'

TYPE_NTFS = '07'
TYPE_NTFS_RE = '27'
TYPE_VFAT = '0b'
TYPE_VFAT_LBA = '0c'

#######################
# Noninteractive Page #
#######################
class PageNoninteractive(PluginUI):
    def __init__(self, controller, *args, **kwargs):
        self.controller = controller
    
    def get_type(self):
        '''For the noninteractive frontend, get_type always returns an empty str
            This is because the noninteractive frontend always runs in "factory"
            mode, which expects such a str""'''
        return ""

    def set_type(self,type):
        pass

    def set_dual(self):
        pass

    def show_info_dialog(self):
        pass

    def show_reboot_dialog(self):
        pass

    def show_dual_dialog(self):
        pass

    def show_exception_dialog(self,e):
        pass

    def get_selected_device(self):
        pass

    def populate_devices(self, devices):
        pass

############
# GTK Page #
############
class PageGtk(PluginUI):
    #OK, so we're not "really" a language page
    #We are just cheating a little bit to make sure our widgets are translated
    plugin_is_language = True

    def __init__(self, controller, *args, **kwargs):
        self.plugin_widgets = None

        oem = 'UBIQUITY_OEM_USER_CONFIG' in os.environ

        self.genuine = magic.check_vendor()

        if not oem:
            import gtk
            builder = gtk.Builder()
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
            self.reboot_dialog = builder.get_object('reboot_dialog')
            self.reboot_dialog.set_title('Dell Recovery')
            self.dual_dialog = builder.get_object('dual_dialog')
            self.dual_dialog.set_title('Dell Recovery')
            self.info_box = builder.get_object('info_box')
            self.info_spinner = gtk.Spinner()
            builder.get_object('info_spinner_box').add(self.info_spinner)
            self.err_dialog = builder.get_object('err_dialog')

            #For debug purposes
            icon = builder.get_object('dell_image')
            with misc.raised_privileges():
                version = magic.check_version()
            with open('/proc/mounts','r') as mounts:
                for line in mounts.readlines():
                    if '/cdrom' in line:
                        icon.set_tooltip_markup("<b>Version</b>: %s\n<b>Mounted from</b>: %s" % (version,line.split()[0]))
                        break
            if 'UBIQUITY_DEBUG' in os.environ and 'UBIQUITY_ONLY' in os.environ and \
                os.path.exists('/usr/bin/gnome-terminal'):
                subprocess.Popen(['gnome-terminal'])

            if not self.genuine:
                self.interactive_recovery_box.hide()
                self.automated_recovery_box.hide()
                self.automated_recovery.set_sensitive(False)
                self.interactive_recovery.set_sensitive(False)
                builder.get_object('genuine_box').show()

    def plugin_get_current_page(self):
        #are we real?
        if not self.genuine:
            self.controller.allow_go_forward(False)

        #The widget has been added into the top level by now, so we can change top level stuff
        import gtk
        window = self.plugin_widgets.get_parent_window()
        window.set_functions(gtk.gdk.FUNC_RESIZE | gtk.gdk.FUNC_MOVE)
        window.set_title('Dell Recovery')
        self.controller._wizard.step_label.set_sensitive(False)

        return self.plugin_widgets

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
            device = model.get_value(iterator,0)
            size = model.get_value(iterator,1)
        return (device, size)

    def set_type(self,type):
        """Sets the type of recovery to do in GUI"""
        if not self.genuine:
            return
        if type == "automatic":
            self.automated_recovery.set_active(True)
        elif type == "interactive":
            self.interactive_recovery.set_active(True)
        else:
            self.hidden_radio.set_active(True)
            if type != "factory":
                self.controller.allow_go_forward(False)
            if type == "hdd":
                self.hdd_recovery_box.show()
                self.interactive_recovery_box.hide()
                self.automated_recovery_box.hide()
                self.interactive_recovery.set_sensitive(False)
                self.automated_recovery.set_sensitive(False)

    def set_dual(self):
        """Marks the UI as dual boot mode"""
        self.interactive_recovery_box.hide()
        self.interactive_recovery.set_sensitive(False)

    def toggle_type(self, widget):
        """Allows the user to go forward after they've made a selection'"""
        self.controller.allow_go_forward(True)
        self.automated_combobox.set_sensitive(self.automated_recovery.get_active())

    def show_info_dialog(self):
        self.controller._wizard.step_label.set_markup('')
        self.controller._wizard.quit.set_label(self.controller.get_string('ubiquity/imported/cancel'))
        self.controller.allow_go_forward(False)
        self.automated_recovery_box.hide()
        self.interactive_recovery_box.hide()
        self.info_box.show_all()
        self.info_spinner.start()

    def show_reboot_dialog(self):
        self.controller.toggle_top_level()
        self.info_spinner.stop()
        self.reboot_dialog.run()

    def show_dual_dialog(self):
        self.controller.toggle_top_level()
        self.info_spinner.stop()
        self.dual_dialog.run()

    def show_exception_dialog(self, e):
        self.info_spinner.stop()
        self.err_dialog.format_secondary_text(str(e))
        self.err_dialog.run()
        self.err_dialog.hide()

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
       
################
# Debconf Page #
################
class Page(Plugin):
    def __init__(self, frontend, db=None, ui=None):
        self.device = None
        self.device_size = 0
        self.efi = False
        Plugin.__init__(self, frontend, db, ui)

    def install_grub(self):
        """Installs grub on the recovery partition"""

        #If we are GPT, we will have been started a different way.
        #Don't actually install grub onto the RP in that scenario
        if self.disk_layout == 'gpt':
            return

        #Mount R/W
        cd_mount   = misc.execute_root('mount', '-o', 'remount,rw', CDROM_MOUNT)
        if cd_mount is False:
            raise RuntimeError, ("CD Mount failed")

        #Check for a grub.cfg to start - make as necessary
        if not os.path.exists(os.path.join(CDROM_MOUNT, 'grub', 'grub.cfg')):
            with misc.raised_privileges():
                magic.process_conf_file('/usr/share/dell/grub/recovery_partition.cfg', \
                                        os.path.join(CDROM_MOUNT, 'grub', 'grub.cfg'), \
                                        STANDARD_RP_PARTITION, self.dual)

        #Do the actual grub installation
        bind_mount = misc.execute_root('mount', '-o', 'bind', CDROM_MOUNT, '/boot')
        if bind_mount is False:
            raise RuntimeError, ("Bind Mount failed")
        grub_inst  = misc.execute_root('grub-install', '--force', self.device + STANDARD_RP_PARTITION)
        if grub_inst is False:
            raise RuntimeError, ("Grub install failed")
        unbind_mount = misc.execute_root('umount', '/boot')
        if unbind_mount is False:
            raise RuntimeError, ("Unmount /boot failed")
        uncd_mount   = misc.execute_root('mount', '-o', 'remount,ro', CDROM_MOUNT)
        if uncd_mount is False:
            raise RuntimeError, ("Uncd mount failed")

    def disable_swap(self):
        """Disables any swap partitions in use"""
        bus = dbus.SystemBus()

        udisk_obj = bus.get_object('org.freedesktop.UDisks', '/org/freedesktop/UDisks')
        ud = dbus.Interface(udisk_obj, 'org.freedesktop.UDisks')
        devices = ud.EnumerateDevices()
        for device in devices:
            dev_obj = bus.get_object('org.freedesktop.UDisks', device)
            dev = dbus.Interface(dev_obj, 'org.freedesktop.DBus.Properties')

            #Find mounted swap
            if dev.Get('org.freedesktop.UDisks.Device','IdType') == 'swap':
                device = dev.Get('org.freedesktop.Udisks.Device','DeviceFile')
                misc.execute_root('swapoff', device)
                if misc is False:
                    raise RuntimeError, ("Error removing swap for device %s" % device)

    def remove_extra_partitions(self):
        """Removes partitions we are installing on for the process to start"""
        if self.disk_layout == 'msdos':
            #First set the new partition active
            active = misc.execute_root('sfdisk', '-A%s' % self.fail_partition, self.device)
            if active is False:
                self.debug("Failed to set partition %s active on %s" % (self.fail_partition, self.device))
        #check for extended partitions
        with misc.raised_privileges():
            total_partitions = len(fetch_output(['partx', self.device]).split('\n'))-1
        #remove extras
        for number in (self.os_part, self.swap_part):
            if number.isdigit():
                remove = misc.execute_root('parted', '-s', self.device, 'rm', number)
                if remove is False:
                    self.debug("Error removing partition number: %s on %s (this may be normal)'" % (number, self.device))
                refresh = misc.execute_root('partx', '-d', '--nr', number, self.device)
                if refresh is False:
                    self.debug("Error updating partition %s for kernel device %s (this may be normal)'" % (number, self.device))
        #if there were extended, cleanup
        if total_partitions > 4:
            refresh = misc.execute_root('partx', '-d', '--nr', '5-' + str(total_partitions), self.device)
            if refresh is False:
                self.debug("Error removing extended partitions 5-%s for kernel device %s (this may be normal)'" % (total_partitions, self.device))

    def explode_sdr(self):
        '''Explodes all content explicitly defined in an SDR
           If no SDR was found, don't change drive at all
        '''
        sdr_file = glob.glob(CDROM_MOUNT + "/*SDR")
        if not sdr_file:
            return

        #RP Needs to be writable no matter what
        cd_mount = misc.execute_root('mount', '-o', 'remount,rw', CDROM_MOUNT)
        if cd_mount is False:
            raise RuntimeError, ("Error remounting RP to explode SDR.")

        #Parse SDR
        srv_list = []
        with open(sdr_file[0], 'r') as fd:
            sdr_lines = fd.readlines()
        for line in sdr_lines:
            if line.startswith('SI'):
                columns = line.split()
                if len(columns) > 2:
                    #always assume lower case (in case file system is case sensitive)
                    srv_list.append(columns[2].lower())

        #Explode SRVs that match SDR
        for srv in srv_list:
            file = os.path.join(os.path.join(CDROM_MOUNT, 'srv','%s' % srv))
            if os.path.exists('%s.tgz' % file):
                import tarfile
                archive = tarfile.open('%s.tgz' % file)
            elif os.path.exists('%s.zip' % file):
                import zipfile
                archive = zipfile.ZipFile('%s.zip' % file)
            else:
                self.debug("Skipping SRV %s due to no file on filesystem." % srv)
                continue
            with misc.raised_privileges():
                self.debug("Extracting SRV %s onto filesystem" % srv)
                archive.extractall(path=CDROM_MOUNT)
            archive.close()

    def explode_utility_partition(self):
        '''Explodes all content onto the utility partition
        '''
        #For now on GPT we don't include an UP since we can't boot
        # 16 bit code as necessary for the UP to be working
        if self.disk_layout == 'gpt':
            return

        mount = False
        #If we have DRMK available, explode that first
        if os.path.exists(os.path.join(CDROM_MOUNT, 'misc', 'drmk.zip')):
            mount = misc.execute_root('mount', self.device + STANDARD_UP_PARTITION, '/boot')
            if mount is False:
                raise RuntimeError, ("Error mounting utility partition pre-explosion.")
            import zipfile
            archive = zipfile.ZipFile(os.path.join(CDROM_MOUNT, 'misc', 'drmk.zip'))
            with misc.raised_privileges():
                archive.extractall(path='/boot')
            archive.close()

        #Now check for additional UP content to explode
        for file in magic.up_filenames:
            if os.path.exists(os.path.join(CDROM_MOUNT, file)):
                #Restore full UP backup (dd)
                if '.bin' in file or '.gz' in file:
                    with misc.raised_privileges():
                        with open(self.device + STANDARD_UP_PARTITION, 'w') as partition:
                            p1 = subprocess.Popen(['gzip','-dc',os.path.join(CDROM_MOUNT, file)], stdout=subprocess.PIPE)
                            partition.write(p1.communicate()[0])
                #Restore UP (zip/tgz)
                elif '.zip' in file or '.tgz' in file:
                    if not mount:
                        mount = misc.execute_root('mount', self.device + STANDARD_UP_PARTITION, '/boot')
                        if mount is False:
                            raise RuntimeError, ("Error mounting utility partition pre-explosion.")
                    if '.zip' in file:
                        import zipfile
                        archive = zipfile.ZipFile(os.path.join(CDROM_MOUNT, file))
                    elif '.tgz' in file:
                        import tarfile
                        archive = tarfile.open(os.path.join(CDROM_MOUNT, file))
                    with misc.raised_privileges():
                        archive.extractall(path='/boot')
                    archive.close()
        #If we didn't include an autoexec.bat (as is the case from normal DellDiags releases)
        #Then make the files we need to be automatically bootable
        if not os.path.exists('/boot/autoexec.bat') and os.path.exists('/boot/autoexec.up'):
            with misc.raised_privileges():
                shutil.copy('/boot/autoexec.up','/boot/autoexec.bat')
        if not os.path.exists('/boot/config.sys') and os.path.exists('/boot/config.up'):
            with misc.raised_privileges():
                shutil.copy('/boot/config.up','/boot/config.sys')
        if mount:
            umount = misc.execute_root('umount', '/boot')
            if umount is False:
                raise RuntimeError, ("Error unmounting utility partition post-explosion.")


    def boot_rp(self):
        """reboots the system"""
        subprocess.call(['/etc/init.d/casper','stop'])

        #Set up a listen for udisks to let us know a usb device has left
        bus = dbus.SystemBus()
        bus.add_signal_receiver(reboot_machine, 'DeviceRemoved', 'org.freedesktop.UDisks')

        if self.dual:
            self.ui.show_dual_dialog()
        else:
            self.ui.show_reboot_dialog()

        reboot_machine(None)

    def unset_drive_preseeds(self):
        """Unsets any preseeds that are related to setting a drive"""
        for key in [ 'partman-auto/init_automatically_partition',
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
                     'dell-recovery/fail_partition',
                     'ubiquity/reboot' ]:
            self.db.fset(key, 'seen', 'false')
            self.db.set(key, '')
        self.db.set('ubiquity/partman-skip-unmount', 'false')
        self.db.set('partman/filter_mounted', 'true')

    def fixup_recovery_devices(self):
        """Discovers the first hard disk to install to"""
        bus = dbus.SystemBus()
        disks = []

        udisk_obj = bus.get_object('org.freedesktop.UDisks', '/org/freedesktop/UDisks')
        ud = dbus.Interface(udisk_obj, 'org.freedesktop.UDisks')
        devices = ud.EnumerateDevices()
        for device in devices:
            dev_obj = bus.get_object('org.freedesktop.UDisks', device)
            dev = dbus.Interface(dev_obj, 'org.freedesktop.DBus.Properties')

            #Skip USB, Removable Disks, Partitions, External, Loopback, Readonly
            if dev.Get('org.freedesktop.UDisks.Device','DriveConnectionInterface') == 'usb' or \
               dev.Get('org.freedesktop.UDisks.Device','DeviceIsRemovable') == 1 or \
               dev.Get('org.freedesktop.UDisks.Device','DeviceIsPartition') == 1 or \
               dev.Get('org.freedesktop.UDisks.Device','DeviceIsSystemInternal') == 0 or \
               dev.Get('org.freedesktop.UDisks.Device','DeviceIsLinuxLoop') == 1 or \
               dev.Get('org.freedesktop.UDisks.Device','DeviceIsReadOnly') == 1 :
                continue

            #if we made it this far, add it
            devicefile = dev.Get('org.freedesktop.Udisks.Device','DeviceFile')
            devicemodel = dev.Get('org.freedesktop.Udisks.Device','DriveModel')
            devicevendor = dev.Get('org.freedesktop.Udisks.Device','DriveVendor')
            devicesize = dev.Get('org.freedesktop.Udisks.Device','DeviceSize')
            devicesize_gb = "%i" % (devicesize / 1000000000)
            disks.append([devicefile, devicesize, "%s GB %s %s (%s)" % (devicesize_gb, devicevendor, devicemodel, devicefile)])

        #If multiple candidates were found, record in the logs
        if len(disks) == 0:
            raise RuntimeError, ("Unable to find and candidate hard disks to install to.")
        if len(disks) > 1:
            disks.sort()
            self.debug("Multiple disk candidates were found: %s" % disks)

        #Always choose the first candidate to start
        self.device = disks[0][0]
        self.debug("Fixed up device we are operating on is %s" % self.device)

        #populate UI
        self.ui.populate_devices(disks)

    def fixup_factory_devices(self):
        #Ignore any EDD settings - we want to just plop on the same drive with
        #the right FS label (which will be valid right now)
        #Don't you dare put a USB stick in the system with that label right now!
        rp = magic.find_factory_rp_stats()
        if not rp:
            raise RuntimeError, ("Unable to find factory recovery partition (was going to use %s)" % self.device)

        self.device = rp["slave"]
        if os.path.exists(self.pool_cmd):
            early = '&& %s' % self.pool_cmd
        else:
            early = ''
        self.db.set('oem-config/early_command', 'mount -o ro %s %s %s' % (rp["device"], CDROM_MOUNT, early))
        self.db.set('partman-auto/disk', self.device)
        self.db.set('grub-installer/bootdev', self.device + self.os_part)
        if rp["fs"] == "ntfs":
            self.rp_filesystem = TYPE_NTFS_RE
        elif rp["fs"] == "vfat":
            self.rp_filesystem = TYPE_VFAT_LBA
        else:
            raise RuntimeError, ("Unknown filesystem on recovery partition: %s" % rp["fs"])
        self.debug("Detected device we are operating on is %s" % self.device)
        self.debug("Detected a %s filesystem on the %s recovery partition" % (rp["fs"], rp["label"]))

    def prepare(self, unfiltered=False):
        type = None
        try:
            type = self.db.get('dell-recovery/recovery_type')
            #These require interactivity - so don't fly by even if --automatic
            if type != 'factory':
                self.db.set('dell-recovery/recovery_type','')
                self.db.fset('dell-recovery/recovery_type', 'seen', 'false')
            else:
                self.db.fset('dell-recovery/recovery_type', 'seen', 'true')
        except debconf.DebconfError, e:
            self.debug(str(e))
            #TODO superm1 : 2-18-10
            # if the template doesn't exist, this might be a casper bug
            # where the template wasn't registered at package install
            # work around it by assuming no template == factory
            type = 'factory'
            self.db.register('debian-installer/dummy', 'dell-recovery/recovery_type')
            self.db.set('dell-recovery/recovery_type', type)
            self.db.fset('dell-recovery/recovery_type', 'seen', 'true')
        self.ui.set_type(type)

        #In case we preseeded the partitions we need installed to
        try:
            self.os_part = self.db.get('dell-recovery/os_partition')
        except debconf.DebconfError, e:
            self.debug(str(e))
            self.os_part = '3'

        try:
            self.swap_part = self.db.get('dell-recovery/swap_partition')
        except debconf.DebconfError, e:
            self.debug(str(e))
            self.swap_part = '4'

        #Support special cases where the recovery partition isn't a linux partition
        try:
            self.rp_filesystem = self.db.get('dell-recovery/recovery_partition_filesystem')
        except debconf.DebconfError, e:
            self.debug(str(e))
            self.rp_filesystem = TYPE_VFAT_LBA

        #For rebuilding the pool in oem-config and during install
        try:
            self.pool_cmd = self.db.get('dell-recovery/pool_command')
        except debconf.DebconfError, e:
            self.debug(str(e))
            self.pool_cmd = '/cdrom/scripts/pool.sh'
            self.preseed('dell-recovery/pool_command', self.pool_cmd)

        #When running a dual boot install, this is useful
        try:
            self.dual = self.db.get('dell-recovery/dual_boot_seed')
            if self.dual:
                self.ui.set_dual()
        except debconf.DebconfError, e:
            self.debug(str(e))
            self.dual = ''

        #If we are successful for an MBR install, this is where we boot to
        try:
            pass_partition = self.db.get('dell-recovery/active_partition')
        except debconf.DebconfError, e:
            self.debug(str(e))
            self.preseed('dell-recovery/active_partition', self.os_part)

        #In case an MBR install fails, this is where we boot to
        try:
            self.fail_partition = self.db.get('dell-recovery/fail_partition')
        except debconf.DebconfError, e:
            self.debug(str(e))
            self.fail_partition = STANDARD_RP_PARTITION
            self.preseed('dell-recovery/fail_partition', self.fail_partition)

        #The requested disk layout type
        #This is generally for debug purposes, but will be overridden if we
        #determine that we are actually going to be doing an EFI install
        try:
            self.disk_layout = self.db.get('dell-recovery/disk_layout')
        except debconf.DebconfError, e:
            self.debug(str(e))
            self.disk_layout = 'msdos'
            self.preseed('dell-recovery/disk_layout', self.disk_layout)

        #If we detect that we are booted into uEFI mode, then we only want
        #to do a GPT install.  Actually a MBR install would work in most
        #cases, but we can't make assumptions about 16-bit anymore (and
        #preparing a UP because of it)
        if os.path.isdir('/proc/efi'):
            self.efi = True
            self.disk_layout = 'gpt'
            #Force efibootmgr to set the EFI system partition active when done
            self.preseed('dell-recovery/active_partition', EFI_PART)

        #set the language in the UI
        try:
            language = self.db.get('debian-installer/language')
        except debconf.DebconfError, e:
            language = ''
        if not language:
            with open('/proc/cmdline', 'r') as fd:
                for item in fd.readline().split():
                    if 'locale=' in item:
                        items = item.split('=')
                        if len(items) > 1:
                            language = items[1]
                            break
        if language:
            self.preseed('debian-installer/locale', language)
            self.ui.controller.translate(language)

        #Clarify which device we're operating on initially in the UI
        try:
            if type != 'factory' and type != 'hdd':
                self.fixup_recovery_devices()
            else:
                self.fixup_factory_devices()
        except Exception, e:
            self.handle_exception(e)
            self.cancel_handler()

        return (['/usr/share/ubiquity/dell-bootstrap'], ['dell-recovery/recovery_type'])

    def ok_handler(self):
        """Copy answers from debconf questions"""
        type = self.ui.get_type()
        self.preseed('dell-recovery/recovery_type', type)
        (device, size) = self.ui.get_selected_device()
        if device:
            self.device = device
        if size:
            self.device_size = size
        return Plugin.ok_handler(self)

    def cleanup(self):
        #All this processing happens in cleanup because that ensures it runs for all scenarios
        type = self.db.get('dell-recovery/recovery_type')

        try:
            # User recovery - need to copy RP
            if type == "automatic":
                self.ui.show_info_dialog()
                self.disable_swap()
                with misc.raised_privileges():
                    mem = fetch_output('/usr/lib/base-installer/dmi-available-memory').strip('\n')
                self.rp_builder = rp_builder(self.device, self.device_size, self.rp_filesystem, mem, self.dual, self.disk_layout, self.efi)
                self.rp_builder.exit = self.exit_ui_loops
                self.rp_builder.start()
                self.enter_ui_loop()
                self.rp_builder.join()
                if self.rp_builder.exception:
                    self.handle_exception(self.rp_builder.exception)
                self.boot_rp()

            # User recovery - resizing drives
            elif type == "interactive":
                self.unset_drive_preseeds()

            # Factory install, and booting from RP
            else:
                self.disable_swap()
                self.remove_extra_partitions()
                self.explode_utility_partition()
                self.explode_sdr()
                if self.rp_filesystem == TYPE_VFAT or \
                   self.rp_filesystem == TYPE_VFAT_LBA:
                    self.install_grub()
        except Exception, e:
            #For interactive types of installs show an error then reboot
            #Otherwise, just reboot the system
            if type == "automatic" or type == "interactive" or \
               ('UBIQUITY_DEBUG' in os.environ and 'UBIQUITY_ONLY' in os.environ):
                self.handle_exception(e)
            self.cancel_handler()

        #translate languages
        self.ui.controller.translate(just_me=False, not_me=True, reget=True)
        Plugin.cleanup(self)

    def cancel_handler(self):
        """Called when we don't want to perform recovery'"""
        misc.execute_root('reboot','-n')

    def handle_exception(self, e):
        self.debug(str(e))
        self.ui.show_exception_dialog(e)

############################
# RP Builder Worker Thread #
############################
class rp_builder(Thread):
    def __init__(self, device, size, rp_type, mem, dual, disk_layout, efi):
        self.device = device
        self.device_size = size
        self.rp_type = rp_type
        self.mem = mem
        self.dual = dual
        self.disk_layout = disk_layout
        self.efi = efi
        self.exception = None
        Thread.__init__(self)

    def build_rp(self, cushion=300):
        """Copies content to the recovery partition using a parted wrapper.

           This might be better implemented in python-parted or parted_server/partman,
           but those would require extra dependencies, and are generally more complex
           than necessary for what needs to be accomplished here."""

        white_pattern = re.compile('.')

        #Things we know ahead of time will cause us to error out
        if self.disk_layout == 'gpt':
            raise RuntimeError, ("GPT disk layout is not yet supported in dell-recovery.")

            if self.dual:
                raise RuntimeError, ("Dual boot is not yet supported when configuring the disk as GPT.")
        elif self.disk_layout == 'msdos':
            pass
        else:
            raise RuntimeError, ("Unsupported disk layout: %s" % self.disk_layout)

        #Check if we are booted from same device as target
        with open('/proc/mounts','r') as mounts:
            for line in mounts.readlines():
                if '/cdrom' in line:
                    mounted_device = line.split()[0]
                    break
        if self.device in mounted_device:
            raise RuntimeError, ("Attempting to install to the same device as booted from.\n\
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
            raise RuntimeError, ("Unsupported recovery partition filesystem: %s" % self.rp_type)

        #Default partition numbers
        self.up_part   = STANDARD_UP_PARTITION
        self.rp_part   = STANDARD_RP_PARTITION
        self.grub_part = STANDARD_RP_PARTITION

        #Calculate RP size
        rp_size = magic.white_tree("size", white_pattern, CDROM_MOUNT)
        #in mbytes
        rp_size = (rp_size / 1048576) + cushion

        # Build new partition table
        command = ('parted', '-s', self.device, 'mklabel', self.disk_layout)
        result = misc.execute_root(*command)
        if result is False:
            raise RuntimeError, ("Error creating new partition table %s on %s" % (self.disk_layout, self.device))

        if self.disk_layout == 'msdos':
            #Create a DRMK MBR
            with open('/usr/share/dell/up/mbr.bin','rb') as mbr:
                with misc.raised_privileges():
                    with open(self.device,'wb') as out:
                        out.write(mbr.read(440))

            #Utility partition files (tgz/zip)#
            up_size = 32

            #Utility partition image (dd)#
            for file in magic.up_filenames:
                if 'img' in file and os.path.exists(os.path.join(CDROM_MOUNT, file)):
                    #in bytes
                    up_size = int(fetch_output(['gzip', '-lq', os.path.join(CDROM_MOUNT, file)]).split()[1])
                    #in mbytes
                    up_size = up_size / 1048576

            #Build UP
            command = ('parted', '-a', 'minimal', '-s', self.device, 'mkpartfs', 'primary', 'fat16', '0', str(up_size))
            result = misc.execute_root(*command)
            if result is False:
                raise RuntimeError, ("Error creating new %s mb utility partition on %s" % (up_size, self.device))

            #parted marks it as w95 fat16 (LBA).  It *needs* to be type 'de'
            data = 't\nde\n\nw\n'
            with misc.raised_privileges():
                fetch_output(['fdisk', self.device], data)

            #build the bootsector of the partition
            with open('/usr/share/dell/up/up.bs','rb') as rfd:
                with misc.raised_privileges():
                    with open(self.device + self.up_part,'wb') as wfd:
                        wfd.write(rfd.read(11))  # writes the jump to instruction and oem name
                        rfd.seek(43)
                        wfd.seek(43)
                        wfd.write(rfd.read(469)) # write the label, FS type, bootstrap code and signature

            #Build RP
            command = ('parted', '-a', 'minimal', '-s', self.device, 'mkpart', 'primary', self.rp_type, str(up_size), str(up_size + rp_size))
            result = misc.execute_root(*command)
            if result is False:
                raise RuntimeError, ("Error creating new %s mb recovery partition on %s" % (rp_size, self.device))

            #Set RP active (bootable)
            command = ('parted', '-s', self.device, 'set', self.rp_part, 'boot', 'on')
            result = misc.execute_root(*command)
            if result is False:
                raise RuntimeError, ("Error setting recovery partition active %s" % (self.device))

            #Dual boot creates more partitions
            if self.dual:
                my_os_part = 5120 #mb
                other_os_part_end = (int(self.device_size) / 1048576) - my_os_part

                commands = [('parted', '-a', 'minimal', '-s', self.device, 'mkpart', 'primary', 'ntfs', str(up_size + rp_size), str(other_os_part_end)),
                            ('parted', '-a', 'minimal', '-s', self.device, 'mkpart', 'primary', 'fat32', str(other_os_part_end), str(other_os_part_end + my_os_part)),
                            ('mkfs.ntfs' , '-f', '-L', 'OS', self.device + '3'),
                            ('mkfs.msdos', '-n', 'ubuntu'  , self.device + '4')]
                for command in commands:
                    fs = misc.execute_root(*command)
                    if fs is False:
                        raise RuntimeError, ("Error building dual boot partitions")

                #Grub needs to be on the 4th partition to kick off the ubuntu install
                self.grub_part = '4'

        #GPT Layout
        elif self.disk_layout == 'gpt':
            #no UP in gpt
            self.up_part = ''
            #GPT Doesn't support active partitions, so we must install directly to the disk rather than
            #partition
            self.grub_part = ''
            self.rp_part = '1'

        #Build RP filesystem
        if self.rp_type == 'fat32':
            command = ('mkfs.msdos', '-n', 'install', self.device + self.rp_part)
        elif self.rp_type == 'ntfs':
            command = ('mkfs.ntfs', '-f', '-L', 'RECOVERY', self.device + self.rp_part)
        fs = misc.execute_root(*command)
        if fs is False:
            raise RuntimeError, ("Error creating %s filesystem on %s%s" % (self.rp_type, self.device, self.rp_part))

        #Mount RP
        mount = misc.execute_root('mount', self.device + self.rp_part, '/boot')
        if mount is False:
            raise RuntimeError, ("Error mounting %s%s" % (self.device, self.rp_part))

        #Copy RP Files
        with misc.raised_privileges():
            magic.white_tree("copy", white_pattern, CDROM_MOUNT, '/boot')



        #If dual boot, mount the proper /boot partition first
        if self.dual:
            mount = misc.execute_root('mount', self.device + self.grub_part, '/boot')
            if mount is False:
                raise RuntimeError, ("Error mounting %s%s" % (self.device, self.grub_part))

        #Check for a grub.cfg - replace as necessary
        if os.path.exists(os.path.join('/boot', 'grub', 'grub.cfg')):
            with misc.raised_privileges():
                os.remove(os.path.join('/boot', 'grub', 'grub.cfg'))
        with misc.raised_privileges():
            magic.process_conf_file('/usr/share/dell/grub/recovery_partition.cfg', \
                                    os.path.join('/boot', 'grub', 'grub.cfg'),     \
                                    self.rp_part, self.dual)

        #Install grub
        if self.efi:
            raise RuntimeError, ("EFI install of GRUB is not yet supported.  You may be able to manually do it though.")
        else:
            grub = misc.execute_root('grub-install', '--force', self.device + self.grub_part)
            if grub is False:
                raise RuntimeError, ("Error installing grub to %s%s" % (self.device, STANDARD_RP_PARTITION))

        #dual boot needs primary #4 unmounted
        if self.dual:
            misc.execute_root('umount', '/boot')

        #Build new UUID
        if int(self.mem) >= 1000000:
            with misc.raised_privileges():
                magic.create_new_uuid(os.path.join(CDROM_MOUNT, 'casper'),
                        os.path.join(CDROM_MOUNT, '.disk'),
                        os.path.join('/boot', 'casper'),
                        os.path.join('/boot', '.disk'))
        else:
            #The new UUID just fixes the installed-twice-on-same-system scenario
            #most users won't need that anyway so it's just nice to have
            syslog.syslog("Skipping casper UUID build due to low memory")

        misc.execute_root('umount', '/boot')

    def exit(self):
        pass

    def run(self):
        try:
            self.build_rp()
        except Exception, e:
            self.exception = e
        self.exit()

####################
# Helper Functions #
####################
def fetch_output(cmd, data=None):
    '''Helper function to just read the output from a command'''
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE)
    (out,err) = proc.communicate(data)
    if proc.returncode is None:
        proc.wait()
    if proc.returncode != 0:
        error = "Command %s failed with stdout/stderr: %s\n%s" % (cmd, out, err)
        syslog.syslog(error)
        raise RuntimeError, (error)
    return out

def reboot_machine(objpath):
    reboot_cmd = '/sbin/reboot'
    reboot = misc.execute_root(reboot_cmd,'-n')
    if reboot is False:
        raise RuntimeError, ("Reboot failed")

###########################################
# Commands Processed During Install class #
###########################################
class Install(InstallPlugin):
    def find_unconditional_debs(self):
        '''Finds any debs from debs/main that we want unconditionally installed
           (but ONLY the latest version on the media)'''
        import apt_inst
        import apt_pkg

        def parse(file):
            """ read a deb """
            control = apt_inst.debExtractControl(open(file))
            sections = apt_pkg.ParseSection(control)
            return sections["Package"]

        #process debs/main
        to_install = []
        if os.path.isdir(CDROM_MOUNT + '/debs/main'):
            for file in os.listdir(CDROM_MOUNT + '/debs/main'):
                if '.deb' in file:
                    to_install.append(parse(os.path.join(CDROM_MOUNT + '/debs/main',file)))

        #These aren't in all images, but desirable if available
        to_install.append('dkms')
        to_install.append('adobe-flashplugin')

        return to_install

    def enable_oem_config(self, target):
        '''Enables OEM config on the target'''
        oem_dir = os.path.join(target,'var/lib/oem-config')
        if not os.path.exists(oem_dir):
            os.makedirs(oem_dir)
        with open(os.path.join(oem_dir,'run'),'w'):
            pass

    def remove_ricoh_mmc(self):
        '''Removes the ricoh_mmc kernel module which is known to cause problems
           with MDIAGS'''
        lsmod = fetch_output('lsmod').split('\n')
        for line in lsmod:
            if line.startswith('ricoh_mmc'):
                misc.execute('rmmod',line.split()[0])

    def propagate_kernel_parameters(self, target):
        '''Copies in kernel command line parameters that were needed during
           installation'''
        extra = magic.find_extra_kernel_options()
        new = ''
        for item in extra.split():
            if not 'dell-recovery/'    in item and \
               not 'debian-installer/' in item and \
               not 'console-setup/'    in item and \
               not 'locale='           in item and \
               not 'ubiquity'          in item:
                new+='%s ' % item
        extra = new.strip()

        if extra and os.path.exists(os.path.join(target, 'etc/default/grub')):
            #read/write new grub
            with open(os.path.join(target, 'etc/default/grub'),'r') as grub:
                default_grub = grub.readlines()
            with open(os.path.join(target, 'etc/default/grub'),'w') as grub:
                for line in default_grub:
                    if 'GRUB_CMDLINE_LINUX_DEFAULT' in line:
                        line = line.replace('GRUB_CMDLINE_LINUX_DEFAULT="', 'GRUB_CMDLINE_LINUX_DEFAULT="%s ' % extra)
                    grub.write(line)
            from ubiquity import install_misc
            install_misc.chrex(target, 'update-grub')

    def remove_unwanted_drivers(self, progress):
        '''Removes any drivers that were preseeded to not be wanted during postinstall'''
        to_remove = []
        drivers = ''

        try:
            drivers = progress.get('dell-recovery/disable-driver-install').split(',')
        except debconf.DebconfError, e:
            pass

        if len(drivers) > 0:
            from apt.cache import Cache
            cache = Cache()
            for driver in drivers:
                if 'nvidia' in driver:
                    for key in cache.keys():
                        if 'nvidia' in key and cache[key].isInstalled:
                            to_remove.append(key)
                elif cache.has_key('%s-modaliases' % driver) and \
                   cache['%s-modaliases' % driver].isInstalled:
                    to_remove.append('%s-modaliases' % driver)
            del cache

        return to_remove

    def install(self, target, progress, *args, **kwargs):
        '''This is highly dependent upon being called AFTER configure_apt
        in install.  If that is ever converted into a plugin, we'll
        have some major problems!'''
        genuine = magic.check_vendor()
        if not genuine:
            raise RuntimeError, ("This recovery media only works on Dell Hardware.")

        up,  rp  = magic.find_partitions('','')

        from ubiquity import install_misc
        to_install = []
        to_remove  = []

        #Determine if we are doing OOBE
        try:
            if progress.get('oem-config/enable') == 'true':
                self.enable_oem_config(target)
        except debconf.DebconfError, e:
            pass

        #The last thing to do is set an active partition
        #This happens at the end of success command
        active = ''
        try:
            active = progress.get('dell-recovery/active_partition')
        except debconf.DebconfError, e:
            pass
        try:
            layout = progress.get('dell-recovery/disk_layout')
        except debconf.DebconfError, e:
            layout = 'msdos'

        if active.isdigit():
            disk = progress.get('partman-auto/disk')
            with open('/tmp/set_active_partition', 'w') as fd:
                #If we have an MBR, we use the active partition bit in it
                if layout == 'msdos':
                    fd.write('sfdisk -A%s %s\n' % (active, disk))
                #If we have GPT, we need to go down other paths
                elif layout == 'gpt':
                    #If we're booted in EFI mode, then we set the active partition in NVRAM
                    if os.path.isdir('/proc/efi'):
                        # --disk: disk to boot to
                        # --label: label shown in firmware boot list
                        # --create: creates a bootnum in that list
                        # --active: sets the new bootnum active
                        # --write-signature: writes a special signature to MBR if needed
                        # --part: partition containing EFI cool beans
                        # --loader: the name of the loader we are choosing
                        fd.write('efibootmbr --disk %s --part %s --label Ubuntu --create --active --write-signature --loader /\grub.efi\n' % (disk,active))
                    #If we're not booted to EFI mode, we need to reinstall grub to 
                    #set the active partition again - it's on the MBR
                    else:
                        fd.write('grub-install --no-floppy %s\n' % disk)
            os.chmod('/tmp/set_active_partition', 0755)

        #Fixup pool to only accept stuff on /cdrom
        #This is reverted during SUCCESS_SCRIPT
        try:
            pool_cmd = progress.get('dell-recovery/pool_command')
            if os.path.exists(pool_cmd):
                install_misc.chrex(target, pool_cmd)
        except debconf.DebconfError, e:
            pass

        #Stuff that is installed on all configs without fish scripts
        to_install += self.find_unconditional_debs()

        #Query Dual boot or not
        try:
            dual = self.db.get('dell-recovery/dual_boot_seed')
        except debconf.DebconfError, e:
            dual = ''

        #we don't want EULA, DesktopUI or dell-recovery in dual mode
        if dual:
            for package in ['dell-eula', 'dell-oobe', 'dell-recovery']:
                try:
                    to_install.remove(package)
                    to_remove.append(package)
                except ValueError:
                    continue
        #install dell-recovery in non dual mode only if there is an RP
        elif rp:
            to_install.append('dell-recovery')

        to_remove += self.remove_unwanted_drivers(progress)
                    
        self.remove_ricoh_mmc()

        self.propagate_kernel_parameters(target)

        install_misc.record_installed(to_install)
        install_misc.record_removed(to_remove)

        return InstallPlugin.install(self, target, progress, *args, **kwargs)

