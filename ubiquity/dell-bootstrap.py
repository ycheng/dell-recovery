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
import Dell.recovery_common as magic
import subprocess
import os
import re

NAME = 'dell-bootstrap'
AFTER = None
BEFORE = 'language'
WEIGHT = 12

#Gtk widgets
class PageGtk(PluginUI):
    def __init__(self, controller, *args, **kwargs):
        self.plugin_widgets = None

        with open('/proc/cmdline') as file:
            cmdline = file.readline().strip('\n')

        oem = 'UBIQUITY_OEM_USER_CONFIG' in os.environ

        with misc.raised_privileges():
            self.genuine = magic.check_vendor()

        self.reinstall = 'REINSTALL' in cmdline
        self.dvdboot = 'DVDBOOT' in cmdline

        if (self.reinstall or self.dvdboot or not self.genuine) and not oem:
            try:
                import gtk
                builder = gtk.Builder()
                builder.add_from_file('/usr/share/ubiquity/gtk/stepDellBootstrap.ui')
                builder.connect_signals(self)
                self.controller = controller
                self.plugin_widgets = builder.get_object('stepDellBootstrap')
                self.automated_recovery = builder.get_object('automated_recovery')
                self.interactive_recovery = builder.get_object('interactive_recovery')
                self.hidden_radio = builder.get_object('hidden_radio')
                if not self.genuine:
                    builder.get_object('interactive_recovery_box').hide()
                    builder.get_object('automated_recovery_box').hide()
                    self.automated_recovery.set_sensitive(False)
                    self.interactive_recovery.set_sensitive(False)
                    builder.get_object('genuine_box').show()
                elif not self.dvdboot:
                    builder.get_object('interactive_recovery_box').hide()
                    self.interactive_recovery.set_sensitive(False)
            except Exception, e:
                self.debug('Could not create Dell Bootstrap page: %s', e)
        else:
            if not (self.reinstall or self.dvdboot):
                self.debug('Disabling %s because of problems with cmdline: [%s]', NAME, cmdline)
            elif oem:
                self.debug('Disabling %s because of running in OEM mode', NAME)

    def plugin_get_current_page(self):
        if not self.genuine:
            self.controller.allow_go_forward(False)
        return self.plugin_widgets

    def get_type(self):
        """Returns the type of recovery to do from GUI"""
        if self.automated_recovery.get_active():
            return "automatic"
        elif self.interactive_recovery.get_active():
            return "interactive"
        else:
            return ""

    def set_type(self,type):
        """Sets the type of recovery to do in GUI"""
        if type == "automatic":
            self.automated_recovery.set_active(True)
        elif type == "interactive":
            self.interactive_recovery.set_active(True)
        else:
            self.hidden_radio.set_active(True)
            self.controller.allow_go_forward(False)

    def toggle_type(self, widget):
        """Allows the user to go forward after they've made a selection'"""
        self.controller.allow_go_forward(True)

