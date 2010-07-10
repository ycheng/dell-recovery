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

##                ##
##Common Variables##
##                ##

DBUS_BUS_NAME = 'com.dell.RecoveryMedia'
DBUS_INTERFACE_NAME = 'com.dell.RecoveryMedia'


#Translation Support
domain='dell-recovery'
LOCALEDIR='/usr/share/locale'

#UI file directory
if os.path.isdir('gtk') and 'DEBUG' in os.environ:
    UIDIR= 'gtk'
else:
    UIDIR = '/usr/share/dell'


#Supported burners and their arguments
dvd_burners = { 'brasero':['-i'],
               'nautilus-cd-burner':['--source-iso='] }
usb_burners = { 'usb-creator':['-n','--iso'],
                'usb-creator-gtk':['-n','--iso'],
                'usb-creator-kde':['-n','--iso'] }

if 'INTRANET' in os.environ:
    url="humbolt.us.dell.com/pub/linux.dell.com/srv/www/vhosts/linux.dell.com/html"
else:
    url="linux.dell.com"

git_trees = { 'ubuntu': 'git://' + url + '/ubuntu-fid.git',
              'redhat': 'http://humbolt.us.dell.com/pub/Applications/git-internal-projects/redhat-fid.git',
            }

#UP File names
up_filenames =  [ 'upimg.bin',
                  'upimg.gz' ,
                  'up.zip'   ,
                  'up.tgz'   ,
                ]

##                ##
##Common Functions##
##                ##
def white_tree(action,whitelist,src,dst='',base=None):
    """Recursively ACTIONs files from src to dest only
       when they match the whitelist outlined in whitelist"""
    from distutils.file_util import copy_file
    from distutils.dir_util import mkpath

    if base is None:
        base=src
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
        end=src_name.split(base)[1]

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
    if os.path.exists('/sys/class/dmi/id/bios_vendor'):
        with open('/sys/class/dmi/id/bios_vendor') as file:
            vendor = file.readline().split()[0].lower()
    else:
        vendor = ''
    return (vendor == 'dell')

def check_version():
    """Returns the currently installed version of the tool"""
    try:
        import apt.cache
        cache = apt.cache.Cache()
        if cache['dell-recovery'].is_installed:
            return cache['dell-recovery'].installed.version
    except Exception, e:
        return "unknown"

def process_conf_file(original, new, uuid, rp_number, dual_seed=''):
    """Replaces all instances of a partition, OS, and extra in a conf type file
       Generally used for things that need to touch grub"""
    if not os.path.isdir(os.path.split(new)[0]):
        os.makedirs(os.path.split(new)[0])
    import lsb_release
    release = lsb_release.get_distro_information()
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
                    line = line.replace("#EXTRA#", "%s" % extra_cmdline)
                if '#DUAL#' in line:
                    line = line.replace("#DUAL#", "%s"  % dual_seed)
                output.write(line)

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
    rp = {}

    try:
        udisk_obj = bus.get_object('org.freedesktop.UDisks', '/org/freedesktop/UDisks')
        ud = dbus.Interface(udisk_obj, 'org.freedesktop.UDisks')
        devices = ud.EnumerateDevices()
        for check_label in ['RECOVERY', 'install', 'OS']:
            for device in devices:
                dev_obj = bus.get_object('org.freedesktop.UDisks', device)
                dev = dbus.Interface(dev_obj, 'org.freedesktop.DBus.Properties')

                if check_label == dev.Get('org.freedesktop.UDisks.Device','IdLabel'):
                    rp["label" ] = check_label
                    rp["device"] = dev.Get('org.freedesktop.Udisks.Device','DeviceFile')
                    rp["fs"    ] = dev.Get('org.freedesktop.Udisks.Device','IdType')
                    rp["slave" ] = dev.Get('org.freedesktop.Udisks.Device','PartitionSlave')
                    rp["number"] = dev.Get('org.freedesktop.Udisks.Device','PartitionNumber')
                    rp["parent"] = dev.Get('org.freedesktop.Udisks.Device','PartitionSlave')
                    rp["uuid"]   = dev.Get('org.freedesktop.Udisks.Device','IdUuid')
                    parent_obj   = bus.get_object('org.freedesktop.UDisks', rp["parent"])
                    parent_dev   = dbus.Interface(parent_obj, 'org.freedesktop.DBus.Properties')
                    rp["size_gb"]= parent_dev.Get('org.freedesktop.Udisks.Device','DeviceSize') / 1000000000
                    break
            if rp:
                dev_obj = bus.get_object('org.freedesktop.UDisks', rp["slave"])
                dev = dbus.Interface(dev_obj, 'org.freedesktop.DBus.Properties')
                rp["slave"] = dev.Get('org.freedesktop.Udisks.Device','DeviceFile')
                break

    except dbus.DBusException, e:
        print "%s, UDisks Failed" % str(e)

    return rp

