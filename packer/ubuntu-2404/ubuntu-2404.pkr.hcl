# Ubuntu 24.04 endpoint VM template for vSphere
# Build with: packer build -var-file=../../environments/example/packer/endpoint-template.pkrvars.hcl .

packer {
  required_version = ">= 1.10.0"
  required_plugins {
    vsphere = {
      version = ">= 1.3.0"
      source  = "github.com/hashicorp/vsphere"
    }
  }
}

# vSphere connection variables
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
  description = "vCenter password (use PKR_VAR_vsphere_password)"
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
  description = "vSphere datastore for template"
}

variable "vsphere_network" {
  type        = string
  description = "vSphere network/port group"
}

# ISO configuration
variable "iso_datastore" {
  type        = string
  description = "Datastore containing ISO"
}

variable "iso_path" {
  type        = string
  description = "Path to ISO on datastore"
}

# VM configuration
variable "vm_name" {
  type        = string
  default     = "ubuntu-2404-template"
  description = "Template VM name"
}

variable "vm_ip" {
  type        = string
  description = "Static IP for build VM (cleaned before template)"
}

variable "vm_gateway" {
  type        = string
  description = "Gateway IP"
}

variable "vm_netmask" {
  type        = string
  default     = "24"
  description = "Network mask (CIDR notation)"
}

variable "vm_dns" {
  type        = list(string)
  description = "DNS servers"
}

# SSH/user configuration
variable "ssh_username" {
  type        = string
  default     = "ubuntu"
  description = "SSH username for provisioning"
  validation {
    condition     = can(regex("^[a-z_][a-z0-9_-]{0,31}$", var.ssh_username))
    error_message = "The SSH username must be a safe Linux build account."
  }
}

variable "ssh_password" {
  type        = string
  sensitive   = true
  description = "SSH password for provisioning (temporary)"
  validation {
    condition     = length(var.ssh_password) > 0
    error_message = "An explicit runtime ssh_password value is required."
  }
}

variable "ssh_password_hash" {
  type        = string
  sensitive   = true
  description = "SHA-512 password hash for cloud-init"
  validation {
    condition     = length(var.ssh_password_hash) > 0
    error_message = "An explicit runtime ssh_password_hash value is required."
  }
}

variable "ssh_authorized_keys" {
  type        = list(string)
  description = "SSH public keys to authorize"
  validation {
    condition     = length(var.ssh_authorized_keys) > 0
    error_message = "An explicit runtime ssh_authorized_keys value is required."
  }
}

variable "created_by" {
  type        = string
  default     = "packer"
  description = "Creator tag"
}

locals {
  timestamp  = formatdate("YYYY-MM-DD-hhmm", timestamp())
  build_name = var.vm_name
}

source "vsphere-iso" "ubuntu-2404" {
  # vSphere connection
  vcenter_server      = var.vsphere_server
  username            = var.vsphere_user
  password            = var.vsphere_password
  insecure_connection = true

  # vSphere location
  datacenter = var.vsphere_datacenter
  cluster    = var.vsphere_cluster
  datastore  = var.vsphere_datastore

  # VM configuration
  vm_name              = local.build_name
  guest_os_type        = "ubuntu64Guest"
  firmware             = "efi"
  boot_order           = "cdrom,disk"
  CPUs                 = 4
  RAM                  = 8192
  RAM_reserve_all      = false
  disk_controller_type = ["pvscsi"]

  storage {
    disk_size             = 50000
    disk_thin_provisioned = true
  }

  network_adapters {
    network      = var.vsphere_network
    network_card = "vmxnet3"
  }

  # ISO configuration
  iso_paths = ["[${var.iso_datastore}] ${var.iso_path}"]

  # Boot configuration for Ubuntu 24.04 autoinstall
  boot_command = [
    "<enter>",
    "<wait45s>",
    "yes<enter>"
  ]
  boot_wait = "5s"

  # Cloud-init data as secondary CD-ROM using templatefile
  cd_content = {
    "meta-data" = file("${path.root}/http/meta-data")
    "user-data" = templatefile("${path.root}/http/user-data.pkrtpl.hcl", {
      vm_ip               = var.vm_ip
      vm_gateway          = var.vm_gateway
      vm_netmask          = var.vm_netmask
      vm_dns              = var.vm_dns
      ssh_username        = var.ssh_username
      ssh_password_hash   = var.ssh_password_hash
      ssh_authorized_keys = var.ssh_authorized_keys
    })
  }
  cd_label = "cidata"

  # SSH configuration
  ssh_username           = var.ssh_username
  ssh_password           = var.ssh_password
  ssh_timeout            = "30m"
  ssh_handshake_attempts = 100

  # Shutdown and template conversion
  shutdown_command = "sudo -n bash /tmp/finalize-endpoint-template.sh '${var.ssh_username}'"

  # Convert to template
  convert_to_template = true

  # VM notes/tags
  notes = "Ubuntu 24.04 LTS template\nCreated by: ${var.created_by}\nCreated at: ${local.timestamp}"
}

