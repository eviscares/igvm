class IGVMError(Exception):
    pass


class ConfigError(IGVMError):
    """Indicates an error with the admintool configuration."""
    pass


class HypervisorError(IGVMError):
    """Something went wrong on the hypervisor."""
    pass


class NetworkError(IGVMError):
    pass


class RemoteCommandError(IGVMError):
    """A command on the remote host failed."""
    pass


class StorageError(IGVMError):
    """Something related to storage went wrong."""
    pass


class InvalidStateError(IGVMError):
    """Host state is invalid for the requested operation."""
    pass


class TimeoutError(IGVMError):
    """An operation timed out."""
    pass


class InconsistentAttributeError(IGVMError):
    """An attribute on the VM differs from the excepted value from
    Serveradmin."""
    def __init__(self, vm, attribute, actual_value):
        self.hostname = vm.hostname
        self.attribute = attribute
        self.actual_value = actual_value
        self.config_value = vm.admintool[attribute]
        assert self.config_value != self.actual_value

    def __str__(self):
        return (
            'Attribute "{}" on {} is out of sync: '
            '{} (config) != {} (actual)'
            .format(
                self.attribute,
                self.hostname,
                self.config_value,
                self.actual_value,
            )
        )