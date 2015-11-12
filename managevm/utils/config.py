from __future__ import division

import os, sys, re
from glob import glob
import math

from adminapi.dataset import query, DatasetError
from fabric.api import run, settings, hide, puts, prompt
from managevm.signals import send_signal
from managevm.utils.virtutils import get_virtconn
from managevm.utils.storage import get_volume_groups, get_logical_volumes
from managevm.utils.network import get_network_config
from managevm.utils.resources import get_meminfo, get_cpuinfo

def get_vm(hostname):
    """ Get VM from admintool by config['guest'] hostname.

        Returns admintool object."""

    try:
        vm = query(hostname=hostname).get()
    except DatasetError:
        raise Exception("VM '{0}' not found".format(hostname))

    return vm


def get_srchv(hostname):
    """ Get source Hypervisor from admintool by config['srchv'] hostname.

        Returns admintool object."""

    try:
        srchv = query(hostname=hostname).get()
    except DatasetError:
        raise Exception("Source Hypervisor '{0}' not found".format(config['srchv']))

    return srchv

def get_dsthv(hostname):
    """ Get destination Hypervisor from admintool by config['dsthv'] hostname.

        Returns admintool object."""

    try:
        dsthv = query(hostname=hostname, cancelled=False).get()
    except DatasetError:
        raise Exception("Destination Hypervisor '{0}' not found or is cancelled or of wrong servertype".format(hostname))

    return dsthv

def init_vm_config(config):
    """ Put some hardcoded defaults into config dictionary.
        Those parameters are required only for new VM.

        Returns nothing, data is stored in 'config' dictionary."""

    config['swap_size'] = 1024
    config['mailname'] = config['vm_hostname'] + '.ig.local'
    config['dns_servers']=['10.0.0.102', '10.0.0.85', '10.0.0.83']

def import_vm_disk(config):
    lvs = get_logical_volumes()
    for lv in lvs:
        if lv['name'].split('/')[3] == config['vm_hostname']:
            config['src_device'] = lv['name']
            config['disk_size_gib'] = int(math.ceil(lv['size_MiB'] / 1024))

def import_vm_config_from_admintool(config):
    """ Import configuration from Admintool.

        Returns nothing, data is stored in 'config' dictionary."""

    # TODO: Use those values directly instead of importing them
    config['mem'] = config['vm']['memory']
    config['num_cpu'] = config['vm']['num_cpu']
    config['os'] = config['vm']['os']
    config['disk_size_gib'] = config['vm']['disk_size_gib']

def import_vm_config_from_kvm(config):
    """ Import configuration from Hypervisor currently hosting the VM.

        Returns nothing, data is stored in 'config' dictionary."""

    # Some parameters must be retrieved from KVM.
    # Live migration will be performed, so they must be accurate.
    vm_obj = config['srchv_conn'].lookupByName(config['vm_hostname'])
    vm_info = vm_obj.info()

    config['max_mem'] = int(vm_info[1] / 1024)
    if config['max_mem'] == 0:
        config['max_mem'] = config['vm']['memory']

    config['mem']     = int(vm_info[2] / 1024)
    if config['mem'] == 0:
        config['mem'] = config['vm']['memory']

    config['num_cpu'] = vm_info[3]
    if config['num_cpu'] == 0:
        config['num_cpu'] = config['vm']['num_cpu']

    # Some we trust from Admintool
    config['os']      = config['srchv']['os']

    # And some must be retrieved from running source hypervisor OS
    import_vm_disk(config)

def import_vm_config_from_xen(config):
    """ Import configuration from Hypervisor currently hosting the VM.

        Returns nothing, data is stored in 'config' dictionary."""

    # Some parameters must be retrieved from KVM.
    # Data in Admintool is currently known to be inaccurate
    config['num_cpu'] = int(run('xm list --long {0} | grep \'(online_vcpus \' | sed -E \'s/[ a-z\(_]+ ([0-9]+)\)/\\1/\''.format(config['vm_hostname'])))
    config['mem'] =     int(run('xm list --long {0} | grep \'(memory \' | sed -E \'s/[ a-z\(_]+ ([0-9]+)\)/\\1/\''.format(config['vm_hostname'])))
    config['max_mem'] = config['mem']

    # Some we trust from Admintool
    config['os']      = config['srchv']['os']

    # But not for disk size
    import_vm_disk(config)

def check_dsthv_memory(config):
    """ Check various parameters of DstHV and VM memory.

        Returns nothing.
        Will raise an exception if there is not enough memory.
        Will modify config if all is fine. """

    config['mem_hotplug'] = False
    config['numa_interleave'] = False

    if config['dsthv']['hypervisor'] == 'kvm':

        # Check memory hotplugging capability.
        version = config['dsthv_conn'].getVersion()
        # According to documentation:
        # value is major * 1,000,000 + minor * 1,000 + release
        release = version % 1000
        minor = int(version/1000%1000)
        major = int(version/1000000%1000000)
        if major >= 2 and minor >=3 :
            config['mem_hotplug'] = True

        # Get amount of memory available to Hypervisor.
        # Start with what OS sees as total memory (not hardware installed memory)
        total_MiB = config['dsthv_conn'].getMemoryStats(-1)['total'] / 1024
        # Always keep extra 2GiB free for Hypervisor
        total_MiB -= 2*1024

        # Calculate memory used by other VMs.
        # We can not trust hv_conn.getFreeMemory(), sum up memory used by each VM instead
        used_KiB = 0
        for dom_id in config['dsthv_conn'].listDomainsID():
            dom = config['dsthv_conn'].lookupByID(dom_id)
            used_KiB += dom.maxMemory()
        free_MiB = total_MiB - used_KiB/1024
        if config['mem'] > free_MiB:
            raise Exception('Not enough memory. Destination Hypervisor has {0}MiB but VM requires {1}MiB'.format(free_MiB, config['mem']))

        if config['mem'] >= 0.5 * total_MiB:
            config['numa_interleave'] = True


def check_dsthv_cpu(config):
    cpuinfo = get_cpuinfo()
    num_cpus = len(cpuinfo)
    if config['num_cpu'] > num_cpus:
        raise Exception('Not enough CPUs. Destination Hypervisor has {0} but VM requires {1}.'.format(num_cpus, config['num_cpu']))


def check_vm_config(config):
    send_signal('config_created', config)

    if 'mem' not in config:
        raise Exception('"mem" is not set.')

    if config['mem'] < 1:
        raise Exception('"mem" is not greater than 0.')

    if 'max_mem' not in config:
        if config['mem'] > 12288:
            config['max_mem'] = config['mem'] + 10240
        else:
            config['max_mem'] = 16384

    if config['max_mem'] < 1:
        raise Exception('"max_mem" is not greater than 0.')

    if config['max_mem'] <= config['mem']:
        puts('Max Mem setting was wrong, fixing it')
        # TODO: remove this dup of code and set it with libvirt api
        if config['mem'] > 12288:
            config['max_mem'] = config['mem'] + 10240
        else:
            config['max_mem'] = 16384

    if 'num_cpu' not in config:
        raise Exception('"num_cpu" is not set.')

    if config['num_cpu'] < 1:
        raise Exception('"num_cpu" is not greater than 0')

    if 'os' not in config:
        raise Exception('"os" is not set.')

    if 'disk_size_gib' not in config:
        raise Exception('"disk_size_gib" is not set.')

    if config['disk_size_gib'] < 1:
        raise Exception('"disk_size_gib" is not greater than 0')

    if 'image' not in config:
        config['image'] = config['os'] + '-base.tar.gz'

    send_signal('config_finished', config)