build {
  sources = ["source.vsphere-iso.ubuntu-2404"]

  # Wait for cloud-init to complete
  provisioner "shell" {
    inline = [
      "echo 'Waiting for cloud-init to complete...'",
      "cloud-init status --wait"
    ]
  }

  # Full system update and endpoint validation tooling install
  provisioner "shell" {
    inline = [
      "echo 'Running full system update...'",
      "sudo DEBIAN_FRONTEND=noninteractive apt-get update",
      "sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y",
      "sudo DEBIAN_FRONTEND=noninteractive apt-get dist-upgrade -y",
      "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y curl iputils-tracepath netcat-openbsd openssl traceroute open-vm-tools"
    ]
  }

  # Disable automatic updates for disconnected tenant endpoint networks
  provisioner "shell" {
    inline = [
      "echo 'Disabling automatic apt updates...'",
      "sudo tee /etc/apt/apt.conf.d/20auto-upgrades > /dev/null << 'EOF'",
      "APT::Periodic::Update-Package-Lists \"0\";",
      "APT::Periodic::Unattended-Upgrade \"0\";",
      "EOF",
      "sudo tee /etc/apt/apt.conf.d/99disable-periodic > /dev/null << 'EOF'",
      "APT::Periodic::Enable \"0\";",
      "APT::Periodic::Update-Package-Lists \"0\";",
      "APT::Periodic::Download-Upgradeable-Packages \"0\";",
      "APT::Periodic::AutocleanInterval \"0\";",
      "APT::Periodic::Unattended-Upgrade \"0\";",
      "EOF",
      "sudo systemctl stop apt-daily.service apt-daily.timer apt-daily-upgrade.service apt-daily-upgrade.timer unattended-upgrades.service || true",
      "sudo systemctl disable apt-daily.service apt-daily.timer apt-daily-upgrade.service apt-daily-upgrade.timer unattended-upgrades.service || true",
      "sudo systemctl mask apt-daily.service apt-daily.timer apt-daily-upgrade.service apt-daily-upgrade.timer unattended-upgrades.service || true"
    ]
  }

  # Disable cloud-init (cloud-init clean removes the marker, so touch after)
  provisioner "shell" {
    inline = [
      "echo 'Disabling cloud-init...'",
      "sudo cloud-init clean --logs",
      "sudo touch /etc/cloud/cloud-init.disabled"
    ]
  }

  # Clean apt cache
  provisioner "shell" {
    inline = [
      "echo 'Cleaning apt cache...'",
      "sudo apt-get clean",
      "sudo rm -rf /var/lib/apt/lists/*"
    ]
  }

  # Clean network configuration
  provisioner "shell" {
    inline = [
      "echo 'Cleaning network configuration...'",
      "sudo rm -f /etc/netplan/*.yaml",
      "sudo truncate -s 0 /etc/machine-id",
      "sudo rm -f /var/lib/dbus/machine-id"
    ]
  }

  # Clear SSH host keys
  provisioner "shell" {
    inline = [
      "echo 'Clearing SSH host keys...'",
      "sudo rm -f /etc/ssh/ssh_host_*"
    ]
  }

  # Create systemd service to regenerate SSH host keys on first boot
  provisioner "shell" {
    inline = [
      "echo 'Creating SSH key regeneration service...'",
      "sudo tee /etc/systemd/system/regenerate-ssh-keys.service > /dev/null << 'EOF'",
      "[Unit]",
      "Description=Regenerate SSH host keys on first boot",
      "Before=ssh.service",
      "ConditionPathExists=!/etc/ssh/ssh_host_rsa_key",
      "",
      "[Service]",
      "Type=oneshot",
      "ExecStart=/usr/bin/ssh-keygen -A",
      "RemainAfterExit=yes",
      "",
      "[Install]",
      "WantedBy=multi-user.target",
      "EOF",
      "sudo systemctl enable regenerate-ssh-keys.service"
    ]
  }

  # Clean temporary files
  provisioner "shell" {
    inline = [
      "echo 'Cleaning temporary files...'",
      "sudo rm -rf /tmp/*",
      "sudo rm -rf /var/tmp/*",
      "sudo rm -f /var/log/*.log",
      "sudo rm -f /var/log/**/*.log"
    ]
  }

  # Final validation
  provisioner "shell" {
    inline = [
      "echo '=== Template Validation ==='",
      "echo 'Checking cloud-init disabled:'",
      "ls -la /etc/cloud/cloud-init.disabled || echo 'WARNING: cloud-init.disabled not found'",
      "echo 'Checking network config cleaned:'",
      "ls -la /etc/netplan/ || echo 'netplan dir empty'",
      "echo 'Checking machine-id cleared:'",
      "cat /etc/machine-id || echo 'machine-id cleared'",
      "echo 'Checking SSH key regeneration service:'",
      "systemctl is-enabled regenerate-ssh-keys.service",
      "echo 'Checking endpoint validation tools:'",
      "for tool in curl nc openssl tracepath traceroute; do command -v $tool; done",
      "echo 'Checking open-vm-tools:'",
      "dpkg -l | grep open-vm-tools",
      "echo 'Checking automatic apt jobs are disabled:'",
      "systemctl is-enabled apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service 2>/dev/null | grep -Ev 'disabled|masked' && exit 1 || true",
      "echo '=== Validation complete ==='",
      "echo 'Template ready for conversion'"
    ]
  }
  # Upload after /tmp cleanup. Finalization runs in the final authenticated
  # shutdown session, so password removal cannot break an earlier reconnect.
  provisioner "file" {
    source      = "${path.root}/../../scripts/finalize-endpoint-template.sh"
    destination = "/tmp/finalize-endpoint-template.sh"
  }

}
