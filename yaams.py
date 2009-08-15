#!/usr/bin/python -W ignore::DeprecationWarning

# License
#
# Yet Another Auto Mounter System (YAAMS) is distributed under the zlib/libpng
# license, which is OSS (Open Source Software) compliant.
#
# Copyright (C) 2009 Tim Aerts
#
# This software is provided 'as-is', without any express or implied
# warranty.  In no event will the authors be held liable for any damages
# arising from the use of this software.
#
# Permission is granted to anyone to use this software for any purpose,
# including commercial applications, and to alter it and redistribute it
# freely, subject to the following restrictions:
#
# 1. The origin of this software must not be misrepresented; you must not
#    claim that you wrote the original software. If you use this software
#    in a product, an acknowledgment in the product documentation would be
#    appreciated but is not required.
# 2. Altered source versions must be plainly marked as such, and must not be
#    misrepresented as being the original software.
# 3. This notice may not be removed or altered from any source distribution.
#
# Tim Aerts <aardbeiplantje@gmail.com>

# PLEDGE:
#
# Yet Another Auto Mounter System (YAAMS) is an automounter, written in python.
# It is loosly based on the pymount.py script from Mashi. The original script
# can only mount devices/volumes/storage devices when they are in /etc/fstab.
# The original script also doesn't support cdroms being ejected. This script
# uses the label, and if not found, the UUID to me mounted under /media. Maybe
# in the future, both can be merged if needed. It is not my intention to manage
# yet another auto mounter just for fun. I also like the idea of it being
# written in a higher level VM language, especially for things like this.
#
#  Tim - 2009/08/13

# TODO:
#
#   * still look at writing udev rules: no need for HAL/DBus then ;-)
#   * write decent docs
#   * write usage/cmdline option parsing
#   * configurable mount options, not via fstab
#   * implement locking with a PID file
#   * implement /etc/init.d/yaams {start|stop|reload} behavior
#   * implement better logging (perhaps with a debugging possibility)
#   * make sure this thing gets out of hand bulky ;-)
#   * implement signal behavior: INT|TERM clean exit, instead of err raise
#   * more strict error/exception behavior
#   * all mounts are mounted 'ro' : read-only. Better fix that someday for
#     usefullness. (unless it has an fstab entry nvdr.)

import sys
import os

from gobject    import MainLoop
from functools  import partial
from subprocess import call
import dbus
from dbus.mainloop.glib import DBusGMainLoop

LOGFILE   = '/var/log/yaams.log'
MOUNTBASE = '/media'

logout = sys.stdout
logerr = sys.stderr

udi_to_dev_map = {}
blk_to_dev_map = {}
mnt_to_dev_map = {}

i = 0
    
def get_fstab_dev(dev_udi):
    uuid = dev_udi[dev_udi.rfind('volume_uuid_')+12:].replace('_','-')
    fstab_entry_raw = None
    fstab = open('/etc/fstab', 'r')
    for line in fstab:
        if line.startswith('UUID='+uuid):
            fstab_entry_raw = line.strip()
            break
    fstab.close()
    if not fstab_entry_raw:
        return None
    
    dev = dict(zip(('name', 'mountpoint', 'fstype', \
                    'options', 'dumpfreq', 'passnum'), \
                   fstab_entry_raw.split()))
    dev['uuid'] = uuid
    dev['uid'] = dev['gid'] = 0
    for o in dev['options'].split():
        if o.startswith('gid='):
            dev['gid'] = int(o[4:])
        elif o.startswith('uid='):
            dev['uid'] = int(o[4:])
    return dev

def runcmd(cmd_args):
    cmd = ''.join(cmd_args)
    logout.write("command:{0}\n".format(cmd))
    logout.flush()
    retcode = call(cmd_args, stdout=logout, stderr=logerr)
    if retcode != 0:
        logout.write("return code:{0}\n".format(retcode))
    return retcode

def get_mntpoint(bus, udi):
    print(udi)
    dev = get_fstab_dev(udi)
    if not dev:
        dev = { 
            'uid' : 0, 
            'gid' : 0, 
            'udi' : udi,
            'options' : 'noatime,nodiratime,ro', 
        }
    
    dev_obj = bus.get_object('org.freedesktop.Hal', udi)
    dev_int = dbus.Interface (dev_obj, 'org.freedesktop.Hal.Device')
    if dev_int.PropertyExists("volume.fsusage"):
        fsusage = dev_int.GetProperty("volume.fsusage")
        is_mounted = dev_int.GetProperty('volume.is_mounted')
        dev['block']  = dev_int.GetProperty("block.device")
        dev['fstype'] = dev_int.GetProperty('volume.fstype')
        dev['uuid']   = dev_int.GetProperty('volume.uuid')
        if is_mounted:
            print("is mounted:"+dev['block'])
            dev['mountpoint'] = dev_int.GetProperty('volume.mount_point')
            udi_to_dev_map[udi]               = dev
            blk_to_dev_map[dev['block']]      = dev
            mnt_to_dev_map[dev['mountpoint']] = dev
            return
        if fsusage == 'filesystem':
            
            # search for a vendor
            vendor = find_vendor_and_product(bus, udi)
            print(vendor)

            size = None
            if dev_int.PropertyExists('volume.size'):
                size = dev_int.GetProperty('volume.size')
                if size:
                    vendor += ' (' + str(size/(1000*1000*1000)) + ' GB)'

            # take the label as a valid mountpoint
            dev['mountpoint'] = dev_int.GetProperty("volume.label")
            if not dev['mountpoint']:
                dev['mountpoint'] = vendor
            else:
                dev['mountpoint'] = dev['mountpoint'] + ' (' + vendor + ')'
                
            # prefix
            dev['mountpoint'] = MOUNTBASE + '/' + dev['mountpoint']
    
            if dev['mountpoint'] in mnt_to_dev_map:
                global i
                i += 1
                dev['mountpoint'] = dev['mountpoint']+'_'+str(i)
            
            return dev

    return None

