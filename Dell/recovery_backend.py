#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# «recovery_backend» - Backend Manager.  Handles backend service calls
#
# Copyright (C) 2009, Dell Inc.
#           (C) 2008 Canonical Ltd.
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

import logging, os, os.path, signal, sys, re

import gobject
import dbus
import dbus.service
import dbus.mainloop.glib
from threading import Thread, Event

import atexit
import tempfile
import subprocess
import tarfile
import shutil
import datetime
import distutils.dir_util
import stat
import zipfile

from Dell.recovery_common import (DOMAIN, LOCALEDIR, UP_FILENAMES,
                                  walk_cleanup, create_new_uuid, white_tree,
                                  DBUS_BUS_NAME, DBUS_INTERFACE_NAME,
                                  RestoreFailed, CreateFailed,
                                  PermissionDeniedByPolicy)

#Translation support
from gettext import gettext as _
from gettext import bindtextdomain, textdomain


#--------------------------------------------------------------------#
#Borrowed from USB-Creator initially
class ProgressBySize(Thread):
    """Used for emitting progress for subcalls that don't nicely use stdout'"""
    def __init__(self, input_str, device, to_write):
        Thread.__init__(self)
        self._stopevent = Event()
        self.str = input_str
        self.to_write = to_write
        self.device = device
        statvfs = os.statvfs(device)
        self.start_free = statvfs.f_bsize * statvfs.f_bavail

    def progress(self, input_str, percent):
        """Function intended to be overridden to the correct external function
        """
        pass

    def run(self):
        """Runs the thread"""
        try:
            while not self._stopevent.isSet():
                statvfs = os.statvfs(self.device)
                free = statvfs.f_bsize * statvfs.f_bavail
                written = self.start_free - free
                veecent = int((written / float(self.to_write)) * 100)
                if callable(self.progress):
                    self.progress(self.str, veecent)
                self._stopevent.wait(2)
        except Exception:
            logging.exception('Could not update progress:')

    def join(self, timeout=None):
        """Stops the thread"""
        self._stopevent.set()
        Thread.join(self, timeout)

class ProgressByPulse(Thread):
    """Used for emitting the thought of progress for subcalls that don't show
       anything'"""
    def __init__(self, input_str):
        Thread.__init__(self)
        self._stopevent = Event()
        self.str = input_str

    def progress(self, input_str, percent):
        """Function intended to be overridden to the correct external function
        """
        pass

    def run(self):
        """Runs the thread"""
        try:
            while not self._stopevent.isSet():
                if callable(self.progress):
                    self.progress(self.str, "-1")
                self._stopevent.wait(.5)
        except Exception:
            logging.exception('Could not update progress:')

    def join(self, timeout=None):
        """Stops the thread"""
        self._stopevent.set()
        Thread.join(self, timeout)

#--------------------------------------------------------------------#

