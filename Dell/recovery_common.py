#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# «recovery_common» - Misc Functions and variables that are useful in many areas
#
# Copyright (C) 2009-2010, Dell Inc.
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

import dbus.mainloop.glib
import subprocess
import gobject
import os
import re
import tempfile
import glob
import sys

##                ##
##Common Variables##
##                ##

DBUS_BUS_NAME = 'com.dell.RecoveryMedia'
DBUS_INTERFACE_NAME = 'com.dell.RecoveryMedia'


#Translation Support
DOMAIN = 'dell-recovery'
LOCALEDIR = '/usr/share/locale'

#UI file directory
if os.path.isdir('gtk') and 'DEBUG' in os.environ:
    UIDIR = 'gtk'
else:
    UIDIR = '/usr/share/dell'


#Supported burners and their arguments
DVD_BURNERS = { 'brasero':['-i'],
               'nautilus-cd-burner':['--source-iso='] }
USB_BURNERS = { 'usb-creator':['-n', '--iso'],
                'usb-creator-gtk':['-n', '--iso'],
                'usb-creator-kde':['-n', '--iso'] }

if 'INTRANET' in os.environ:
    URL = "humbolt.us.dell.com/pub/linux.dell.com/srv/www/vhosts/linux.dell.com/html"
else:
    URL = "linux.dell.com"

GIT_TREES = { 'ubuntu': 'git://' + URL + '/ubuntu-fid.git',
              'redhat': 'http://humbolt.us.dell.com/pub/Applications/git-internal-projects/redhat-fid.git',
            }

#UP File names
UP_FILENAMES =  [ 'upimg.bin',
                  'upimg.gz' ,
                  'up.zip'   ,
                  'up.tgz'   ,
                ]

##                ##
##Common Functions##
##                ##
def white_tree(action, whitelist, src, dst='', base=None):
    """Recursively ACTIONs files from src to dest only
       when they match the whitelist outlined in whitelist"""
    from distutils.file_util import copy_file

    if base is None:
        base = src
        if not base.endswith('/'):
            base += '/'

    names = os.listdir(src)

    if action == "copy":
        outputs = []
    elif action == "size":
        outputs = 0

    for n in names:
        src_name = os.path.join(src, n)
        dst_name = os.path.join(dst, n)
        end = src_name.split(base)[1]

        #don't copy symlinks or hardlinks, vfat seems to hate them
        if os.path.islink(src_name):
            continue

        #recurse till we find FILES
        elif os.path.isdir(src_name):
            if action == "copy":
                outputs.extend(
                    white_tree(action, whitelist, src_name, dst_name, base))
            elif action == "size":
                #add the directory we're in
                outputs += os.path.getsize(src_name)
                #add the files in that directory
                outputs += white_tree(action, whitelist, src_name, dst_name, base)

        #only copy the file if it matches the whitelist
        elif whitelist.search(end):
            if action == "copy":
                if not os.path.isdir(dst):
                    os.makedirs(dst)
                copy_file(src_name, dst_name, preserve_mode=1,
                          preserve_times=1, update=1, dry_run=0)
                outputs.append(dst_name)

            elif action == "size":
                outputs += os.path.getsize(src_name)

    return outputs

def check_vendor():
    """Checks to make sure that the app is running on Dell HW"""
    if os.path.exists('/sys/class/dmi/id/bios_vendor'):
        with open('/sys/class/dmi/id/bios_vendor') as rfd:
            vendor = rfd.readline().split()[0].lower()
    else:
        vendor = ''
    return (vendor == 'dell')

def check_version():
    """Queries the package management system for the current tool version"""
    try:
        import apt.cache
        cache = apt.cache.Cache()
        if cache['dell-recovery'].is_installed:
            return cache['dell-recovery'].installed.version
    except Exception, msg:
        print >> sys.stderr, "Error checking dell-recovery version: %s" % msg
        return "unknown"

