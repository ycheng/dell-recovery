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

import logging, os, os.path, signal, sys

import gobject
import dbus
import dbus.service
import dbus.mainloop.glib

import getopt
import atexit
import tempfile
import subprocess
import tarfile
import shutil
import datetime
import distutils.dir_util

DBUS_BUS_NAME = 'com.dell.RecoveryMedia'

#--------------------------------------------------------------------#

class UnknownHandlerException(dbus.DBusException):
    _dbus_error_name = 'com.dell.RecoveryMedia.UnknownHandlerException'

class InvalidModeException(dbus.DBusException):
    _dbus_error_name = 'com.dell.RecoveryMedia.InvalidModeException'

class InvalidDriverDBException(dbus.DBusException):
    _dbus_error_name = 'com.dell.RecoveryMedia.InvalidDriverDBException'

class PermissionDeniedByPolicy(dbus.DBusException):
    _dbus_error_name = 'com.dell.RecoveryMedia.PermissionDeniedByPolicy'

class BackendCrashError(SystemError):
    pass

#--------------------------------------------------------------------#

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

#--------------------------------------------------------------------#

class Backend(dbus.service.Object):
    '''Backend manager.

    This encapsulates all services calls of the backend. It
    is implemented as a dbus.service.Object, so that it can be called through
    D-BUS as well (on the /RecoveryMedia object path).
    '''
    DBUS_INTERFACE_NAME = 'com.dell.RecoveryMedia'

    #
    # D-BUS control API
    #

    def __init__(self):
        # cached D-BUS interfaces for _check_polkit_privilege()
        self.dbus_info = None
        self.polkit = None
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
        backend.dbus_name = dbus.service.BusName(DBUS_BUS_NAME, backend.bus)
        return backend

    @classmethod
    def create_dbus_client(klass, session_bus=False):
        '''Return a client-side D-BUS interface for Backend.

        Normally this connects to the system bus. Set session_bus to True to
        connect to the session bus (for testing).
        '''
        if session_bus:
            bus = dbus.SessionBus()
        else:
            bus = dbus.SystemBus()
        obj = bus.get_object(DBUS_BUS_NAME, '/RecoveryMedia')
        return dbus.Interface(obj, Backend.DBUS_INTERFACE_NAME)

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
                    ('unix-process', {'pid': dbus.UInt32(pid, variant_level=1)}), 
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

    def walk_cleanup(self,directory):
        '''Cleans up a temporary directory and all it's files'''
        if os.path.exists(directory):
            for root,dirs,files in os.walk(directory, topdown=False):
                for name in files:
                    os.remove(os.path.join(root,name))
                for name in dirs:
                    if os.path.isdir(os.path.join(root,name)):
                        os.rmdir(os.path.join(root,name))
                    else:
                        os.remove(os.path.join(root,name))
            os.rmdir(directory)

    def request_mount(self,rp):
        '''Attempts to mount the rp.

           If successful, return mntdir.
           If we find that it's already mounted elsewhere, return that mount
           If unsuccessful, return an empty string
        '''
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
        command=subprocess.Popen(mnt_args,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        output=command.communicate()
        ret=command.wait()
        if ret is not 0:
            os.rmdir(mntdir)
            if ret == 32:
                mntdir=output[1].strip('\n').split('on')[1].strip(' ')
            else:
                mntdir=''
                print >> sys.stderr, "WARNING: unable to mount recovery partition"
                print >> sys.stderr, output
        else:
            atexit.register(self.unmount_drive,mntdir)
        return mntdir

    def unmount_drive(self,mnt):
        if os.path.exists(mnt):
            ret=subprocess.call(['umount', mnt])
            if ret is not 0:
                print >> sys.stderr, "Error unmounting %s" % mnt
            os.rmdir(mnt)          

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
        in_signature='ssas', out_signature='s', sender_keyword='sender',
        connection_keyword='conn')
    def assemble_image(self, base, fid, fish, sender=None, conn=None):
        """Takes the different pieces that would be used for a BTO image and puts them together"""

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
        self._check_polkit_privilege(sender, conn, 'com.dell.recoverymedia.create')

        base_mnt = self.request_mount(base)

        assembly_tmp=tempfile.mkdtemp()
        atexit.register(self.walk_cleanup,assembly_tmp)

        #copy the base iso/mnt point/etc
        self.report_progress(_('Adding in base image'),'10.0')
        distutils.dir_util.copy_tree(base_mnt,assembly_tmp,preserve_symlinks=1,verbose=1,update=1)

        #TODO, purge support

        #Add in FID content
        if os.path.isdir(fid):
            self.report_progress(_('Putting together FID content'),'30.0')
            distutils.dir_util.copy_tree(fid,assembly_tmp,preserve_symlinks=1,verbose=1,update=1)
        elif os.path.exists(fid) and tarfile.is_tarfile(fid):
            self.report_progress(_('Putting together FID content'),'30.0')
            safe_tar_extract(fid,assembly_tmp)

        length=float(len(fish))
        for fishie in fish:
            self.report_progress(_('Inserting FISH packages'),fish.index(fishie)/length*100 + 30)
            if os.path.exists(fishie) and tarfile.is_tarfile(fishie):
                safe_tar_extract(fishie,assembly_tmp)

        return assembly_tmp

    @dbus.service.method(DBUS_INTERFACE_NAME,
        in_signature='s', out_signature='ssss', sender_keyword='sender',
        connection_keyword='conn')
    def query_iso_information(self, iso, sender=None, conn=None):
        """Queries what type of ISO this is.  This same method will be used regardless
           of OS."""
        self._reset_timeout()
        self._check_polkit_privilege(sender, conn, 'com.dell.recoverymedia.query_iso_information')

        mntdir = self.request_mount(iso)

        (bto_version,bto_date) = self.query_bto_version(iso)

        distributor_string='Unknown Base Image'
        release=''
        distributor=''
        #Ubuntu disks have .disk/info
        if os.path.exists(os.path.join(mntdir,'.disk','info')):
            file=open(os.path.join(mntdir,'.disk','info'),'r')
            distributor_string=file.readline().strip('\n')
            file.close()
            distributor=distributor_string.split()[0].lower()
            release=distributor_string.split()[1].lower()

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
        version='A00'
        date=''
        mntdir = self.request_mount(rp)

        if os.path.exists(os.path.join(mntdir,'bto_version')):
            file=open(os.path.join(mntdir,'bto_version'),'r')
            version=file.readline().strip('\n')
            date=file.readline().strip('\n')
            file.close()
            if len(version) == 0:
                version='A00'
            elif not '.' in version:
                version+= '.1'
            else:
                pieces=version.split('.')
                increment=int(pieces[1]) + 1
                version="%s.%d" % (pieces[0],increment)

        return (version,date)

    @dbus.service.method(DBUS_INTERFACE_NAME,
        in_signature='ssss', out_signature='', sender_keyword='sender',
        connection_keyword='conn')
    def create_ubuntu(self, up, rp, version, iso, sender=None, conn=None):

        self._reset_timeout()
        self._check_polkit_privilege(sender, conn, 'com.dell.recoverymedia.create')
        
        #create temporary workspace
        tmpdir=tempfile.mkdtemp()
        atexit.register(self.walk_cleanup,tmpdir)

        #mount the RP
        mntdir=self.request_mount(rp)

        #Generate BTO version string
        file=open(os.path.join(tmpdir,'bto_version'),'w')
        file.write(version + '\n')
        file.write(str(datetime.date.today()) + '\n')
        file.close()

        #If necessary, build the UP
        if not os.path.exists(os.path.join(mntdir,'upimg.bin')) and up:
            self.report_progress(_('Building Dell Utility Partition'),'0.0')
            p1 = subprocess.Popen(['dd','if=' + up,'bs=1M'], stdout=subprocess.PIPE)
            p2 = subprocess.Popen(['gzip','-c'], stdin=p1.stdout, stdout=subprocess.PIPE)
            partition_file=open(os.path.join(tmpdir, 'upimg.bin'), "w")
            partition_file.write(p2.communicate()[0])
            partition_file.close()

        #Renerate UUID
        os.mkdir(os.path.join(tmpdir,'.disk'))
        os.mkdir(os.path.join(tmpdir,'casper'))
        self.report_progress(_('Regenerating UUID / Rebuilding initramfs'),'0.0')
        initrd=os.path.join(mntdir,'casper','initrd')
        if os.path.exists(initrd + '.gz'):
            initrd=initrd + '.gz'
        elif os.path.exists(initrd + '.lz'):
            initrd=initrd + '.lz'
        uuid_args = ['/usr/share/dell/bin/create-new-uuid',
                              initrd,
                              os.path.join(tmpdir,'casper'),
                              tmpdir + '/.disk']
        uuid = subprocess.Popen(uuid_args)
        retval = uuid.poll()
        while (retval is None):
            retval = uuid.poll()
        if retval is not 0:
            print >> sys.stderr, \
                "create-new-uuid exited with a nonstandard return value."
            return

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
            '-m', os.path.join(mntdir,'bto_version'),
            '-m', os.path.join(mntdir,'.disk','casper-uuid-generic'),
            '-m', initrd]

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

    @dbus.service.signal(DBUS_INTERFACE_NAME)
    def report_progress(self, progress_str, percent):
        '''Report ISO build progress to UI.
        '''
        return True
