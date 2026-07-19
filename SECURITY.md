# Security policy

## Reporting vulnerabilities

Please report security issues privately to `philipp@dreimann.net`. Do not include device
passwords, firmware images, backups, private addresses, or diagnostic logs in a public issue.

## Network threat model

Gira provisioning protocols were designed for trusted local networks. Some device operations
use plaintext HTTP Basic authentication. Other services use self-signed certificates that
pygira cannot verify against the public CA ecosystem, so TLS verification is currently
disabled for those connections.

Use pygira only from a trusted management network. Do not expose device management ports to
the internet or an untrusted client network. GDS library clients can opt into system CA
verification or provide an `ssl.SSLContext`; equivalent configuration-service support and
certificate fingerprint pinning remain planned.

## Stored credentials

`devices.toml` stores credentials in plaintext. Files created by `pygira config` use atomic
replacement and owner-only permissions on POSIX systems, but filesystem backups and privileged
users can still read them. Never commit or synchronize the file, and use a dedicated device
account with the least privilege supported by the device.