def process_conf_file(original, new, uuid, rp_number, dual_seed='', ako=''):
    """Replaces all instances of a partition, OS, and extra in a conf type file
       Generally used for things that need to touch grub"""
    if not os.path.isdir(os.path.split(new)[0]):
        os.makedirs(os.path.split(new)[0])
    import lsb_release
    release = lsb_release.get_distro_information()

    extra_cmdline = ako
    if extra_cmdline:
        #remove any duplicate entries
        ka_list = find_extra_kernel_options().split(' ')
        ako_list = extra_cmdline.split(' ')
        for var in ka_list:
            found = False
            for item in ako_list:
                left = item.split('=')[0].strip()
                if left and left in var:
                    found = True
            #propagate anything but BOOT_IMAGE (it gets added from isolinux)
            if not found and not 'BOOT_IMAGE' in var:
                extra_cmdline += ' ' + var
    else:
        extra_cmdline = find_extra_kernel_options()

    #starting with 10.10, we replace the whole drive string (/dev/sdX,msdosY)
    #earlier releases are hardcoded to (hd0,Y)
    if float(release["RELEASE"]) >= 10.10:
        rp_number = 'msdos' + rp_number

    with open(original, "r") as base:
        with open(new, 'w') as output:
            for line in base.readlines():
                if "#UUID#" in line:
                    line = line.replace("#UUID#", uuid)
                if "#PARTITION#" in line:
                    line = line.replace("#PARTITION#", rp_number)
                if "#OS#" in line:
                    line = line.replace("#OS#", "%s %s" % (release["ID"], release["RELEASE"]))
                if "#EXTRA#" in line:
                    line = line.replace("#EXTRA#", "%s" % extra_cmdline.strip())
                if '#DUAL#' in line:
                    line = line.replace("#DUAL#", "%s"  % dual_seed)
                output.write(line)

def fetch_output(cmd, data=None):
    '''Helper function to just read the output from a command'''
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,
                                 stdin=subprocess.PIPE)
    (out, err) = proc.communicate(data)
    if proc.returncode is None:
        proc.wait()
    if proc.returncode != 0:
        error = "Command %s failed with stdout/stderr: %s\n%s" % (cmd, out, err)
        syslog.syslog(error)
        raise RuntimeError, (error)
    return out

def find_supported_ui():
    """Finds potential user interfaces"""
    desktop = { 'ude'     : 'gnome.desktop',
                'une-efl' : 'une-efl.desktop',
                'une'     : 'une.desktop'}
    name =    { 'ude'     : 'Desktop Edition',
                'une'     : 'Netbook Edition',
                'une-efl' : 'Netbook Edition (2D)'}
    for item in desktop:
        if not os.path.exists(os.path.join('/usr/share/xsessions/', desktop[item])):
            name.pop(item)
    return name

def find_extra_kernel_options():
    """Finds any extra kernel command line options"""
    with open('/proc/cmdline', 'r') as cmdline:
        cmd = cmdline.readline().strip().split('--')
    if len(cmd) > 1:
        return cmd[1].strip()
    else:
        return ''

def find_factory_rp_stats():
    """Uses udisks to find the RP of a system and return stats on it
       Only use this method during bootstrap."""
    bus = dbus.SystemBus()
    recovery = {}
    udisk_bus_name = 'org.freedesktop.UDisks'
    dev_bus_name   = 'org.freedesktop.UDisks.Device'

    try:
        obj = bus.get_object(udisk_bus_name, '/org/freedesktop/UDisks')
        iface = dbus.Interface(obj, udisk_bus_name)
        devices = iface.EnumerateDevices()
        for check_label in ['RECOVERY', 'install', 'OS']:
            for device in devices:
                obj = bus.get_object(udisk_bus_name, device)
                dev = dbus.Interface(obj, 'org.freedesktop.DBus.Properties')

                if check_label == dev.Get(dev_bus_name, 'IdLabel'):
                    recovery["label" ] = check_label
                    recovery["device"] = dev.Get(dev_bus_name, 'DeviceFile')
                    recovery["fs"    ] = dev.Get(dev_bus_name, 'IdType')
                    recovery["slave" ] = dev.Get(dev_bus_name, 'PartitionSlave')
                    recovery["number"] = dev.Get(dev_bus_name, 'PartitionNumber')
                    recovery["parent"] = dev.Get(dev_bus_name, 'PartitionSlave')
                    recovery["uuid"]   = dev.Get(dev_bus_name, 'IdUuid')
                    parent_obj    = bus.get_object(udisk_bus_name, recovery["parent"])
                    parent_dev    = dbus.Interface(parent_obj, 'org.freedesktop.DBus.Properties')
                    recovery["size_gb"] = parent_dev.Get(dev_bus_name, 'DeviceSize') \
                                    / 1000000000
                    break
            if recovery:
                dev_obj = bus.get_object(udisk_bus_name, recovery["slave"])
                dev = dbus.Interface(dev_obj, 'org.freedesktop.DBus.Properties')
                recovery["slave"] = dev.Get(dev_bus_name, 'DeviceFile')
                break

    except dbus.DBusException, msg:
        print "%s, UDisks Failed" % str(msg)

    return recovery