def find_partitions(up,rp):
    """Searches the system for utility and recovery partitions"""
    bus = dbus.SystemBus()

    try:
        #first try to use udisks, if this fails, fall back to devkit-disks.
        udisk_obj = bus.get_object('org.freedesktop.UDisks', '/org/freedesktop/UDisks')
        ud = dbus.Interface(udisk_obj, 'org.freedesktop.UDisks')
        devices = ud.EnumerateDevices()
        for device in devices:
            dev_obj = bus.get_object('org.freedesktop.UDisks', device)
            dev = dbus.Interface(dev_obj, 'org.freedesktop.DBus.Properties')

            label = dev.Get('org.freedesktop.UDisks.Device','IdLabel')
            fs = dev.Get('org.freedesktop.Udisks.Device','IdType')

            if not up and 'DellUtility' in label:
                up=dev.Get('org.freedesktop.UDisks.Device','DeviceFile')
            elif not rp and (('install' in label or 'OS' in label) and 'vfat' in fs) or \
                            ('RECOVERY' in label and 'ntfs' in fs):
                rp=dev.Get('org.freedesktop.Udisks.Device','DeviceFile')
        return (up,rp)
    except dbus.DBusException, e:
        print "%s, UDisks Failed" % str(e)

    try:
        #next try to use devkit-disks. if this fails, then we can fall back to hal
        dk_obj = bus.get_object('org.freedesktop.DeviceKit.Disks', '/org/freedesktop/DeviceKit/Disks')
        dk = dbus.Interface(dk_obj, 'org.freedesktop.DeviceKit.Disks')
        devices = dk.EnumerateDevices()
        for device in devices:
            dev_obj = bus.get_object('org.freedesktop.DeviceKit.Disks', device)
            dev = dbus.Interface(dev_obj, 'org.freedesktop.DBus.Properties')

            label = dev.Get('org.freedesktop.DeviceKit.Disks.Device','id-label')
            fs = dev.Get('org.freedesktop.DeviceKit.Disks.Device','id-type')

            if not up and 'DellUtility' in label:
                up=dev.Get('org.freedesktop.DeviceKit.Disks.Device','device-file')
            elif not rp and (('install' in label or 'OS' in label) and 'vfat' in fs) or \
                            ('RECOVERY' in label and 'ntfs' in fs):
                rp=dev.Get('org.freedesktop.DeviceKit.Disks.Device','device-file')
        return (up,rp)

    except dbus.DBusException, e:
        print "%s, DeviceKit-Disks Failed" % str(e)

    try:
        hal_obj = bus.get_object('org.freedesktop.Hal', '/org/freedesktop/Hal/Manager')
        hal = dbus.Interface(hal_obj, 'org.freedesktop.Hal.Manager')
        devices = hal.FindDeviceByCapability('volume')

        for device in devices:
            dev_obj = bus.get_object('org.freedesktop.Hal', device)
            dev = dbus.Interface(dev_obj, 'org.freedesktop.Hal.Device')

            label = dev.GetProperty('volume.label')
            fs = dev.GetProperty('volume.fstype')
            if not up and 'DellUtility' in label:
                up=dev.GetProperty('block.device')
            elif not rp and (('install' in label or 'OS' in label) and 'vfat' in fs) or \
                            ('RECOVERY' in label and 'ntfs' in fs):
                rp=dev.GetProperty('block.device')
        return (up,rp)
    except dbus.DBusException, e:
        print "%s, HAL Failed" % str(e)

