import os
import uuid

from fabric.api import env, run, puts
from fabric.contrib.files import exists

from jinja2 import Environment, PackageLoader

from managevm.utils import cmd, fail_gracefully
from managevm.utils.template import upload_template
from managevm.utils.virtutils import get_virtconn, close_virtconns
from managevm.utils.resources import get_cpuinfo

run = fail_gracefully(run)
exists = fail_gracefully(exists)

class HypervisorError(Exception):
    pass

def create_sxp(hostname, num_vcpus, mem_size, max_mem, device, sxp_file=None):
    if sxp_file is None:
        sxp_file = 'etc/xen/domains/hostname.sxp'
    dest = os.path.join('/etc/xen/domains', hostname + '.sxp')
    upload_template(sxp_file, dest, {
        'hostname': hostname,
        'num_vcpus': num_vcpus,
        'mem_size': mem_size,
        'max_mem': max_mem,
        'device': device,
    })

def start_machine_xm(hostname):
    sxp_file = os.path.join('/etc/xen/domains', hostname + '.sxp')
    run(cmd('xm create {0}', sxp_file))

def create_domain_xml(hostname, num_vcpus, mem_size, max_mem, vlan, device):
    jenv = Environment(loader=PackageLoader('buildvm', '../templates'))
    domain_xml = jenv.get_template('libvirt/domain.xml').render(**{
        'hostname': hostname,
        'uuid': uuid.uuid1(),
        'num_vcpus': num_vcpus,
        'mem_size': mem_size,
        'max_mem': max_mem,
        'device': device,
        'vlan': vlan,
    })
    return domain_xml

def create_domain(domain_xml, hypervisor):
    conn = get_virtconn(env.host_string, hypervisor)
    puts('Defining domain on libvirt')
    conn.defineXML(domain_xml)
    
    # Refresh storage pools to register the vm image
    for pool_name in conn.listStoragePools():
        pool = conn.storagePoolLookupByName(pool_name)
        pool.refresh(0)

def start_machine_libvirt(hostname, hypervisor):
    conn = get_virtconn(env.host_string, hypervisor)
    puts('Starting domain on libvirt')
    domain = conn.lookupByName(hostname)
    domain.create()

def create_definition(hostname, num_vcpus, mem_size, max_mem, vlan, device, hypervisor, hypervisor_extra):
    if hypervisor == 'kvm':
        xml = create_domain_xml(hostname, num_vcpus, mem_size, max_mem, vlan, device)
        return create_domain(xml, hypervisor)
    elif hypervisor == 'xen':
        sxp_file = hypervisor_extra.get('sxp_file')
        return create_sxp(hostname, num_vcpus, mem_size, max_mem, device, sxp_file)
    else:
        raise ValueError('Not a valid hypervisor: {0}'.format(hypervisor))

def start_machine(hostname, hypervisor):
    if hypervisor == 'kvm':
        start_machine_libvirt(hostname, hypervisor)
    elif hypervisor == 'xen':
        start_machine_xm(hostname)
    else:
        raise ValueError('Not a valid hypervisor: {0}'.format(hypervisor))

def check_dsthv_mem(config):
    if config['dsthv']['hypervisor'] == 'kvm':
        conn = config['dsthv_conn']
        # Always keep extra 2GiB free
        free_MiB = (conn.getFreeMemory() / 1024 / 1024) - 2048
        if config['mem'] > (free_MiB):
            # Avoid ugly error messages
            close_virtconns()
            raise HypervisorError('Not enough memory. Destination Hypervisor has {0}MiB but VM requires {1}MiB'.format(free_MiB, config['mem']))
    # Add statements to check hypervisor different than kvm

def check_dsthv_cpu(config):
    cpuinfo = get_cpuinfo()
    num_cpus = len(cpuinfo)
    if config['num_cpu'] > num_cpus:
        raise Exception('Not enough CPUs. Destination Hypervisor has {0} but VM requires {1}.'.format(num_cpus, config['num_cpu']))