def find_partitions(utility, recovery):
    """Searches the system for utility and recovery partitions"""
    bus = dbus.SystemBus()

    try:
        #first try to use udisks, if this fails, fall back to devkit-disks.
        obj = bus.get_object('org.freedesktop.UDisks', '/org/freedesktop/UDisks')
        iface = dbus.Interface(obj, 'org.freedesktop.UDisks')
        devices = iface.EnumerateDevices()
        for device in devices:
            dev_obj = bus.get_object('org.freedesktop.UDisks', device)
            dev = dbus.Interface(dev_obj, 'org.freedesktop.DBus.Properties')

            label = dev.Get('org.freedesktop.UDisks.Device', 'IdLabel')
            filesystem = dev.Get('org.freedesktop.Udisks.Device', 'IdType')

            if not utility and 'DellUtility' in label:
                utility = dev.Get('org.freedesktop.UDisks.Device', 'DeviceFile')
            elif not recovery and (('install' in label or 'OS' in label) and 'vfat' in filesystem) or \
                            ('RECOVERY' in label and 'ntfs' in filesystem):
                recovery = dev.Get('org.freedesktop.Udisks.Device', 'DeviceFile')
        return (utility, recovery)
    except dbus.DBusException, msg:
        print "%s, UDisks Failed" % str(msg)

    try:
        #next try to use devkit-disks. if this fails, then we can fall back to hal
        obj = bus.get_object('org.freedesktop.DeviceKit.Disks', '/org/freedesktop/DeviceKit/Disks')
        iface = dbus.Interface(obj, 'org.freedesktop.DeviceKit.Disks')
        devices = iface.EnumerateDevices()
        for device in devices:
            dev_obj = bus.get_object('org.freedesktop.DeviceKit.Disks', device)
            dev = dbus.Interface(dev_obj, 'org.freedesktop.DBus.Properties')

            label = dev.Get('org.freedesktop.DeviceKit.Disks.Device', 'id-label')
            filesystem = dev.Get('org.freedesktop.DeviceKit.Disks.Device', 'id-type')

            if not utility and 'DellUtility' in label:
                utility = dev.Get('org.freedesktop.DeviceKit.Disks.Device', 'device-file')
            elif not recovery and (('install' in label or 'OS' in label) and 'vfat' in filesystem) or \
                            ('RECOVERY' in label and 'ntfs' in filesystem):
                recovery = dev.Get('org.freedesktop.DeviceKit.Disks.Device', 'device-file')
        return (utility, recovery)

    except dbus.DBusException, msg:
        print "%s, DeviceKit-Disks Failed" % str(msg)

    try:
        obj = bus.get_object('org.freedesktop.Hal', '/org/freedesktop/Hal/Manager')
        iface = dbus.Interface(obj, 'org.freedesktop.Hal.Manager')
        devices = iface.FindDeviceByCapability('volume')

        for device in devices:
            dev_obj = bus.get_object('org.freedesktop.Hal', device)
            dev = dbus.Interface(dev_obj, 'org.freedesktop.Hal.Device')

            label = dev.GetProperty('volume.label')
            filesystem = dev.GetProperty('volume.fstype')
            if not utility and 'DellUtility' in label:
                utility = dev.GetProperty('block.device')
            elif not recovery and (('install' in label or 'OS' in label) and 'vfat' in filesystem) or \
                            ('RECOVERY' in label and 'ntfs' in filesystem):
                recovery = dev.GetProperty('block.device')
        return (utility, recovery)
    except dbus.DBusException, msg:
        print "%s, HAL Failed" % str(msg)