def find_burners():
    """Checks for what utilities are available to burn with"""
    def which(program):
        import os
        def is_exe(fpath):
            return os.path.exists(fpath) and os.access(fpath, os.X_OK)

        fpath, fname = os.path.split(program)
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
        for item in array:
            path=which(item)
            if path is not None:
                return [path] + array[item]
        return None

    dvd = find_command(dvd_burners)
    usb = find_command(usb_burners)

    #If we have apps for DVD burning, check hardware
    if dvd:
        found_supported_dvdr = False
        try:
            bus = dbus.SystemBus()
            #first try to use udisks, if this fails, fall back to devkit-disks.
            udisk_obj = bus.get_object('org.freedesktop.UDisks', '/org/freedesktop/UDisks')
            ud = dbus.Interface(udisk_obj, 'org.freedesktop.UDisks')
            devices = ud.EnumerateDevices()
            for device in devices:
                dev_obj = bus.get_object('org.freedesktop.UDisks', device)
                dev = dbus.Interface(dev_obj, 'org.freedesktop.DBus.Properties')

                supported_media = dev.Get('org.freedesktop.UDisks.Device','DriveMediaCompatibility')
                for item in supported_media:
                    if 'optical_dvd_r' in item:
                        found_supported_dvdr = True
                        break
                if found_supported_dvdr:
                    break
            if not found_supported_dvdr:
                dvd = None
            return (dvd,usb)
        except dbus.DBusException, e:
            print "%s, UDisks Failed burner parse" % str(e)
        try:
            #first try to use devkit-disks. if this fails, then, it's OK
            dk_obj = bus.get_object('org.freedesktop.DeviceKit.Disks', '/org/freedesktop/DeviceKit/Disks')
            dk = dbus.Interface(dk_obj, 'org.freedesktop.DeviceKit.Disks')
            devices = dk.EnumerateDevices()
            for device in devices:
                dev_obj = bus.get_object('org.freedesktop.DeviceKit.Disks', device)
                dev = dbus.Interface(dev_obj, 'org.freedesktop.DBus.Properties')

                supported_media = dev.Get('org.freedesktop.DeviceKit.Disks.Device','DriveMediaCompatibility')
                for item in supported_media:
                    if 'optical_dvd_r' in item:
                        found_supported_dvdr = True
                        break
                if found_supported_dvdr:
                    break
            if not found_supported_dvdr:
                dvd = None
        except dbus.DBusException, e:
            print "%s, device kit Failed burner parse" % str(e)

    return (dvd,usb)

def match_system_device(bus, vendor, device):
    '''Attempts to match the vendor and device combination to the system on the specified bus
       Allows the following formats:
       base 16 int (eg 0x1234)
       base 16 int in a str (eg '0x1234')
    '''
    def recursive_check_ids(directory, check_vendor, check_device, depth=1):
        vendor = device = ''
        for root, dirs, files in os.walk(directory, topdown=True):
            for file in files:
                if not vendor and (file == 'idVendor' or file == 'vendor'):
                    with open(os.path.join(root,file),'r') as filehandle:
                        vendor = filehandle.readline().strip('\n')
                    if len(vendor) > 4 and '0x' not in vendor:
                        vendor = ''
                elif not device and (file == 'idProduct' or file == 'device'):
                    with open(os.path.join(root,file),'r') as filehandle:
                        device = filehandle.readline().strip('\n')
                    if len(device) > 4 and '0x' not in device:
                        device = ''
            if vendor and device:
                if ( int(vendor,16) == int(check_vendor)) and \
                   ( int(device,16) == int(check_device)) :
                   return True
                else:
                    #reset devices so they aren't checked multiple times needlessly
                    vendor = device = ''
            if not files:
                if depth > 0:
                    for dir in [os.path.join(root, d) for d in dirs]:
                        if recursive_check_ids(dir, check_vendor, check_device, depth-1):
                            return True
        return False

    if bus != "usb" and bus != "pci":
        return False

    if type(vendor) == str and '0x' in vendor:
        vendor = int(vendor,16)
    if type(device) == str and '0x' in device:
        device = int(device,16)

    return recursive_check_ids('/sys/bus/%s/devices' % bus, vendor, device)

def increment_bto_version(version):
    match = re.match(r"(?:(?P<alpha1>\w+\.[a-z]*)(?P<digits>\d+))"
                     r"|(?P<alpha2>\w+(?:\.[a-z]+)?)",
                     version, re.I)

    if match:
        if match.group('digits'):
            version="%s%d" % (match.group('alpha1'),
                              int(match.group('digits'))+1)
        else:
            if '.' in match.group('alpha2'):
                version="%s1" % match.group('alpha2')
            else:
                version="%s.1" % match.group('alpha2')
    else:
        return 'A00'

    return version

