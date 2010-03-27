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

import getopt
import atexit
import tempfile
import subprocess
import tarfile
import shutil
import datetime
import distutils.dir_util
import re
import stat
import zipfile
import glob

from Dell.recovery_common import *

#--------------------------------------------------------------------#
#Borrowed from USB-Creator initially
#Used for emitting progress for subcalls that don't use stdout nicely
class progress_by_size(Thread):
    def __init__(self, str, device, to_write):
        Thread.__init__(self)
        self._stopevent = Event()
        self.str=str
        self.to_write = to_write
        self.device = device
        statvfs = os.statvfs(device)
        self.start_free = statvfs.f_bsize * statvfs.f_bavail

    def progress(self, str, per):
        pass

    def run(self):
        try:
            while not self._stopevent.isSet():
                statvfs = os.statvfs(self.device)
                free = statvfs.f_bsize * statvfs.f_bavail
                written = self.start_free - free
                v = int((written / float(self.to_write)) * 100)
                if callable(self.progress):
                    self.progress(self.str,v)
                self._stopevent.wait(2)
        except Exception:
            logging.exception('Could not update progress:')

    def join(self, timeout=None):
        self._stopevent.set()
        Thread.join(self, timeout)

class progress_by_pulse(Thread):
    def __init__(self,str):
        Thread.__init__(self)
        self._stopevent = Event()
        self.str = str

    def progress(self, str, per):
        pass

    def run(self):
        try:
            while not self._stopevent.isSet():
                if callable(self.progress):
                    self.progress(self.str,"-1")
                self._stopevent.wait(.5)
        except Exception:
            logging.exception('Could not update progress:')

    def join(self, timeout=None):
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
        # cached D-BUS interfaces for _check_polkit_privilege()
        self.dbus_info = None
        self.polkit = None
        self.progress_thread = None
        self.enforce_polkit = True

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
            def _t():
                self.main_loop.quit()
                return True
            gobject.timeout_add(timeout * 1000, _t)

        # send parent process a signal that we are ready now
        if send_usr1:
            os.kill(os.getppid(), signal.SIGUSR1)

        # run until we time out
        while not self._timeout:
            if timeout:
                self._timeout = True
            self.main_loop.run()

    @classmethod
    def create_dbus_server(klass, session_bus=False):
        '''Return a D-BUS server backend instance.

        Normally this connects to the system bus. Set session_bus to True to
        connect to the session bus (for testing).

        '''
        import dbus.mainloop.glib

        backend = Backend()
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        if session_bus:
            backend.bus = dbus.SessionBus()
            backend.enforce_polkit = False
        else:
            backend.bus = dbus.SystemBus()
        try:
            backend.dbus_name = dbus.service.BusName(DBUS_BUS_NAME, backend.bus)
        except dbus.exceptions.DBusException,msg:
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
            (is_auth, _, details) = self.polkit.CheckAuthorization(
                    ('unix-process', {'pid': dbus.UInt32(pid, variant_level=1),
                        'start-time': dbus.UInt64(0, variant_level=1)}),
                    privilege, {'': ''}, dbus.UInt32(1), '', timeout=600)
        except dbus.DBusException, e:
            if e._dbus_error_name == 'org.freedesktop.DBus.Error.ServiceUnknown':
                # polkitd timed out, connect again
                self.polkit = None
                return self._check_polkit_privilege(sender, conn, privilege)
            else:
                raise

        if not is_auth:
            logging.debug('_check_polkit_privilege: sender %s on connection %s pid %i is not authorized for %s: %s' %
                    (sender, conn, pid, privilege, str(details)))
            raise PermissionDeniedByPolicy(privilege)

    #
    # Internal API for calling from Handlers (not exported through D-BUS)
    #

    def request_mount(self,rp,sender=None,conn=None):
        '''Attempts to mount the rp.

           If successful, return mntdir.
           If we find that it's already mounted elsewhere, return that mount
           If unsuccessful, return an empty string
        '''
        #Work around issues sending a UTF-8 directory over dbus
        rp = rp.encode('utf8')

        #In this is just a directory
        if os.path.isdir(rp):
            return rp

        #check for an existing mount
        command=subprocess.Popen(['mount'],stdout=subprocess.PIPE)
        output=command.communicate()[0].split('\n')
        for line in output:
            processed_line=line.split()
            if len(processed_line) > 0 and processed_line[0] == rp:
                return processed_line[2]

        #if not already, mounted, produce a mount point
        mntdir=tempfile.mkdtemp()
        mnt_args = ['mount','-r',rp, mntdir]
        if ".iso" in rp:
            mnt_args.insert(1,'loop')
            mnt_args.insert(1,'-o')
        else:
            self._check_polkit_privilege(sender, conn, 'com.dell.recoverymedia.create')
        command=subprocess.Popen(mnt_args,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        output=command.communicate()
        ret=command.wait()
        if ret is not 0:
            os.rmdir(mntdir)
            if ret == 32:
                try:
                    mntdir=output[1].strip('\n').split('on')[1].strip(' ')
                except IndexError:
                    mntdir=''
                    logging.warning("IndexError when operating on output string")
            else:
                mntdir=''
                logging.warning("Unable to mount recovery partition")
                logging.warning(output)
        else:
            atexit.register(self.unmount_drive,mntdir)
        return mntdir

    def unmount_drive(self,mnt):
        if os.path.exists(mnt):
            ret=subprocess.call(['umount', mnt])
            if ret is not 0:
                print >> sys.stderr, "Error unmounting %s" % mnt
            try:
                os.rmdir(mnt)
            except OSError, e:
                print >> sys.stderr, "Error cleaning up: %s" % str(e)

    def start_sizable_progress_thread(self, str, mnt, w_size):
        """Initializes the extra progress thread, or resets it
           if it already exists'"""
        self.progress_thread = progress_by_size(str, mnt, w_size)
        self.progress_thread.progress = self.report_progress
        self.progress_thread.start()

    def stop_progress_thread(self):
        """Stops the extra thread for reporting progress"""
        self.progress_thread.join()

    def start_pulsable_progress_thread(self, str):
        self.progress_thread = progress_by_pulse(str)
        self.progress_thread.progress = self.report_progress
        self.progress_thread.start()
    #
    # Client API (through D-BUS)
    #
    @dbus.service.method(DBUS_INTERFACE_NAME,
        in_signature='', out_signature='', sender_keyword='sender',
        connection_keyword='conn')
    def request_exit(self, sender=None, conn=None):
        """Closes the backend and cleans up"""
        self._check_polkit_privilege(sender, conn, 'com.dell.recoverymedia.request_exit')
        self._timeout = True
        self.main_loop.quit()

    @dbus.service.method(DBUS_INTERFACE_NAME,
        in_signature='ssasssss', out_signature='', sender_keyword='sender',
        connection_keyword='conn')
    def assemble_image(self, base, fid, fish, create_fn, up, version, iso, sender=None, conn=None):
        """Takes the different pieces that would be used for a BTO image and puts them together
           base: mount point of base image (or directory)
           fid: mount point of fid overlay
           fish: list of packages to fish
           create_fn: function to call for creation of ISO
           up: utility partition
           version: version for ISO creation purposes
           iso: iso file name to create"""

        def safe_tar_extract(filename,destination):
            """Safely extracts a tarball into destination"""
            file=tarfile.open(filename)
            dangerous_file=False
            for name in file.getnames():
                if name.startswith('..') or name.startswith('/'):
                    dangerous_file=True
                    break
            if not dangerous_file:
                file.extractall(destination)
            file.close()

        self._reset_timeout()

        base_mnt = self.request_mount(base,sender,conn)

        assembly_tmp=tempfile.mkdtemp()
        atexit.register(walk_cleanup,assembly_tmp)

        #Build a filter list using re for stuff that will be purged during copy
        filter=''
        purge_list_file=os.path.join(fid,'..','examples','purgedvd.lst')
        if os.path.exists(purge_list_file):
            try:
                purge_list = open(purge_list_file).readlines()
                for line in purge_list:
                    folder=line.strip('\n')
                    if not filter and folder:
                        filter = "^" + folder
                    elif folder:
                        filter += "|^" + folder
            except IOError:
                print  >> sys.stderr, "Error reading purge list, but file exists"
        if filter:
            filter += "|"
        filter += "^syslinux"
        logging.debug('assemble_image: filter is %s' % filter)
        white_pattern=re.compile(filter)


        #copy the base iso/mnt point/etc
        w_size=white_tree("size", white_pattern, base_mnt)
        self.start_sizable_progress_thread(_('Adding in base image'), assembly_tmp, w_size)
        white_tree("copy", white_pattern, base_mnt, assembly_tmp)
        self.stop_progress_thread()

        #Add in FID content
        if os.path.exists(fid):
            self.report_progress(_('Overlaying FID content'),'99.0')
            if os.path.isdir(fid):
                distutils.dir_util.copy_tree(fid,assembly_tmp,preserve_symlinks=0,verbose=1,update=0)
            elif tarfile.is_tarfile(fid):
                safe_tar_extract(fid,assembly_tmp)
            logging.debug('assemble_image: done overlaying FID content')

        #Add in FISH content
        length=float(len(fish))
        if length > 0:
            if os.path.exists(os.path.join(assembly_tmp,'bto_manifest')):
                manifest=open(os.path.join(assembly_tmp,'bto_manifest'),'a')
            else:
                manifest=open(os.path.join(assembly_tmp,'bto_manifest'),'w')
            for fishie in fish:
                self.report_progress(_('Inserting FISH packages'),fish.index(fishie)/length*100)
                manifest.write(fishie + '\n')
                dest=None
                if fishie.endswith('.deb'):
                    dest=os.path.join(assembly_tmp,'debs','main')
                    logging.debug("assemble_image: Copying debian archive fishie %s" % fishie)
                elif fishie.endswith('.pdf'):
                    dest=os.path.join(assembly_tmp,'docs')
                    logging.debug("assemble_image: Copying document fishie fishie %s" % fishie)
                elif fishie.endswith('.py') or fishie.endswith('.sh'):
                    dest=os.path.join(assembly_tmp,'scripts','chroot-scripts','fish')
                    logging.debug("assemble_image: Copying python or shell fishie %s" % fishie)
                elif os.path.exists(fishie) and tarfile.is_tarfile(fishie):
                    safe_tar_extract(fishie,assembly_tmp)
                    logging.debug("assemble_image: Extracting tar fishie %s" % fishie)
                else:
                    logging.debug("assemble_image: ignoring fishie %s" % fishie)

                #If we just do a flat copy
                if dest is not None:
                    if not os.path.isdir(dest):
                        os.makedirs(dest)
                    distutils.file_util.copy_file(fishie,dest,verbose=1,update=0)
            logging.debug("assemble_image: done inserting fish")
            manifest.close()

        #If a UP exists and we wanted to replace it, wipe it away
        if up:
            for file in up_filenames:
                if os.path.exists(os.path.join(assembly_tmp, file)):
                    os.remove(os.path.join(assembly_tmp, file))

        function=getattr(Backend,create_fn)
        function(self,up,assembly_tmp,version,iso)

    @dbus.service.method(DBUS_INTERFACE_NAME,
        in_signature='s', out_signature='ssss', sender_keyword='sender',
        connection_keyword='conn')
    def query_iso_information(self, iso, sender=None, conn=None):
        """Queries what type of ISO this is.  This same method will be used regardless
           of OS."""
        def find_float(str):
            """Finds the floating point number in a string"""
            for piece in str.split():
                try:
                    release=float(piece)
                except ValueError:
                    continue
                return piece
            return ''

        self._reset_timeout()
        self._check_polkit_privilege(sender, conn, 'com.dell.recoverymedia.query_iso_information')


        (bto_version,bto_date) = self.query_bto_version(iso, sender, conn)

        distributor_string='Unknown Base Image'
        distributor=''

        #Ubuntu disks have .disk/info
        if os.path.isfile(iso) and iso.endswith('.iso'):
            cmd = ['isoinfo', '-J', '-i', iso, '-x', '/.disk/info']
            invokation = subprocess.Popen(cmd, stdout=subprocess.PIPE)
            out, err = invokation.communicate()
            if invokation.returncode is None:
                invokation.wait()
            if out:
                distributor_string = out
                distributor="ubuntu"
        else:
            mntdir = self.request_mount(iso, sender, conn)

            if os.path.exists(os.path.join(mntdir,'.disk','info')):
                file=open(os.path.join(mntdir,'.disk','info'),'r')
                distributor_string=file.readline().strip('\n')
                file.close()
                distributor="ubuntu"

            #RHEL disks have .discinfo
            elif os.path.exists(os.path.join(mntdir,'.discinfo')):
                file=open(os.path.join(mntdir,'.discinfo'),'r')
                timestamp=file.readline().strip('\n')
                distributor_string=file.readline().strip('\n')
                arch=file.readline().strip('\n')
                distributor="redhat"
                distributor_string += ' ' + arch

        release=find_float(distributor_string)

        if bto_version and bto_date:
            distributor_string="<b>Dell BTO Image</b>, version %s built on %s\n%s" %(bto_version.split('.')[0], bto_date, distributor_string)
        else:
            bto_version=''

        return (bto_version, distributor, release, distributor_string)


    @dbus.service.method(DBUS_INTERFACE_NAME,
        in_signature='s', out_signature='ss', sender_keyword='sender',
        connection_keyword='conn')
    def query_bto_version(self, rp, sender=None, conn=None):
        self._reset_timeout()
        self._check_polkit_privilege(sender, conn, 'com.dell.recoverymedia.query_bto_version')

        #mount the RP
        version=''
        date=''

        if os.path.isfile(rp) and rp.endswith('.iso'):
            cmd = ['isoinfo', '-J', '-i', rp, '-x', '/bto_version']
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
            mntdir = self.request_mount(rp, sender, conn)
            if os.path.exists(os.path.join(mntdir,'bto_version')):
                file=open(os.path.join(mntdir,'bto_version'),'r')
                version=file.readline().strip('\n')
                date=file.readline().strip('\n')
                file.close()

        return (version,date)


    @dbus.service.method(DBUS_INTERFACE_NAME,
        in_signature='ssss', out_signature='', sender_keyword='sender',
        connection_keyword='conn')
    def create_ubuntu(self, up, rp, version, iso, sender=None, conn=None):

        self._reset_timeout()
        self._check_polkit_privilege(sender, conn, 'com.dell.recoverymedia.create')

        #create temporary workspace
        tmpdir=tempfile.mkdtemp()
        atexit.register(walk_cleanup,tmpdir)

        #mount the RP
        mntdir=self.request_mount(rp, sender, conn)

        if not os.path.exists(os.path.join(mntdir,'.disk', 'info')):
            print >> sys.stderr, \
                "recovery partition is missing critical ubuntu files."
            raise CreateFailed("Recovery partition is missing critical Ubuntu files.")

        #Generate BTO version string
        file=open(os.path.join(tmpdir,'bto_version'),'w')
        file.write(version + '\n')
        file.write(str(datetime.date.today()) + '\n')
        file.close()

        #If necessary, include the UP
        if up and os.path.exists(up):
            #device node
            if stat.S_ISBLK(os.stat(up).st_mode):
                self.start_pulsable_progress_thread(_('Building Dell Utility Partition'))
                p1 = subprocess.Popen(['dd','if=' + up,'bs=1M'], stdout=subprocess.PIPE)
                p2 = subprocess.Popen(['gzip','-c'], stdin=p1.stdout, stdout=subprocess.PIPE)
                partition_file=open(os.path.join(tmpdir, 'upimg.gz'), "w")
                partition_file.write(p2.communicate()[0])
                partition_file.close()
                self.stop_progress_thread()

            #tgz type
            elif tarfile.is_tarfile(up):
                try:
                    shutil.copy(up, os.path.join(tmpdir, 'up.tgz'))
                except Exception, e:
                    print >> sys.stderr, \
                        "Error with tgz: %s." % str(e)
                    raise CreateFailed("Error building Utility Partition : %s" % str(e))

            #probably a zip
            else:
                try:
                    file = zipfile.ZipFile(up)
                    shutil.copy(up, os.path.join(tmpdir, 'up.zip'))
                except Exception, e:
                    print >> sys.stderr, \
                        "Error with zipfile: %s." % str(e)
                    raise CreateFailed("Error building Utility Partition : %s" % str(e))

        #Arg list
        genisoargs=['genisoimage',
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
            '-m', os.path.join(mntdir,'isolinux'),
            '-m', os.path.join(mntdir,'bto_version')]

        #Renerate UUID
        os.mkdir(os.path.join(tmpdir,'.disk'))
        os.mkdir(os.path.join(tmpdir,'casper'))
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
        if os.path.exists(os.path.join(mntdir,'syslinux')):
            shutil.copytree(os.path.join(mntdir,'syslinux'), os.path.join(tmpdir,'isolinux'))
            if os.path.exists(os.path.join(tmpdir,'isolinux','syslinux.cfg')):
                shutil.move(os.path.join(tmpdir,'isolinux','syslinux.cfg'), os.path.join(tmpdir,'isolinux','isolinux.cfg'))
        else:
            #Copy boot section for ISO to somewhere writable
            shutil.copytree(os.path.join(mntdir,'isolinux'), os.path.join(tmpdir,'isolinux'))

        #Directories to install
        genisoargs.append(tmpdir + '/')
        genisoargs.append(mntdir + '/')

        #ISO Creation
        p3 = subprocess.Popen(genisoargs,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        retval = p3.poll()
        while (retval is None):
            output = p3.stderr.readline()
            if ( output != "" ):
                progress = output.split()[0]
                if (progress[-1:] == '%'):
                    self.report_progress(_('Building ISO'),progress[:-1])
            retval = p3.poll()
        if retval is not 0:
            print >> sys.stderr, genisoargs
            print >> sys.stderr, p3.stderr.readlines()
            print >> sys.stderr, p3.stdout.readlines()
            print >> sys.stderr, \
                "genisoimage exited with a nonstandard return value."
            raise CreateFailed("genisoimage exited with a nonstandard return value.")

    @dbus.service.signal(DBUS_INTERFACE_NAME)
    def report_progress(self, progress_str, percent):
        '''Report ISO build progress to UI.
        '''
        return True