def find_burners():
    """Checks for what utilities are available to burn with"""
    def which(program):
        """Emulates the functionality of the unix which command"""
        def is_exe(fpath):
            """Determines if a filepath is executable"""
            return os.path.exists(fpath) and os.access(fpath, os.X_OK)

        fpath = os.path.split(program)[0]
        if fpath:
            if is_exe(program):
                return program
        else:
            for path in os.environ["PATH"].split(os.pathsep):
                exe_file = os.path.join(path, program)
                if is_exe(exe_file):
                    return exe_file

        return None

    def find_command(array):
        """Determines if a command listed in the array is valid"""
        for item in array:
            path = which(item)
            if path is not None:
                return [path] + array[item]
        return None

    dvd = find_command(DVD_BURNERS)
    usb = find_command(USB_BURNERS)

    #If we have apps for DVD burning, check hardware
    if dvd:
        found_supported_dvdr = False
        try:
            bus = dbus.SystemBus()
            #first try to use udisks, if this fails, fall back to devkit-disks.
            obj = bus.get_object('org.freedesktop.UDisks', '/org/freedesktop/UDisks')
            iface = dbus.Interface(obj, 'org.freedesktop.UDisks')
            devices = iface.EnumerateDevices()
            for device in devices:
                obj = bus.get_object('org.freedesktop.UDisks', device)
                dev = dbus.Interface(obj, 'org.freedesktop.DBus.Properties')

                supported_media = dev.Get('org.freedesktop.UDisks.Device', 'DriveMediaCompatibility')
                for item in supported_media:
                    if 'optical_dvd_r' in item:
                        found_supported_dvdr = True
                        break
                if found_supported_dvdr:
                    break
            if not found_supported_dvdr:
                dvd = None
            return (dvd, usb)
        except dbus.DBusException, msg:
            print "%s, UDisks Failed burner parse" % str(msg)
        try:
            #first try to use devkit-disks. if this fails, then, it's OK
            obj = bus.get_object('org.freedesktop.DeviceKit.Disks', '/org/freedesktop/DeviceKit/Disks')
            iface = dbus.Interface(obj, 'org.freedesktop.DeviceKit.Disks')
            devices = iface.EnumerateDevices()
            for device in devices:
                obj = bus.get_object('org.freedesktop.DeviceKit.Disks', device)
                dev = dbus.Interface(obj, 'org.freedesktop.DBus.Properties')

                supported_media = dev.Get('org.freedesktop.DeviceKit.Disks.Device', 'DriveMediaCompatibility')
                for item in supported_media:
                    if 'optical_dvd_r' in item:
                        found_supported_dvdr = True
                        break
                if found_supported_dvdr:
                    break
            if not found_supported_dvdr:
                dvd = None
        except dbus.DBusException, msg:
            print "%s, device kit Failed burner parse" % str(msg)

    return (dvd, usb)

def match_system_device(bus, vendor, device):
    '''Attempts to match the vendor and device combination  on the specified bus
       Allows the following formats:
       base 16 int (eg 0x1234)
       base 16 int in a str (eg '0x1234')
    '''
    def recursive_check_ids(directory, cvendor, cdevice, depth=1):
        """Recurses into a directory to check all files in that directory"""
        vendor = device = ''
        for root, dirs, files in os.walk(directory, topdown=True):
            for fname in files:
                if not vendor and (fname == 'idVendor' or fname == 'vendor'):
                    with open(os.path.join(root, fname), 'r') as filehandle:
                        vendor = filehandle.readline().strip('\n')
                    if len(vendor) > 4 and '0x' not in vendor:
                        vendor = ''
                elif not device and (fname == 'idProduct' or fname == 'device'):
                    with open(os.path.join(root, fname), 'r') as filehandle:
                        device = filehandle.readline().strip('\n')
                    if len(device) > 4 and '0x' not in device:
                        device = ''
            if vendor and device:
                if ( int(vendor, 16) == int(cvendor)) and \
                   ( int(device, 16) == int(cdevice)) :
                    return True
                else:
                    #reset devices so they aren't checked multiple times needlessly
                    vendor = device = ''
            if not files:
                if depth > 0:
                    for directory in [os.path.join(root, d) for d in dirs]:
                        if recursive_check_ids(directory, cvendor, cdevice, depth-1):
                            return True
        return False

    if bus != "usb" and bus != "pci":
        return False

    if type(vendor) == str and '0x' in vendor:
        vendor = int(vendor, 16)
    if type(device) == str and '0x' in device:
        device = int(device, 16)

    return recursive_check_ids('/sys/bus/%s/devices' % bus, vendor, device)

