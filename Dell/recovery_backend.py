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
import shutil

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
        main_loop = gobject.MainLoop()
        self._timeout = False
        if timeout:
            def _t():
                main_loop.quit()
                return True
            gobject.timeout_add(timeout * 1000, _t)

        # send parent process a signal that we are ready now
        if send_usr1:
            os.kill(os.getppid(), signal.SIGUSR1)

        # run until we time out
        while not self._timeout:
            if timeout:
                self._timeout = True
            main_loop.run()

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
        for root,dirs,files in os.walk(directory, topdown=False):
            for name in files:
                os.remove(os.path.join(root,name))
            for name in dirs:
                os.rmdir(os.path.join(root,name))       

    def unmount_drives(self,mntdir,tmpdir):
        #only unmount places if they actually still exist
        if os.path.exists(mntdir):
            subprocess.call(['umount', mntdir + '/.disk/casper-uuid-generic'])
            subprocess.call(['umount', mntdir + '/bto_version'])
            subprocess.call(['umount', mntdir + '/casper/initrd.lz'])
            ret=subprocess.call(['umount', mntdir])
            #only cleanup the mntdir if we could properly umount
            if ret is 0:
                self.walk_cleanup(mntdir)
                os.rmdir(mntdir)

        if os.path.exists(tmpdir):
            subprocess.call(['umount', tmpdir])
            self.walk_cleanup(tmpdir)
            os.rmdir(tmpdir)

    #
    # Client API (through D-BUS)
    #

    @dbus.service.method(DBUS_INTERFACE_NAME,
        in_signature='s', out_signature='s', sender_keyword='sender',
        connection_keyword='conn')
    def query_version(self, rp, sender=None, conn=None):
        self._reset_timeout()
        self._check_polkit_privilege(sender, conn, 'com.dell.recoverymedia.query_version')

        mntdir=tempfile.mkdtemp()

        #mount the RP
        version='A00'
        ret=subprocess.call(['mount', '-o', 'ro',  rp , mntdir])
        if ret is 0 and os.path.exists(os.path.join(mntdir,'bto_version')):
            file=open(os.path.join(mntdir,'bto_version'),'r')
            version=file.readline().strip('\n')
            file.close()
            if len(version) == 0:
                version='A00'
            elif not '.' in version:
                version+= '.1'
            else:
                pieces=version.split('.')
                increment=int(pieces[1]) + 1
                version="%s.%d" % (pieces[0],increment)
        self.unmount_drives('', mntdir)

        return version

    @dbus.service.method(DBUS_INTERFACE_NAME,
        in_signature='ssss', out_signature='', sender_keyword='sender',
        connection_keyword='conn')
    def create_ubuntu(self, up, rp, version, iso, sender=None, conn=None):

        self._reset_timeout()
        self._check_polkit_privilege(sender, conn, 'com.dell.recoverymedia.create_ubuntu')
        
        #create temporary workspace
        tmpdir=tempfile.mkdtemp()
        os.mkdir(tmpdir + '/up')
        mntdir=tempfile.mkdtemp()

        #cleanup any mounts on exit
        atexit.register(self.unmount_drives,mntdir,tmpdir)

        #mount the RP
        subprocess.call(['mount', rp , mntdir])

        #Cleanup the RP
        #FIXME, we should just ignore rather than delete these files
        for file in os.listdir(mntdir):
            if ".exe" in file or ".sys" in file:
                os.remove(mntdir + '/' + file)

        #Generate BTO version string
        file=open(os.path.join(tmpdir,'bto_version'),'w')
        file.write(version)
        file.close()

        #If necessary, build the UP
        if not os.path.exists(mntdir + '/upimg.bin'):
            self.report_progress(_('Building UP'),'0.0')
            p1 = subprocess.Popen(['dd','if=' + up,'bs=1M'], stdout=subprocess.PIPE)
            p2 = subprocess.Popen(['gzip','-c'], stdin=p1.stdout, stdout=subprocess.PIPE)
            partition_file=open(tmpdir + '/up/' + 'upimg.bin', "w")
            partition_file.write(p2.communicate()[0])
            partition_file.close()

        #Renerate UUID
        self.report_progress(_('Generating UUID'),'0.0')
        uuid_args = ['/usr/share/dell/bin/create-new-uuid',
                              mntdir + '/casper/initrd.lz',
                              tmpdir + '/',
                              tmpdir + '/']
        uuid = subprocess.Popen(uuid_args)
        retval = uuid.poll()
        while (retval is None):
            retval = uuid.poll()
        if retval is not 0:
            print >> sys.stderr, \
                "create-new-uuid exited with a nonstandard return value."
            sys.exit(1)

        #if we have ran this from a USB key, we might have syslinux which will
        #break our build
        if os.path.exists(mntdir + '/syslinux'):
            if os.path.exists(mntdir + '/isolinux'):
                #this means we might have been alternating between
                #recovery media formats too much
                self.walk_cleanup(mntdir + '/isolinux')
                os.rmdir(mntdir + '/isolinux')
            shutil.move(mntdir + '/syslinux', mntdir + '/isolinux')
        if os.path.exists(mntdir + '/isolinux/syslinux.cfg'):
            shutil.move(mntdir + '/isolinux/syslinux.cfg', mntdir + '/isolinux/isolinux.cfg')
        #FIXME^^^, this needs to learn how to do it without writing to the RP so the RP can be read only
        # possible solution is commented below:
        #if os.path.exists(mntdir + '/syslinux') and not os.path.exists(mntdir + '/isolinux'):
        #    subprocess.call(['mount', '-o', 'ro' ,'--bind', mntdir + '/syslinux', mntdir + '/isolinux'])

        #Arg list
        genisoargs=['genisoimage', '-o', iso,
            '-input-charset', 'utf-8',
            '-b', 'isolinux/isolinux.bin', '-c', 'isolinux/boot.catalog',
            '-no-emul-boot', '-boot-load-size', '4', '-boot-info-table',
            '-pad', '-r', '-J', '-joliet-long', '-N', '-hide-joliet-trans-tbl',
            '-cache-inodes', '-l',
            '-publisher', 'Dell Inc.',
            '-V', 'Dell Ubuntu Reinstallation Media',
            mntdir + '/',
            tmpdir + '/up/']

        #Boot sector for ISO
        shutil.copy(mntdir + '/isolinux/isolinux.bin', tmpdir)

        #Loop mount these UUIDs so that they are included on the disk
        subprocess.call(['mount', '-o', 'ro' ,'--bind', tmpdir + '/initrd.lz', mntdir + '/casper/initrd.lz'])
        subprocess.call(['mount', '-o', 'ro', '--bind', tmpdir + '/casper-uuid-generic', mntdir + '/.disk/casper-uuid-generic'])
        if os.path.exists(os.path.join(mntdir,'bto_version')):
            subprocess.call(['mount', '-o', 'ro', '--bind', tmpdir + '/bto_version', mntdir + '/bto_version'])
        else:
            genisoargs.append(os.path.join(tmpdir,'bto_version'))

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
            print >> sys.stderr, \
                "genisoimage exited with a nonstandard return value."
            sys.exit(1)

    @dbus.service.signal(DBUS_INTERFACE_NAME)
    def report_progress(self, progress_str, percent):
        '''Report ISO build progress to UI.
        '''
        return True
