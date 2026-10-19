#cloud-config
autoinstall:
  version: 1
  locale: en_US.UTF-8
  keyboard:
    layout: us

  # Network configuration - static IP during build
  network:
    version: 2
    ethernets:
      ens192:
        addresses:
          - ${vm_ip}/${vm_netmask}
        gateway4: ${vm_gateway}
        nameservers:
          addresses:
%{ for dns in vm_dns ~}
            - ${dns}
%{ endfor ~}

  # Storage - single disk, use entire disk
  storage:
    layout:
      name: direct

  # User configuration
  identity:
    hostname: ubuntu-template
    username: ${ssh_username}
    # Temporary private build hash; locked in final shutdown before conversion.
    password: "${ssh_password_hash}"

  # SSH configuration
  ssh:
    install-server: true
    allow-pw: true
    authorized-keys:
%{ for key in ssh_authorized_keys ~}
      - ${jsonencode(key)}
%{ endfor ~}

  # Packages to install during setup
  packages:
    - open-vm-tools
    - openssh-server
    - curl
    - wget
    - sudo

  # Late commands run after installation
  late-commands:
    # Enable passwordless sudo for ubuntu user
    - echo '${ssh_username} ALL=(ALL) NOPASSWD:ALL' > /target/etc/sudoers.d/${ssh_username}
    - chmod 440 /target/etc/sudoers.d/${ssh_username}
    # Ensure SSH is enabled
    - curtin in-target --target=/target -- systemctl enable ssh

  # Reboot after installation
  shutdown: reboot