def increment_bto_version(version):
    """Increments the BTO version"""
    match = re.match(r"(?:(?P<alpha1>\w+\.[a-z]*)(?P<digits>\d+))"
                     r"|(?P<alpha2>\w+(?:\.[a-z]+)?)",
                     version, re.I)

    if match:
        if match.group('digits'):
            version = "%s%d" % (match.group('alpha1'),
                              int(match.group('digits'))+1)
        else:
            if '.' in match.group('alpha2'):
                version = "%s1" % match.group('alpha2')
            else:
                version = "%s.1" % match.group('alpha2')
    else:
        return 'A00'

    return version

def walk_cleanup(directory):
    """Walks a directory, removes all files, and removes that directory"""
    if os.path.exists(directory):
        for root, dirs, files in os.walk(directory, topdown=False):
            for name in files:
                os.remove(os.path.join(root, name))
            for name in dirs:
                full_name = os.path.join(root, name)
                if os.path.islink(full_name):
                    os.remove(full_name)
                elif os.path.isdir(full_name):
                    os.rmdir(full_name)
                #covers broken links
                else:
                    os.remove(full_name)
        os.rmdir(directory)

def create_new_uuid(old_initrd_directory, old_casper_directory,
                    new_initrd_directory, new_casper_directory,
                    new_compression="auto",
                    include_bootstrap=False):
    """ Regenerates the UUID contained in a casper initramfs
        Supported compression types:
        * auto (auto detects lzma/gzip)
        * lzma
        * gzip
        * None
        Returns full path of the old initrd and casper files (for blacklisting)
    """
    tmpdir = tempfile.mkdtemp()

    #Detect the old initramfs stuff
    try:
        old_initrd_file = glob.glob('%s/initrd*' % old_initrd_directory)[0]
    except Exception, msg:
        print str(msg)
        raise dbus.DBusException, ("Missing initrd in image.")
    try:
        old_uuid_file   = glob.glob('%s/casper-uuid*' % old_casper_directory)[0]
    except Exception, msg:
        print str(msg)
        raise dbus.DBusException, ("Missing casper UUID in image.")

    print "Old initrd: %s" % old_initrd_file
    print "Old uuid file: %s" % old_uuid_file

    old_suffix = ''
    if len(old_initrd_file.split('.')) > 1:
        old_suffix = old_initrd_file.split('.')[1]

    old_compression = ''
    if old_suffix == "lz":
        old_compression = "lzma"
    elif old_suffix == "gz":
        old_compression = "gzip"
    print "Old suffix: %s" % old_suffix
    print "Old compression method: %s" % old_compression

    #Extract old initramfs
    chain0 = subprocess.Popen([old_compression, '-cd', old_initrd_file, '-S',
                               old_suffix], stdout=subprocess.PIPE)
    chain1 = subprocess.Popen(['cpio', '-id'], stdin=chain0.stdout, cwd=tmpdir)
    chain1.communicate()

    #Generate new UUID
    new_uuid_file = os.path.join(new_casper_directory,
                                 os.path.basename(old_uuid_file))
    print "New uuid file: %s" % new_uuid_file
    chain0 = subprocess.Popen(['uuidgen', '-r'], stdout=subprocess.PIPE)
    new_uuid = chain0.communicate()[0]
    print "New UUID: %s" % new_uuid.strip()
    for item in [new_uuid_file, os.path.join(tmpdir, 'conf', 'uuid.conf')]:
        with open(item, "w") as uuid_fd:
            uuid_fd.write(new_uuid)

    #Newer (Ubuntu 11.04+) images may support including the bootstrap in initrd
    if include_bootstrap:
        chain0 = subprocess.Popen(['/usr/share/dell/casper/hooks/dell-bootstrap'], env={'DESTDIR': tmpdir})
        chain0.communicate()    

    #Detect compression
    new_suffix = ''
    if new_compression == "gzip":
        new_suffix = '.gz'
    elif new_compression == 'lzma':
        new_suffix = '.lz'
    elif new_compression == "auto":
        new_compression = old_compression
        new_suffix = '.' + old_suffix
    print "New suffix: %s" % new_suffix
    print "New compression method: %s" % new_compression

    #Generate new initramfs
    new_initrd_file = os.path.join(new_initrd_directory, 'initrd' + new_suffix)
    print "New initrd file: %s" % new_initrd_file
    chain0 = subprocess.Popen(['find'], cwd=tmpdir, stdout=subprocess.PIPE)
    chain1 = subprocess.Popen(['cpio', '--quiet', '--dereference', '-o',
                               '-H', 'newc'],
                               cwd=tmpdir, stdin=chain0.stdout,
                               stdout=subprocess.PIPE)
    with open(new_initrd_file, 'w') as initrd_fd:
        if new_compression:
            chain2 = subprocess.Popen([new_compression, '-9c'],
                                      stdin=chain1.stdout,
                                      stdout=subprocess.PIPE)
            initrd_fd.write(chain2.communicate()[0])
        else:
            initrd_fd.write(chain1.communicate()[0])
    walk_cleanup(tmpdir)

    return (old_initrd_file, old_uuid_file)