class Page(Plugin):
    def __init__(self, frontend, db=None, ui=None):
        self.kexec = False
        self.device = '/dev/sda'
        Plugin.__init__(self, frontend, db, ui)

    def build_rp(self, cushion=300):
        """Copies content to the recovery partition"""

        def fetch_output(cmd, data=None):
            '''Helper function to just read the output from a command'''
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE)
            (out,err) = proc.communicate(data)
            return out

        white_pattern = re.compile('/')

        #Calculate UP#
        if os.path.exists('/cdrom/upimg.bin'):
            #in bytes
            up_size = int(fetch_output(['gzip','-lq','/cdrom/upimg.bin']).split()[1])
            #in mbytes
            up_size = up_size / 1048576
        else:
            up_size = 0

        #Calculate RP
        rp_size = magic.white_tree("size", white_pattern, '/cdrom')
        #in mbytes
        rp_size = (rp_size / 1048576) + cushion

        #Zero out the MBR
        with open('/dev/zero','rb') as zeros:
            with misc.raised_privileges():
                with open(self.device,'w') as out:
                    out.write(zeros.read(1024))

        #Create a DOS MBR
        with open('/usr/lib/syslinux/mbr.bin')as mbr:
            with misc.raised_privileges():
                with open(self.device,'w') as out:
                    out.write(mbr.read(404))

        #Partitioner commands
        data = 'n\np\n1\n\n' # New partition 1
        data += '+' + str(up_size) + 'M\n\nt\nde\n\n' # Size and make it type de
        data += 'n\np\n2\n\n' # New partition 2
        data += '+' + str(rp_size) + 'M\n\nt\np\n2\n0b\n\n' # Size and make it type 0b
        data += 'a\n2\n\n' # Make partition 2 active
        data += 'w\n' # Save and quit
        with misc.raised_privileges():
            fetch_output(['fdisk', '/dev/sda'], data)

        #Refresh the kernel partition list
        with misc.raised_privileges():
            probe = misc.execute_root('partprobe', self.device)
            if not probe:
                self.debug("Partition probe failed")

        #Restore UP
        if os.path.exists('/cdrom/upimg.bin'):
            with misc.raised_privileges():
                with open(self.device + '1','w') as partition:
                    p1 = subprocess.Popen(['gzip','-dc','/cdrom/upimg.bin'], stdout=subprocess.PIPE)
                    partition.write(p1.communicate()[0])

        #Build RP FS
        fs = misc.execute_root('mkfs.msdos','-n','install',self.device + '2')
        if not fs:
            self.debug("Error creating vfat filesystem on %s2" % self.device)

        #Mount RP
        mount = misc.execute_root('mount', '-t', 'vfat', self.device + '2', '/boot')
        if not mount:
            self.debug("Error mounting %s2" % self.device)

        #Copy RP Files
        with misc.raised_privileges():
            magic.white_tree("copy", white_pattern, '/cdrom', '/boot')

        #Install grub
        grub = misc.execute_root('grub-install', '--force', self.device + '2')
        if not grub:
            self.debug("Error installing grub to %s2" % self.device)

        #Build new UUID
        uuid = misc.execute_root('casper-new-uuid',
                             '/cdrom/casper/initrd.lz',
                             '/boot/casper',
                             '/boot/.disk')
        if not uuid:
            self.debug("Error rebuilding new casper UUID")

        #Load kexec kernel
        if self.kexec:
            with open('/proc/cmdline') as file:
                cmdline = file.readline().strip('\n').replace('DVDBOOT','').replace('REINSTALL','')
            kexec_run = misc.execute_root('kexec',
                          '-l', '/boot/casper/vmlinuz',
                          '--initrd=/boot/casper/initrd.lz',
                          '--command-line="' + cmdline + '"')
            if not kexec_run:
                self.debug("kexec loading of kernel and initrd failed")

        #Unmount devices
        umount = misc.execute_root('umount', '/boot')
        if not umount:
            self.debug("Umount after file copy failed")

    def install_grub(self):
        """Installs grub on the recovery partition"""
        cd_mount   = misc.execute_root('mount', '-o', 'remount,rw', '/cdrom')
        if not cd_mount:
            self.debug("CD Mount failed")
        bind_mount = misc.execute_root('mount', '-o', 'bind', '/cdrom', '/boot')
        if not bind_mount:
            self.debug("Bind Mount failed")
        grub_inst  = misc.execute_root('grub-install', '--force', self.device + '2')
        if not grub_inst:
            self.debug("Grub install failed")
        unbind_mount = misc.execute_root('umount', '/boot')
        if not unbind_mount:
            self.debug("Unmount /boot failed")
        uncd_mount   = misc.execute_root('mount', '-o', 'remount,ro', '/cdrom')
        if not uncd_mount:
            self.debug("Uncd mount failed")

    def remove_extra_partitions(self):
        """Removes partitions 3 and 4 for the process to start"""
        active = misc.execute_root('sfdisk', '-A2', self.device)
        if not active:
            self.debug("Failed to set partition 2 active on %s" % self.device)
        for number in ('3','4'):
            remove = misc.execute_root('parted', '-s', self.device, 'rm', number)
            if not remove:
                self.debug("Error removing partition number: %d on %s" % (number,self.device))

    def boot_rp(self):
        """attempts to kexec a new kernel and falls back to a reboot"""
        #TODO: notify in GUI of media ejections
        #eject = misc.execute_root('eject', '-p', '-m' '/cdrom')
        #if not eject:
        #    self.debug("Eject was: %d" % eject)
        if self.kexec:
            kexec = misc.execute_root('kexec', '-e')
            if not kexec:
                self.debug("kexec failed")

        reboot = misc.execute_root('reboot','-n')
        if not reboot:
            self.debug("Reboot failed")

    def unset_drive_preseeds(self):
        """Unsets any preseeds that are related to setting a drive"""
        for key in [ 'partman-auto/init_automatically_partition',
                     'partman-auto/disk',
                     'partman-auto/expert_recipe',
                     'partman-basicfilesystems/no_swap',
                     'grub-installer/only_debian',
                     'grub-installer/with_other_os',
                     'grub-installer/bootdev',
                     'grub-installer/make_active' ]:
            self.db.fset(key, 'seen', 'false')
            self.db.set(key, '')
        self.db.set('ubiquity/partman-skip-unmount', 'false')
        self.db.set('partman/filter_mounted', 'true')

    def prepare(self, unfiltered=False):
        try:
            type = self.db.get('dell-recovery/recovery_type')
            self.ui.set_type(type)
        except debconf.DebconfError:
            pass

        try:
            self.kexec = misc.create_bool(self.db.get('dell-recovery/kexec'))
        except debconf.DebconfError:
            pass
        try:
            self.device = self.db.get('partman-auto/disk')
        except debconf.DebconfError:
            pass

        #If the system doesn't support edd, just assume first drive
        if not os.path.exists(self.device) and 'edd' in self.device:
            with misc.raised_privileges():
                os.symlink('../../sda', self.device)

        #Follow the symlink
        if os.path.islink(self.device):
            self.device = os.path.join(os.path.dirname(self.device), os.readlink(self.device))

        return Plugin.prepare(self, unfiltered=unfiltered)

    def ok_handler(self):
        type = self.ui.get_type()
        self.preseed('dell-recovery/recovery_type', type)

        # User recovery - need to copy RP
        if type == "automatic":
            self.build_rp()
            self.boot_rp()

        # User recovery - resizing drives
        elif type == "interactive":
            self.unset_drive_preseeds()

        # Factory install and post kexec
        else:
            self.remove_extra_partitions()
            self.install_grub()
        return Plugin.ok_handler(self)


#Currently we have actual stuff that's run as a late command
#class Install(InstallPlugin):
#
#    def install(self, target, progress, *args, **kwargs):
#        return InstallPlugin.install(self, target, progress, *args, **kwargs)

