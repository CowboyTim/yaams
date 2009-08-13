#!/usr/bin/python

# Yet Another Auto Mounter System (YAAMS)
#

import dbus
from dbus.mainloop.glib import DBusGMainLoop
import sys
import os
import gobject
from functools import partial
from subprocess import call

PIDPATH   = '/var/run/pymount.pid'
LOGFILE   = '/var/log/pymount.log'
MOUNTBASE = '/media'

logout = sys.stdout
logerr = sys.stderr

udi_to_dev_map = {}
dev_to_udi_map = {}
    
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
    
    dev = dict(zip(('name', 'mountpoint', 'fstype', 'options', 'dumpfreq', 'passnum'), fstab_entry_raw.split()))
    dev['uuid'] = uuid
    dev['uid'] = dev['gid'] = 0
    for o in dev['options'].split():
        if o.startswith('gid='):
            dev['gid'] = int(o[4:])
        elif o.startswith('uid='):
            dev['uid'] = int(o[4:])
    return dev

def runcmd(cmd):
    logout.write("command:{0}\n".format(cmd))
    logout.flush()
    retcode = call(cmd.split(), stdout=logout, stderr=logerr)
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
            udi_to_dev_map[udi] = dev
            dev_to_udi_map[dev['block']] = dev
            return
        if fsusage == 'filesystem':
            dev['mountpoint'] = dev_int.GetProperty("volume.label")
            if not dev['mountpoint']:
                dev['mountpoint'] = dev['uuid']
            dev['mountpoint'] = MOUNTBASE + '/' + dev['mountpoint']
            
            return dev

    return None

def eject_device(bus, block_dev, what, nop):
    print("eject:"+what+",block_dev:"+block_dev)
    if what != 'EjectPressed':
        return
    unmount_device(bus, dev_to_udi_map[block_dev]['udi'])
    runcmd('eject {0}'.format(block_dev))

def mount_device(bus, udi):
    print("mount "+udi)
    dev = get_mntpoint(bus, udi)
    if not dev:
        return
    try:
        if not os.path.isdir(dev['mountpoint']):
            os.mkdir(dev['mountpoint'], 0755)
        os.chown(dev['mountpoint'], dev['uid'], dev['gid'])
        
        cmd = 'mount -t {0} -o {1} {2} {3}'
        cmd = cmd.format(dev['fstype'], dev['options'], dev['block'], dev['mountpoint'])
        runcmd(cmd)

        # store it for later unmounting
        udi_to_dev_map[udi] = dev
        dev_to_udi_map[dev['block']] = dev
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
    del dev_to_udi_map[dev['block']]
    try:
        retcode = runcmd('umount {0}'.format(dev['mountpoint']))
        if retcode == 0:
            os.rmdir(dev['mountpoint'])
    except Exception, e:
        print(e)

def do_fork():
    try:
        pid = os.fork()
        if pid > 0:
            sys.exit(0)
    except OSError, e:
        print >>sys.stderr, "fork #1 failed: %d (%s)" % (e.errno, e.strerror)
        sys.exit(1)

    # daemonize this child process
    os.chdir("/")
    os.setsid()
    os.umask(0)

def start_loop():
    dbus_loop = DBusGMainLoop()

    bus = dbus.SystemBus(mainloop=dbus_loop)

    # get a HAL object and an interface to HAL to make function calls
    hal_obj = bus.get_object ('org.freedesktop.Hal', '/org/freedesktop/Hal/Manager')
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
            dev_int.connect_to_signal("Condition", partial(eject_device, bus, block_dev))


    # add the callbacks
    hal_manager.connect_to_signal("DeviceAdded",   partial(mount_device,   bus))
    hal_manager.connect_to_signal("DeviceRemoved", partial(unmount_device, bus))

    # start the main loop
    gloop = gobject.MainLoop()
    gloop.run()


if __name__ == '__main__':
    try:
        daemon = sys.argv[1] == '-d'
    except:
        daemon = False

    if daemon:
        do_fork()
        open(PIDPATH, 'w').write(os.getpid())
        logout = logerr = open('LOGFILE', 'a')
    
    start_loop()
