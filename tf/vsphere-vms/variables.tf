variable "vsphere_server" {
  type        = string
  description = "vCenter server hostname"
}

variable "vsphere_user" {
  type        = string
  description = "vCenter username"
}

variable "vsphere_password" {
  type        = string
  sensitive   = true
  description = "vCenter password"
}

variable "vsphere_datacenter" {
  type        = string
  description = "vSphere datacenter name"
}

variable "vsphere_cluster" {
  type        = string
  description = "vSphere cluster name"
}

variable "vsphere_datastore" {
  type        = string
  description = "vSphere datastore name"
}

variable "vsphere_template" {
  type        = string
  description = "VM template name"
}

variable "vsphere_folder" {
  type        = string
  default     = null
  description = "VM folder path"
}

variable "vm_domain" {
  type        = string
  description = "DNS domain for guest customization"
}

variable "vm_dns_servers" {
  type        = list(string)
  description = "DNS servers for guest customization"
}

variable "default_vm_cpu" {
  type        = number
  default     = 2
  description = "Default vCPU count"
}

variable "default_vm_memory" {
  type        = number
  default     = 4096
  description = "Default memory in MB"
}

variable "default_vm_disk_size" {
  type        = number
  default     = 40
  description = "Default OS disk size in GB"
}

variable "vms" {
  description = "VMs to deploy from the shared template"
  type = map(object({
    hostname = string
    network_interfaces = optional(list(object({
      network      = string
      ipv4_address = string
      ipv4_netmask = number
    })))
    network      = optional(string)
    ipv4_address = optional(string)
    ipv4_netmask = optional(number)
    ipv4_gateway = string
    cpu          = optional(number)
    memory       = optional(number)
    disk_size    = optional(number)
    disks = optional(list(object({
      label = optional(string)
      size  = number
    })))
    annotation = optional(string)
  }))

  validation {
    condition = alltrue([
      for vm in values(var.vms) :
      vm.network_interfaces != null || (vm.network != null && vm.ipv4_address != null && vm.ipv4_netmask != null)
    ])
    error_message = "Each VM must define either network_interfaces or legacy network/ipv4_address/ipv4_netmask fields."
  }
}