def walk_cleanup(directory):
    if os.path.exists(directory):
        for root,dirs,files in os.walk(directory, topdown=False):
            for name in files:
                os.remove(os.path.join(root,name))
            for name in dirs:
                full_name=os.path.join(root,name)
                if os.path.islink(full_name):
                    os.remove(full_name)
                elif os.path.isdir(full_name):
                    os.rmdir(full_name)
                #covers broken links
                else:
                    os.remove(full_name)
        os.rmdir(directory)

def create_new_uuid(old_initrd_directory, old_casper_directory, new_initrd_directory, new_casper_directory, new_compression="auto"):
    """ Regenerates the UUID contained in a casper initramfs using a particular compression
        Supported compression types:
        * auto (auto detects lzma/gzip)
        * lzma
        * gzip
        * None

        Returns the full path of the old initrd and casper files (for blacklisting)
    """
    tmpdir=tempfile.mkdtemp()

    #Detect the old initramfs stuff
    try:
        old_initrd_file = glob.glob('%s/initrd*' % old_initrd_directory)[0]
    except Exception, e:
        print str(e)
        raise dbus.DBusException,("Missing initrd in image.")
    try:
        old_uuid_file   = glob.glob('%s/casper-uuid*' % old_casper_directory)[0]
    except Exception, e:
        print str(e)
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
    chain0 = subprocess.Popen([old_compression, '-cd', old_initrd_file, '-S', old_suffix], stdout=subprocess.PIPE)
    chain1 = subprocess.Popen(['cpio', '-id'], stdin=chain0.stdout, cwd=tmpdir)
    chain1.communicate()

    #Generate new UUID
    new_uuid_file = os.path.join(new_casper_directory, os.path.basename(old_uuid_file))
    print "New uuid file: %s" % new_uuid_file
    chain0 = subprocess.Popen(['uuidgen', '-r'], stdout=subprocess.PIPE)
    new_uuid = chain0.communicate()[0]
    print "New UUID: %s" % new_uuid.strip()
    for item in [new_uuid_file, os.path.join(tmpdir, 'conf', 'uuid.conf')]:
        with open(item, "w") as uuid_fd:
            uuid_fd.write(new_uuid)

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
    chain1 = subprocess.Popen(['cpio', '--quiet', '--dereference', '-o', '-H', 'newc'], cwd=tmpdir, stdin=chain0.stdout, stdout=subprocess.PIPE)
    with open(new_initrd_file, 'w') as initrd_fd:
        if new_compression:
            chain2 = subprocess.Popen([new_compression, '-9c'], stdin=chain1.stdout, stdout=subprocess.PIPE)
            initrd_fd.write(chain2.communicate()[0])
        else:
            initrd_fd.write(chain1.communicate()[0])
    walk_cleanup(tmpdir)

    return (old_initrd_file, old_uuid_file)

def dbus_sync_call_signal_wrapper(dbus_iface, fn, handler_map, *args, **kwargs):
    '''Run a D-BUS method call while receiving signals.

    This function is an Ugly Hack™, since a normal synchronous dbus_iface.fn()
    call does not cause signals to be received until the method returns. Thus
    it calls fn asynchronously and sets up a temporary main loop to receive
    signals and call their handlers; these are assigned in handler_map (signal
    name → signal handler).
    '''
    if not hasattr(dbus_iface, 'connect_to_signal'):
        # not a D-BUS object
        return getattr(dbus_iface, fn)(*args, **kwargs)

    def _h_reply(result=None):
        global _h_reply_result
        _h_reply_result = result
        loop.quit()

    def _h_error(exception=None):
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
    dbus_iface.get_dbus_method(fn)(*args, **kwargs)
    loop.run()
    if _h_exception_exc:
        raise _h_exception_exc
    return _h_reply_result


##                ##
## Common Classes ##
##                ##

class CreateFailed(dbus.DBusException):
    _dbus_error_name = 'com.dell.RecoveryMedia.CreateFailedException'

class PermissionDeniedByPolicy(dbus.DBusException):
    _dbus_error_name = 'com.dell.RecoveryMedia.PermissionDeniedByPolicy'

class BackendCrashError(SystemError):
    pass
