# -*- coding: utf-8; Mode: Python; indent-tabs-mode: nil; tab-width: 4 -*-
#
# Copyright (C) 2008 Canonical Ltd.
# Portions imported from Ubiquity project

#Translation support
from gettext import gettext as _

from collections import namedtuple
import contextlib
import grp
import os
import pwd
import re
import subprocess
import syslog

#define this for compatibility between both modes
class InstallPlugin:
   def __init__(self, *args, **kwargs):
       pass

class PluginUI:
    # We define an init even if empty so that arguments that we give but are
    # not used don't cause an error.
    def __init__(self, *args, **kwargs):
        pass

class Plugin:
    # We define an init even if empty so that arguments that we give but are
    # not used don't cause an error.
    def __init__(self, *args, **kwargs):
        pass
    def preseed(self, this, that):
        pass

_dropped_privileges = 0

def set_groups_for_uid(uid):
    if uid == os.geteuid() or uid == os.getuid():
        return
    user = pwd.getpwuid(uid).pw_name
    try:
        os.setgroups([g.gr_gid for g in grp.getgrall() if user in g.gr_mem])
    except OSError:
        import traceback
        for line in traceback.format_exc().split('\n'):
            syslog.syslog(syslog.LOG_ERR, line)




def drop_privileges():
    global _dropped_privileges
    assert _dropped_privileges is not None
    if _dropped_privileges == 0:
        uid = os.environ.get('PKEXEC_UID')
        gid = None
        if uid is not None:
            uid = int(uid)
            set_groups_for_uid(uid)
            gid = pwd.getpwuid(uid).pw_gid
        if gid is not None:
            gid = int(gid)
            os.setegid(gid)
        if uid is not None:
            os.seteuid(uid)
    _dropped_privileges += 1


def regain_privileges():
    global _dropped_privileges
    assert _dropped_privileges is not None
    _dropped_privileges -= 1
    if _dropped_privileges == 0:
        os.seteuid(0)
        os.setegid(0)
        os.setgroups([])


@contextlib.contextmanager
def raised_privileges():
    """As regain_privileges/drop_privileges, but in context manager style."""
    regain_privileges()
    try:
        yield
    finally:
        drop_privileges()


def raise_privileges(func):
    """As raised_privileges, but as a function decorator."""
    from functools import wraps

    @wraps(func)
    def helper(*args, **kwargs):
        with raised_privileges():
            return func(*args, **kwargs)

    return helper


def execute(*args):
    """runs args* in shell mode. Output status is taken."""

    log_args = []
    log_args.extend(args)

    try:
        status = subprocess.call(log_args)
    except IOError as e:
        return False
    else:
        if status != 0:
            return False
        syslog.syslog(' '.join(log_args))
        return True


@raise_privileges
def execute_root(*args):
    return execute(*args)