def find_vendor_and_product(bus, udi):
    print("find_vendor_and_product:"+udi)
    dev_obj = bus.get_object('org.freedesktop.Hal', udi)
    dev_int = dbus.Interface (dev_obj, 'org.freedesktop.Hal.Device')

    vendor    = None
    subsystem = None
    if dev_int.PropertyExists('info.category'):
        subsystem = dev_int.GetProperty('info.category') + '.vendor'
    elif dev_int.PropertyExists('info.subsystem'):
        subsystem = dev_int.GetProperty('info.subsystem') + '.vendor'
        if subsystem == 'usb.vendor':
            subsystem = None
    if subsystem and dev_int.PropertyExists(subsystem):
        vendor = dev_int.GetProperty(subsystem)
        if vendor:
            product = dev_int.GetProperty('info.product')
            vendor  = product + ' - ' + vendor
    while not vendor:
        # got find the parent
        new_udi = dev_int.GetProperty('info.parent')
        if new_udi == udi:
            return None
        return find_vendor_and_product(bus, new_udi)

    return vendor
    

def eject_device(bus, block_dev, what, nop):
    print("eject:"+what+",block_dev:"+block_dev)
    if what != 'EjectPressed':
        return
    unmount_device(bus, blk_to_dev_map[block_dev]['udi'])
    runcmd(['eject', block_dev])

def mount_device(bus, udi):
    print("mount "+udi)
    dev = get_mntpoint(bus, udi)
    if not dev:
        return
    try:
        if not os.path.isdir(dev['mountpoint']):
            os.mkdir(dev['mountpoint'], 0755)
        os.chown(dev['mountpoint'], dev['uid'], dev['gid'])

        # ntfs-3g specifics: users aren't allowed to read the files and
        # directories with the default options. We change that here.  According
        # to the doc, dmask/fmask is only for vfat. It is also not listed in
        # the volume.mount.ntfs.valid_options from HAL. However, it seems to
        # work.
        if dev['fstype'] == 'ntfs-3g':
            if dev['options']:
                dev['options'] += ','
            dev['options'] += 'dmask=0222,fmask=0333'
        
        runcmd(['mount', '-t', dev['fstype'], \
                        '-o', dev['options'], \
                         dev['block'], \
                         dev['mountpoint']])

        # store it for later unmounting
        udi_to_dev_map[udi]               = dev
        blk_to_dev_map[dev['block']]      = dev
        mnt_to_dev_map[dev['mountpoint']] = dev
    except Exception, e:
        print(e)
        
def unmount_device(bus, udi):
    print("unmount "+udi)
    if not udi in udi_to_dev_map:
        print("unmount "+udi+" -> wasn't mounted by me")
        return
    dev = udi_to_dev_map[udi]
    print(dev)
    print("unmount:"+dev['block'])
    del udi_to_dev_map[udi]
    del blk_to_dev_map[dev['block']]
    del mnt_to_dev_map[dev['mountpoint']]
    try:
        retcode = runcmd(['umount', dev['mountpoint']])
        if retcode == 0:
            os.rmdir(dev['mountpoint'])
    except Exception, e:
        print(e)

def do_fork():
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
    except OSError, err:
        print("fork failed: {0} ({1})".format(err.errno, err.strerror))
        sys.exit(1)

    # daemonize this child process
    os.chdir("/")
    os.setsid()
    os.umask(0)
    null = os.open('/dev/null', os.O_RDWR)
    for fd in range(3):
        os.close(fd)
        os.dup2(null, fd)

def loop():
    bus = dbus.SystemBus(mainloop=DBusGMainLoop())

    # get a HAL object and an interface to HAL to make function calls
    hal_obj = bus.get_object ('org.freedesktop.Hal', \
                              '/org/freedesktop/Hal/Manager')
    hal_manager = dbus.Interface (hal_obj, 'org.freedesktop.Hal.Manager')

    # get all the devices that are volumes and premount them
    for d in hal_manager.FindDeviceByCapability('volume'):
        mount_device(bus, d)

    # get all the that support cdroms: add the EjectPressed handler
    for udi in hal_manager.FindDeviceByCapability('storage'):
        dev_obj = bus.get_object('org.freedesktop.Hal', udi)
        dev_int = dbus.Interface (dev_obj, 'org.freedesktop.Hal.Device')
        if dev_int.GetProperty("storage.drive_type") == 'cdrom':
            block_dev = dev_int.GetProperty("block.device")
            dev_int.connect_to_signal("Condition", \
                                      partial(eject_device, bus, block_dev))


    # add the callbacks
    hal_manager.connect_to_signal("DeviceAdded",   \
                                  partial(mount_device,   bus))
    hal_manager.connect_to_signal("DeviceRemoved", \
                                  partial(unmount_device, bus))

    # start the main loop, sadly still with gobject
    MainLoop().run()

if __name__ == '__main__':
    try:
        daemon = sys.argv[1] == '-d'
    except:
        daemon = False

    if daemon:
        do_fork()
        logout = logerr = open('LOGFILE', 'a')
    
    loop()
