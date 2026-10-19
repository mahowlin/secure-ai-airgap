# Minimal one-interface cleanup VM for producing the reusable validation template.
# Deploy via .github/workflows/deploy-vsphere-vms.yaml with vm_set=runner-template-cleanup-vm.

vsphere_server     = "example-infra-vc.example.com"
vsphere_user       = "administrator@example.com"
vsphere_datacenter = "EXAMPLE-DC"
vsphere_cluster    = "example-infra-hx"
vsphere_datastore  = "example-hx-ds-30TB"
vsphere_template   = "github-runner-template"
vsphere_folder     = null

vm_domain      = "example.com"
vm_dns_servers = ["10.0.0.53", "10.0.0.54"]

vms = {
  saif-runner-template-cleanup = {
    hostname = "saif-runner-template-cleanup"
    network_interfaces = [
      {
        network      = "example-production|AP-01|example-epg-vlan2130"
        ipv4_address = "198.19.31.163"
        ipv4_netmask = 23
      },
    ]
    ipv4_gateway = "10.0.0.1"
    cpu          = 2
    memory       = 4096
    disks = [
      {
        label = "disk0"
        size  = 100
      },
    ]
    annotation = "Temporary cleanup VM cloned from github-runner-template to produce a reusable validation VM template. Managed by saif-sys-admin Terraform."
  }
}
