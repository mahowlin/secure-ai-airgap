# Three-interface validation VM for example-management tenant reachability tests.
# Deploy via .github/workflows/deploy-vsphere-vms.yaml with vm_set=tenant-endpoint-test-vms.

vsphere_server     = "example-infra-vc.example.com"
vsphere_user       = "administrator@example.com"
vsphere_datacenter = "EXAMPLE-DC"
vsphere_cluster    = "example-infra-hx"
vsphere_datastore  = "example-hx-ds-30TB"
vsphere_template   = "example-ubuntu-2404-endpoint-v1"
vsphere_folder     = null

vm_domain      = "example.com"
vm_dns_servers = ["10.0.0.53", "10.0.0.54"]

vms = {
  hcp-tenant-validation = {
    hostname = "hcp-tenant-validation"
    network_interfaces = [
      {
        network      = "example-production|AP-01|example-epg-vlan2130"
        ipv4_address = "198.19.31.164"
        ipv4_netmask = 23
      },
      {
        network      = "Tenant-A|AP-tenant-a-endpoints|EPG-tenant-a-endpoints"
        ipv4_address = "198.18.12.66"
        ipv4_netmask = 26
      },
      {
        network      = "Tenant-B|AP-tenant-b-endpoints|EPG-tenant-b-endpoints"
        ipv4_address = "198.18.14.66"
        ipv4_netmask = 26
      },
    ]
    ipv4_gateway = "10.0.0.1"
    cpu          = 2
    memory       = 4096
    disk_size    = 100
    annotation   = "Three-interface tenant validation VM for example-management management, Tenant-A, and Tenant-B reachability tests. Managed by saif-sys-admin Terraform."
  }
}