class Backend(dbus.service.Object):
    '''Backend manager.

    This encapsulates all services calls of the backend. It
    is implemented as a dbus.service.Object, so that it can be called through
    D-BUS as well (on the /RecoveryMedia object path).
    '''

    #
    # D-BUS control API
    #

    def __init__(self):
        dbus.service.Object.__init__(self)

        #initialize variables that will be used during create and run
        self.bus = None
        self.main_loop = None
        self._timeout = False
        self.dbus_name = None
        
        # cached D-BUS interfaces for _check_polkit_privilege()
        self.dbus_info = None
        self.polkit = None
        self.progress_thread = None
        self.enforce_polkit = True

        #Enable translation for strings used
        bindtextdomain(DOMAIN, LOCALEDIR)
        textdomain(DOMAIN)

    def run_dbus_service(self, timeout=None, send_usr1=False):
        '''Run D-BUS server.

        If no timeout is given, the server will run forever, otherwise it will
        return after the specified number of seconds.

        If send_usr1 is True, this will send a SIGUSR1 to the parent process
        once the server is ready to take requests.
        '''
        dbus.service.Object.__init__(self, self.bus, '/RecoveryMedia')
        self.main_loop = gobject.MainLoop()
        self._timeout = False
        if timeout:
            def _quit():
                """This function is ran at the end of timeout"""
                self.main_loop.quit()
                return True
            gobject.timeout_add(timeout * 1000, _quit)

        # send parent process a signal that we are ready now
        if send_usr1:
            os.kill(os.getppid(), signal.SIGUSR1)

        # run until we time out
        while not self._timeout:
            if timeout:
                self._timeout = True
            self.main_loop.run()

    @classmethod
    def create_dbus_server(cls, session_bus=False):
        '''Return a D-BUS server backend instance.

        Normally this connects to the system bus. Set session_bus to True to
        connect to the session bus (for testing).

        '''
        backend = Backend()
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        if session_bus:
            backend.bus = dbus.SessionBus()
            backend.enforce_polkit = False
        else:
            backend.bus = dbus.SystemBus()
        try:
            backend.dbus_name = dbus.service.BusName(DBUS_BUS_NAME, backend.bus)
        except dbus.exceptions.DBusException, msg:
            logging.error("Exception when spawning dbus service")
            logging.error(msg)
            return None
        return backend

    #
    # Internal methods
    #

    def _reset_timeout(self):
        '''Reset the D-BUS server timeout.'''

        self._timeout = False

    def _check_polkit_privilege(self, sender, conn, privilege):
        '''Verify that sender has a given PolicyKit privilege.

        sender is the sender's (private) D-BUS name, such as ":1:42"
        (sender_keyword in @dbus.service.methods). conn is
        the dbus.Connection object (connection_keyword in
        @dbus.service.methods). privilege is the PolicyKit privilege string.

        This method returns if the caller is privileged, and otherwise throws a
        PermissionDeniedByPolicy exception.
        '''
        if sender is None and conn is None:
            # called locally, not through D-BUS
            return
        if not self.enforce_polkit:
            # that happens for testing purposes when running on the session
            # bus, and it does not make sense to restrict operations here
            return

        # get peer PID
        if self.dbus_info is None:
            self.dbus_info = dbus.Interface(conn.get_object('org.freedesktop.DBus',
                '/org/freedesktop/DBus/Bus', False), 'org.freedesktop.DBus')
        pid = self.dbus_info.GetConnectionUnixProcessID(sender)

        # query PolicyKit
        if self.polkit is None:
            self.polkit = dbus.Interface(dbus.SystemBus().get_object(
                'org.freedesktop.PolicyKit1', '/org/freedesktop/PolicyKit1/Authority', False),
                'org.freedesktop.PolicyKit1.Authority')
        try:
            # we don't need is_challenge return here, since we call with AllowUserInteraction
            (is_auth, unused, details) = self.polkit.CheckAuthorization(
                    ('unix-process', {'pid': dbus.UInt32(pid, variant_level=1),
                        'start-time': dbus.UInt64(0, variant_level=1)}),
                    privilege, {'': ''}, dbus.UInt32(1), '', timeout=600)
        except dbus.DBusException, msg:
            if msg.get_dbus_name() == \
                                    'org.freedesktop.DBus.Error.ServiceUnknown':
                # polkitd timed out, connect again
                self.polkit = None
                return self._check_polkit_privilege(sender, conn, privilege)
            else:
                raise

        if not is_auth:
            logging.debug('_check_polkit_privilege: sender %s on connection %s pid %i is not authorized for %s: %s',
                    sender, conn, pid, privilege, str(details))
            raise PermissionDeniedByPolicy(privilege)

    #
    # Internal API for calling from Handlers (not exported through D-BUS)
    #

    def request_mount(self, recovery, sender=None, conn=None):
        '''Attempts to mount the recovery partition

           If successful, return mntdir.
           If we find that it's already mounted elsewhere, return that mount
           If unsuccessful, return an empty string
        '''
        #Work around issues sending a UTF-8 directory over dbus
        recovery = recovery.encode('utf8')

        #In this is just a directory
        if os.path.isdir(recovery):
            return recovery

        #check for an existing mount
        command = subprocess.Popen(['mount'], stdout=subprocess.PIPE)
        output = command.communicate()[0].split('\n')
        for line in output:
            processed_line = line.split()
            if len(processed_line) > 0 and processed_line[0] == recovery:
                return processed_line[2]

        #if not already, mounted, produce a mount point
        mntdir = tempfile.mkdtemp()
        mnt_args = ['mount', '-r', recovery, mntdir]
        if ".iso" in recovery:
            mnt_args.insert(1, 'loop')
            mnt_args.insert(1, '-o')
        else:
            self._check_polkit_privilege(sender, conn,
                                                'com.dell.recoverymedia.create')
        command = subprocess.Popen(mnt_args,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE)
        output = command.communicate()
        ret = command.wait()
        if ret is not 0:
            os.rmdir(mntdir)
            if ret == 32:
                try:
                    mntdir = output[1].strip('\n').split('on')[1].strip(' ')
                except IndexError:
                    mntdir = ''
                    logging.warning("IndexError when operating on output string")
            else:
                mntdir = ''
                logging.warning("Unable to mount recovery partition")
                logging.warning(output)
        else:
            atexit.register(self._unmount_drive, mntdir)
        return mntdir

    def _unmount_drive(self, mnt):
        """Unmounts something mounted at a particular mount point"""
        if os.path.exists(mnt):
            ret = subprocess.call(['umount', mnt])
            if ret is not 0:
                print >> sys.stderr, "Error unmounting %s" % mnt
            try:
                os.rmdir(mnt)
            except OSError, msg:
                print >> sys.stderr, "Error cleaning up: %s" % str(msg)

    def start_sizable_progress_thread(self, input_str, mnt, w_size):
        """Initializes the extra progress thread, or resets it
           if it already exists'"""
        self.progress_thread = ProgressBySize(input_str, mnt, w_size)
        self.progress_thread.progress = self.report_progress
        self.progress_thread.start()

    def stop_progress_thread(self):
        """Stops the extra thread for reporting progress"""
        self.progress_thread.join()

    def start_pulsable_progress_thread(self, input_str):
        """Starts the extra thread for pulsing progress in the UI"""
        self.progress_thread = ProgressByPulse(input_str)
        self.progress_thread.progress = self.report_progress
        self.progress_thread.start()
    #
    # Client API (through D-BUS)
    #
    @dbus.service.method(DBUS_INTERFACE_NAME,
        in_signature = '', out_signature = '', sender_keyword = 'sender',
        connection_keyword = 'conn')
    def request_exit(self, sender=None, conn=None):
        """Closes the backend and cleans up"""
        self._check_polkit_privilege(sender, conn, 'com.dell.recoverymedia.request_exit')
        self._timeout = True
        self.main_loop.quit()

    @dbus.service.method(DBUS_INTERFACE_NAME,
        in_signature = 'ssasa{ss}sssss', out_signature = '', sender_keyword = 'sender',
        connection_keyword = 'conn')
    def assemble_image(self, base, fid, driver_fish, application_fish,
                       dell_recovery_package, create_fn, utility,
                       version, iso, sender=None, conn=None):
        """Assemble pieces that would be used for building a BTO image.
           base: mount point of base image (or directory)
           fid: mount point of fid overlay
           fish: list of packages to fish
           create_fn: function to call for creation of ISO
           utility: utility partition
           version: version for ISO creation purposes
           iso: iso file name to create"""

        def safe_tar_extract(filename, destination):
            """Safely extracts a tarball into destination"""
            logging.debug('safe_tar_extract: %s to %s', (filename, destination))
            rfd = tarfile.open(filename)
            dangerous_file = False
            for name in rfd.getnames():
                if name.startswith('..') or name.startswith('/'):
                    dangerous_file = True
                    break
            if not dangerous_file:
                rfd.extractall(destination)
            rfd.close()

        self._reset_timeout()

        base_mnt = self.request_mount(base, sender, conn)

        assembly_tmp = tempfile.mkdtemp()
        atexit.register(walk_cleanup, assembly_tmp)

        #Build a filter list using re for stuff that will be purged during copy
        purge_filter = ''
        purge_list_file = os.path.join(fid, '..', 'examples', 'purgedvd.lst')
        if os.path.exists(purge_list_file):
            try:
                purge_list = open(purge_list_file).readlines()
                for line in purge_list:
                    folder = line.strip('\n')
                    if not purge_filter and folder:
                        purge_filter = "^" + folder
                    elif folder:
                        purge_filter += "|^" + folder
                if purge_filter:
                    purge_filter += "|^syslinux"
            except IOError:
                print >> sys.stderr, "Error reading purge list, but file exists"
        logging.debug('assemble_image: purge_filter is %s', purge_filter)
        white_pattern = re.compile(purge_filter)


        #copy the base iso/mnt point/etc
        w_size = white_tree("size", white_pattern, base_mnt)
        self.start_sizable_progress_thread(_('Adding in base image'),
                                           assembly_tmp,
                                           w_size)
        white_tree("copy", white_pattern, base_mnt, assembly_tmp)
        self.stop_progress_thread()

        #Add in FID content
        if os.path.exists(fid):
            self.report_progress(_('Overlaying FID content'), '99.0')
            if os.path.isdir(fid):
                distutils.dir_util.copy_tree(fid, assembly_tmp,
                                             preserve_symlinks=0,
                                             verbose=1, update=0)
            elif tarfile.is_tarfile(fid):
                safe_tar_extract(fid, assembly_tmp)
            logging.debug('assemble_image: done overlaying FID content')

        #Add in driver FISH content
        length = float(len(driver_fish))
        if length > 0:
            if os.path.exists(os.path.join(assembly_tmp, 'bto_manifest')):
                manifest = open(os.path.join(assembly_tmp, 'bto_manifest'), 'a')
            else:
                manifest = open(os.path.join(assembly_tmp, 'bto_manifest'), 'w')
            for fishie in driver_fish:
                self.report_progress(_('Inserting FISH packages'),
                                     driver_fish.index(fishie)/length*100)
                manifest.write("driver: %s\n" % os.path.basename(fishie))
                dest = None
                if fishie.endswith('.deb'):
                    dest = os.path.join(assembly_tmp, 'debs', 'main')
                    logging.debug("assemble_image: Copying debian archive fishie %s", fishie)
                elif fishie.endswith('.pdf'):
                    dest = os.path.join(assembly_tmp, 'docs')
                    logging.debug("assemble_image: Copying document fishie fishie %s", fishie)
                elif fishie.endswith('.py') or fishie.endswith('.sh'):
                    dest = os.path.join(assembly_tmp, 'scripts', 'chroot-scripts', 'fish')
                    logging.debug("assemble_image: Copying python or shell fishie %s", fishie)
                elif os.path.exists(fishie) and tarfile.is_tarfile(fishie):
                    safe_tar_extract(fishie, assembly_tmp)
                    logging.debug("assemble_image: Extracting tar fishie %s", fishie)
                else:
                    logging.debug("assemble_image: ignoring fishie %s", fishie)

                #If we just do a flat copy
                if dest is not None:
                    if not os.path.isdir(dest):
                        os.makedirs(dest)
                    distutils.file_util.copy_file(fishie, dest,
                                                  verbose=1, update=0)
            logging.debug("assemble_image: done inserting driver fish")
            manifest.close()

        #Add in application FISH content
        length = float(len(application_fish))
        if length > 0:
            if os.path.exists(os.path.join(assembly_tmp, 'bto_manifest')):
                manifest = open(os.path.join(assembly_tmp, 'bto_manifest'), 'a')
            else:
                manifest = open(os.path.join(assembly_tmp, 'bto_manifest'), 'w')
            dest = os.path.join(assembly_tmp, 'srv')
            os.makedirs(dest)
            for fishie in application_fish:
                new_name = application_fish[fishie]
                manifest.write("application: %s (%s)\n" % (os.path.basename(fishie), new_name))
                if fishie.endswith('.zip'):
                    new_name += '.zip'
                elif os.path.exists(fishie) and tarfile.is_tarfile(fishie):
                    new_name += '.tgz'
                distutils.file_util.copy_file(fishie,
                                              os.path.join(dest, new_name),
                                              verbose=1, update=0)
            manifest.close()

        #If a utility partition exists and we wanted to replace it, wipe it away
        if utility:
            for fname in UP_FILENAMES:
                if os.path.exists(os.path.join(assembly_tmp, fname)):
                    os.remove(os.path.join(assembly_tmp, fname))

        #If dell-recovery needs to be injected into the image
        if dell_recovery_package:
            dest = os.path.join(assembly_tmp, 'debs')
            if not os.path.isdir(dest):
                os.makedirs(dest)
            if 'dpkg-repack' in dell_recovery_package:
                logging.debug("Repacking dell-recovery using dpkg-repack")
                call = subprocess.Popen(['dpkg-repack', 'dell-recovery'], cwd=dest)
                (out, err) = call.communicate()
            else:
                logging.debug("Adding manually included dell-recovery package, %s", dell_recovery_package)
                distutils.file_util.copy_file(dell_recovery_package, dest)

        function = getattr(Backend, create_fn)
        function(self, utility, assembly_tmp, version, iso)

    @dbus.service.method(DBUS_INTERFACE_NAME,
        in_signature = 's', out_signature = 'ssss', sender_keyword = 'sender',
        connection_keyword = 'conn')
    def query_iso_information(self, iso, sender=None, conn=None):
        """Queries what type of ISO this is.  This same method will be used regardless
           of OS."""
        def find_float(input_str):
            """Finds the floating point number in a string"""
            for piece in input_str.split():
                try:
                    release = float(piece)
                except ValueError:
                    continue
                logging.debug("query_iso_information: find_float found %d", release)
                return piece
            return ''

        self._reset_timeout()
        self._check_polkit_privilege(sender, conn,
                                'com.dell.recoverymedia.query_iso_information')


        (bto_version, bto_date) = self.query_bto_version(iso, sender, conn)

        distributor_str = 'Unknown Base Image'
        distributor = ''

        #Ubuntu disks have .disk/info
        if os.path.isfile(iso) and iso.endswith('.iso'):
            cmd = ['isoinfo', '-J', '-i', iso, '-x', '/.disk/info']
            invokation = subprocess.Popen(cmd, stdout=subprocess.PIPE)
            out, err = invokation.communicate()
            if invokation.returncode is None:
                invokation.wait()
            if out:
                distributor_str = out
                distributor = "ubuntu"
            if err:
                logging.debug("error during isoinfo invokation: %s", err)
        else:
            mntdir = self.request_mount(iso, sender, conn)

            if os.path.exists(os.path.join(mntdir, '.disk', 'info')):
                with open(os.path.join(mntdir, '.disk', 'info'), 'r') as rfd:
                    distributor_str = rfd.readline().strip('\n')
                distributor = "ubuntu"

            #RHEL disks have .discinfo
            elif os.path.exists(os.path.join(mntdir, '.discinfo')):
                with open(os.path.join(mntdir, '.discinfo'), 'r') as rfd:
                    timestamp = rfd.readline().strip('\n')
                    distributor_string = rfd.readline().strip('\n')
                    arch = rfd.readline().strip('\n')
                distributor = "redhat"
                distributor_str += ' ' + arch

        release = find_float(distributor_str)

        if bto_version and bto_date:
            distributor_str = "<b>Dell BTO Image</b>, version %s built on %s\n%s" % (bto_version.split('.')[0], bto_date, distributor_str)
        else:
            bto_version = ''

        return (bto_version, distributor, release, distributor_str)


    @dbus.service.method(DBUS_INTERFACE_NAME,
        in_signature = 's', out_signature = 'ss', sender_keyword = 'sender',
        connection_keyword = 'conn')
    def query_bto_version(self, recovery, sender=None, conn=None):
        """Queries the BTO version number internally stored in an ISO or RP"""
        self._reset_timeout()
        self._check_polkit_privilege(sender, conn,
                                    'com.dell.recoverymedia.query_bto_version')

        #mount the recovery partition
        version = ''
        date = ''

        if os.path.isfile(recovery) and recovery.endswith('.iso'):
            cmd = ['isoinfo', '-J', '-i', recovery, '-x', '/bto_version']
            invokation = subprocess.Popen(cmd, stdout=subprocess.PIPE)
            out, err = invokation.communicate()
            if invokation.returncode is None:
                invokation.wait()
            if out:
                out = out.split('\n')
                if len(out) > 1:
                    version = out[0]
                    date = out[1]

        else:
            mntdir = self.request_mount(recovery, sender, conn)
            if os.path.exists(os.path.join(mntdir, 'bto_version')):
                with open(os.path.join(mntdir, 'bto_version'), 'r') as rfd:
                    version = rfd.readline().strip('\n')
                    date = rfd.readline().strip('\n')

        return (version, date)

    @dbus.service.method(DBUS_INTERFACE_NAME,
        in_signature = 'ss', out_signature = 'b', sender_keyword = 'sender',
        connection_keyword = 'conn')
    def query_have_dell_recovery(self, recovery, framework, sender=None, conn=None):
        '''Checks if the given image and BTO framework contain the dell-recovery
           package suite'''

        def run_isoinfo_command(cmd):
            """Returns the output of an isoinfo command"""
            invokation = subprocess.Popen(cmd, stdout=subprocess.PIPE)
            out, err = invokation.communicate()
            if invokation.returncode is None:
                invokation.wait()
            if err:
                logging.debug("error invoking isoinfo: %s", err)
            return out

        def check_mentions(feed):
            '''Checks if given file mentions dell-recovery'''
            for line in feed.split('\n'):
                if 'dell-recovery' in line:
                    return True
            return False

        found = False

        #Recovery Partition is an ISO
        if os.path.isfile(recovery) and recovery.endswith('.iso'):
            #first find the interesting files
            cmd = ['isoinfo', '-J', '-i', recovery, '-f']
            logging.debug("query_have_dell_recovery: Checking %s", recovery)
            interesting_files = []
            for fname in run_isoinfo_command(cmd).split('\n'):
                if 'dell-recovery' in fname and (fname.endswith('.deb') or fname.endswith('.rpm')):
                    logging.debug("query_have_dell_recovery: Found %s", fname)
                    found = True
                    break
                elif fname.endswith('.manifest'):
                    interesting_files.append(fname)
                    logging.debug("query_have_dell_recovery: Appending %s to interesting_files", fname)

            if not found:
                for fname in interesting_files:
                    cmd = ['isoinfo', '-J', '-i', recovery, '-x', fname]
                    logging.debug("query_have_dell_recovery: Checking %s ", fname)
                    if check_mentions(run_isoinfo_command(cmd)):
                        logging.debug("query_have_dell_recovery: Found in %s", fname)
                        found = True
                        break
        #Recovery partition is mount point or directory
        else:
            #Search for a flat file first (or a manifest for later)
            logging.debug("query_have_dell_recovery: Searching mount point %s", recovery)
            interesting_files = []
            for root, dirs, files in os.walk(recovery, topdown=False):
                for fname in files:
                    if 'dell-recovery' in fname and (fname.endswith('.deb') or fname.endswith('.rpm')):
                        found = True
                        logging.debug("query_have_dell_recovery: Found in %s", os.path.join(root, fname))
                        break
                    elif fname.endswith('.manifest'):
                        interesting_files.append(os.path.join(root, fname))
                        logging.debug("query_have_dell_recovery: Appending %s to interesting_files", os.path.join(root, fname))

            if not found:
                for fname in interesting_files:
                    with open(fname, 'r') as rfd:
                        output = rfd.read()
                    if check_mentions(output):
                        logging.debug("query_have_dell_recovery: Found in %s", fname)
                        found = True
                        break

        #If we didn't find it in the ISO, search the framework
        if not found and framework:
            logging.debug("query_have_dell_recovery: Searching framework %s", framework)
            for root, dirs, files in os.walk(framework, topdown=False):
                for name in files:
                    if 'dell-recovery' in name and (name.endswith('.deb') or name.endswith('.rpm')):
                        found = True
                        logging.debug("query_have_dell_recovery: Found in %s", os.path.join(root, name))
                        break

        return found

    @dbus.service.method(DBUS_INTERFACE_NAME,
        in_signature = '', out_signature = '', sender_keyword = 'sender',
        connection_keyword = 'conn')
    def enable_boot_to_restore(self, sender=None, conn=None):
        """Enables the default one-time boot option to be recovery"""
        self._reset_timeout()
        self._check_polkit_privilege(sender, conn, 'com.dell.recoverymedia.restore')

        #find our one time boot entry
        if not os.path.exists("/etc/grub.d/99_dell_recovery"):
            raise RestoreFailed("missing 99_dell_recovery to parse")

        with open('/etc/grub.d/99_dell_recovery') as rfd:
            dell_rec_file = rfd.readlines()

        entry = False
        for line in dell_rec_file:
            if "menuentry" in line:
                split = line.split('"')
                if len(split) > 1:
                    entry = split[1]
                    break

        if not entry:
            raise RestoreFailed("Error parsing 99_dell_recovery for bootentry.")

        #set us up to boot saved entries
        with open('/etc/default/grub', 'r') as rfd:
            default_grub = rfd.readlines()
        with open('/etc/default/grub', 'w') as wfd:
            for line in default_grub:
                if line.startswith("GRUB_DEFAULT="):
                    line = "GRUB_DEFAULT=saved\n"
                wfd.write(line)

        ret = subprocess.call(['/usr/sbin/update-grub'])
        if ret is not 0:
            raise RestoreFailed("error updating grub configuration")

        ret = subprocess.call(['/usr/sbin/grub-reboot', entry])
        if ret is not 0:
            raise RestoreFailed("error setting one time grub entry")


    @dbus.service.method(DBUS_INTERFACE_NAME,
        in_signature = 'ssss', out_signature = '', sender_keyword = 'sender',
        connection_keyword = 'conn')
    def create_ubuntu(self, utility, recovery, version, iso, sender=None, conn=None):
        """Creates Ubuntu compatible recovery media"""
        self._reset_timeout()
        self._check_polkit_privilege(sender, conn,
                                                'com.dell.recoverymedia.create')

        #create temporary workspace
        tmpdir = tempfile.mkdtemp()
        atexit.register(walk_cleanup, tmpdir)

        #mount the recovery partition
        mntdir = self.request_mount(recovery, sender, conn)

        if not os.path.exists(os.path.join(mntdir, '.disk', 'info')):
            print >> sys.stderr, \
                "recovery partition is missing critical ubuntu files."
            raise CreateFailed("Recovery partition is missing critical Ubuntu files.")

        #Generate BTO version string
        with open(os.path.join(tmpdir, 'bto_version'), 'w') as wfd:
            wfd.write(version + '\n')
            wfd.write(str(datetime.date.today()) + '\n')

        #If necessary, include the utility partition
        if utility and os.path.exists(utility):
            #device node
            if stat.S_ISBLK(os.stat(utility).st_mode):
                self.start_pulsable_progress_thread(
                    _('Building Dell Utility Partition'))

                seg1 = subprocess.Popen(['dd', 'if=' + utility, 'bs=1M'],
                                      stdout=subprocess.PIPE)
                seg2 = subprocess.Popen(['gzip', '-c'],
                                      stdin=seg1.stdout,
                                      stdout=subprocess.PIPE)
                partition_file = open(os.path.join(tmpdir, 'upimg.gz'), "w")
                partition_file.write(seg2.communicate()[0])
                partition_file.close()
                self.stop_progress_thread()

            #tgz type
            elif tarfile.is_tarfile(utility):
                try:
                    shutil.copy(utility, os.path.join(tmpdir, 'up.tgz'))
                except Exception, msg:
                    print >> sys.stderr, \
                        "Error with tgz: %s." % str(msg)
                    raise CreateFailed("Error building Utility Partition : %s" %
                                       str(msg))

            #probably a zip
            else:
                try:
                    zip_obj = zipfile.ZipFile(utility)
                    shutil.copy(utility, os.path.join(tmpdir, 'up.zip'))
                except Exception, msg:
                    print >> sys.stderr, \
                        "Error with zipfile: %s." % str(msg)
                    raise CreateFailed("Error building Utility Partition : %s" %
                                       str(msg))

        #Arg list
        genisoargs = ['genisoimage',
            '-o', iso,
            '-input-charset', 'utf-8',
            '-b', 'isolinux/isolinux.bin',
            '-c', 'isolinux/boot.catalog',
            '-no-emul-boot',
            '-boot-load-size', '4',
            '-boot-info-table',
            '-pad',
            '-r',
            '-J',
            '-joliet-long',
            '-N',
            '-hide-joliet-trans-tbl',
            '-cache-inodes',
            '-l',
            '-publisher', 'Dell Inc.',
            '-V', 'Dell Ubuntu Reinstallation Media',
            '-m', '*.exe',
            '-m', '*.sys',
            '-m', 'syslinux',
            '-m', 'syslinux.cfg',
            '-m', os.path.join(mntdir, 'isolinux'),
            '-m', os.path.join(mntdir, 'bto_version')]

        #Renerate UUID
        os.mkdir(os.path.join(tmpdir, '.disk'))
        os.mkdir(os.path.join(tmpdir, 'casper'))
        self.start_pulsable_progress_thread(_('Regenerating UUID / Rebuilding initramfs'))
        (old_initrd,
         old_uuid) = create_new_uuid(os.path.join(mntdir, 'casper'),
                        os.path.join(mntdir, '.disk'),
                        os.path.join(tmpdir, 'casper'),
                        os.path.join(tmpdir, '.disk'))
        self.stop_progress_thread()
        genisoargs.append('-m')
        genisoargs.append(os.path.join('.disk', old_uuid))
        genisoargs.append('-m')
        genisoargs.append(os.path.join('casper', old_initrd))

        #if we have ran this from a USB key, we might have syslinux which will
        #break our build
        if os.path.exists(os.path.join(mntdir, 'syslinux')):
            shutil.copytree(os.path.join(mntdir, 'syslinux'), os.path.join(tmpdir, 'isolinux'))
            if os.path.exists(os.path.join(tmpdir, 'isolinux', 'syslinux.cfg')):
                shutil.move(os.path.join(tmpdir, 'isolinux', 'syslinux.cfg'), os.path.join(tmpdir, 'isolinux', 'isolinux.cfg'))
        else:
            #Copy boot section for ISO to somewhere writable
            shutil.copytree(os.path.join(mntdir, 'isolinux'), os.path.join(tmpdir, 'isolinux'))

        #Directories to install
        genisoargs.append(tmpdir + '/')
        genisoargs.append(mntdir + '/')

        #ISO Creation
        seg1 = subprocess.Popen(genisoargs,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE)
        retval = seg1.poll()
        output = ""
        while (retval is None):
            stdout = seg1.stderr.readline()
            if stdout != "":
                output = stdout
            if output:
                progress = output.split()[0]
                if (progress[-1:] == '%'):
                    self.report_progress(_('Building ISO'), progress[:-1])
            retval = seg1.poll()
        if retval is not 0:
            print >> sys.stderr, genisoargs
            print >> sys.stderr, output.strip()
            print >> sys.stderr, seg1.stderr.readlines()
            print >> sys.stderr, seg1.stdout.readlines()
            print >> sys.stderr, \
                "genisoimage exited with a nonstandard return value."
            raise CreateFailed("ISO Building exited unexpectedly:\n%s" %
                               output.strip())

    @dbus.service.signal(DBUS_INTERFACE_NAME)
    def report_progress(self, progress_str, percent):
        '''Report ISO build progress to UI.
        '''
        return True
