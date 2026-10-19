provider "vsphere" {
  vsphere_server       = var.vsphere_server
  user                 = var.vsphere_user
  password             = var.vsphere_password
  allow_unverified_ssl = true
}

data "vsphere_datacenter" "dc" {
  name = var.vsphere_datacenter
}

data "vsphere_compute_cluster" "cluster" {
  name          = var.vsphere_cluster
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_datastore" "datastore" {
  name          = var.vsphere_datastore
  datacenter_id = data.vsphere_datacenter.dc.id
}

data "vsphere_virtual_machine" "template" {
  name          = var.vsphere_template
  datacenter_id = data.vsphere_datacenter.dc.id
}

locals {
  vm_network_interfaces = {
    for name, vm in var.vms : name => coalesce(vm.network_interfaces, [{
      network      = vm.network
      ipv4_address = vm.ipv4_address
      ipv4_netmask = vm.ipv4_netmask
    }])
  }

  network_names = toset(flatten([
    for interfaces in values(local.vm_network_interfaces) : [
      for interface in interfaces : interface.network
    ]
  ]))

  vm_disks = {
    for name, vm in var.vms : name => coalesce(vm.disks, [{
      label = "disk0"
      size  = coalesce(vm.disk_size, var.default_vm_disk_size)
    }])
  }
}

data "vsphere_network" "networks" {
  for_each      = local.network_names
  name          = each.value
  datacenter_id = data.vsphere_datacenter.dc.id
}

resource "vsphere_virtual_machine" "vm" {
  for_each         = var.vms
  name             = each.key
  resource_pool_id = data.vsphere_compute_cluster.cluster.resource_pool_id
  datastore_id     = data.vsphere_datastore.datastore.id
  folder           = var.vsphere_folder

  annotation = coalesce(each.value.annotation, "Managed by Terraform (saif-sys-admin generic vSphere VM workflow)")

  num_cpus = coalesce(each.value.cpu, var.default_vm_cpu)
  memory   = coalesce(each.value.memory, var.default_vm_memory)
  guest_id = data.vsphere_virtual_machine.template.guest_id
  firmware = data.vsphere_virtual_machine.template.firmware

  scsi_type = data.vsphere_virtual_machine.template.scsi_type

  dynamic "network_interface" {
    for_each = local.vm_network_interfaces[each.key]
    content {
      network_id   = data.vsphere_network.networks[network_interface.value.network].id
      adapter_type = data.vsphere_virtual_machine.template.network_interface_types[0]
    }
  }

  dynamic "disk" {
    for_each = local.vm_disks[each.key]
    content {
      label            = coalesce(disk.value.label, "disk${disk.key}")
      size             = disk.value.size
      unit_number      = disk.key
      thin_provisioned = true
    }
  }

  clone {
    template_uuid = data.vsphere_virtual_machine.template.id

    customize {
      linux_options {
        host_name = each.value.hostname
        domain    = var.vm_domain
      }

      dynamic "network_interface" {
        for_each = local.vm_network_interfaces[each.key]
        content {
          ipv4_address = network_interface.value.ipv4_address
          ipv4_netmask = network_interface.value.ipv4_netmask
        }
      }

      ipv4_gateway    = each.value.ipv4_gateway
      dns_server_list = var.vm_dns_servers
    }
  }

  lifecycle {
    ignore_changes = [
      clone[0].template_uuid,
    ]
  }
}
