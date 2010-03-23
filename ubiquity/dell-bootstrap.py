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
import gtk
import shutil
import dbus
from dbus.mainloop.glib import DBusGMainLoop
DBusGMainLoop(set_as_default=True)
import syslog

NAME = 'dell-bootstrap'
AFTER = 'language'
WEIGHT = 12
OEM = False

UP_PART =     '1'
RP_PART =     '2'
CDROM_MOUNT = '/cdrom'

TYPE_NTFS = '07'
TYPE_VFAT = '0b'

#######################
# Noninteractive Page #
#######################
class PageNoninteractive(PluginUI):
    def get_type(self):
        '''For the noninteractive frontend, get_type always returns an empty str
            This is because the noninteractive frontend always runs in "factory"
            mode, which expects such a str""'''
        return ""

    def set_type(self,type):
        pass

    def show_info_dialog(self):
        pass

    def show_reboot_dialog(self):
        pass

    def show_exception_dialog(self,e):
        pass

############
# GTK Page #
############
class PageGtk(PluginUI):
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
            self.plugin_widgets = builder.get_object('stepDellBootstrap')
            self.automated_recovery = builder.get_object('automated_recovery')
            self.automated_recovery_box = builder.get_object('automated_recovery_box')
            self.interactive_recovery = builder.get_object('interactive_recovery')
            self.interactive_recovery_box = builder.get_object('interactive_recovery_box')
            self.hdd_recovery = builder.get_object('hdd_recovery')
            self.hdd_recovery_box = builder.get_object('hdd_recovery_box')
            self.hidden_radio = builder.get_object('hidden_radio')
            self.reboot_dialog = builder.get_object('reboot_dialog')
            self.reboot_dialog.set_title('Dell Recovery')
            self.info_box = builder.get_object('info_box')
            self.info_spinner = builder.get_object('info_spinner')
            self.err_dialog = builder.get_object('err_dialog')
            if not self.genuine:
                self.interactive_recovery_box.hide()
                self.automated_recovery_box.hide()
                self.automated_recovery.set_sensitive(False)
                self.interactive_recovery.set_sensitive(False)
                builder.get_object('genuine_box').show()

    def plugin_get_current_page(self):
        if not self.genuine:
            self.controller.allow_go_forward(False)
        #The widget has been added into the top level by now, so we can change top level stuff
        window = self.plugin_widgets.get_parent_window()
        window.set_title('Dell Recovery')
        window.set_functions(gtk.gdk.FUNC_RESIZE | gtk.gdk.FUNC_MOVE)
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

    def toggle_type(self, widget):
        """Allows the user to go forward after they've made a selection'"""
        self.controller.allow_go_forward(True)

    def show_info_dialog(self):
        self.controller._wizard.step_label.set_markup('')
        self.controller.allow_go_forward(False)
        self.automated_recovery_box.hide()
        self.interactive_recovery_box.hide()
        self.info_box.show()
        self.info_spinner.start()

    def show_reboot_dialog(self):
        self.controller.toggle_top_level()
        self.info_spinner.stop()
        self.reboot_dialog.run()

    def show_exception_dialog(self, e):
        self.info_spinner.stop()
        self.err_dialog.format_secondary_text(str(e))
        self.err_dialog.run()
        self.err_dialog.hide()

