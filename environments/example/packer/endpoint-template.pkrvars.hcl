# SAIF environment configuration for Packer templates
# Note: vsphere_password should be set via PKR_VAR_vsphere_password environment variable

# vSphere connection
vsphere_server     = "example-infra-vc.example.com"
vsphere_user       = "administrator@example.com"
vsphere_datacenter = "EXAMPLE-DC"
vsphere_cluster    = "example-infra-hx"
vsphere_datastore  = "example-hx-ds-30TB"
vsphere_network    = "example-production|AP-01|example-epg-vlan2130"

# ISO location
iso_datastore = "DS_iSCSI_Nimble-01_03"
iso_path      = "Ubuntu 24.04/ubuntu-24.04.3-live-server-amd64.iso"

# VM network (temporary, cleaned before template conversion)
vm_ip      = "10.0.0.155"
vm_gateway = "10.0.0.1"
vm_netmask = "23"
vm_dns     = ["10.0.0.53", "10.0.0.54"]

# Template configuration
vm_name    = "example-ubuntu-2404-endpoint-v1"
created_by = "saif-sys-admin-packer-pipeline"

# SSH configuration
ssh_username = "ubuntu"

# Authentication is supplied only through private per-run Packer variables.
