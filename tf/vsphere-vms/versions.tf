terraform {
  required_version = ">= 1.6.0"

  required_providers {
    vsphere = {
      source  = "vmware/vsphere"
      version = "2.15.0"
    }
  }

  backend "http" {}
}
