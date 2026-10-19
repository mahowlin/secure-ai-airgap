output "vm_access" {
  description = "Reusable VM access details without secrets"
  value = {
    for name, vm in vsphere_virtual_machine.vm : name => {
      hostname = var.vms[name].hostname
      ip       = local.vm_network_interfaces[name][0].ipv4_address
      networks = [for interface in local.vm_network_interfaces[name] : interface.network]
      id       = vm.id
    }
  }
}