def dbus_sync_call_signal_wrapper(dbus_iface, func, handler_map, *args, **kwargs):
    '''Run a D-BUS method call while receiving signals.

    This function is an Ugly Hack™, since a normal synchronous dbus_iface.fn()
    call does not cause signals to be received until the method returns. Thus
    it calls func asynchronously and sets up a temporary main loop to receive
    signals and call their handlers; these are assigned in handler_map (signal
    name → signal handler).
    '''
    if not hasattr(dbus_iface, 'connect_to_signal'):
        # not a D-BUS object
        return getattr(dbus_iface, func)(*args, **kwargs)

    def _h_reply(result=None):
        """protected method to send a reply"""
        global _h_reply_result
        _h_reply_result = result
        loop.quit()

    def _h_error(exception=None):
        """protected method to send an error"""
        global _h_exception_exc
        _h_exception_exc = exception
        loop.quit()

    loop = gobject.MainLoop()
    global _h_reply_result, _h_exception_exc
    _h_reply_result = None
    _h_exception_exc = None
    kwargs['reply_handler'] = _h_reply
    kwargs['error_handler'] = _h_error
    kwargs['timeout'] = 86400
    for signame, sighandler in handler_map.iteritems():
        dbus_iface.connect_to_signal(signame, sighandler)
    dbus_iface.get_dbus_method(func)(*args, **kwargs)
    loop.run()
    if _h_exception_exc:
        raise _h_exception_exc
    return _h_reply_result


##                ##
## Common Classes ##
##                ##

class RestoreFailed(dbus.DBusException):
    """Exception Raised if the restoration process failed for any reason"""
    _dbus_error_name = 'com.dell.RecoveryMedia.RestoreFailedException'

class CreateFailed(dbus.DBusException):
    """Exception Raised if the media creation process failed for any reason"""
    _dbus_error_name = 'com.dell.RecoveryMedia.CreateFailedException'

class PermissionDeniedByPolicy(dbus.DBusException):
    """Exception Raised if policy kit denied the user access"""
    _dbus_error_name = 'com.dell.RecoveryMedia.PermissionDeniedByPolicy'

class BackendCrashError(SystemError):
    """Exception Raised if the backend crashes"""
    pass