################
# Debconf Page #
################
class Page(Plugin):
    def __init__(self, frontend, db=None, ui=None):
        self.kexec = False
        self.device = '/dev/sda'
        self.node = ''
        Plugin.__init__(self, frontend, db, ui)

    def install_grub(self):
        """Installs grub on the recovery partition"""
        #Mount R/W
        cd_mount   = misc.execute_root('mount', '-o', 'remount,rw', CDROM_MOUNT)
        if cd_mount is False:
            raise RuntimeError, ("CD Mount failed")

        #Check for a grub.cfg to start - make as necessary
        if not os.path.exists(os.path.join(CDROM_MOUNT, 'grub', 'grub.cfg')):
            with misc.raised_privileges():
                magic.process_conf_file(RP_PART, '/usr/share/dell/grub/recovery_partition.cfg', \
                                    os.path.join(CDROM_MOUNT, 'grub', 'grub.cfg'))

        #Do the actual grub installation
        bind_mount = misc.execute_root('mount', '-o', 'bind', CDROM_MOUNT, '/boot')
        if bind_mount is False:
            raise RuntimeError, ("Bind Mount failed")
        grub_inst  = misc.execute_root('grub-install', '--force', self.device + RP_PART)
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
        with open('/proc/swaps','r') as swap:
            for line in swap.readlines():
                if self.device in line or (self.node and self.node in line):
                    misc.execute_root('swapoff', line.split()[0])
                    if misc is False:
                        raise RuntimeError, ("Error disabling swap on device %s" % line.split()[0])

    def remove_extra_partitions(self):
        """Removes partitions we are installing on for the process to start"""
        #First set the new partition active
        active = misc.execute_root('sfdisk', '-A' + RP_PART, self.device)
        if active is False:
            self.debug("Failed to set partition %s active on %s" % (RP_PART, self.device))
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

    def explode_utility_partition(self):
        '''Explodes all content onto the utility partition
        '''
        for file in magic.up_filenames:
            if os.path.exists(os.path.join(CDROM_MOUNT, file)):
                #Restore full UP backup (dd)
                if '.bin' in file or '.gz' in file:
                    with misc.raised_privileges():
                        with open(self.device + UP_PART, 'w') as partition:
                            p1 = subprocess.Popen(['gzip','-dc',os.path.join(CDROM_MOUNT, file)], stdout=subprocess.PIPE)
                            partition.write(p1.communicate()[0])
                #Restore UP (zip/tgz)
                elif '.zip' in file or '.tgz' in file:
                    mount = misc.execute_root('mount', self.device + UP_PART, '/boot')
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
                    umount = misc.execute_root('umount', '/boot')
                    if umount is False:
                        raise RuntimeError, ("Error unmounting utility partition post-explosion.")
                #Clean up UP so it's not rewritten on next boot to RP
                cd_mount = misc.execute_root('mount', '-o', 'remount,rw', CDROM_MOUNT)
                if cd_mount is False:
                    raise RuntimeError, ("Error remounting RP to clean up post explosion.")
                with misc.raised_privileges():
                    os.remove(os.path.join(CDROM_MOUNT, file))
                #Don't worry about remounting the RP/remounting RO.
                #we'll probably need to do that in grub instead
                break

    def boot_rp(self):
        """attempts to kexec a new kernel and falls back to a reboot"""
        subprocess.call(['/etc/init.d/casper','stop'])

        if self.kexec and os.path.exists(CDROM_MOUNT + '/misc/kexec'):
            shutil.copy(CDROM_MOUNT + '/misc/kexec', '/tmp')

        #Set up a listen for udisks to let us know a usb device has left
        bus = dbus.SystemBus()
        bus.add_signal_receiver(reboot_machine, 'DeviceRemoved', 'org.freedesktop.UDisks')

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
                     'ubiquity/reboot' ]:
            self.db.fset(key, 'seen', 'false')
            self.db.set(key, '')
        self.db.set('ubiquity/partman-skip-unmount', 'false')
        self.db.set('partman/filter_mounted', 'true')

    def fixup_recovery_devices(self):
        """Fixes self.device to not be a symlink"""
        #Normally we do want the first edd device, but if we're booted from a USB
        #stick, that's just not true anymore
        if 'edd' in self.device:
            #First read in /proc/mounts to make sure we don't accidently write over the same
            #device we're booted from - unless it's a hard drive
            ignore = ''
            new = ''
            with open('/proc/mounts','r') as f:
                for line in f.readlines():
                    #Mounted
                    if '/cdrom' in line:
                        #and isn't a hard drive
                        device = line.split()[0]
                        if subprocess.call(['/lib/udev/ata_id',device]) != 0:
                            ignore = device
                            break
            if ignore:
                for root,dirs,files in os.walk('/dev/'):
                    for name in files:
                        if name.startswith('sd'):
                            stripped = name.strip('1234567890')
                            if stripped in ignore:
                                continue
                            else:
                                new = stripped
            if new:
                with misc.raised_privileges():
                    #Check if old device already existed:
                    if os.path.islink(self.device):
                        os.unlink(self.device)
                    os.symlink('../../' + new, self.device)

        #Follow the symlink
        if os.path.islink(self.device):
            self.node = os.readlink(self.device).split('/').pop()
            self.device = os.path.join(os.path.dirname(self.device), os.readlink(self.device))
        self.debug("Fixed up device we are operating on is %s" % self.device)

    def fixup_factory_devices(self):
        #Ignore any EDD settings - we want to just plop on the same drive with
        #the right FS label (which will be valid right now)
        #Don't you dare put a USB stick in the system with that label right now!
        rp = magic.find_factory_rp_stats()
        if not rp:
            raise RuntimeError, ("Unable to find factory recovery partition (was going to use %s)" % self.device)

        self.device = rp["slave"]
        self.db.set('oem-config/early_command', 'mount %s %s' % (rp["device"], CDROM_MOUNT))
        self.db.set('partman-auto/disk', self.device)
        self.db.set('grub-installer/bootdev', self.device + self.os_part)
        if rp["fs"] == "ntfs":
            self.rp_filesystem = TYPE_NTFS
        elif rp["fs"] == "vfat":
            self.rp_filesystem = TYPE_VFAT
        self.debug("Fixed up device we are operating on is %s" % self.device)
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
            self.rp_filesystem = TYPE_VFAT

        #We might try to support this
        try:
            self.kexec = misc.create_bool(self.db.get('dell-recovery/kexec'))
        except debconf.DebconfError:
            pass

        #If we are using multiple disks, we might fill this in
        try:
            self.device = self.db.get('partman-auto/disk')
        except debconf.DebconfError:
            pass

        return (['/usr/share/ubiquity/dell-bootstrap'], ['dell-recovery/recovery_type'])

    def ok_handler(self):
        """Copy answers from debconf questions"""
        type = self.ui.get_type()
        self.preseed('dell-recovery/recovery_type', type)
        return Plugin.ok_handler(self)

    def cleanup(self):
        #All this processing happens in cleanup because that ensures it runs for all scenarios
        type = self.db.get('dell-recovery/recovery_type')

        try:
            # User recovery - need to copy RP
            if type == "automatic":
                self.fixup_recovery_devices()
                self.ui.show_info_dialog()
                self.disable_swap()
                with misc.raised_privileges():
                    mem = fetch_output('/usr/lib/base-installer/dmi-available-memory').strip('\n')
                self.rp_builder = rp_builder(self.device, self.kexec, self.rp_filesystem, mem)
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

            # Factory install, post kexec, and booting from RP
            else:
                self.fixup_factory_devices()
                self.disable_swap()
                self.remove_extra_partitions()
                self.explode_utility_partition()
                if self.rp_filesystem == TYPE_VFAT:
                    self.install_grub()
        except Exception, e:
            #For interactive types of installs show an error then reboot
            #Otherwise, just reboot the system
            if type == "automatic" or type == "interactive":
                self.handle_exception(e)
            self.cancel_handler()
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
    def __init__(self, device, kexec, rp_type, mem):
        self.device = device
        self.kexec = kexec
        self.rp_type = rp_type
        self.mem = mem
        self.exception = None
        Thread.__init__(self)

    def build_rp(self, cushion=300):
        """Copies content to the recovery partition"""

        white_pattern = re.compile('.')

        #Utility partition image (dd)#
        if os.path.exists(CDROM_MOUNT + '/upimg.bin'):
            #in bytes
            up_size = int(fetch_output(['gzip','-lq',CDROM_MOUNT + '/upimg.bin']).split()[1])
            #in mbytes
            up_size = up_size / 1048576
        #Utility partition files (tgz/zip)#
        else:
            up_size = 32

        #Calculate RP
        rp_size = magic.white_tree("size", white_pattern, CDROM_MOUNT)
        #in mbytes
        rp_size = (rp_size / 1048576) + cushion

        #Zero out the MBR
        with open('/dev/zero','rb') as zeros:
            with misc.raised_privileges():
                with open(self.device,'wb') as out:
                    out.write(zeros.read(1024))

        #double check the recovery partition type
        if self.rp_type != TYPE_VFAT and self.rp_type != TYPE_NTFS:
            syslog.syslog("Preseeded RP type unsuported, setting to %s" % TYPE_VFAT)
            self.rp_type = TYPE_VFAT

        #Partitioner commands
        data = 'p\n'    #print current partitions (we might want them for debugging)
        data += 'n\np\n%s\n\n' % UP_PART    # New partition for UP
        data += '+' + str(up_size) + 'M\n\nt\nde\n\n'   # Size and make it type de
        data += 'n\np\n%s\n\n' % RP_PART    # New partition for RP
        data += '+' + str(rp_size) + 'M\n\nt\n%s\n%s\n\n' % (RP_PART, self.rp_type)    # Size and make it type 0b
        data += 'a\n%s\n\n' % RP_PART   # Make RP active
        data += 'w\n'   # Save and quit
        try:
            with misc.raised_privileges():
                fetch_output(['fdisk', self.device], data)
        except RuntimeError, e:
            #If we have a failure, try to re-read using partprobe
            probe = misc.execute_root('partprobe', self.device)
            if probe is False:
                raise RuntimeError, e

        #Create a DOS MBR
        with open('/usr/lib/syslinux/mbr.bin','rb')as mbr:
            with misc.raised_privileges():
                with open(self.device,'wb') as out:
                    out.write(mbr.read(404))

        #Build UP filesystem
        command = ('mkfs.msdos', '-n', 'DellUtility', self.device + UP_PART)
        fs = misc.execute_root(*command)
        if fs is False:
            raise RuntimeError, ("Error creating utility partition filesystem on %s%s" % (self.device, UP_PART))

        #Build RP filesystem
        if self.rp_type == TYPE_VFAT:
            command = ('mkfs.msdos', '-n', 'install', self.device + RP_PART)
        elif self.rp_type == TYPE_NTFS:
            command = ('mkfs.ntfs', '-f', '-L', 'RECOVERY', self.device + RP_PART)
        fs = misc.execute_root(*command)
        if fs is False:
            raise RuntimeError, ("Error creating %s filesystem on %s%s" % (self.rp_type, self.device, RP_PART))

        #Mount RP
        mount = misc.execute_root('mount', self.device + RP_PART, '/boot')
        if mount is False:
            raise RuntimeError, ("Error mounting %s%s" % (self.device, RP_PART))

        #Copy RP Files
        with misc.raised_privileges():
            magic.white_tree("copy", white_pattern, CDROM_MOUNT, '/boot')

        #Check for a grub.cfg - make as necessary
        if not os.path.exists(os.path.join('/boot', 'grub', 'grub.cfg')):
            with misc.raised_privileges():
                magic.process_conf_file(RP_PART, '/usr/share/dell/grub/recovery_partition.cfg', \
                                    os.path.join('/boot', 'grub', 'grub.cfg'))

        #Install grub
        grub = misc.execute_root('grub-install', '--force', self.device + RP_PART)
        if grub is False:
            raise RuntimeError, ("Error installing grub to %s%s" % (self.device, RP_PART))

        #Build new UUID
        if int(self.mem) >= 1000000:
            uuid = misc.execute_root('casper-new-uuid',
                                CDROM_MOUNT + '/casper/initrd.lz',
                                '/boot/casper',
                                '/boot/.disk')
            if uuid is False:
                raise RuntimeError, ("Error rebuilding new casper UUID")
        else:
            #The new UUID just fixes the installed-twice-on-same-system scenario
            #most users won't need that anyway so it's just nice to have
            syslog.syslog("Skipping casper UUID build due to low memory")

        #Load kexec kernel
        if self.kexec and os.path.exists(CDROM_MOUNT + '/misc/kexec'):
            with open('/proc/cmdline') as file:
                cmdline = file.readline().strip('\n').replace('dell-recovery/recovery_type=dvd','dell-recovery/recovery_type=factory').replace('dell-recovery/recovery_type=hdd','dell-recovery/recovery_type=factory')
                kexec_run = misc.execute_root(CDROM_MOUNT + '/misc/kexec',
                          '-l', '/boot/casper/vmlinuz',
                          '--initrd=/boot/casper/initrd.lz',
                          '--command-line="' + cmdline + '"')
                if kexec_run is False:
                    syslog.syslog("kexec loading of kernel and initrd failed")

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
    if os.path.exists('/tmp/kexec'):
        kexec = misc.execute_root('/tmp/kexec', '-e')
        if kexec is False:
            syslog.syslog("unable to kexec")

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

        to_install = []
        if os.path.isdir(CDROM_MOUNT + '/debs/main'):
            for file in os.listdir(CDROM_MOUNT + '/debs/main'):
                if '.deb' in file:
                    to_install.append(parse(os.path.join(CDROM_MOUNT + '/debs/main',file)))
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

        #Determine if we are doing OOBE
        try:
            if self.db.get('oem-config/enable') == 'true':
                self.enable_oem_config(target)
                #only install dell-recovery if we actually have an RP on the
                #system and will go through OOBE
                if rp:
                    to_install.append('dell-recovery')

        except debconf.DebconfError, e:
            pass

        #Fixup pool to only accept stuff on /cdrom
        #This is reversed at the end of OEM-config
        if os.path.exists(CDROM_MOUNT + '/scripts/pool.sh'):
            install_misc.chrex(target, CDROM_MOUNT + '/scripts/pool.sh')

        #These aren't in all images, but desirable if available
        to_install.append('dkms')
        to_install.append('adobe-flashplugin')

        #Stuff that is installed on all configs without fish scripts
        to_install += self.find_unconditional_debs()
        install_misc.record_installed(to_install)

        self.remove_ricoh_mmc()

        return InstallPlugin.install(self, target, progress, *args, **kwargs)

