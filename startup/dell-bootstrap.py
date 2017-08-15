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

import Dell.misc as misc
from threading import Thread
import threading
import time
from Dell.recovery_threading import ProgressBySize
import Dell.recovery_common as magic
from Dell.recovery_xml import BTOxml
import os
import re
import shutil
import dbus
from dbus.mainloop.glib import DBusGMainLoop
DBusGMainLoop(set_as_default=True)
import glob
import gi
gi.require_version('UDisks', '2.0')
from gi.repository import GLib,UDisks,Gdk,GObject

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk


#redefine the path of CDROM
# magic.CDROM_MOUNT = '/usr/lib/live/mount/medium'

#Translation support
from gettext import gettext as _

#Partition Definitions
EFI_ESP_PARTITION       =     '1'
EFI_RP_PARTITION        =     '2'
EFI_OS_PARTITION        =     '3'
EFI_SWAP_PARTITION      =     '4'

###############################
# Standalone GTK Page #
###############################
class StandalonePageGtk:
    """GTK frontend for the dell-bootstrap"""

    def __init__(self, *args, **kwargs):

        self.builder = None
        self.builder = Gtk.Builder()
        self.builder.add_from_file('/usr/share/dell/gtk/stepDellBootstrap.ui')
        self.builder.connect_signals(self)
        self.plugin_widgets = self.builder.get_object('stepDellBootstrap')
        self.automated_recovery = self.builder.get_object('automated_recovery')
        self.automated_recovery_box = self.builder.get_object('automated_recovery_box')
        self.automated_combobox = self.builder.get_object('hard_drive_combobox')
        self.hidden_radio = self.builder.get_object('hidden_radio')
        self.info_box = self.builder.get_object('info_box')
        self.info_spinner = Gtk.Spinner()
        self.builder.get_object('info_spinner_box').add(self.info_spinner)
        self.restart_box = self.builder.get_object('restart_box')
        self.err_dialog = self.builder.get_object('err_dialog')
        self.log_dialog = self.builder.get_object('log_dialog')
        global install_progress
        install_progress = self.builder.get_object('install_progress')
        global install_progress_text
        install_progress_text = self.builder.get_object('install_progress_text')
        #advanced page widgets
        icon = self.builder.get_object('dell_image')
        icon.set_tooltip_markup("Dell Recovery Advanced Options")
        self.advanced_page = self.builder.get_object('advanced_window')
        self.version_detail = self.builder.get_object('version_detail')
        self.mount_detail = self.builder.get_object('mountpoint_detail')
        self.memory_detail = self.builder.get_object('memory_detail')
        #Device detected variables
        self.device = None
        self.standalone = None
        self.prepare()

    def get_selected_device(self,widget):
        """Returns the selected device from the GUI"""
        device = size = ''
        iterator = self.automated_combobox.get_active_iter()
        if iterator is not None:
            model = self.automated_combobox.get_model()
            device = model[iterator][0]
            self.device = device

    def toggle_type(self, widget):
        """Allows the user to go forward after they've made a selection'"""
        self.automated_combobox.set_sensitive(self.automated_recovery.get_active())

    def show_dialog(self, which, data = None):
        """Shows a dialog"""
        if which == "info":
            self.automated_recovery_box.hide()
            self.info_box.show_all()
            self.info_spinner.start()
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
            # print(device)
            liststore.append(device)
        #default to the first item active (it should be sorted anyway)
        self.automated_combobox.set_active(0)

    ##                      ##
    ## Advanced GUI options ##
    ##                      ##
    def toggle_advanced(self, widget, data = None):
        """Shows the advanced page"""
        self.advanced_page.run()
        self.advanced_page.hide()

    def set_advanced(self, item, value):
        """Populates the options that should be on the advanced page"""
        if item == "mem" and value:
            self.memory_detail.set_markup("Total Memory: %f GB" % value)
        # elif item == "version":
        #     self.version_detail.set_markup("Version: %s" % value)
        elif item == "mount":
            self.mount_detail.set_markup("Mounted From: %s" % value)
        else:
            if type(value) is bool:
                if value:
                    value = 'true'
                else:
                    value = 'false'

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
                output = block.get_cached_property("Id").get_string()
                model = output.split("-")[-1].replace("_", " ")
                nvme_dev_size = block.get_cached_property("Size").unpack()
                disks.append([device_path, nvme_dev_size, "%s (%s)" % (model, device_path)])
                continue

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

        if len(disks) == 0:
            raise RuntimeError("Unable to find and candidate hard disks to install to.")

        # Search for the recovery partition on the same disk first
        the_same_disk = None
        for disk in disks:
            device = disk[0]
            with open('/proc/mounts', 'r') as mounts:
                for line in mounts.readlines():
                    if device in line:
                        self.device = device
                        the_same_disk = disk
                        break
            if the_same_disk:
                break
        if the_same_disk:
            disks.remove(the_same_disk)
            disks.insert(0, the_same_disk)
        else:
            disks.sort()
            self.device = disks[0][0]
        #populate UI
        self.populate_devices(disks)

    def prepare(self):
        """Prepare the Debconf portion of the plugin and gather all data"""
        mount = find_boot_device()
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
                   "mem": self.mem}
        # The order invoking set_advanced() is important. (LP: #1324394)
        for twaddle in reversed(sorted(twiddle)):
            self.set_advanced(twaddle, twiddle[twaddle])

        #Clarify which device we're operating on initially in the UI
        try:
            self.fixup_recovery_devices()
        except Exception as err:
            self.handle_exception(err)
            self.cancel_handler()
        self.builder.get_object("standalone").show()


    def report_progress(self, info, percent):
        """Reports to the frontend an update about th progress"""
        Gdk.threads_enter()
        install_progress_text.set_label(info)
        install_progress.set_fraction(percent/100)
        Gdk.threads_leave()
        time.sleep(0.3)


    def cleanup(self, widget):
        """Do all the real processing like winPE FID.
        """
        try:
            # User recovery - need to copy RP
            self.sleep_network()
            self.builder.get_object("progress_section").show_all()
            self.standalone = threading.Thread(target=self.standalone_builder)
            self.standalone.daemon = True
            self.standalone.start()

        except Exception as err:
            #For interactive types of installs show an error then reboot
            #Otherwise, just reboot the system
            self.show_dialog("exception", err)

    def cancel_handler(self, widget):
        """Called when we don't want to perform recovery'"""
        reboot_machine(None)

    def handle_exception(self, err):
        """Handle all exceptions thrown by any part of the application"""
        Gdk.threads_enter()
        self.show_dialog("exception", err)
        Gdk.threads_leave()

    def destroy(self,widget):
        killstring = "ps -ef | grep dell-bootstrap.py | grep -v grep | awk '{print $2}' | xargs kill -9"
        os.system(killstring)

    def standalone_builder(self):
        """Queries the BTO version number internally stored in an ISO or RP"""
        # Partition Definitions
        EFI_ESP_PARTITION = '1'
        EFI_RP_PARTITION = '2'

        try:
            misc.execute_root('umount', '/mnt')
        except:
            pass

        cushion = 600
        if os.path.exists(magic.CDROM_MOUNT + '/IMAGE/rcx.flg'):
            cushion = 1600
        black_pattern = re.compile("no_black_pattern")

        # Check if we are booted from same device as target
        mounted_device = find_boot_device()
        if self.device in mounted_device:
            self.handle_exception("Attempting to install to the same device as booted from.\n\
        You will need to clear the contents of the recovery partition\n\
        manually to proceed.")
            raise RuntimeError("Attempting to install to the same device as booted from.\n\
        You will need to clear the contents of the recovery partition\n\
        manually to proceed.")
        # Calculate RP size
        rp_size = magic.black_tree("size", black_pattern, magic.CDROM_MOUNT + "/IMAGE")
        # in mbytes
        rp_size_mb = (rp_size / 1000000) + cushion

        # Build new partition table
        command = ('parted', '-s', self.device, 'mklabel', 'gpt')
        result = misc.execute_root(*command)
        if result is False:
            command = ('partprobe')
            result = misc.execute_root(*command)
            if result is False:
                self.handle_exception("Error creating new partition table on %s" % (self.device))
                raise RuntimeError("Error creating new partition table on %s" % (self.device))

        self.report_progress("Creating Partitions", 100)
        grub_size = 250
        commands = [('parted', '-a', 'optimal', '-s', self.device, 'mkpart', 'primary', 'fat16', '0', str(grub_size)),
                    ('parted', '-s', self.device, 'name', '1', "'EFI System Partition'"),
                    ('parted', '-s', self.device, 'set', '1', 'boot', 'on')]
        if '/dev/nvme' in self.device or '/dev/mmcblk' in self.device:
            commands.append(('mkfs.msdos', self.device + 'p' + EFI_ESP_PARTITION))
            rp_part = 'p' + EFI_RP_PARTITION
            esp_part = 'p' + EFI_ESP_PARTITION
        else:
            commands.append(('mkfs.msdos', self.device + EFI_ESP_PARTITION))
            rp_part = EFI_RP_PARTITION
            esp_part = EFI_ESP_PARTITION
        for command in commands:
            # wait for settle
            if command[0] == 'mkfs.msdos':
                while not os.path.exists(command[-1]):
                    time.sleep(1)
            result = misc.execute_root(*command)
            if result is False:
                self.handle_exception("Error formatting disk.")
                raise RuntimeError("Error formatting disk.")

        # Drag some variable of parted command to support RCX
        file_format = 'fat32'
        file_type = 'mkfs.msdos'
        file_para = '-n'
        part_label = 'OS'

        # Change file system if installed RCX
        if os.path.exists(magic.CDROM_MOUNT + '/IMAGE/rcx.flg'):
            # Set RCX variable parameters
            file_format = 'ext2'
            file_type = 'mkfs.ext2'
            file_para = '-L'
            part_label = 'rhimg'
            # Build OS Part
            command = ('parted', '-a', 'optimal', '-s', self.device, 'mkpart', 'fat32', 'fat32', str(grub_size),
                       str(250 + grub_size))
            result = misc.execute_root(*command)
            if result is False:
                self.handle_exception("Error creating new 250 mb OS partition on %s" % (self.device))
                raise RuntimeError("Error creating new 250 mb OS partition on %s" % (self.device))
            # Build OS filesystem
            command = ('mkfs.msdos', '-n', 'OS', self.device + rp_part)
            while not os.path.exists(command[-1]):
                time.sleep(1)
            result = misc.execute_root(*command)
            if result is False:
                self.handle_exception("Error creating fat32 filesystem on %s%s" % (self.device, rp_part))
                raise RuntimeError("Error creating fat32 filesystem on %s%s" % (self.device, rp_part))
            # Refresh the grub_size and rp_part value
            grub_size = grub_size + 250
            rp_part = rp_part[:-1] + '3'
            rp_size_mb = rp_size_mb + 5000

        # Build RP
        command = ('parted', '-a', 'optimal', '-s', self.device, 'mkpart', file_format, file_format, str(grub_size),
                   str(rp_size_mb + grub_size))
        result = misc.execute_root(*command)
        if result is False:
            self.handle_exception("Error creating new %s mb recovery partition on %s" % (rp_size_mb, self.device))
            raise RuntimeError("Error creating new %s mb recovery partition on %s" % (rp_size_mb, self.device))

        # Build RP filesystem
        self.report_progress(_('Formatting Partitions'), 200)
        if os.path.exists(magic.CDROM_MOUNT + '/IMAGE/rcx.flg'):
            command = (file_type, '-F', file_para, part_label, self.device + rp_part)
        else:
            command = (file_type, file_para, part_label, self.device + rp_part)
        while not os.path.exists(command[-1]):
            time.sleep(1)
        result = misc.execute_root(*command)
        if result is False:
            self.handle_exception("Error creating %s filesystem on %s%s" % (file_format, self.device, rp_part))
            raise RuntimeError("Error creating %s filesystem on %s%s" % (file_format, self.device, rp_part))

        # Mount RP
        mount = misc.execute_root('mount', self.device + rp_part, '/mnt')
        if mount is False:
            self.handle_exception("Error mounting %s%s" % (self.device, rp_part))
            raise RuntimeError("Error mounting %s%s" % (self.device, rp_part))

        # Update status and start the file size thread
        size_thread = ProgressBySize("Copying Files",
                                           "/mnt",
                                           rp_size)
        size_thread.progress = self.report_progress
        size_thread.reset_write(rp_size)
        size_thread.set_starting_value(2)
        size_thread.start()

        # Copy RP Files
        with misc.raised_privileges():
            if os.path.exists(magic.ISO_MOUNT):
                magic.black_tree("copy", re.compile(".*\.iso$"), magic.ISO_MOUNT + '/IMAGE', '/mnt')
            magic.black_tree("copy", black_pattern, magic.CDROM_MOUNT + '/IMAGE', '/mnt')

        size_thread.join()

        # combine the RCX iso image as its size is too larger to store in vfat sticky
        if os.path.exists(magic.CDROM_MOUNT + '/IMAGE/rcx.flg'):
            # lock.acquire()
            tgz_file = "/tmp/RCX_ISO.tar.gz"
            with misc.raised_privileges():
                # merge compress iso files
                gza_files = glob.glob("/mnt/*.tar.gza*")
                with open(tgz_file, 'wb') as outfile:
                    ISO_size = sum(map(os.path.getsize, gza_files))
                    size_thread = ProgressBySize("Merge Compress RCX ISO Files ...",
                                                 tgz_file,
                                                 ISO_size)
                    size_thread.progress = self.report_progress
                    size_thread.reset_write(ISO_size)
                    size_thread.set_starting_value(2)
                    size_thread.start()
                    for fname in sorted(gza_files):
                        with open(fname, 'rb') as infile:
                            for line in infile:
                                outfile.write(line)
                    size_thread.join()
                # remove the gza files
                for gza_file in gza_files:
                    os.remove(gza_file)

            import tarfile
            tf = tarfile.open(tgz_file)
            tarfile_size = sum(map(lambda x:getattr(x,"size"),tf.getmembers()))
            ISO_file = os.path.join("/mnt", tf.getmembers()[0].name)
            if not os.path.exists(ISO_file):
                os.mknod(ISO_file)
            size_thread = ProgressBySize("Unpacking RCX ISO Image ...",
                                         ISO_file,
                                         tarfile_size)
            size_thread.progress = self.report_progress
            size_thread.reset_write(tarfile_size)
            size_thread.set_starting_value(2)
            size_thread.start()
            tf.extractall(path="/mnt/")
            tf.close()
            size_thread.join()
            os.remove(tgz_file)


        with misc.raised_privileges():
            blkid = magic.fetch_output(['blkid', self.device + rp_part, "-p", "-o", "udev"]).split('\n')
            for item in blkid:
                if item.startswith('ID_FS_UUID'):
                    uuid = item.split('=')[1]
                    break

            with misc.raised_privileges():
                magic.process_conf_file(magic.CDROM_MOUNT + '/IMAGE/factory/grub.cfg', \
                                        '/mnt/factory/grub.cfg', uuid, EFI_RP_PARTITION)

        # Install grub
        self.report_progress(_('Installing GRUB'), 880)

        ##If we don't have grub binaries, build them
        grub_files = [magic.CDROM_MOUNT + '/IMAGE/efi/boot/bootx64.efi',
                      magic.CDROM_MOUNT + '/IMAGE/efi/boot/grubx64.efi']

        ##Mount ESP
        if not os.path.exists(os.path.join('/mnt', 'efi')):
            with misc.raised_privileges():
                os.makedirs(os.path.join('/mnt', 'efi'))
        mount = misc.execute_root('mount', self.device + esp_part, '/mnt/efi')
        if mount is False:
            self.handle_exception("Error mounting %s%s" % (self.device, esp_part))
            raise RuntimeError("Error mounting %s%s" % (self.device, esp_part))

        ##find old entries and prep directory
        direct_path = '/mnt/efi' + '/EFI/linux'
        with misc.raised_privileges():
            os.makedirs(direct_path)

            # copy boot loader files
            for item in grub_files:
                if not os.path.exists(item):
                    self.handle_exception("Error, %s doesn't exist." % item)
                    raise RuntimeError("Error, %s doesn't exist." % item)
                shutil.copy(item, direct_path)

            # find old entries
            bootmgr_output = magic.fetch_output(['efibootmgr', '-v']).split('\n')

            # delete old entries
            for line in bootmgr_output:
                bootnum = ''
                if line.startswith('Boot') and 'LinuxIns' in line.lower():
                    bootnum = line.split('Boot')[1].replace('*', '').split()[0]
                if bootnum:
                    bootmgr = misc.execute_root('efibootmgr', '-v', '-b', bootnum, '-B')
                    if bootmgr is False:
                        self.handle_exception("Error removing old EFI boot manager entries")
                        raise RuntimeError("Error removing old EFI boot manager entries")

        target = 'shimx64.efi'
        source = 'bootx64.efi'
        # RCX bootloader source file name is different
        if os.path.exists(magic.CDROM_MOUNT + '/IMAGE/rcx.flg'):
            source = 'grubx64.efi'

        with misc.raised_privileges():
            os.rename(os.path.join(direct_path, source),
                      os.path.join(direct_path, target))

        add = misc.execute_root('efibootmgr', '-v', '-c', '-d', self.device, '-p', EFI_ESP_PARTITION, '-l',
                                '\\EFI\\linux\\%s' % target, '-L', 'LinuxIns')
        if add is False:
            self.handle_exception("Error adding efi entry to %s%s" % (self.device, esp_part))
            raise RuntimeError("Error adding efi entry to %s%s" % (self.device, esp_part))
        # set the LinuxIns entry on the first place for next reboot
        with misc.raised_privileges():
            bootmgr_output = magic.fetch_output(['efibootmgr', '-v']).split('\n')
            for line in bootmgr_output:
                bootnum = ''
                if line.startswith('Boot') and 'LinuxIns' in line:
                    bootnum = line.split('Boot')[1].replace('*', '').split()[0]
                    misc.execute_root('efibootmgr', '-n', bootnum)
        ##copy other neokylin bootloader files
        with misc.raised_privileges():
            if os.path.exists(magic.ISO_MOUNT):
                shutil.copy(magic.ISO_MOUNT + '/IMAGE/factory/grub.cfg', '/mnt/efi/EFI/linux/grub.cfg')
            shutil.copy(magic.CDROM_MOUNT + '/IMAGE/factory/grub.cfg', '/mnt/efi/EFI/linux/grub.cfg')

        ##clean up ESP mount
        misc.execute_root('umount', '/mnt/efi')

        # set install_in_progress flag
        with misc.raised_privileges():
            magic.fetch_output(['grub-editenv', '/mnt/factory/grubenv', 'set', 'install_in_progress=1'])

        misc.execute_root('umount', '/mnt')

        for count in range(100, 0, -10):
            self.report_progress("Restarting in %d seconds." % int(count / 10), count)
            time.sleep(1)

        reboot_machine(None)

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

def main():
    GObject.threads_init()
    Gdk.threads_init()
    Gdk.threads_enter()
    StandalonePageGtk()
    Gtk.main()
    Gdk.threads_leave()

if __name__ == '__main__':
    main()