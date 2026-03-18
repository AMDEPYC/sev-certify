# SNP Guest Attestation UKI

The guest-image UKI's include a systemd service that executes the SNP regular attestation workflow on the SNP enabled guest using the [ snpguest tool ](https://github.com/virtee/snpguest.git). To certify that SNP attestation is completely supported by the different distros, the following tasks need to be completed successfully:

 1. Verify SNP enablement in guest by probing the SEV guest MSRs.
 2. Get the Attestation Report (AR).
 3. Request the appropriate certificate chain (ARK, ASK) and endorsement key (VCEK).
 4. Verify the chain and the VCEK.
 5. Verify the signature and TCB of the AR using the VCEK.
 6. Verify the request data.
 7. Verify the measurement on the AR.

## Testing

The following are the steps on how to set up your guest boot to succesfully run the guest-image with the snpguest service:

1. Download the or install the latest version of snpguest for your guest distro inside of your host.

    a) Example download
    ```sh
    $  wget https://github.com/virtee/snpguest/releases/download/v0.9.1/snpguest -O snpguest && chmod +x snpguest
	```

2. Calculate the expected SNP guest measurement for your guest image:
    ```sh
    $ ./snpguest generate measurement \
      --ovmf /usr/share/edk2/ovmf/OVMF.amdsev.fd \
      --vcpu-type "EPYC-V4" \
      --kernel guest-fedora-41.efi \
      --measurement-file expected-measurement.txt \
      --output-format hex
    ```

3. Hash the calculated SNP guest measurement in order to pass as a boot parameter:

	a) Get the SNP Guest Measurement from the file
	```sh
	$ calculated_measurement_hex=$(awk -F "0x" '{print $2}' expected-measurement.txt )
	```

	b) Generate the SHA-256 hash digest of the `hex-calculated-measurement` in the base64 format:
	```sh
	$ guest_measurement_sha256sum=$(echo ${calculated_measurement_hex} | sha256sum | cut -d ' ' -f 1 | xxd -r -p | base64 )
	```

4. Boot the SNP guest image with the calculated measurement:
    c) Boot the SNP guest image, and pass the calculated measurement as the host-data parameter
    ```sh
    $ qemu-system-x86_64 \
        -enable-kvm \
        -cpu EPYC-v4 \
        -nographic \
        -machine memory-encryption=sev0 \
        -object memory-backend-memfd,id=ram1,size=2048M \
        -machine memory-backend=ram1 \
        -object sev-snp-guest,id=sev0,cbitpos=51,reduced-phys-bits=1,kernel-hashes=on,host-data=${guest_measurement_sha256sum} \
        -bios /usr/share/edk2/ovmf/OVMF.amdsev.fd  \
        -kernel guest-fedora-41.efi
    ```